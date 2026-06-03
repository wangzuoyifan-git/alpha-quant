"""
agents/trade_agent.py — Risk/Trade Agent

Phase 1/2：风控过滤 + 指令单生成 + 人工确认
Phase 3：在风控约束内自主执行
"""
import logging

logger = logging.getLogger(__name__)


def process_signals(signals: list[dict], current_equity: float = None,
                    vix: float = None) -> dict:
    """
    风控 Agent 处理信号：逐条检查 RC1~RC8，生成含风控标签的指令单。

    返回 {"orders": list, "blocked": list, "risk_summary": dict}
    """
    from agent_tools import filter_signals_by_risk, generate_orders

    filtered = filter_signals_by_risk(signals, current_equity=current_equity, vix=vix)
    passed = filtered.get("passed", [])
    blocked_count = filtered.get("blocked_count", 0)

    orders = []
    if passed:
        result = generate_orders(passed, current_capital=current_equity)
        orders = result.get("orders", [])

    return {
        "orders": orders,
        "blocked_count": blocked_count,
        "risk_summary": {
            "total_signals": len(signals),
            "passed": len(passed),
            "blocked": blocked_count,
            "orders_generated": len(orders),
        },
    }


def confirm_trade(order_id: str) -> dict:
    """人工确认交易（Phase 2 可被程序化调用替代人工）"""
    from agent_tools import confirm_order
    return confirm_order(order_id)


def cancel_trade(order_id: str) -> dict:
    """取消交易"""
    from agent_tools.trade_tools import cancel_order as cancel
    return cancel(order_id)
