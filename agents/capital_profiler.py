"""
agents/capital_profiler.py — 资金画像系统（Phase 1：个股级）

回答：这只股票当前被什么资金主导？正/负反馈？持续性如何？
输出结构统一：meta / answer(dominant_capital, feedback, persistence, status) /
structure_score / capital_details / key_signals / warnings / data_quality。

设计见 docs/capital_profiler.md（基于 reference/资金画像0.1.md 刷新，匹配本项目数据与接口）。
数据不足时如实返回 undetermined（S1~S6）并给出原因与建议。

Phase 1 范围：level='stock'；板块/大类和 IC 评估为 Phase 2/3。
"""
import logging

import pandas as pd

import data.store as store
import data.store_moneyflow as store_mf
from agents.smart_money_agent import (
    LABEL_HOT,
    LABEL_INST,
    LABEL_NORTH,
    LABEL_QUANT,
    _norm_code6,
    classify_seat,
    load_seat_episodes,
)

logger = logging.getLogger(__name__)

DISCLAIMER = "本画像基于公开数据与规则化分析生成，仅供研究参考，不构成任何投资建议。"

LOOKBACK_DEFAULT = 60
MIN_TRADING_DAYS = 20

# 方向判定阈值（业界通用，对齐 reference/资金画像0.1.md 11.3 thresholds.yaml）
THR_LHB = 5e7          # 游资/量化/机构净额 5000 万
THR_NORTH = 3e7        # 陆股通净买入 3000 万
THR_MARGIN = 0.03      # 融资余额变动率 3%
THR_INDUSTRIAL = 1e8   # 产业资本（回购/增持）1 亿

_CAT_NAME = {
    "hot_money": "知名游资",
    "quant": "量化席位",
    "institutional": "机构专用",
    "northbound": "陆股通",
    "margin": "两融",
    "industrial_capital": "产业资本",
}
_ORDER = ("institutional", "hot_money", "quant", "northbound", "industrial_capital", "margin")

# 持续性规则（对象=stock）：(category, feedback_code) -> 描述
_PERSIST = {
    ("hot_money", "strong_positive"): "极弱（1日）",
    ("hot_money", "positive"): "极弱（1日）",
    ("quant", "neutral"): "弱（1-3日）",
    ("northbound", "exogenous"): "中（1-2周）",
    ("institutional", "positive"): "中（1-2周）",
    ("institutional", "neutral"): "中（1-2周）",
    ("industrial_capital", "strong_negative"): "强（1月+）",
    ("margin", "conditional"): "中（1-2周）",
}
_DEFAULT_PERSIST = {
    "hot_money": "极弱（1日）", "quant": "弱（1-3日）", "northbound": "中（1-2周）",
    "institutional": "中（1-2周）", "industrial_capital": "强（1月+）", "margin": "中（1-2周）",
}
_FEEDBACK_CN = {
    "strong_positive": "正反馈", "strong_negative": "负反馈",
    "neutral": "中性", "conditional": "条件依赖",
}


def _stock_name(code6: str) -> str:
    try:
        ipo = store.load_ipo_dates()
        return ipo.get(f"cn:{code6}", {}).get("name", code6)
    except Exception:
        return code6


def _direction(net, thr) -> str:
    if net is None:
        return "absent"
    if abs(net) < thr:
        return "stable"
    return "buy" if net > 0 else "sell"


def _sum_lhb(eps: pd.DataFrame, pred) -> tuple:
    if eps is None or eps.empty:
        return None, 0
    labels = eps["agency"].map(lambda a: classify_seat(a)["label"])
    sub = eps[labels.map(pred)]
    if sub.empty:
        return None, 0
    return float(sub["net"].sum()), len(sub)


def _col_of(details: dict, cat: str):
    d = details.get(cat) or {}
    v = d.get("net_buy")
    return v


# ═══════════════════════════════════════════════════════════════
# 资金明细
# ═══════════════════════════════════════════════════════════════

