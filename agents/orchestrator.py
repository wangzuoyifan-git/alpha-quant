"""
agents/orchestrator.py — LangGraph 工作流编排

多 Agent 协作工作流（规则编排，LLM 待接入）：
  check_data → (update_data?) → run_scan → risk_filter → await_approval → generate_orders → notify → log_result
  注：evaluate（策略评估）节点为 Phase 2 计划，当前未接入工作流。
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


# ── 节点实现 ────────────────────────────────────────────────

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
    """持久化运行日志"""
    import json
    from pathlib import Path
    log_dir = Path(__file__).parent.parent / "data" / "processed"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(log_dir / f"run_log_{ts}.json", "w") as f:
        json.dump({
            "date": state.get("date", datetime.now().strftime("%Y-%m-%d")),
            "logs": state.get("log", []),
            "signals_count": len(state.get("filtered_signals", [])),
            "orders_count": len(state.get("orders", [])),
            "error": state.get("error"),
        }, f, ensure_ascii=False, indent=2)
    return state


# ── 构建图 ──────────────────────────────────────────────────

def build_workflow() -> StateGraph:
    """构建 Orchestrator 工作流，返回编译后的 graph"""
    graph = StateGraph(WorkflowState)

    graph.add_node("check_data", check_data_node)
    graph.add_node("update_data", update_data_node)
    graph.add_node("run_scan", scan_node)
    graph.add_node("risk_filter", risk_filter_node)
    graph.add_node("await_approval", human_approval_node)
    graph.add_node("generate_orders", order_node)
    graph.add_node("notify", notify_node)
    graph.add_node("log_result", log_result_node)

    graph.set_entry_point("check_data")

    # check_data → update_data（如果有过期）→ run_scan
    def route_after_check(state: WorkflowState) -> str:
        stale = state.get("data_status", {}).get("stale", [])
        if stale and "market/stock" in stale:
            return "update_data"
        return "run_scan"

    graph.add_conditional_edges("check_data", route_after_check, {
        "update_data": "update_data",
        "run_scan": "run_scan",
    })
    graph.add_edge("update_data", "run_scan")

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

    返回 {"success": bool, "signals": int, "orders": int, "log": list, "error": str}
    """
    wf = build_workflow()
    state: WorkflowState = {
        "date": date or datetime.now().strftime("%Y-%m-%d"),
        "asset_types": asset_types or ["stock"],
        "strategy_keys": strategy_keys,
        "log": [],
    }
    try:
        final = wf.invoke(state)
        return {
            "success": not final.get("error"),
            "signals": len(final.get("filtered_signals", [])),
            "orders": len(final.get("orders", [])),
            "log": final.get("log", []),
            "error": final.get("error"),
        }
    except Exception as e:
        logger.error(f"Workflow failed: {e}")
        return {"success": False, "error": str(e), "signals": 0, "orders": 0, "log": []}
