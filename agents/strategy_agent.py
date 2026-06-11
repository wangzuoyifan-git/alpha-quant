"""
agents/strategy_agent.py — Strategy Agent

Phase 2 核心：根据市场环境评估各策略绩效，选择最优策略组合。
"""
import logging

logger = logging.getLogger(__name__)


def evaluate_market_condition() -> dict:
    """
    评估当前市场环境：读取宏观指标判断趋势。

    阶段一升级：返回结构化 regime 输出（符合 requirements.md L1-R1）

    Returns:
        {
            "regime": str,           # crisis / risk_off / balanced / risk_on / volatile
            "risk_mode": str,        # defensive / balanced / aggressive
            "confidence": float,     # 0.0~1.0，判断置信度
            "indicators": dict,      # 原始指标值
            "trigger_reason": str,   # 判定理由
        }
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
    vix_std_20 = None
    if vix is not None and not vix.empty and "close" in vix.columns:
        indicators["vix"] = round(float(vix.iloc[-1]["close"]), 1)
        # L1-R2: VIX 20 日标准差（用于 volatile 判定）
        if len(vix) >= 20:
            vix_std_20 = round(float(vix["close"].tail(20).std()), 2)
            indicators["vix_std_20"] = vix_std_20

    # A 股指数单日跌幅（沪深300，用于 crisis 判定）
    index_drop = None
    try:
        from data.store import load as load_market
        idx = load_market("000300", asset_type="index")
        if idx is not None and not idx.empty and "close" in idx.columns:
            last = idx.iloc[-1]
            if "preclose" in idx.columns and last["preclose"] and last["preclose"] > 0:
                index_drop = round((float(last["close"]) / float(last["preclose"]) - 1) * 100, 2)
            elif len(idx) >= 2:
                prev_close = float(idx.iloc[-2]["close"])
                if prev_close > 0:
                    index_drop = round((float(last["close"]) / prev_close - 1) * 100, 2)
            if index_drop is not None:
                indicators["index_drop_pct"] = index_drop
    except Exception as e:
        logger.debug(f"读取 A 股指数失败，crisis 仅用 VIX 判定: {e}")

    # ── 阶段一：结构化 regime 判定（规则版） ──

    pmi_val = indicators.get("pmi", 50)
    vix_val = indicators.get("vix", 20)
    m2_val = indicators.get("m2_yoy", 8.0)

    regime = "balanced"
    risk_mode = "balanced"
    confidence = 0.5
    trigger_reason = "默认震荡市"

    # 按 requirements.md L1-R2 映射规则
    # crisis: VIX > 40 或 A股单日跌幅 > 5%
    if (vix_val is not None and vix_val > 40) or (index_drop is not None and index_drop < -5):
        regime = "crisis"
        risk_mode = "defensive"
        confidence = 0.9
        if index_drop is not None and index_drop < -5:
            trigger_reason = f"沪深300单日跌幅={index_drop}% < -5%，金融危机级别"
        else:
            trigger_reason = f"VIX={vix_val} > 40，金融危机级别"
    # risk_off: VIX > 30
    elif vix_val is not None and vix_val > 30:
        regime = "risk_off"
        risk_mode = "defensive"
        confidence = 0.8
        trigger_reason = f"VIX={vix_val} > 30，风险收缩"
    # risk_off: PMI < 48
    elif pmi_val is not None and pmi_val < 48:
        regime = "risk_off"
        risk_mode = "defensive"
        confidence = 0.7
        trigger_reason = f"PMI={pmi_val} < 48，经济收缩"
    # risk_on: PMI > 52 且 VIX < 20
    elif pmi_val is not None and pmi_val > 52 and (vix_val is None or vix_val < 20):
        regime = "risk_on"
        risk_mode = "aggressive"
        confidence = 0.75
        trigger_reason = f"PMI={pmi_val} > 52 且 VIX={vix_val} < 20，风险扩张"
    # volatile: VIX 20 日标准差 > 5
    elif vix_std_20 is not None and vix_std_20 > 5:
        regime = "volatile"
        risk_mode = "defensive"
        confidence = 0.6
        trigger_reason = f"VIX 20日标准差={vix_std_20} > 5，高波动"
    else:
        regime = "balanced"
        risk_mode = "balanced"
        confidence = 0.6
        trigger_reason = f"PMI={pmi_val} 在 48~52 区间，震荡市"

    return {
        "regime": regime,
        "risk_mode": risk_mode,
        "confidence": confidence,
        "indicators": indicators,
        "trigger_reason": trigger_reason,
        # 向后兼容：保留旧字段
        "condition": regime,
    }


def select_strategies(market_condition: str = None, risk_mode: str = None) -> list[str]:
    """
    根据市场环境选择适用的策略组合（AC5 market_condition 驱动）。

    阶段一升级：优先使用 risk_mode（defensive/balanced/aggressive），
               向后兼容旧的 market_condition 参数。

    Args:
        market_condition: 旧参数，向后兼容（bull/bear/range/volatile）
        risk_mode: 新参数，优先级高于 market_condition

    Returns:
        策略 key 列表

    Phase 2 会扩展为基于历史回测绩效动态选择。
    """
    from strategies.builtin import BUILTIN_STRATEGIES

    # 优先使用 risk_mode
    if risk_mode is not None:
        # 按 requirements.md L3-R1 映射
        if risk_mode == "defensive":
            # 防御策略：基本面价值 + ETF 动量（偏红利）
            return ["fundamental_value", "etf_momentum"]
        elif risk_mode == "aggressive":
            # 进攻策略：动量 + 突破 + 资金流
            return ["ma_cross", "vol_price_breakout", "money_flow"]
        else:  # balanced
            # 均衡模式：全部策略
            return list(BUILTIN_STRATEGIES.keys())

    # 向后兼容：使用旧的 market_condition
    if market_condition is not None:
        selected = []
        for key, cls in BUILTIN_STRATEGIES.items():
            mc = getattr(cls, "market_condition", "range")
            if mc == market_condition or mc == "range":
                selected.append(key)
        return selected if selected else list(BUILTIN_STRATEGIES.keys())

    # 都没提供，返回全部
    return list(BUILTIN_STRATEGIES.keys())


def recommend_daily_plan() -> dict:
    """
    Strategy Agent 每日推荐：评估环境 → 选择策略 → 给出扫描建议。

    阶段一升级：返回 regime / risk_mode 结构化字段。

    返回 {"regime": str, "risk_mode": str, "strategies": list, "scan_params": dict}
    """
    env = evaluate_market_condition()
    strategies = select_strategies(risk_mode=env["risk_mode"])

    return {
        "regime": env["regime"],
        "risk_mode": env["risk_mode"],
        "confidence": env["confidence"],
        "indicators": env["indicators"],
        "trigger_reason": env["trigger_reason"],
        "strategies": strategies,
        "scan_params": {
            "exclude_st": True,
            "min_price": 2.0,
            "max_price": 500.0,
        },
        # 向后兼容
        "condition": env["regime"],
    }


# 仓位上限映射（按 risk_mode）
_MAX_POSITION_BY_MODE = {
    "defensive": 0.3,
    "balanced": 0.7,
    "aggressive": 1.0,
}


def build_decision_context(env: dict = None) -> dict:
    """
    构建分层决策上下文（decision_context），符合 requirements.md L1-R3。

    这是贯穿 L1→L2→L3 的约束对象。L1 填充 regime/risk_mode/max_position/strategy_keys，
    L2（阶段二）填充 allowed_industries/industry_weights。

    Args:
        env: evaluate_market_condition() 的结果，不传则内部调用

    Returns:
        decision_context dict
    """
    from datetime import datetime

    if env is None:
        env = evaluate_market_condition()

    risk_mode = env["risk_mode"]
    strategy_keys = select_strategies(risk_mode=risk_mode)

    return {
        "regime": env["regime"],
        "risk_mode": risk_mode,
        "max_position": _MAX_POSITION_BY_MODE.get(risk_mode, 0.7),
        "allowed_industries": [],   # 阶段二 L2 填充
        "industry_weights": {},     # 阶段二 L2 填充
        "strategy_keys": strategy_keys,
        "timestamp": datetime.now().isoformat(),
    }