def _capital_details(code6: str, start: str, end: str, prices: pd.DataFrame) -> dict:
    details = {}
    try:
        eps = load_seat_episodes(start=start, end=end, code=code6)
    except Exception as e:
        logger.debug(f"top_inst 加载失败 {code6}: {e}")
        eps = pd.DataFrame()

    def mk(cat, net, thr, metric, conf=0.8, note=""):
        details[cat] = {"net_buy": net, "direction": _direction(net, thr),
                        "proxy_metric": metric, "confidence": conf, "note": note}

    hot, _ = _sum_lhb(eps, lambda x: x == LABEL_HOT)
    quant, _ = _sum_lhb(eps, lambda x: x == LABEL_QUANT)
    inst, _ = _sum_lhb(eps, lambda x: x == LABEL_INST)
    north_seat, _ = _sum_lhb(eps, lambda x: x == LABEL_NORTH)
    mk("hot_money", hot, THR_LHB, "知名游资席位净买入", 0.8,
       "" if hot is not None else "窗口内无游资席位上榜")
    mk("quant", quant, THR_LHB, "量化席位净买卖", 0.75,
       "" if quant is not None else "窗口内无量化席位上榜")
    mk("institutional", inst, THR_LHB, "机构专用席位净买卖", 0.8,
       "" if inst is not None else "窗口内无机构专用席位上榜")

    # 北向：优先日频陆股通席位，否则季度持股变动
    if north_seat is not None:
        mk("northbound", north_seat, THR_NORTH, "龙虎榜陆股通席位净买入", 0.75)
    else:
        nb = _northbound_holding(code6, end)
        if nb is not None:
            details["northbound"] = {"net_buy": nb, "direction": _direction(nb, 0.0),
                                     "proxy_metric": "北向季度持股比例变动", "confidence": 0.6,
                                     "note": "季度披露，非日频"}
        else:
            details["northbound"] = {"net_buy": None, "direction": "unknown",
                                     "proxy_metric": "陆股通席位/北向持股", "confidence": 0.0,
                                     "note": "无北向数据"}

    # 两融
    m = store_mf.load("margin", code=code6, start=start, end=end)
    if m is None or m.empty or "rzye" not in m.columns:
        details["margin"] = {"net_buy": None, "direction": "absent",
                             "proxy_metric": "个股融资余额变动率", "confidence": 0.0,
                             "note": "无两融数据"}
    else:
        rzye = pd.to_numeric(m["rzye"], errors="coerce").dropna()
        rate = float(rzye.iloc[-1] / rzye.iloc[0] - 1) if len(rzye) > 1 and rzye.iloc[0] else 0.0
        details["margin"] = {"net_buy": rate, "direction": _direction(rate, THR_MARGIN),
                             "proxy_metric": "个股融资余额变动率", "confidence": 0.9,
                             "note": f"融资余额变动 {rate*100:+.2f}%"}

    # 产业资本（回购/增持；阈值 1 亿）
    amount, has_increase, has_reduce = _industrial(code6, start, end)
    if has_increase:
        details["industrial_capital"] = {"net_buy": amount,
                                         "direction": _direction(amount, THR_INDUSTRIAL),
                                         "proxy_metric": "回购/增持公告", "confidence": 0.9,
                                         "note": f"窗口内增持金额约 {amount/1e8:+.2f}亿"}
    elif has_reduce:
        details["industrial_capital"] = {"net_buy": -THR_INDUSTRIAL, "direction": "sell",
                                         "proxy_metric": "回购/减持公告", "confidence": 0.9,
                                         "note": "窗口内有减持/质押事件"}
    else:
        details["industrial_capital"] = {"net_buy": None, "direction": "absent",
                                         "proxy_metric": "回购/增减持公告", "confidence": 0.9,
                                         "note": "无回购/增持公告"}
    return details


def _northbound_holding(code6: str, end: str):
    df = store_mf.load("hk_hold", code=code6)
    if df is None or df.empty or "ratio" not in df.columns:
        return None
    df = df[df.index <= pd.Timestamp(end)].sort_index()
    if len(df) < 2:
        return None
    return float(df["ratio"].iloc[-1] - df["ratio"].iloc[0])


