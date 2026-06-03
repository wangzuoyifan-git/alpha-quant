"""
Agent Tool Layer — 薄适配层，将下层能力封装为结构化 Tool 供 Agent 调用。
所有函数返回 dict 格式（AC1）。
"""
from agent_tools.data_tools import check_data_freshness, update_domain, get_domain_stats
from agent_tools.strategy_tools import run_scan, run_backtest, list_strategies
from agent_tools.trade_tools import filter_signals_by_risk, generate_orders, confirm_order, record_execution

__all__ = [
    "check_data_freshness", "update_domain", "get_domain_stats",
    "run_scan", "run_backtest", "list_strategies",
    "filter_signals_by_risk", "generate_orders", "confirm_order", "record_execution",
]
