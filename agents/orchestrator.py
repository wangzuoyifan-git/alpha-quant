"""
agents/orchestrator.py — LangGraph 工作流编排

多 Agent 协作工作流（规则编排，LLM 待接入）：
  check_data → (update_data?) → macro_regime → industry_rotation → run_scan
  → risk_filter → await_approval → generate_orders → notify → log_result

阶段一：L1 宏观场景层（macro_regime）
阶段二：L2 行业轮动层（industry_rotation，约束穿透引擎层）
阶段三：Checkpoint 断点续跑 + 决策日志增强（OB-R1）+ 节点审计（OB-R5）
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)

# ── 阶段三：Checkpoint 工厂（OB-R4/AC15） ───────────────────

try:
    from langgraph.checkpoint.sqlite import SqliteSaver
except ImportError:
    SqliteSaver = None  # type: ignore

_DEFAULT_CHECKPOINT_PATH = str(
    Path(__file__).parent.parent / "data" / "cache" / "checkpoints" / "workflow.sqlite"
)


def make_checkpointer(db_path: str | None = _DEFAULT_CHECKPOINT_PATH):
    """
    创建 LangGraph checkpointer（OB-R4）。

    优先 SqliteSaver（持久化跨进程恢复）；依赖缺失时降级 InMemorySaver（AC15）。
    db_path=None 返回 None（不启用 checkpoint，向后兼容 AC14）。
    """
    if db_path is None:
        return None

    if SqliteSaver is not None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = __import__("sqlite3").connect(db_path, check_same_thread=False)
        return SqliteSaver(conn)
    else:
        # 降级：InMemorySaver（仅进程内，不跨进程恢复）
        logger.warning(
            "langgraph-checkpoint-sqlite 未安装，降级为 InMemorySaver（无法跨进程恢复）。"
            "安装：pip install langgraph-checkpoint-sqlite"
        )
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver()


# ── 阶段三：节点审计 helper（OB-R5） ────────────────────────

def _to_native(obj):
    """
    递归将 numpy 类型转为 Python 原生类型（OB-R4）。

    checkpoint 持久化用 msgpack 序列化，numpy.float64/int64 不可序列化，
    L2 行业评分、宏观指标等来自 pandas 的数值需转换，否则 checkpoint 报错。
    """
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(v) for v in obj]
    # numpy 标量有 .item() 方法
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            return obj.item()
        except (ValueError, AttributeError):
            return obj
    return obj


def _audit(state: "WorkflowState", node: str,
           input_summary: dict, output_summary: dict) -> None:
    """向 state['node_audit'] 追加节点输入/输出摘要快照（OB-R5）。"""
    if "node_audit" not in state:
        state["node_audit"] = []
    state["node_audit"].append({
        "node": node,
        "timestamp": datetime.now().isoformat(),
        "input_summary": _to_native(input_summary),
        "output_summary": _to_native(output_summary),
    })


# ── 阶段三：日志辅助函数（OB-R1） ───────────────────────────

# 申万行业代码 → 行业名缓存（惰性加载）
_SW_CODE_TO_NAME: dict | None = None


def _get_sw_code_to_name() -> dict:
    """从 industry_sw 获取行业代码→名称映射（惰性加载）。"""
    global _SW_CODE_TO_NAME
    if _SW_CODE_TO_NAME is None:
        try:
            from data import store_meta
            ind_df = store_meta.load("industry_sw")
            if ind_df is not None and not ind_df.empty:
                _SW_CODE_TO_NAME = (
                    ind_df[["industry_code", "industry_name"]]
                    .drop_duplicates("industry_code")
                    .set_index("industry_code")["industry_name"]
                    .astype(str)
                    .to_dict()
                )
            else:
                _SW_CODE_TO_NAME = {}
        except Exception:
            _SW_CODE_TO_NAME = {}
    return _SW_CODE_TO_NAME


_SW_CODE_TO_STOCK: dict | None = None  # code → industry_name


def _get_stock_to_industry() -> dict:
    """返回 stock_code → industry_name 映射（惰性加载）。"""
    global _SW_CODE_TO_STOCK
    if _SW_CODE_TO_STOCK is None:
        try:
            from data import store_meta
            ind_df = store_meta.load("industry_sw")
            if ind_df is not None and not ind_df.empty:
                _SW_CODE_TO_STOCK = (
                    ind_df[["stock_code", "industry_name"]]
                    .drop_duplicates("stock_code")
                    .set_index("stock_code")["industry_name"]
                    .astype(str)
                    .to_dict()
                )
            else:
                _SW_CODE_TO_STOCK = {}
        except Exception:
            _SW_CODE_TO_STOCK = {}
    return _SW_CODE_TO_STOCK


def _extract_industry_scores(decision_context: dict) -> list:
    """
    从 decision_context 提取 L2 行业评分明细列表（OB-R1 industry_scores）。

    decision_context["industry_weights"] 是 {code: score}，
    用 industry_sw 查行业名，返回按 score 降序的列表。
    """
    weights = decision_context.get("industry_weights") or {}
    if not weights:
        return []
    code_to_name = _get_sw_code_to_name()
    scores = [
        {
            "industry_code": code,
            "industry_name": code_to_name.get(str(code), str(code)),
            "score": round(float(score), 1),
        }
        for code, score in weights.items()
    ]
    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores


# 内置策略 name → key 映射（用于 by_strategy 统计）
_STRATEGY_NAMES = {
    "均线金叉": "ma_cross",
    "MACD+RSI共振": "macd_rsi",
    "量价突破": "vol_price_breakout",
    "ETF动量轮动": "etf_momentum",
    "基本面价值": "fundamental_value",
    "资金流信号": "money_flow",
}


def _build_scan_summary(scan_results: dict) -> dict:
    """
    构建扫描摘要（OB-R1 scan_results_summary），含 by_industry/by_strategy 分组统计。

    by_industry：信号 code join industry_sw 后按行业名计数。
    by_strategy：信号 reason 文本匹配策略 name 后计数（粗粒度，一条信号可多策略）。
    """
    signals = scan_results.get("signals") or []

    by_strategy: dict[str, int] = {}
    for name in _STRATEGY_NAMES:
        cnt = sum(1 for s in signals if name in s.get("reason", ""))
        if cnt:
            by_strategy[name] = cnt

    stock_to_industry = _get_stock_to_industry()
    by_industry: dict[str, int] = {}
    for s in signals:
        ind = stock_to_industry.get(str(s.get("code", "")), "")
        if ind:
            by_industry[ind] = by_industry.get(ind, 0) + 1

    return {
        "total_signals": scan_results.get("count", len(signals)),
        "elapsed": scan_results.get("elapsed", 0),
        "by_industry": by_industry,
        "by_strategy": by_strategy,
    }


class WorkflowState(TypedDict, total=False):
    date: str
    asset_types: list[str]
    strategy_keys: list[str]
    data_status: dict
    scan_results: dict
    filtered_signals: list
    orders: list
    human_approved: bool
    error: Optional[str]
    log: list[str]
    # 阶段一新增字段（AC10: total=False 确保向后兼容）
    macro_regime: dict          # L1 宏观层输出
    decision_context: dict      # 逐层收窄的约束上下文
    # 阶段三新增字段（OB-R5）
    node_audit: list[dict]      # 每层节点 input/output 审计快照
    # 台阶一新增字段
    watch_pool: list            # L3-B 配置仓候选（WATCH 信号）
    industry_override: list[str]  # 手动指定行业范围（验证用，跳过 L2 自动评分）
    industry_level: str         # 申万行业层级 L1/L2/L3（L2-R5）


# ── 节点实现 ────────────────────────────────────────────────

def macro_regime_node(state: WorkflowState) -> WorkflowState:
    """
    阶段一新增：L1 宏观场景感知层。
    阶段四升级：规则评估后接入 LLM 二次研判（LLM-R5，autonomy_level 总闸门控制）。

    评估宏观环境，输出 regime/risk_mode，构建 decision_context。
    LLM 研判结果存入 macro_regime["llm_review"]（只写咨询字段，AC17）。
    """
    from agents.strategy_agent import evaluate_market_condition, build_decision_context, llm_review_regime

    state["log"] = state.get("log", [])

    try:
        # 规则评估（核心路径，永远先跑，不受 LLM 影响）
        env = evaluate_market_condition()
        macro = _to_native(env)

        # LLM 二次研判（静默降级，失败不影响 workflow，LLM-R3）
        try:
            llm_review = llm_review_regime(env)
        except Exception as llm_exc:
            logger.warning("LLM regime 研判异常，静默降级: %s", llm_exc)
            llm_review = {"llm_available": False}
        macro["llm_review"] = llm_review

        state["macro_regime"] = macro

        # 构建决策上下文（用规则 env，LLM 不进 decision_context）
        decision_context = _to_native(build_decision_context(env))
        state["decision_context"] = decision_context

        # 根据 decision_context 更新策略选择（覆盖传入的 strategy_keys）
        state["strategy_keys"] = decision_context["strategy_keys"]

        state["log"].append(
            f"[macro_regime] regime={env['regime']} risk_mode={env['risk_mode']} "
            f"confidence={env['confidence']:.2f} strategies={len(decision_context['strategy_keys'])} "
            f"llm={llm_review.get('llm_available', False)}"
        )
        logger.info(
            "Macro regime: %s, risk_mode: %s, reason: %s, llm_available: %s",
            env['regime'], env['risk_mode'], env['trigger_reason'],
            llm_review.get('llm_available', False),
        )

        # 阶段三：节点审计（OB-R5）
        _audit(
            state, "macro_regime",
            input_summary={"date": state.get("date")},
            output_summary={
                "regime": env.get("regime"),
                "risk_mode": env.get("risk_mode"),
                "confidence": env.get("confidence"),
                "trigger_reason": env.get("trigger_reason"),
                "strategy_keys": decision_context.get("strategy_keys"),
                "llm_available": llm_review.get("llm_available", False),
            },
        )

    except Exception as e:
        logger.error(f"Macro regime evaluation failed: {e}")
        state["error"] = f"宏观评估失败: {str(e)}"

    return state


def industry_rotation_node(state: WorkflowState) -> WorkflowState:
    """
    阶段二新增：L2 行业轮动层。

    基于 L1 的 risk_mode，用行业资金流筛出行业池，
    写入 decision_context["allowed_industries"]，收窄 L3 扫描范围。
    """
    from agents.industry_agent import evaluate_industry_rotation

    state["log"] = state.get("log", [])

    try:
        ctx = state.get("decision_context", {})
        risk_mode = ctx.get("risk_mode", "balanced")
        level = state.get("industry_level", "L1")
        l2_weights = ctx.get("l2_weight_config")

        result = evaluate_industry_rotation(risk_mode=risk_mode, date=state.get("date"),
                                            level=level, weights=l2_weights)

        # 验证用：industry_override 手动指定行业范围（跳过 L2 自动产出的全部行业，
        # 收窄扫描规模，便于快速验证完整 workflow）
        override = state.get("industry_override")
        if override:
            full_detail = result.get("industry_detail", {})
            full_horizon = result.get("industry_horizon_map", {})
            result["allowed_industries"] = [c for c in override]
            result["industry_detail"] = {c: full_detail[c] for c in override if c in full_detail}
            result["industry_horizon_map"] = {c: full_horizon.get(c, "short") for c in override}
            result["industry_scores"] = [
                s for s in result.get("industry_scores", [])
                if s["industry_code"] in override
            ]
            state["log"].append(f"[industry_rotation] 手动指定行业范围（override）: {override}")

        allowed = result.get("allowed_industries", [])
        # 回填 decision_context（阶段三：sanitize numpy 类型，OB-R4）
        ctx["allowed_industries"] = allowed
        ctx["industry_weights"] = _to_native({
            s["industry_code"]: s["score"] for s in result.get("industry_scores", [])
        })
        # 台阶一新增：多周期约束字段回填
        ctx["industry_detail"] = _to_native(result.get("industry_detail", {}))
        ctx["industry_horizon_map"] = _to_native(result.get("industry_horizon_map", {}))
        ctx["allowed_styles"] = result.get("allowed_styles", [])
        # allowed_time_horizons：从 horizon_map 中汇总出现的 horizon 类型
        horizons = set(result.get("industry_horizon_map", {}).values())
        ctx["allowed_time_horizons"] = sorted(horizons) if horizons else ["short"]
        # 重算 strategy_keys（现在 allowed_styles 已填充）
        from agents.strategy_agent import select_strategies
        ctx["strategy_keys"] = select_strategies(ctx)
        state["decision_context"] = ctx
        state["strategy_keys"] = ctx["strategy_keys"]

        if result.get("fallback"):
            state["log"].append(
                "[industry_rotation] 行业数据缺失，退化为全市场扫描（fallback）"
            )
        else:
            names = [s["industry_name"] for s in result.get("industry_scores", [])][:5]
            state["log"].append(
                f"[industry_rotation] risk_mode={risk_mode} 选出 {len(allowed)} 个行业: {names}"
            )
        logger.info(f"L2 industry pool: {len(allowed)} industries (risk_mode={risk_mode})")

        # 阶段三：节点审计（OB-R5）
        _audit(
            state, "industry_rotation",
            input_summary={"risk_mode": risk_mode, "date": state.get("date")},
            output_summary={
                "allowed_industries_count": len(allowed),
                "allowed_industries": allowed,
                "fallback": result.get("fallback", False),
                "top_industries": [
                    s["industry_name"] for s in result.get("industry_scores", [])
                ][:5],
            },
        )

    except Exception as e:
        logger.error(f"Industry rotation failed: {e}")
        # 容错：失败时不阻断流程，退化为全市场
        ctx = state.get("decision_context", {})
        ctx["allowed_industries"] = []
        state["decision_context"] = ctx
        state["log"].append(f"[industry_rotation] 异常，退化为全市场: {e}")
        _audit(
            state, "industry_rotation",
            input_summary={"risk_mode": state.get("decision_context", {}).get("risk_mode")},
            output_summary={"error": str(e), "fallback": True},
        )

    return state


def check_data_node(state: WorkflowState) -> WorkflowState:
    """检查数据新鲜度"""
    from agent_tools import check_data_freshness
    state["log"] = state.get("log", [])
    result = check_data_freshness()
    state["data_status"] = result
    state["log"].append(f"[check_data] domains={len(result['domains'])} stale={result['stale']}")
    if not result["success"]:
        state["error"] = result.get("error", "数据检查失败")

    # 阶段三：节点审计（OB-R5）
    _audit(
        state, "check_data",
        input_summary={"date": state.get("date")},
        output_summary={
            "domains_count": len(result.get("domains", {})),
            "stale": result.get("stale", []),
            "success": result.get("success", False),
        },
    )
    return state


def update_data_node(state: WorkflowState) -> WorkflowState:
    """增量更新过期域"""
    from agent_tools import update_domain
    stale = state.get("data_status", {}).get("stale", [])
    for domain in stale:
        result = update_domain(domain)
        state["log"].append(f"[update_data] {domain}: {'ok' if result['success'] else 'fail'}")
    return state


def scan_node(state: WorkflowState) -> WorkflowState:
    """执行扫描（阶段二：受 L2 行业池约束前置收窄；台阶一：落盘 WATCH 配置仓候选）"""
    from agent_tools import run_scan
    state["log"] = state.get("log", [])
    ctx = state.get("decision_context", {})
    allowed_industries = ctx.get("allowed_industries") or None  # 空列表→None（全市场）
    result = run_scan(
        date=state.get("date"),
        strategies=state.get("strategy_keys"),
        industry_filter=allowed_industries,
        industry_level=state.get("industry_level", "L1"),
    )
    # 阶段三：sanitize numpy 类型（OB-R4）
    state["scan_results"] = _to_native(result)

    # 台阶一：分离 WATCH 配置仓候选落盘（D-7 L3-B 候选链路）
    signals = result.get("signals", [])
    watch_pool = [s for s in signals if s.get("signal") == "WATCH"]
    if watch_pool:
        state["watch_pool"] = watch_pool
        state["log"].append(f"[scan] {len(watch_pool)} WATCH 配置仓候选（人工确认后进配置仓）")

    state["log"].append(f"[scan] {result.get('count', 0)} signals in {result.get('elapsed', 0)}s")
    if not result["success"]:
        state["error"] = result.get("error", "扫描失败")

    # 阶段三：节点审计（OB-R5）
    _audit(
        state, "run_scan",
        input_summary={
            "strategy_keys": state.get("strategy_keys"),
            "industry_filter_count": len(allowed_industries) if allowed_industries else 0,
        },
        output_summary={
            "count": result.get("count", 0),
            "watch_count": len(watch_pool),
            "elapsed": result.get("elapsed", 0),
            "success": result.get("success", False),
        },
    )
    return state


def risk_filter_node(state: WorkflowState) -> WorkflowState:
    """RC1~RC8 风控过滤 + 辅助分析"""
    from agent_tools import filter_signals_by_risk
    signals = state.get("scan_results", {}).get("signals", [])
    result = filter_signals_by_risk(signals)
    state["filtered_signals"] = _to_native(result.get("passed", []))
    state["log"].append(
        f"[risk_filter] passed={len(result.get('passed', []))} blocked={result.get('blocked_count', 0)}"
    )

    # 阶段三：节点审计（OB-R5）
    _audit(
        state, "risk_filter",
        input_summary={"input_signals": len(signals)},
        output_summary={
            "passed": len(result.get("passed", [])),
            "blocked_count": result.get("blocked_count", 0),
        },
    )
    return state


def llm_candidate_review_node(state: WorkflowState) -> WorkflowState:
    """
    LLM-R6: 对 Top N 候选标的做 LLM 定性点评，结果落盘 decision_log。
    不修改 filtered_signals 任何字段。autonomy=OFF 或 LLM 不可用时静默跳过。
    """
    from agents.strategy_agent import llm_review_candidates
    state["log"] = state.get("log", [])
    signals = state.get("filtered_signals", [])
    try:
        reviews = llm_review_candidates(signals)
        state["llm_candidate_reviews"] = reviews
        if reviews:
            state["log"].append(f"[llm_review] {len(reviews)} 候选标的已点评")
    except Exception as e:
        logger.warning("llm_candidate_review_node 异常，静默跳过: %s", e)
        state["llm_candidate_reviews"] = []
    return state


def human_approval_node(state: WorkflowState) -> WorkflowState:
    """Human-in-the-loop：Phase 1/2 必须人工确认"""
    # Phase 2 实现：向推送渠道发送确认请求，等待回复
    # Phase 1 当前：直接推送信号，人工在 CLIENT 端操作
    state["human_approved"] = True
    state["log"].append("[approval] auto-passed (Phase 1/2 人工在 CLI 确认)")
    return state


def order_node(state: WorkflowState) -> WorkflowState:
    """生成指令单"""
    from agent_tools import generate_orders
    signals = state.get("filtered_signals", [])
    if not signals:
        state["log"].append("[orders] no signals, skip")
        return state
    ctx = state.get("decision_context", {})
    result = generate_orders(
        signals,
        allocation=ctx.get("allocation"),
        max_position=ctx.get("max_position"),
    )
    state["orders"] = result.get("orders", [])
    state["log"].append(f"[orders] generated {result.get('count', 0)} orders")
    return state


def notify_node(state: WorkflowState) -> WorkflowState:
    """推送通知"""
    from agent_tools.strategy_tools import notify_signals
    signals = state.get("filtered_signals", [])
    if signals:
        notify_signals(signals)
    state["log"].append(f"[notify] sent {len(signals)} signals")
    return state


def log_result_node(state: WorkflowState) -> WorkflowState:
    """
    持久化运行日志。

    阶段一：增加 macro_regime / decision_context 结构化记录
    阶段三：补全 OB-R1 schema（industry_scores 明细、scan_summary 分组、node_audit）
    """
    import json
    from pathlib import Path
    log_dir = Path(__file__).parent.parent / "data" / "processed"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_str = state.get("date", datetime.now().strftime("%Y-%m-%d"))

    # 兼容旧格式：run_log_<ts>.json
    with open(log_dir / f"run_log_{ts}.json", "w", encoding="utf-8") as f:
        json.dump({
            "date": date_str,
            "logs": state.get("log", []),
            "signals_count": len(state.get("filtered_signals", [])),
            "orders_count": len(state.get("orders", [])),
            "error": state.get("error"),
        }, f, ensure_ascii=False, indent=2)

    # 阶段三：完整结构化决策日志 decision_log_<date>.json（OB-R1）
    scan_results = state.get("scan_results", {})
    decision_context = state.get("decision_context", {})
    decision_log = {
        "date": date_str,
        "macro_regime": state.get("macro_regime", {}),
        "decision_context": decision_context,
        # 阶段三补全：L2 行业评分明细
        "industry_scores": _extract_industry_scores(decision_context),
        # 阶段三补全：扫描摘要含 by_industry/by_strategy 分组
        "scan_results_summary": _build_scan_summary(scan_results),
        # 阶段三新增：每层节点审计快照（OB-R5）
        "node_audit": state.get("node_audit", []),
        "filtered_signals_count": len(state.get("filtered_signals", [])),
        "llm_candidate_reviews": state.get("llm_candidate_reviews", []),
        "orders_count": len(state.get("orders", [])),
        "logs": state.get("log", []),
        "error": state.get("error"),
    }
    date_compact = date_str.replace("-", "")
    with open(log_dir / f"decision_log_{date_compact}.json", "w", encoding="utf-8") as f:
        json.dump(decision_log, f, ensure_ascii=False, indent=2)

    return state


# ── 构建图 ──────────────────────────────────────────────────

def build_workflow(checkpointer=None) -> StateGraph:
    """
    构建 Orchestrator 工作流，返回编译后的 graph。

    :param checkpointer: 可选 LangGraph checkpointer（阶段三 OB-R4）。
                         None 时不启用 checkpoint（向后兼容 AC14）。
    """
    graph = StateGraph(WorkflowState)

    # 阶段一：增加 macro_regime 节点；阶段二：增加 industry_rotation 节点
    graph.add_node("check_data", check_data_node)
    graph.add_node("macro_regime", macro_regime_node)
    graph.add_node("industry_rotation", industry_rotation_node)
    graph.add_node("update_data", update_data_node)
    graph.add_node("run_scan", scan_node)
    graph.add_node("risk_filter", risk_filter_node)
    graph.add_node("llm_candidate_review", llm_candidate_review_node)
    graph.add_node("await_approval", human_approval_node)
    graph.add_node("generate_orders", order_node)
    graph.add_node("notify", notify_node)
    graph.add_node("log_result", log_result_node)

    graph.set_entry_point("check_data")

    # check_data → update_data（如果有过期）→ macro_regime
    def route_after_check(state: WorkflowState) -> str:
        stale = state.get("data_status", {}).get("stale", [])
        if stale and "market/stock" in stale:
            return "update_data"
        return "macro_regime"  # 阶段一：先走宏观评估

    graph.add_conditional_edges("check_data", route_after_check, {
        "update_data": "update_data",
        "macro_regime": "macro_regime",
    })
    graph.add_edge("update_data", "macro_regime")

    # 阶段一/二：macro_regime → 条件边路由（按 risk_mode 切换下层路径）
    def route_after_macro(state: WorkflowState) -> str:
        """
        根据 risk_mode 路由（阶段二：统一经 industry_rotation 收窄行业池）。

        阶段一：直接到 run_scan（strategy_keys 已按 risk_mode 调整）。
        阶段二：先经 L2 行业轮动层 industry_rotation，再到 L3 run_scan。
        不同 risk_mode 在 industry_rotation 内部决定行业池宽窄。
        """
        macro = state.get("macro_regime", {})
        risk_mode = macro.get("risk_mode", "balanced")

        # AC11: 必须有 default 分支
        logger.debug(f"Routing after macro: risk_mode={risk_mode}")

        # 阶段二：统一走 L2 行业轮动层（行业池宽窄由 risk_mode 在 L2 内决定）
        return "industry_rotation"

    graph.add_conditional_edges("macro_regime", route_after_macro, {
        "industry_rotation": "industry_rotation",
        # default 兜底：极端情况下直接扫描（保留向后兼容路径）
        "run_scan": "run_scan",
    })

    # L2 → L3：industry_rotation → run_scan
    graph.add_edge("industry_rotation", "run_scan")

    # run_scan → risk_filter
    graph.add_edge("run_scan", "risk_filter")

    # risk_filter → llm_candidate_review → await_approval
    graph.add_edge("risk_filter", "llm_candidate_review")
    graph.add_edge("llm_candidate_review", "await_approval")

    # await_approval → generate_orders
    graph.add_edge("await_approval", "generate_orders")

    # generate_orders → notify → log → END
    graph.add_edge("generate_orders", "notify")
    graph.add_edge("notify", "log_result")
    graph.add_edge("log_result", END)

    # 阶段三：编译时注入 checkpointer（OB-R4）
    return graph.compile(checkpointer=checkpointer)


def run_daily_workflow(date: str = None, asset_types: list = None,
                       strategy_keys: list = None,
                       enable_checkpoint: bool = False,
                       industry_override: list = None,
                       industry_level: str = "L1") -> dict:
    """
    一键执行每日完整流程。

    阶段一升级：返回包含 regime / risk_mode 信息。
    阶段三升级：可选 checkpoint 断点续跑（OB-R4）。
    台阶一升级：industry_override 手动指定行业范围（验证用，收窄扫描规模）。

    :param enable_checkpoint: 启用 checkpoint 后，崩溃可用相同 thread_id（=date）重跑恢复。
    :param industry_override: 手动指定申万行业代码列表（如 ["801080","801770"]），
                              跳过 L2 自动产出的全部行业，只扫描指定行业（快速验证）。

    Returns:
        含 success/signals/orders/log/regime/risk_mode/allowed_industries/
        watch_pool/decision_log_path 等字段的 dict
    """
    run_date = date or datetime.now().strftime("%Y-%m-%d")

    # 阶段三：按需启用 checkpoint
    checkpointer = make_checkpointer() if enable_checkpoint else None
    wf = build_workflow(checkpointer=checkpointer)
    invoke_config = (
        {"configurable": {"thread_id": run_date}} if enable_checkpoint else None
    )

    state: WorkflowState = {
        "date": run_date,
        "asset_types": asset_types or ["stock"],
        "strategy_keys": strategy_keys,  # 会被 macro_regime_node 覆盖
        "log": [],
    }
    if industry_override:
        state["industry_override"] = industry_override
    if industry_level and industry_level != "L1":
        state["industry_level"] = industry_level
    try:
        final = wf.invoke(state, config=invoke_config) if invoke_config else wf.invoke(state)
        macro = final.get("macro_regime", {})
        ctx = final.get("decision_context", {})
        return {
            "success": not final.get("error"),
            "signals": len(final.get("filtered_signals", [])),
            "orders": len(final.get("orders", [])),
            "log": final.get("log", []),
            "error": final.get("error"),
            # 阶段一新增
            "regime": macro.get("regime", "unknown"),
            "risk_mode": macro.get("risk_mode", "balanced"),
            "confidence": macro.get("confidence", 0.5),
            # 台阶一新增：可视化所需的分层明细
            "decision_context": ctx,
            "allowed_industries": ctx.get("allowed_industries", []),
            "industry_detail": ctx.get("industry_detail", {}),
            "strategy_keys": ctx.get("strategy_keys", []),
            "llm_review": macro.get("llm_review", {"llm_available": False}),
            "scan_results": final.get("scan_results", {}),
            "filtered_signals": final.get("filtered_signals", []),
            "watch_pool": final.get("watch_pool", []),
            "decision_log_path": str(
                Path(__file__).parent.parent / "data" / "processed"
                / f"decision_log_{run_date.replace('-', '')}.json"
            ),
        }
    except Exception as e:
        logger.error(f"Workflow failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "signals": 0,
            "orders": 0,
            "log": [],
            "regime": "error",
            "risk_mode": "balanced",
            "confidence": 0.0,
        }
