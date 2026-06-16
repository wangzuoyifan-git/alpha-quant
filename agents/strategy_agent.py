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


def select_strategies(decision_context=None, market_condition: str = None,
                      risk_mode: str = None) -> list[str]:
    """
    根据 decision_context 画像匹配选择策略组合（阶段四升级，L3-R1）。

    签名兼容三种调用方式：
      select_strategies(decision_context)       # 新（画像匹配）
      select_strategies(risk_mode="defensive")  # 旧关键字，向后兼容
      select_strategies(market_condition="bull") # 更旧，向后兼容

    Args:
        decision_context: dict，含 regime / risk_mode（优先）
        market_condition: @deprecated
        risk_mode: 旧关键字参数，向后兼容

    Returns:
        策略 key 列表
    """
    from strategies.builtin import BUILTIN_STRATEGIES

    # ── 新路径：画像匹配 ──
    if isinstance(decision_context, dict):
        regime = decision_context.get("regime")
        allowed_styles = decision_context.get("allowed_styles", [])
        allowed_horizons = decision_context.get("allowed_time_horizons", ["short"])
        result = []
        for key, cls in BUILTIN_STRATEGIES.items():
            prof = cls().profile()
            # 主筛：regime 命中
            if regime and regime not in prof["applicable_regimes"]:
                continue
            # 可选收窄：allowed_styles（L2 传下来，为空则不筛）
            if allowed_styles and prof["style"] not in allowed_styles:
                continue
            # 可选收窄：allowed_time_horizons
            if prof["time_horizon"] not in allowed_horizons:
                continue
            result.append(key)
        return result if result else list(BUILTIN_STRATEGIES.keys())

    # ── 旧路径：向后兼容 ──
    effective_risk_mode = risk_mode
    if effective_risk_mode is not None:
        if effective_risk_mode == "defensive":
            regime = "risk_off"
        elif effective_risk_mode == "aggressive":
            regime = "risk_on"
        else:
            regime = "balanced"
        return select_strategies({"regime": regime, "risk_mode": effective_risk_mode})

    if market_condition is not None:
        selected = []
        for key, cls in BUILTIN_STRATEGIES.items():
            mc = getattr(cls, "market_condition", "range")
            if mc == market_condition or mc == "range":
                selected.append(key)
        return selected if selected else list(BUILTIN_STRATEGIES.keys())

    return list(BUILTIN_STRATEGIES.keys())


def llm_review_regime(env: dict, **kwargs) -> dict:
    """
    LLM 对规则 regime 研判做二次评估（LLM-R5/AC17）。

    - 只调整 confidence（±confidence_adjust_max，默认 0.2）
    - regime / risk_mode / strategy_keys 永不被 LLM 改变
    - LLM 不可用时静默返回 {"llm_available": False}（LLM-R3）

    Args:
        env: evaluate_market_condition() 返回的结果

    Returns:
        咨询性 dict，字段：llm_available / agree / confidence_adjust /
        reasoning / provider（LLM 不可用时仅含 llm_available=False）
    """
    import json
    try:
        from llm_clients import get_client
        from llm_clients.prompts import REGIME_REVIEW_SYSTEM, regime_review_user
        from config.settings import LLM as cfg
    except Exception:
        return {"llm_available": False}

    client = get_client()
    # 不检查 is_available，直接 chat；不可用时返回 available=False（LLM-R3）
    messages = [
        {"role": "system", "content": REGIME_REVIEW_SYSTEM},
        {"role": "user", "content": regime_review_user(env)},
    ]
    result = client.chat(messages)
    if not result.get("available"):
        return {"llm_available": False}

    # 解析 LLM 输出（LLM-R7 结构化推理）
    try:
        parsed = json.loads(result["content"])
    except (json.JSONDecodeError, TypeError):
        logger.warning("LLM regime 研判输出解析失败，降级")
        return {"llm_available": False}

    # clamp confidence_adjust（LLM-R5）
    max_adj = float(cfg.get("confidence_adjust_max", 0.2))
    raw_adj = float(parsed.get("confidence_adjust", 0.0))
    clamped = max(-max_adj, min(max_adj, raw_adj))

    # AC17：只返回咨询字段，不含 regime/risk_mode
    return {
        "llm_available": True,
        "agree": bool(parsed.get("agree", True)),
        "confidence_adjust": clamped,
        "reasoning": str(parsed.get("reasoning", "")),
        "provider": result.get("provider", ""),
    }


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