def _industrial(code6: str, start: str, end: str):
    """返回 (增持金额估算, 有增持, 有减持)。"""
    try:
        from data.store_events import load_corp_events
        events = load_corp_events(code6)
    except Exception:
        return 0.0, False, False
    amount = 0.0
    inc = red = False
    for e in events or []:
        d = e.get("date", "")
        if not (start <= d <= end):
            continue
        et = e.get("event_type", "")
        det = e.get("detail", {}) or {}
        if et == "increase":
            inc = True
            vol = det.get("change_vol")
            px = det.get("avg_price")
            amount += float(vol) * (float(px) if px else 0.0) if vol else 0.0
        elif et in ("reduce", "decrease"):
            red = True
    return amount, inc, red


# ═══════════════════════════════════════════════════════════════
# 反馈性质
# ═══════════════════════════════════════════════════════════════

def _detect_feedback(code6: str, prices: pd.DataFrame) -> str:
    """
    价格日收益 vs 主力资金净流入的相关性。

    口径：优先用 moneyflow/stock 的「大单+超大单」净额（主力口径），字段缺失时回退 net_mf_amount。
    注意：moneyflow/stock 按**单笔金额档位**分类，不区分席位/资金主体；
    席位级方向由 dominant_capital（席位字典等）负责，此处仅作价量反馈的连续代理。
    """
    start = prices.index.min().strftime("%Y-%m-%d")
    end = prices.index.max().strftime("%Y-%m-%d")
    mf = store_mf.load("stock", code=code6, start=start, end=end)
    if mf is None or mf.empty:
        return "unknown"
    lg_cols = ["buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"]
    if all(c in mf.columns for c in lg_cols):
        flow = (pd.to_numeric(mf["buy_lg_amount"], errors="coerce")
                + pd.to_numeric(mf["buy_elg_amount"], errors="coerce")
                - pd.to_numeric(mf["sell_lg_amount"], errors="coerce")
                - pd.to_numeric(mf["sell_elg_amount"], errors="coerce"))
    elif "net_mf_amount" in mf.columns:
        flow = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
    else:
        return "unknown"
    close = pd.to_numeric(prices["close"], errors="coerce")
    ret = close.pct_change()
    df = pd.concat([ret.rename("ret"), flow.rename("flow")], axis=1).dropna()
    if len(df) < 10 or df["ret"].std() == 0 or df["flow"].std() == 0:
        return "unknown"
    corr = float(df["ret"].corr(df["flow"]))
    if corr > 0.6:
        return "strong_positive"
    if corr < -0.4:
        return "strong_negative"
    if -0.4 <= corr <= 0.2:
        return "neutral"
    return "conditional"


# ═══════════════════════════════════════════════════════════════
# 主导资金 / 持续性 / 评分
# ═══════════════════════════════════════════════════════════════

def _dominant(details: dict) -> tuple:
    """返回 (category, direction) 或 (None, None)。按“超阈强度”排序。"""
    thr = {"hot_money": THR_LHB, "quant": THR_LHB, "institutional": THR_LHB,
           "northbound": THR_NORTH, "margin": THR_MARGIN,
           "industrial_capital": THR_INDUSTRIAL}
    best, best_strength, best_dir = None, 0.0, None
    for cat in _ORDER:
        d = details.get(cat) or {}
        if d.get("direction") not in ("buy", "sell"):
            continue
        v = d.get("net_buy")
        if v is None:
            continue
        strength = abs(v) / (thr.get(cat, 1.0) or 1.0)
        if strength >= 1.0 and strength > best_strength:
            best, best_strength, best_dir = cat, strength, d["direction"]
    return best, best_dir


def _persistence(cat: str, feedback: str) -> str:
    if cat is None:
        return "未知"
    return _PERSIST.get((cat, feedback)) or _DEFAULT_PERSIST.get(cat, "未知")


