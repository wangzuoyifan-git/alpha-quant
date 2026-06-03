"""
agents/strategy_agent.py — Strategy Agent

Phase 2 核心：根据市场环境评估各策略绩效，选择最优策略组合。
"""
import logging

logger = logging.getLogger(__name__)


def evaluate_market_condition() -> dict:
    """
    评估当前市场环境：读取宏观指标判断趋势。

    返回 {"condition": "bull|bear|range|volatile", "indicators": {...}}
    """
    from data.store_macro import load as load_macro

    indicators = {}

    # 国债收益率利差
    bond = load_macro("bond_yield")
    if bond is not None and not bond.empty and "cn_10y" in bond.columns:
        last = bond.iloc[-1]
        indicators["cn_10y"] = round(float(last["cn_10y"]), 2) if "cn_10y" in last else None
        if "cn_spread_10y_2y" in last:
            indicators["spread"] = round(float(last["cn_spread_10y_2y"]), 2)

    # PMI
    pmi = load_macro("pmi")
    if pmi is not None and not pmi.empty:
        pmi_cols = [c for c in pmi.columns if "制造业" in c or "manufacturing" in c.lower()]
        if pmi_cols:
            indicators["pmi"] = round(float(pmi.iloc[-1][pmi_cols[0]]), 1)

    # M2 同比
    m2 = load_macro("m2")
    if m2 is not None and not m2.empty:
        m2_cols = [c for c in m2.columns if "同比" in c]
        if m2_cols:
            indicators["m2_yoy"] = round(float(m2.iloc[-1][m2_cols[0]]), 1)

    # VIX
    vix = load_macro("vix")
    if vix is not None and not vix.empty and "close" in vix.columns:
        indicators["vix"] = round(float(vix.iloc[-1]["close"]), 1)

    # 判断环境
    condition = "range"
    pmi_val = indicators.get("pmi", 50)
    vix_val = indicators.get("vix", 20)

    if vix_val is not None and vix_val > 30:
        condition = "volatile"
    elif pmi_val is not None and pmi_val < 48:
        condition = "bear"
    elif pmi_val is not None and pmi_val > 52 and (vix_val is None or vix_val < 25):
        condition = "bull"

    return {"condition": condition, "indicators": indicators}


def select_strategies(market_condition: str) -> list[str]:
    """
    根据市场环境选择适用的策略组合（AC5 market_condition 驱动）。

    Phase 2 会扩展为基于历史回测绩效动态选择。
    """
    from strategies.builtin import BUILTIN_STRATEGIES
    from strategies.base import BaseStrategy

    selected = []
    for key, cls in BUILTIN_STRATEGIES.items():
        mc = getattr(cls, "market_condition", "range")
        if mc == market_condition or mc == "range":
            selected.append(key)

    return selected if selected else list(BUILTIN_STRATEGIES.keys())


def recommend_daily_plan() -> dict:
    """
    Strategy Agent 每日推荐：评估环境 → 选择策略 → 给出扫描建议。

    返回 {"condition": str, "strategies": list, "scan_params": dict}
    """
    env = evaluate_market_condition()
    strategies = select_strategies(env["condition"])

    return {
        "condition": env["condition"],
        "indicators": env["indicators"],
        "strategies": strategies,
        "scan_params": {
            "exclude_st": True,
            "min_price": 2.0,
            "max_price": 500.0,
        },
    }
