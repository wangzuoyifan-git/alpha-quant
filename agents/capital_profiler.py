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
    assess_consensus,
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

def _capital_details(code6: str, start: str, end: str, prices: pd.DataFrame,
                     eps: pd.DataFrame | None = None) -> dict:
    details = {}
    if eps is None:
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
                                         "note": f"窗口内回购/增持金额约 {amount/1e8:+.2f}亿"}
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
    def _num(v) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    amount = 0.0
    inc = red = False
    for e in events or []:
        d = e.get("date", "")
        if not (start <= d <= end):
            continue
        et = e.get("event_type", "")
        det = e.get("detail", {}) or {}
        if et == "buyback":
            inc = True
            amount += _num(det.get("amount"))
        elif et == "increase":
            inc = True
            amount += _num(det.get("change_vol")) * _num(det.get("avg_price"))
        elif et in ("reduce", "decrease"):
            red = True
    return amount, inc, red


# ═══════════════════════════════════════════════════════════════
# 反馈性质
# ═══════════════════════════════════════════════════════════════

def _main_flow_series(code6: str, prices: pd.DataFrame):
    """
    主力资金净流入日频序列（万元）。

    口径：优先「大单+超大单」净额 `(buy_lg+buy_elg)-(sell_lg+sell_elg)`，缺列回退 `net_mf_amount`。
    注意：moneyflow/stock 按**单笔金额档位**分类，不区分席位/资金主体。
    """
    start = prices.index.min().strftime("%Y-%m-%d")
    end = prices.index.max().strftime("%Y-%m-%d")
    mf = store_mf.load("stock", code=code6, start=start, end=end)
    if mf is None or mf.empty:
        return None
    lg_cols = ["buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"]
    if all(c in mf.columns for c in lg_cols):
        flow = (pd.to_numeric(mf["buy_lg_amount"], errors="coerce")
                + pd.to_numeric(mf["buy_elg_amount"], errors="coerce")
                - pd.to_numeric(mf["sell_lg_amount"], errors="coerce")
                - pd.to_numeric(mf["sell_elg_amount"], errors="coerce"))
    elif "net_mf_amount" in mf.columns:
        flow = pd.to_numeric(mf["net_mf_amount"], errors="coerce")
    else:
        return None
    flow = pd.to_numeric(flow, errors="coerce").dropna()
    return flow if not flow.empty else None


def _detect_feedback(code6: str, prices: pd.DataFrame) -> str:
    """价格日收益 vs 主力资金净流入（连续日频代理，不区分席位）的相关性。"""
    flow = _main_flow_series(code6, prices)
    if flow is None:
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


def _main_flow_daily(flow) -> dict:
    """近窗口主力净流入汇总与连续性（背景）。flow 为 _main_flow_series 的返回。"""
    if flow is None or flow.empty:
        return {"available": False}
    total = float(flow.sum())
    streak, sign = 0, 0
    for v in reversed(flow.tolist()):
        sg = 1 if v > 0 else -1 if v < 0 else 0
        if sg == 0:
            break
        if sign == 0:
            sign = sg
        if sg == sign:
            streak += 1
        else:
            break
    return {"available": True, "sum_net_wan": round(total, 1),
            "days": len(flow), "streak_days": streak,
            "streak_dir": ("inflow" if sign > 0 else "outflow" if sign < 0 else "flat")}


def _seat_combined_net(details: dict):
    """席位类四类合计净额（游资+量化+机构+陆股通），无数据返回 None。"""
    cats = ("hot_money", "quant", "institutional", "northbound")
    vals = [details[c]["net_buy"] for c in cats
            if details.get(c, {}).get("net_buy") is not None]
    return sum(vals) if vals else None