def _structure_score(details: dict) -> tuple:
    hot = _col_of(details, "hot_money") or 0.0
    quant = _col_of(details, "quant") or 0.0
    inst = _col_of(details, "institutional") or 0.0
    ind = _col_of(details, "industrial_capital") or 0.0
    margin = details.get("margin", {}).get("net_buy")
    lhb_total = abs(hot) + abs(quant) + abs(inst)
    if lhb_total == 0 and margin is None:
        return None, None
    # 权重对齐 reference/资金画像0.1.md 10.1（0.30 投机 / 0.35 机构 / 0.20 两融 / 0.15 股东户数，
    # 本项无股东户数数据，按可用维度重归一）
    comps = []
    if lhb_total > 0:
        spec = abs(hot + quant) / lhb_total
        inst_ratio = abs(inst + ind) / max(lhb_total, 1.0)
        comps.append((0.30, (1 - spec) * 100))
        comps.append((0.35, min(inst_ratio, 1.0) * 100))
    if margin is not None:
        comps.append((0.20, max(0.0, min(1.0, 1 - abs(margin) / 0.15)) * 100))
    wsum = sum(w for w, _ in comps)
    score = sum(w * v for w, v in comps) / wsum if wsum else None
    if score is None:
        return None, None
    grade = "健康" if score >= 80 else "一般" if score >= 60 else "脆弱" if score >= 40 else "危险"
    return round(score, 1), grade


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def _undetermined(code6, name, date, lookback, scenario, partial=None):
    reasons = {
        "S1": ("当日无交易数据（停牌）", ["查询复牌日期后重试", "若长期停牌，关注停牌原因公告"]),
        "S2": ("数据源缺失，无法计算", ["检查数据源", "降低回看窗口"]),
        "S3": ("所有资金信号均为缺席/稳定，无法识别主导资金",
                ["扩大回看窗口至20日以上", "关注股东户数季度变化", "连续多日无信号则可能无人问津"]),
        "S4": ("正负反馈资金同时活跃，方向矛盾", ["分拆主导资金分别观察", "等待方向明朗"]),
        "S5": ("回看窗口内有效交易日不足", ["扩大回看窗口至60日以上", "使用更长周期资金数据"]),
        "S6": ("历史数据不足，无法计算分位数", ["使用固定阈值替代分位数", "等待数据积累"]),
    }
    reason, sugg = reasons.get(scenario, ("无法判断", []))
    return {
        "meta": {"level": "stock", "code": code6, "name": name, "date": date,
                 "lookback_days": lookback},
        "answer": {"status": "undetermined", "dominant_capital": None, "feedback": None,
                   "persistence": None, "undetermined_reason": reason,
                   "suggestions": sugg, "partial_findings": partial or {}},
        "structure_score": None, "structure_grade": None,
        "capital_details": {}, "key_signals": [], "warnings": [reason],
        "data_quality": {"completeness": 0.0, "accuracy": 1.0, "timeliness": "normal"},
        "disclaimer": DISCLAIMER,
    }


def profile(level: str = "stock", code: str | None = None, date: str | None = None,
            lookback_days: int = LOOKBACK_DEFAULT) -> dict:
    code6 = _norm_code6(code or "")
    name = _stock_name(code6) if code6 else ""
    if level != "stock":
        r = _undetermined(code6, name, date or "", lookback_days, "S2")
        r["warnings"] = [f"{level} 画像尚未实现（Phase 2/3），当前仅支持 level=stock"]
        r["answer"]["undetermined_reason"] = f"{level} 画像尚未实现"
        r["answer"]["suggestions"] = ["当前仅实现个股级（Phase 1）", "板块/大期待 Phase 2/3"]
        return r
    if not code6:
        return _undetermined("", "", date or "", lookback_days, "S2")

    prices = store.load(code6, asset_type="stock", market="cn", end=date)
    if prices is None or prices.empty:
        return _undetermined(code6, name, date or "", lookback_days, "S1")
    prices = prices.sort_index()
    eff = prices.tail(lookback_days)
    if len(eff) < MIN_TRADING_DAYS:
        return _undetermined(code6, name, date or "", lookback_days, "S5")
    start = eff.index.min().strftime("%Y-%m-%d")
    end = eff.index.max().strftime("%Y-%m-%d")

    details = _capital_details(code6, start, end, eff)
    feedback = _detect_feedback(code6, eff)
    dom_cat, dom_dir = _dominant(details)

    available = sum(1 for c in _CAT_NAME if (details.get(c, {}).get("net_buy") is not None))
    active = [c for c in _CAT_NAME if details.get(c, {}).get("direction") in ("buy", "sell")]
    buys = [c for c in active if details[c]["direction"] == "buy"]
    sells = [c for c in active if details[c]["direction"] == "sell"]

    partial = {c: (details.get(c, {}) or {}).get("note") or
               f"{_CAT_NAME[c]} {details.get(c, {}).get('direction')}" for c in _CAT_NAME}

    # 无法判断：信号全缺席 / 矛盾
    if not active:
        return _undetermined(code6, name, date or "", lookback_days, "S3", partial)
    if buys and sells:
        r = _undetermined(code6, name, date or "", lookback_days, "S4", partial)
        r["capital_details"] = details
        r["data_quality"]["completeness"] = round(available / len(_CAT_NAME), 2)
        return r
    if dom_cat is None:
        return _undetermined(code6, name, date or "", lookback_days, "S3", partial)

    score, grade = _structure_score(details)
    dom_name = f"{_CAT_NAME[dom_cat]}（{'净买入' if dom_dir == 'buy' else '净卖出'}）"
    key_signals = _key_signals(details, score)
    warnings = _warnings(details)

    return {
        "meta": {"level": "stock", "code": code6, "name": name, "date": end,
                 "lookback_days": lookback_days},
        "answer": {
            "status": "resolved",
            "dominant_capital": dom_name,
            "feedback": _FEEDBACK_CN.get(feedback, "未知"),
            "persistence": _persistence(dom_cat, feedback),
            "undetermined_reason": None,
            "suggestions": None,
            "partial_findings": None,
        },
        "structure_score": score,
        "structure_grade": grade,
        "capital_details": details,
        "key_signals": key_signals,
        "warnings": warnings,
        "data_quality": {"completeness": round(available / len(_CAT_NAME), 2),
                         "accuracy": 0.98, "timeliness": "normal"},
        "disclaimer": DISCLAIMER,
    }