# L1→L2 三维权重配置（随 regime，遵循 requirements.md L1-R3）
# w1=景气度, w2=估值, w3=资金流（台阶二景气度接入，w1 激活）
# 原则：防御市重基本面（景气+估值），进攻市重资金流（动量）
_L2_WEIGHT_BY_REGIME = {
    "crisis":   {"w1": 0.4, "w2": 0.4, "w3": 0.2},  # 危机重质量+低估
    "risk_off": {"w1": 0.4, "w2": 0.35, "w3": 0.25},  # 防御重基本面
    "balanced": {"w1": 0.3, "w2": 0.25, "w3": 0.45},
    "risk_on":  {"w1": 0.2, "w2": 0.15, "w3": 0.65},  # 进攻重资金流
    "volatile": {"w1": 0.3, "w2": 0.3, "w3": 0.4},
}

# L1 配置仓/交易仓资金分配（按 risk_mode，台阶一只传递记录不真正约束）
_ALLOCATION_BY_MODE = {
    "defensive": {"position_pct": 0.7, "trading_pct": 0.3},
    "balanced":  {"position_pct": 0.5, "trading_pct": 0.5},
    "aggressive": {"position_pct": 0.3, "trading_pct": 0.7},
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
    regime = env["regime"]
    # 台阶一：strategy_keys 在 L1 阶段先用 regime 画像匹配产出初值，
    # L2 回填 allowed_styles 后会在 industry_rotation_node 重算收窄。
    strategy_keys = select_strategies({"regime": regime, "risk_mode": risk_mode})

    return {
        # ── L1 输出 ──
        "regime": regime,
        "risk_mode": risk_mode,
        "max_position": _MAX_POSITION_BY_MODE.get(risk_mode, 0.7),
        "allocation": dict(_ALLOCATION_BY_MODE.get(
            risk_mode, {"position_pct": 0.5, "trading_pct": 0.5})),
        "l2_weight_config": dict(_L2_WEIGHT_BY_REGIME.get(
            regime, {"w1": 0.0, "w2": 0.3, "w3": 0.7})),

        # ── L2 输出（industry_rotation_node 回填）──
        "allowed_industries": [],
        "industry_weights": {},
        "industry_detail": {},
        "industry_horizon_map": {},

        # ── 逐层收窄的策略空间约束（L2 回填，台阶一直通）──
        "allowed_styles": [],            # 空=不收窄，L2 按行业特性填充
        "allowed_time_horizons": ["short"],

        # ── 元数据 ──
        "strategy_keys": strategy_keys,
        "timestamp": datetime.now().isoformat(),
    }


def llm_review_candidates(signals: list, top_n: int = None) -> list:
    """
    LLM-R6：对 filtered_signals 中 Top N 候选标的做 LLM 定性点评。

    - 只分析 Top N（top_n，默认从 config candidate_top_n=5），控制成本
    - 不修改 signal/score/position_type 等字段
    - autonomy_level=OFF 或 LLM 不可用时返回 []（LLM-R3 静默降级）

    Returns:
        [{"code", "view", "reasoning", "provider"}, ...] 或 []
    """
    import json
    try:
        from llm_clients import get_client
        from llm_clients.prompts import CANDIDATE_REVIEW_SYSTEM, candidate_review_user
        from config.settings import LLM as cfg
    except Exception:
        return []

    if top_n is None:
        top_n = int(cfg.get("candidate_top_n", 5))

    client = get_client()
    if not client.is_available:
        return []

    top = sorted(signals, key=lambda s: float(s.get("score", 0)), reverse=True)[:top_n]
    reviews = []
    for sig in top:
        try:
            result = client.chat([
                {"role": "system", "content": CANDIDATE_REVIEW_SYSTEM},
                {"role": "user", "content": candidate_review_user(sig)},
            ])
            if not result.get("available"):
                continue
            parsed = json.loads(result["content"])
            reviews.append({
                "code": sig.get("code"),
                "view": parsed.get("view", ""),
                "reasoning": parsed.get("reasoning", ""),
                "provider": result.get("provider", ""),
            })
        except (json.JSONDecodeError, TypeError, KeyError):
            logger.debug(f"llm_review_candidates {sig.get('code')} JSON 解析失败，跳过")
        except Exception as e:
            logger.warning(f"llm_review_candidates {sig.get('code')} 异常: {e}")
    return reviews