def _cross_seat_main(details: dict, main_flow: dict) -> dict | None:
    """席位视图 × 主力视图 一致性四象限（窗口聚合）。"""
    seat_net = _seat_combined_net(details)
    if seat_net is None or not main_flow.get("available"):
        return None
    seat_dir = "buy" if seat_net > 0 else "sell" if seat_net < 0 else "flat"
    msum = main_flow.get("sum_net_wan") or 0.0
    main_dir = "inflow" if msum > 0 else "outflow" if msum < 0 else "flat"
    if seat_dir == "buy" and main_dir == "inflow":
        verdict = "合力向上"
    elif seat_dir == "sell" and main_dir == "outflow":
        verdict = "合力向下"
    elif seat_dir == "buy" and main_dir == "outflow":
        verdict = "对倒/诱多（席位买·主力流出）"
    elif seat_dir == "sell" and main_dir == "inflow":
        verdict = "隐性吸筹（席位卖·主力流入）"
    else:
        verdict = "中性"
    return {"seat_direction": seat_dir, "seat_net": round(seat_net, 1),
            "main_direction": main_dir, "main_sum_wan": round(msum, 1),
            "verdict": verdict}


def _pre_post_main(episodes: pd.DataFrame, prices: pd.DataFrame, flow: pd.Series,
                   pre: int = 5, post: int = 5) -> list:
    """上榜前后主力变化：上榜前净流入=提前吸筹；上榜后净流出=拉高出货风险。

    flow: 已按 prices.index 对齐的主力净流入序列（万元）。
    """
    if episodes is None or episodes.empty or flow is None or flow.empty:
        return []
    idx = prices.index
    signals = []
    for t in sorted(set(episodes["date"])):
        pos = int(idx.searchsorted(pd.Timestamp(t)))
        if pos >= len(idx):
            continue
        seat_net_t = float(episodes[episodes["date"] == t]["net"].sum())
        pre_sum = float(flow.iloc[max(0, pos - pre):pos].sum())
        post_sum = float(flow.iloc[pos + 1:pos + 1 + post].sum())
        if seat_net_t > 0 and pre_sum > 0:
            signals.append({"signal": f"上榜前{pre}日主力已净流入", "verdict": "提前吸筹后上榜",
                            "date": t})
        if seat_net_t > 0 and post_sum < 0:
            signals.append({"signal": f"上榜后{post}日主力转为净流出", "verdict": "拉高出货风险",
                            "date": t})
    return signals


# ═══════════════════════════════════════════════════════════════
# 主导资金 / 持续性 / 评分
# ═══════════════════════════════════════════════════════════════

