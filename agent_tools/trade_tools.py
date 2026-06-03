"""
agent_tools/trade_tools.py — 交易/风控相关 Tools
"""
import logging

from config.settings import BACKTEST, RISK_CONTROL
from trader.assistant import TradeAssistant
from strategies.base import Signal

logger = logging.getLogger(__name__)

ta = TradeAssistant(BACKTEST, RISK_CONTROL)


def filter_signals_by_risk(signals: list, current_equity: float = None,
                            vix: float = None, index_drop: float = None) -> dict:
    """
    RC1~RC8 完整风控过滤。

    输入 signals: [{"code", "name", "signal", "price", "score", "reason"}] 格式的 dict 列表
    返回 {"success": bool, "passed": list, "blocked": list, "filters_applied": list}
    """
    try:
        sig_objs = [Signal(**s) for s in signals]
        filtered = ta.filter_signals_by_risk(
            sig_objs,
            current_equity=current_equity,
            vix=vix,
            index_drop=index_drop,
        )
        return {
            "success": True,
            "passed": [{"code": s.code, "signal": s.signal, "score": s.score,
                         "reason": s.reason} for s in filtered],
            "blocked_count": len(signals) - len(filtered),
        }
    except Exception as e:
        logger.error(f"filter_signals_by_risk 失败: {e}")
        return {"success": False, "error": str(e)}


def generate_orders(signals: list, current_capital: float = None) -> dict:
    """
    根据信号生成含止损止盈的指令单。
    """
    try:
        sig_objs = [Signal(**s) for s in signals]
        orders = ta.generate_orders(sig_objs, current_capital=current_capital)
        return {
            "success": True,
            "orders": [o.to_dict() for o in orders],
            "count": len(orders),
        }
    except Exception as e:
        logger.error(f"generate_orders 失败: {e}")
        return {"success": False, "error": str(e)}


def confirm_order(order_id: str) -> dict:
    """确认指令单"""
    try:
        ok = ta.confirm_order(order_id)
        return {"success": ok, "order_id": order_id}
    except Exception as e:
        return {"success": False, "order_id": order_id, "error": str(e)}


def record_execution(order_id: str, actual_price: float,
                      actual_quantity: int) -> dict:
    """F1/F2：录入实际成交"""
    try:
        ok = ta.record_execution(order_id, actual_price, actual_quantity)
        return {"success": ok, "order_id": order_id}
    except Exception as e:
        return {"success": False, "order_id": order_id, "error": str(e)}
