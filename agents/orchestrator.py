"""
agents/orchestrator.py — LangGraph 工作流编排

多 Agent 协作工作流（规则编排，LLM 待接入）：
  check_data → (update_data?) → macro_regime → run_scan → risk_filter
  → await_approval → generate_orders → notify → log_result

阶段一（分层决策骨架）：
  - 新增 macro_regime 节点（L1 宏观场景层），输出 regime/risk_mode，构建 decision_context
  - route_after_macro 条件边按 risk_mode 路由（阶段二扩展为不同行业筛选路径）
  - log_result 增加结构化决策日志 decision_log_<date>.json
"""
import logging
from datetime import datetime
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)


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


# ── 节点实现 ────────────────────────────────────────────────

def macro_regime_node(state: WorkflowState) -> WorkflowState:
    """
    阶段一新增：L1 宏观场景感知层。
    
    评估宏观环境，输出 regime/risk_mode，构建 decision_context。
    """
    from agents.strategy_agent import evaluate_market_condition, build_decision_context
    
    state["log"] = state.get("log", [])
    
    try:
        # 评估宏观环境
        env = evaluate_market_condition()
        state["macro_regime"] = env
        
        # 构建决策上下文
        decision_context = build_decision_context(env)
        state["decision_context"] = decision_context
        
        # 根据 decision_context 更新策略选择（覆盖传入的 strategy_keys）
        state["strategy_keys"] = decision_context["strategy_keys"]
        
        state["log"].append(
            f"[macro_regime] regime={env['regime']} risk_mode={env['risk_mode']} "
            f"confidence={env['confidence']:.2f} strategies={len(decision_context['strategy_keys'])}"
        )
        logger.info(f"Macro regime: {env['regime']}, risk_mode: {env['risk_mode']}, reason: {env['trigger_reason']}")
        
    except Exception as e:
        logger.error(f"Macro regime evaluation failed: {e}")
        state["error"] = f"宏观评估失败: {str(e)}"
    
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
    """执行全市场扫描"""
    from agent_tools import run_scan
    result = run_scan(
        date=state.get("date"),
        strategies=state.get("strategy_keys"),
    )
    state["scan_results"] = result
    state["log"].append(f"[scan] {result.get('count', 0)} signals in {result.get('elapsed', 0)}s")
    if not result["success"]:
        state["error"] = result.get("error", "扫描失败")
    return state


def risk_filter_node(state: WorkflowState) -> WorkflowState:
    """RC1~RC8 风控过滤 + 辅助分析"""
    from agent_tools import filter_signals_by_risk
    signals = state.get("scan_results", {}).get("signals", [])
    result = filter_signals_by_risk(signals)
    state["filtered_signals"] = result.get("passed", [])
    state["log"].append(
        f"[risk_filter] passed={len(result.get('passed', []))} blocked={result.get('blocked_count', 0)}"
    )
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
    result = generate_orders(signals)
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
    """持久化运行日志（阶段一：增加 macro_regime / decision_context 结构化记录）"""
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

    # 阶段一新增：结构化决策日志 decision_log_<date>.json（符合 OB-R1）
    scan_results = state.get("scan_results", {})
    decision_log = {
        "date": date_str,
        "macro_regime": state.get("macro_regime", {}),
        "decision_context": state.get("decision_context", {}),
        "scan_results_summary": {
            "total_signals": scan_results.get("count", 0),
            "elapsed": scan_results.get("elapsed", 0),
        },
        "filtered_signals_count": len(state.get("filtered_signals", [])),
        "orders_count": len(state.get("orders", [])),
        "logs": state.get("log", []),
        "error": state.get("error"),
    }
    date_compact = date_str.replace("-", "")
    with open(log_dir / f"decision_log_{date_compact}.json", "w", encoding="utf-8") as f:
        json.dump(decision_log, f, ensure_ascii=False, indent=2)

    return state


# ── 构建图 ──────────────────────────────────────────────────

def build_workflow() -> StateGraph:
    """构建 Orchestrator 工作流，返回编译后的 graph"""
    graph = StateGraph(WorkflowState)

    # 阶段一：增加 macro_regime 节点
    graph.add_node("check_data", check_data_node)
    graph.add_node("macro_regime", macro_regime_node)
    graph.add_node("update_data", update_data_node)
    graph.add_node("run_scan", scan_node)
    graph.add_node("risk_filter", risk_filter_node)
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

    # 阶段一：macro_regime → 条件边路由（按 risk_mode 切换策略配置）
    def route_after_macro(state: WorkflowState) -> str:
        """
        根据 risk_mode 路由到不同策略配置（阶段一简化版）。
        
        阶段一：所有路径都走 run_scan，但 state["strategy_keys"] 已按 risk_mode 调整。
        阶段二：可扩展为不同路径走不同的行业筛选逻辑。
        """
        macro = state.get("macro_regime", {})
        risk_mode = macro.get("risk_mode", "balanced")
        
        # AC11: 必须有 default 分支
        logger.debug(f"Routing after macro: risk_mode={risk_mode}")
        
        # 阶段一简化：所有模式都走同一路径，只是 strategy_keys 不同
        # 阶段二可扩展为：defensive_path → industry_rotation → run_scan
        return "run_scan"

    graph.add_conditional_edges("macro_regime", route_after_macro, {
        "run_scan": "run_scan",
    })

    # run_scan → risk_filter
    graph.add_edge("run_scan", "risk_filter")

    # risk_filter → await_approval
    graph.add_edge("risk_filter", "await_approval")

    # await_approval → generate_orders
    graph.add_edge("await_approval", "generate_orders")

    # generate_orders → notify → log → END
    graph.add_edge("generate_orders", "notify")
    graph.add_edge("notify", "log_result")
    graph.add_edge("log_result", END)

    return graph.compile()


def run_daily_workflow(date: str = None, asset_types: list = None,
                       strategy_keys: list = None) -> dict:
    """
    一键执行每日完整流程。

    阶段一升级：返回包含 regime / risk_mode 信息。

    Returns:
        {
            "success": bool,
            "signals": int,
            "orders": int,
            "log": list,
            "error": str,
            "regime": str,           # 阶段一新增
            "risk_mode": str,        # 阶段一新增
        }
    """
    wf = build_workflow()
    state: WorkflowState = {
        "date": date or datetime.now().strftime("%Y-%m-%d"),
        "asset_types": asset_types or ["stock"],
        "strategy_keys": strategy_keys,  # 会被 macro_regime_node 覆盖
        "log": [],
    }
    try:
        final = wf.invoke(state)
        macro = final.get("macro_regime", {})
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