def _dominant(details: dict, cats=None, thr: dict | None = None) -> tuple:
    """返回 (category, direction) 或 (None, None)。按“超阈强度”排序。

    cats/thr 可定制（板块画像传入板块类别与阈值）；默认个股类别。
    """
    if cats is None:
        cats = _ORDER
    if thr is None:
        thr = {"hot_money": THR_LHB, "quant": THR_LHB, "institutional": THR_LHB,
               "northbound": THR_NORTH, "margin": THR_MARGIN,
               "industrial_capital": THR_INDUSTRIAL}
    best, best_strength, best_dir = None, 0.0, None
    for cat in cats:
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
            lookback_days: int = LOOKBACK_DEFAULT,
            allow_hidden_main: bool = False, sector_level: str = "L1") -> dict:
    code6 = _norm_code6(code or "")
    name = _stock_name(code6) if code6 else ""
    if level == "sector_etf":
        return _sector_profile(code6, date, lookback_days, sector_level)
    if level == "sector_macro":
        r = _undetermined(code6, name, date or "", lookback_days, "S2")
        r["warnings"] = ["大类画像为 Phase 3，尚未实现"]
        r["answer"]["undetermined_reason"] = "sector_macro 画像尚未实现（Phase 3）"
        r["answer"]["suggestions"] = ["当前已支持个股（Phase 1）与板块/ETF（Phase 2）"]
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

    try:
        eps = load_seat_episodes(start=start, end=end, code=code6)
    except Exception:
        eps = pd.DataFrame()
    details = _capital_details(code6, start, end, eff, eps=eps)
    feedback = _detect_feedback(code6, eff)
    dom_cat, dom_dir = _dominant(details)

    # 日频主力视图（B）与席位视图（A）交叉
    flow = _main_flow_series(code6, eff)
    main_flow = _main_flow_daily(flow)
    seat_vs_main = _cross_seat_main(details, main_flow)
    pre_post = _pre_post_main(eps, eff, flow.reindex(eff.index) if flow is not None else None)
    consensus = _consensus(code6, lookback_days)

    available = sum(1 for c in _CAT_NAME if (details.get(c, {}).get("net_buy") is not None))
    active = [c for c in _CAT_NAME if details.get(c, {}).get("direction") in ("buy", "sell")]
    buys = [c for c in active if details[c]["direction"] == "buy"]
    sells = [c for c in active if details[c]["direction"] == "sell"]

    partial = {c: (details.get(c, {}) or {}).get("note") or
               f"{_CAT_NAME[c]} {details.get(c, {}).get('direction')}" for c in _CAT_NAME}

    # 无席位/无超阈信号：默认 S3；allow_hidden_main 时给“隐性主力（日频）”弱结论
    if not active:
        if allow_hidden_main and _strong_main_flow(main_flow):
            r = _hidden_main_report(code6, name, end, lookback_days, main_flow,
                                    seat_vs_main, feedback, details, eps)
            r["consensus"] = consensus
            return r
        r = _undetermined(code6, name, date or "", lookback_days, "S3", partial)
        r["main_flow_daily"] = main_flow
        r["consensus"] = consensus
        return r
    if buys and sells:
        r = _undetermined(code6, name, date or "", lookback_days, "S4", partial)
        r["capital_details"] = details
        r["main_flow_daily"] = main_flow
        r["seat_vs_main"] = seat_vs_main
        r["consensus"] = consensus
        r["data_quality"]["completeness"] = round(available / len(_CAT_NAME), 2)
        return r
    if dom_cat is None:
        r = _undetermined(code6, name, date or "", lookback_days, "S3", partial)
        r["main_flow_daily"] = main_flow
        r["consensus"] = consensus
        return r

    score, grade = _structure_score(details)
    dom_name = f"{_CAT_NAME[dom_cat]}（{'净买入' if dom_dir == 'buy' else '净卖出'}）"
    key_signals, warnings = _key_signals(details, score), _warnings(details)
    if seat_vs_main:
        v = seat_vs_main["verdict"]
        if v == "合力向上":
            key_signals.append({"signal": f"席位×主力一致：{v}", "severity": "medium",
                                "implication": "上榜买入与主力净流入同向，置信度提升"})
        elif v == "对倒/诱多（席位买·主力流出）":
            warnings.append("席位买入但主力整体净流出（对倒/诱多风险）")
            key_signals.append({"signal": f"席位×主力背离：{v}", "severity": "high",
                                "implication": "上榜买但大钱在走，警惕拉高出货"})
        elif v == "隐性吸筹（席位卖·主力流入）":
            key_signals.append({"signal": f"席位×主力背离：{v}", "severity": "medium",
                                "implication": "上榜卖但主力净流入，可能换手/承接"})
    for s in pre_post:
        key_signals.append({"signal": f"{s['verdict']}（{s['date']}）：{s['signal']}",
                            "severity": "medium",
                            "implication": "上榜前后主力方向变化，参考持续性"})
    if consensus and consensus.get("verdict"):
        cv = consensus["verdict"]
        if cv.startswith("资金合力"):
            key_signals.append({"signal": f"席位级事件流：{cv}", "severity": "medium",
                                "implication": "多路席位/融资/大宗资金同向"})
        elif cv.startswith("资金分歧"):
            warnings.append(f"席位级事件流分歧：{cv}")
            key_signals.append({"signal": f"席位级事件流：{cv}", "severity": "high",
                                "implication": "多路资金对打，方向不明"})

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
        "main_flow_daily": main_flow,
        "seat_vs_main": seat_vs_main,
        "seat_main_signals": pre_post,
        "consensus": consensus,
        "key_signals": key_signals,
        "warnings": warnings,
        "data_quality": {"completeness": round(available / len(_CAT_NAME), 2),
                         "accuracy": 0.98, "timeliness": "normal"},
        "disclaimer": DISCLAIMER,
    }


def _strong_main_flow(main_flow: dict) -> bool:
    return bool(main_flow.get("available") and abs(main_flow.get("sum_net_wan") or 0) > 0
                and (main_flow.get("streak_days") or 0) >= 3)


def _consensus(code6: str, lookback_days: int) -> dict | None:
    """席位级事件流合力/分歧（复用 smart_money_agent.assess_consensus 四路：北向/机构/融资/大宗）。"""
    try:
        months = max(1, round(lookback_days / 21))
        return assess_consensus(code6, window_months=months, fetch=False)
    except Exception as e:
        logger.debug(f"consensus 失败 {code6}: {e}")
        return None


