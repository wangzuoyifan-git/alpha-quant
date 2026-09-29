"""
Agent Tool Layer — 薄适配层，将下层能力封装为结构化 Tool 供 Agent 调用。
所有函数返回 dict 格式（AC1）。
"""
from agent_tools.data_tools import (
    check_data_freshness,
    check_reduce_window,
    get_active_event_windows,
    get_domain_stats,
    query_kline,
    update_domain,
    validate_data,
)
from agent_tools.smart_money_tools import (
    get_capital_consensus,
    get_northbound_behavior,
    get_recent_lhb_smart_money,
    get_seat_identity,
    get_seat_profile,
    get_stock_smart_money,
    rank_smart_money_seats,
)
from agent_tools.strategy_tools import (
    compare_strategies,
    list_strategies,
    notify_signals,
    run_backtest,
    run_scan,
)
from agent_tools.trade_tools import (
    cancel_order,
    confirm_order,
    filter_signals_by_risk,
    generate_orders,
    get_pending_orders,
    record_execution,
)

__all__ = [
    "cancel_order",
    "check_data_freshness",
    "check_reduce_window",
    "compare_strategies",
    "confirm_order",
    "filter_signals_by_risk",
    "generate_orders",
    "get_active_event_windows",
    "get_capital_consensus",
    "get_domain_stats",
    "get_northbound_behavior",
    "get_pending_orders",
    "get_recent_lhb_smart_money",
    "get_seat_identity",
    "get_seat_profile",
    "get_stock_smart_money",
    "list_strategies",
    "notify_signals",
    "query_kline",
    "rank_smart_money_seats",
    "record_execution",
    "run_backtest",
    "run_scan",
    "update_domain",
    "validate_data",
]