def _key_signals(details: dict, score) -> list:
    sig = []
    if details.get("quant", {}).get("direction") in ("buy", "sell"):
        sig.append({"signal": "量化席位活跃（做T/对冲特征）", "severity": "medium",
                    "implication": "方向性弱，趋势持续性存疑"})
    if details.get("institutional", {}).get("direction") == "sell":
        sig.append({"signal": "机构专用席位净卖出", "severity": "medium",
                    "implication": "机构减持，关注抛压"})
    if details.get("industrial_capital", {}).get("direction") == "buy":
        sig.append({"signal": "产业资本回购/增持", "severity": "high",
                    "implication": "中长期偏多信号"})
    if score is not None and score < 40:
        sig.append({"signal": "结构评分偏低", "severity": "high",
                    "implication": "短期资金主导，结构脆弱"})
    return sig


def _warnings(details: dict) -> list:
    w = []
    if details.get("institutional", {}).get("direction") == "sell":
        w.append("机构专用席位净卖出")
    if details.get("industrial_capital", {}).get("direction") in (None, "absent"):
        w.append("无产业资本回购/增持")
    if details.get("northbound", {}).get("direction") in ("absent", "unknown"):
        w.append("无北向数据")
    return w


def format_text(report: dict) -> str:
    """人类可读文本报告。"""
    m = report["meta"]
    a = report["answer"]
    lines = ["=" * 60, f"  个股资金画像 | {m.get('name')}（{m.get('code')}） | {m.get('date')}", "=" * 60]
    lines.append(f"【核心结论】状态：{'已判定' if a['status'] == 'resolved' else '无法判断'}")
    if a["status"] == "resolved":
        lines.append(f"  主导资金：{a['dominant_capital']}")
        lines.append(f"  反馈性质：{a['feedback']}")
        lines.append(f"  持续性：  {a['persistence']}")
        lines.append(f"  结构评分：{report.get('structure_score')} → {report.get('structure_grade')}")
    else:
        lines.append(f"  原因：{a['undetermined_reason']}")
        for s in a.get("suggestions") or []:
            lines.append(f"    · {s}")
    lines.append("")
    lines.append("【资金明细】")
    for cat, d in (report.get("capital_details") or {}).items():
        lines.append(f"  {_CAT_NAME.get(cat, cat):<8} {d.get('direction'):<7} {d.get('note') or ''}")
    if report.get("warnings"):
        lines.append("")
        lines.append("【风险提示】")
        for x in report["warnings"]:
            lines.append(f"  · {x}")
    lines.append("")
    lines.append(report.get("disclaimer", DISCLAIMER))
    return "\n".join(lines)