def _hidden_main_report(code6, name, end, lookback, main_flow, seat_vs_main,
                        feedback, details, eps) -> dict:
    """无席位数据时，仅凭日频主力给的弱结论（默认关闭，--hidden-main 开启）。"""
    direction = "净流入" if (main_flow.get("sum_net_wan") or 0) > 0 else "净流出"
    return {
        "meta": {"level": "stock", "code": code6, "name": name, "date": end,
                 "lookback_days": lookback},
        "answer": {
            "status": "resolved",
            "dominant_capital": f"隐性主力（日频，弱推断，{direction}）",
            "feedback": _FEEDBACK_CN.get(feedback, "未知"),
            "persistence": "弱（1-3日）",
            "undetermined_reason": None,
            "suggestions": None,
            "partial_findings": None,
            "weak": True,
        },
        "structure_score": None, "structure_grade": None,
        "capital_details": details,
        "main_flow_daily": main_flow,
        "seat_vs_main": seat_vs_main,
        "key_signals": [{"signal": f"无龙虎榜席位数据；主力日频{direction}，连续"
                                   f"{main_flow.get('streak_days')}日", "severity": "low",
                         "implication": "未上榜，仅日频主力背景，弱结论"}],
        "warnings": ["无龙虎榜席位数据，主导资金为日频弱推断"],
        "data_quality": {"completeness": 0.2, "accuracy": 0.9, "timeliness": "normal"},
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
    title = "个股资金画像" if m.get("level") == "stock" else "板块/ETF资金画像"
    extra = f"｜成分 {m.get('members')} 只" if m.get("members") else ""
    lines = ["=" * 60,
             f"  {title} | {m.get('name')}（{m.get('code')}） | {m.get('date')}{extra}",
             "=" * 60]
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
        cname = _CAT_NAME.get(cat) or _SECTOR_CAT.get(cat) or cat
        lines.append(f"  {cname:<8} {d.get('direction'):<7} {d.get('note') or ''}")
    mf = report.get("main_flow_daily") or {}
    if mf.get("available"):
        lines.append("")
        lines.append("【日频主力（背景）】")
        lines.append(f"  窗口主力净流入合计：{mf.get('sum_net_wan'):+.0f} 万元；"
                     f"连续{mf.get('streak_days')}日{mf.get('streak_dir')}")
    svm = report.get("seat_vs_main")
    if svm:
        lines.append(f"  席位×主力一致性：{svm.get('verdict')}"
                     f"（席位{svm.get('seat_direction')} / 主力{svm.get('main_direction')}）")
    for s in report.get("seat_main_signals") or []:
        lines.append(f"  · {s.get('verdict')}（{s.get('date')}）：{s.get('signal')}")
    cons = report.get("consensus")
    if cons and cons.get("verdict"):
        routes = "，".join(f"{k}:{v.get('direction')}" for k, v in (cons.get("routes") or {}).items())
        lines.append(f"  席位级事件流：{cons['verdict']}（{routes}）")
    if report.get("warnings"):
        lines.append("")
        lines.append("【风险提示】")
        for x in report["warnings"]:
            lines.append(f"  · {x}")
    lines.append("")
    lines.append(report.get("disclaimer", DISCLAIMER))
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Phase 2：板块 / ETF 画像
# ═══════════════════════════════════════════════════════════════

_SECTOR_CAT = {
    "etf_flow": "ETF申赎",
    "sector_lhb": "板块龙虎榜",
    "sector_institutional": "板块机构席位",
    "sector_margin": "板块两融",
    "northbound_sector": "北向板块配置",
    "sector_block": "板块大宗",
    "sector_industrial": "板块产业资本",
}
_THR_SECTOR = {
    "etf_flow": 5e8, "sector_lhb": 1e9, "sector_institutional": 1e9,
    "sector_margin": 0.02, "northbound_sector": 5.0, "sector_block": 1e8,
    "sector_industrial": 2e9,
}
# 板块结构评分权重（reference 10.2）
_SECTOR_WEIGHTS = {"etf_flow": 0.30, "sector_institutional": 0.30,
                   "sector_industrial": 0.25, "northbound_sector": 0.15}
_SECTOR_PERSIST = {"etf_flow": "长（1月+）", "sector_lhb": "短（1-3日）",
                   "sector_institutional": "中（1-2周）", "sector_margin": "中（1-2周）",
                   "northbound_sector": "中~长（2周-1月）", "sector_block": "短（1-3日）",
                   "sector_industrial": "长（1月+）"}


def _is_etf_code(code6: str) -> bool:
    return len(code6) == 6 and code6[:2] in ("15", "16", "51", "52", "56", "58")


def _is_sw_code(code6: str) -> bool:
    return len(code6) == 6 and code6.startswith("801")


def _sector_mk(cat, val, note="", conf=0.75):
    return {"net_buy": val, "direction": _direction(val, _THR_SECTOR.get(cat, 0)),
            "proxy_metric": _SECTOR_CAT.get(cat, cat), "confidence": conf, "note": note}


def _sector_price(code6: str, is_etf: bool, date: str):
    if is_etf:
        return store.load(code6, asset_type="etf", market="cn", end=date)
    import data.store_meta as sm
    df = sm.load("sw_index", code=code6)
    if df is None or df.empty:
        return None
    if date:
        df = df[df.index <= pd.Timestamp(date)]
    return df


def _sector_profile(code6: str, date: str | None, lookback_days: int,
                    sector_level: str = "L1") -> dict:
    import agents.sector_aggregator as sa

    if not code6:
        return _undetermined("", "", date or "", lookback_days, "S2")
    is_etf = _is_etf_code(code6)
    if is_etf:
        members_w = sa.etf_members(code6)
        members = set(members_w.keys())
        sname = sa.sector_name(code6)
        stype = "etf"
    else:
        members_l = sa.sector_members(code6, level=sector_level)
        members = set(members_l)
        sname = sa.sector_name(code6)
        stype = "sw"

    prices = _sector_price(code6, is_etf, date)
    if prices is None or prices.empty:
        r = _undetermined(code6, sname, date or "", lookback_days, "S1")
        r["meta"]["level"] = "sector_etf"
        return r
    prices = prices.sort_index()
    eff = prices.tail(lookback_days)
    if len(eff) < MIN_TRADING_DAYS:
        r = _undetermined(code6, sname, date or "", lookback_days, "S5")
        r["meta"]["level"] = "sector_etf"
        return r
    if not members:
        r = _undetermined(code6, sname, date or "", lookback_days, "S2")
        r["meta"]["level"] = "sector_etf"
        r["answer"]["undetermined_reason"] = "板块无成分股映射（缺 ETF→指数权重 或 申万成员）"
        r["answer"]["suggestions"] = ["运行 fund 数据下载或检查申万成分数据"]
        return r
    start = eff.index.min().strftime("%Y-%m-%d")
    end = eff.index.max().strftime("%Y-%m-%d")

    details = {}
    lhb = sa.aggregate_lhb(members, start, end)
    details["sector_lhb"] = _sector_mk("sector_lhb", lhb["lhb_total"] or None,
                                       f"板块龙虎榜净额（{lhb['n']}条）")
    details["sector_institutional"] = _sector_mk("sector_institutional",
                                                 lhb["institutional"] or None,
                                                 "板块内机构专用席位净额")
    marg = sa.aggregate_margin(sorted(members), start, end)
    details["sector_margin"] = _sector_mk("sector_margin", marg["change_rate"],
                                          f"融资余额变动（{marg['members']}只）")
    blk = sa.aggregate_block(members, start, end)
    details["sector_block"] = _sector_mk("sector_block", blk["inst_net"],
                                         f"机构大宗净额（{blk['n']}笔）")
    ind = sa.aggregate_industrial(sorted(members), start, end)
    details["sector_industrial"] = _sector_mk("sector_industrial", ind["amount"] or None,
                                              f"回购/增持金额（{ind['n']}条）")
    nb = sa.aggregate_northbound(sorted(members), end)
    details["northbound_sector"] = _sector_mk("northbound_sector", nb["ratio_change"],
                                              f"北向持股比例变动（{nb['members']}只，季度）")
    if is_etf:
        ef = sa.etf_flow(code6, start, end)
        details["etf_flow"] = _sector_mk("etf_flow", ef["net_amount"],
                                         f"ETF申赎近似（Δ份额×净值，{ef['days']}日）")
    else:
        details["etf_flow"] = {"net_buy": None, "direction": "absent",
                               "proxy_metric": "ETF申赎", "confidence": 0.0,
                               "note": "申万行业无对应 ETF 申赎"}

    feedback = _sector_feedback(code6, is_etf, eff, sorted(members))
    dom_cat, dom_dir = _dominant(details, cats=list(_SECTOR_CAT), thr=_THR_SECTOR)

    available = sum(1 for c in _SECTOR_CAT if (details.get(c, {}).get("net_buy") is not None))
    active = [c for c in _SECTOR_CAT if details.get(c, {}).get("direction") in ("buy", "sell")]
    buys = [c for c in active if details[c]["direction"] == "buy"]
    sells = [c for c in active if details[c]["direction"] == "sell"]
    partial = {c: (details.get(c) or {}).get("note") or _SECTOR_CAT[c] for c in _SECTOR_CAT}

    if not active:
        r = _undetermined(code6, sname, date or "", lookback_days, "S3", partial)
        r["meta"]["level"] = "sector_etf"
        r["capital_details"] = details
        return r
    if buys and sells:
        r = _undetermined(code6, sname, date or "", lookback_days, "S4", partial)
        r["meta"]["level"] = "sector_etf"
        r["capital_details"] = details
        r["data_quality"]["completeness"] = round(available / len(_SECTOR_CAT), 2)
        return r
    if dom_cat is None:
        r = _undetermined(code6, sname, date or "", lookback_days, "S3", partial)
        r["meta"]["level"] = "sector_etf"
        r["capital_details"] = details
        return r

    score, grade = _sector_score(details)
    dom_name = f"{_SECTOR_CAT[dom_cat]}（{'净买入' if dom_dir == 'buy' else '净卖出'}）"
    return {
        "meta": {"level": "sector_etf", "code": code6, "name": sname, "date": end,
                 "lookback_days": lookback_days, "sector_type": stype,
                 "members": len(members)},
        "answer": {"status": "resolved", "dominant_capital": dom_name,
                   "feedback": _FEEDBACK_CN.get(feedback, "未知"),
                   "persistence": _SECTOR_PERSIST.get(dom_cat, "未知"),
                   "undetermined_reason": None, "suggestions": None,
                   "partial_findings": None},
        "structure_score": score, "structure_grade": grade,
        "capital_details": details,
        "key_signals": [], "warnings": [],
        "data_quality": {"completeness": round(available / len(_SECTOR_CAT), 2),
                         "accuracy": 0.97, "timeliness": "normal"},
        "disclaimer": DISCLAIMER,
    }


def _sector_score(details: dict):
    comps = []
    for cat, w in _SECTOR_WEIGHTS.items():
        v = (details.get(cat) or {}).get("net_buy")
        if v is None:
            continue
        thr = _THR_SECTOR.get(cat, 0) or 1.0
        comps.append((w, min(1.0, abs(v) / thr) * 100))
    if not comps:
        return None, None
    wsum = sum(w for w, _ in comps)
    score = sum(w * x for w, x in comps) / wsum
    grade = "健康" if score >= 80 else "一般" if score >= 60 else "脆弱" if score >= 40 else "危险"
    return round(score, 1), grade


def _sector_feedback(code6: str, is_etf: bool, prices: pd.DataFrame, members: list) -> str:
    """板块收益 vs 成员股资金流聚合 的相关性；数据不足返回 unknown。"""
    try:
        flows = []
        for c in members:
            mf = store_mf.load("stock", code=c, start=prices.index.min().strftime("%Y-%m-%d"),
                               end=prices.index.max().strftime("%Y-%m-%d"))
            if mf is None or mf.empty or "net_mf_amount" not in mf.columns:
                continue
            flows.append(pd.to_numeric(mf["net_mf_amount"], errors="coerce").rename(c))
        if not flows:
            return "unknown"
        flow = pd.concat(flows, axis=1).sum(axis=1)
        ret = pd.to_numeric(prices["close"], errors="coerce").pct_change()
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
    except Exception as e:
        logger.debug(f"sector feedback 失败 {code6}: {e}")
        return "unknown"
