"""
agent_tools/analysis.py — A2 辅助分析聚合（P0 字段）

为每条信号聚合技术面/资金流/基本面摘要，供推送与 Web 看板展示。
遵循 A3：数据缺失时明确标注"数据不可用"（值为 None + available=False），
不展示空白或错误数据；不做主观判断，只呈现客观数据。

P0 字段（对应 docs/requirements.md A2）：
- 技术面摘要：当日涨跌幅、量比、换手率、近20日价格区间        （D1）
- 资金流摘要：近5日主力净流入额/占比、近5日北向净买入        （D3a + D3c）
- 基本面摘要：ROE、净利润同比、毛利率、资产负债率、PE/PB     （D2 + D1）
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _safe_float(v) -> Optional[float]:
    """转 float，NaN/None/异常返回 None。"""
    try:
        f = float(v)
        return None if (f != f) else f  # NaN 检测
    except (TypeError, ValueError):
        return None


def _technical_summary(code: str, date: str = None) -> dict:
    """
    技术面摘要（D1，来自 market 域 K 线）。
    量比 = 当日成交量 / 近5日均量（A2 IT-AA-04）。
    """
    import data.store as store
    out = {"available": False}
    df = store.load(code, asset_type="stock", market="cn", end=date)
    if df is None or df.empty:
        return out
    df = df.sort_index()
    last = df.iloc[-1]

    pct_chg = _safe_float(last.get("pct_chg"))
    turn = _safe_float(last.get("turn"))

    # 量比：当日量 / 近5日（不含当日）均量
    vol_ratio = None
    vol = _safe_float(last.get("volume"))
    if vol is not None and len(df) >= 6:
        prev5 = df["volume"].iloc[-6:-1]
        mean5 = _safe_float(prev5.mean())
        if mean5 and mean5 > 0:
            vol_ratio = round(vol / mean5, 3)

    # 近20日价格区间
    price_low_20 = price_high_20 = None
    if len(df) >= 1:
        window = df.iloc[-20:]
        price_low_20 = _safe_float(window["low"].min()) if "low" in window else None
        price_high_20 = _safe_float(window["high"].max()) if "high" in window else None

    out.update({
        "available": True,
        "pct_chg": round(pct_chg, 2) if pct_chg is not None else None,
        "volume_ratio": vol_ratio,
        "turnover": round(turn, 2) if turn is not None else None,
        "price_low_20d": round(price_low_20, 2) if price_low_20 is not None else None,
        "price_high_20d": round(price_high_20, 2) if price_high_20 is not None else None,
    })
    return out


def _moneyflow_summary(code: str, date: str = None, market_amount: float = None) -> dict:
    """
    资金流摘要（D3a 个股资金流 + D3c 北向）。
    近5日主力净流入 = 近5条 moneyflow 记录之和（A2 IT-AA-05）。
    主力 = 大单(lg) + 超大单(elg)。net_mf_amount 单位为万元（Tushare）。
    """
    import data.store_moneyflow as mf
    out = {"available": False}
    df = mf.load("stock", code=code, end=date)
    if df is None or df.empty:
        return out
    df = df.sort_index()
    recent = df.iloc[-5:]

    # 主力净流入 = (买大+买超大) - (卖大+卖超大)，单位万元
    main_net = None
    cols = ["buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"]
    if all(c in recent.columns for c in cols):
        net = (recent["buy_lg_amount"] + recent["buy_elg_amount"]
               - recent["sell_lg_amount"] - recent["sell_elg_amount"])
        main_net = _safe_float(net.sum())
    elif "net_mf_amount" in recent.columns:
        main_net = _safe_float(recent["net_mf_amount"].sum())

    out.update({
        "available": True,
        "main_net_inflow_5d": round(main_net, 2) if main_net is not None else None,  # 万元
        "days_counted": int(len(recent)),
    })
    return out


def _northbound_summary(date: str = None) -> dict:
    """北向近5日净买入（D3c，市场级背景）。沪股通+深股通当日资金流入合计。"""
    import data.store_moneyflow as mf
    out = {"available": False}
    df = mf.load("northbound", end=date)
    if df is None or df.empty:
        return out
    df = df.sort_index()
    recent = df.iloc[-5:]
    total = 0.0
    found = False
    for col in ("沪股通_net_buy", "深股通_net_buy", "当日资金流入", "当日资金流入_dup"):
        if col in recent.columns:
            s = pd.to_numeric(recent[col], errors="coerce").sum()
            if s == s:  # 非 NaN
                total += float(s)
                found = True
    if not found:
        return out
    out.update({"available": True, "north_net_buy_5d": round(total, 2)})
    return out


def _fundamental_summary(code: str) -> dict:
    """
    基本面摘要（D2 财务 + D1 估值）。
    ROE 取近 4 季 roeAvg 之和（TTM 还原）；净利润同比取 growth.YOYNI 最新；
    毛利率取 profit.gpMargin 最新；资产负债率取 balance.liabilityToAsset 最新。
    PE/PB 来自 K 线最新值。
    """
    import data.store_fundamental as sf
    import data.store as store
    out = {"available": False}

    roe_ttm = ni_yoy = gp_margin = debt_ratio = None

    profit = sf.load(code, "profit")
    if profit is not None and not profit.empty:
        if "roeAvg" in profit.columns:
            roe_q = profit["roeAvg"].iloc[-4:]
            if len(roe_q) > 0:
                s = _safe_float(roe_q.sum())
                roe_ttm = round(s * 100, 2) if s is not None else None  # 小数→%
        if "gpMargin" in profit.columns:
            gp = _safe_float(profit["gpMargin"].iloc[-1])
            gp_margin = round(gp * 100, 2) if gp is not None else None

    growth = sf.load(code, "growth")
    if growth is not None and not growth.empty and "YOYNI" in growth.columns:
        y = _safe_float(growth["YOYNI"].iloc[-1])
        ni_yoy = round(y * 100, 2) if y is not None else None

    balance = sf.load(code, "balance")
    if balance is not None and not balance.empty and "liabilityToAsset" in balance.columns:
        d = _safe_float(balance["liabilityToAsset"].iloc[-1])
        debt_ratio = round(d * 100, 2) if d is not None else None

    pe = pb = None
    kl = store.load(code, asset_type="stock", market="cn")
    if kl is not None and not kl.empty:
        last = kl.sort_index().iloc[-1]
        pe = _safe_float(last.get("pe_ttm"))
        pb = _safe_float(last.get("pb_mrq"))

    available = any(x is not None for x in (roe_ttm, ni_yoy, gp_margin, debt_ratio, pe, pb))
    out.update({
        "available": available,
        "roe_ttm": roe_ttm,           # %
        "net_profit_yoy": ni_yoy,     # %
        "gross_margin": gp_margin,    # %
        "debt_ratio": debt_ratio,     # %
        "pe_ttm": round(pe, 2) if pe is not None else None,
        "pb_mrq": round(pb, 2) if pb is not None else None,
    })
    return out


def build_auxiliary_analysis(code: str, date: str = None,
                             asset_type: str = "stock") -> dict:
    """
    为单只标的聚合 A2 P0 辅助分析。

    :param code: 6 位股票代码
    :param date: 截止日期（None 为最新）
    :param asset_type: stock / etf / index（仅 stock 聚合基本面/资金流）
    :return: {"technical": {...}, "moneyflow": {...}, "fundamental": {...},
              "northbound": {...}}，每个子块含 available 标记（A3）
    """
    result = {}
    try:
        result["technical"] = _technical_summary(code, date)
    except Exception as e:
        logger.debug(f"技术面摘要 {code} 失败: {e}")
        result["technical"] = {"available": False}

    if asset_type == "stock":
        try:
            result["moneyflow"] = _moneyflow_summary(code, date)
        except Exception as e:
            logger.debug(f"资金流摘要 {code} 失败: {e}")
            result["moneyflow"] = {"available": False}
        try:
            result["fundamental"] = _fundamental_summary(code)
        except Exception as e:
            logger.debug(f"基本面摘要 {code} 失败: {e}")
            result["fundamental"] = {"available": False}
    else:
        result["moneyflow"] = {"available": False}
        result["fundamental"] = {"available": False}

    try:
        result["northbound"] = _northbound_summary(date)
    except Exception as e:
        logger.debug(f"北向摘要失败: {e}")
        result["northbound"] = {"available": False}

    return result


def enrich_signals(signals: list, date: str = None) -> list:
    """
    为信号列表批量附加辅助分析（写入每个 signal dict 的 "analysis" 键）。
    原地修改并返回 signals。
    """
    for s in signals:
        code = s.get("code")
        if not code:
            continue
        asset_type = s.get("asset_type", "stock")
        s["analysis"] = build_auxiliary_analysis(code, date=date, asset_type=asset_type)
    return signals


def enrich_signal_objects(signals: list, date: str = None) -> list:
    """
    为 Signal 对象列表批量附加辅助分析（写入 signal.extra["analysis"]）。
    extra 会随 save_signals 持久化并被 Web 看板消费。原地修改并返回。
    """
    for s in signals:
        code = getattr(s, "code", None)
        if not code:
            continue
        # ETF/指数信号不聚合个股基本面/资金流
        asset_type = (s.extra or {}).get("asset_type", "stock")
        try:
            analysis = build_auxiliary_analysis(code, date=date, asset_type=asset_type)
            if s.extra is None:
                s.extra = {}
            s.extra["analysis"] = analysis
        except Exception as e:
            logger.debug(f"enrich {code} 失败: {e}")
    return signals
