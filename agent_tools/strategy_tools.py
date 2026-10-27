"""
agent_tools/strategy_tools.py — 策略相关 Tools
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from data.fetcher import DataFetcher
from engine.scanner import ScanEngine
from backtest.engine import Backtester
from strategies.builtin import BUILTIN_STRATEGIES

logger = logging.getLogger(__name__)


def run_scan(date: str = None, strategies: list = None,
             industry_filter: list = None, industry_level: str = "L1",
             etf_whitelist: list = None) -> dict:
    """
    执行全市场扫描，返回结构化结果。

    :param date: 扫描日期
    :param strategies: 策略 key 列表，默认全部内置策略
    :param industry_filter: L2 行业池约束（申万行业代码列表），透传到引擎层做约束前置过滤。
                            None 时为全市场扫描（向后兼容）。
    :param industry_level: 行业代码层级 L1/L2/L3。
    :param etf_whitelist: L2 行业ETF白名单（代码列表），非None时ETF扫描仅限白名单。

    返回 {"success": bool, "signals": list, "count": int, "elapsed": float}
    """
    try:
        from config.settings import SCAN, DATA_SOURCE
        fetcher = DataFetcher(DATA_SOURCE)
        engine = ScanEngine(fetcher, SCAN)

        strat_keys = strategies or list(BUILTIN_STRATEGIES.keys())
        for key in strat_keys:
            if key in BUILTIN_STRATEGIES:
                engine.register_strategy(key, BUILTIN_STRATEGIES[key]())

        import time
        t0 = time.time()
        signals = engine.scan(date=date, industry_filter=industry_filter,
                              industry_level=industry_level,
                              etf_whitelist=etf_whitelist)
        elapsed = time.time() - t0

        return {
            "success": True,
            "signals": [
                {"code": s.code, "name": s.name, "signal": s.signal,
                 "price": s.price, "score": s.score, "reason": s.reason,
                 "position_type": s.position_type, "time_horizon": s.time_horizon,
                 "chan": (s.extra or {}).get("chan")}
                for s in signals
            ],
            "count": len(signals),
            "elapsed": round(elapsed, 1),
        }
    except Exception as e:
        logger.error(f"run_scan 失败: {e}")
        return {"success": False, "error": str(e)}


def run_backtest(strategy_key: str, start: str = "2024-01-01",
                  end: str = "2024-12-31", codes: list = None) -> dict:
    """
    运行策略回测，返回结构化结果。
    """
    try:
        from config.settings import BACKTEST, DATA_SOURCE
        fetcher = DataFetcher(DATA_SOURCE)
        bt = Backtester(fetcher, BACKTEST)

        if strategy_key not in BUILTIN_STRATEGIES:
            return {"success": False, "error": f"未知策略: {strategy_key}"}

        strategy = BUILTIN_STRATEGIES[strategy_key]()
        result = bt.run(strategy, codes or [], start, end)

        return {
            "success": True,
            "strategy": strategy_key,
            "total_return": round(result.total_return * 100, 2),
            "annual_return": round(result.annual_return * 100, 2),
            "max_drawdown": round(result.max_drawdown * 100, 2),
            "sharpe_ratio": round(result.sharpe_ratio, 3),
            "win_rate": round(result.win_rate * 100, 1),
            "profit_factor": round(result.profit_factor, 2),
            "total_trades": result.total_trades,
            "coverage_rate": round(result.coverage_rate * 100, 1),
            "reliability": result.reliability,
        }
    except Exception as e:
        logger.error(f"run_backtest 失败: {e}")
        return {"success": False, "error": str(e)}


def list_strategies() -> dict:
    """返回可用策略列表及元数据（符合 agent.md 3.3 契约）"""
    strategies = []
    for key, cls in BUILTIN_STRATEGIES.items():
        try:
            requires_bars = cls().requires_bars()
        except Exception:
            requires_bars = None
        strategies.append({
            "key": key,
            "name": cls.name,
            "description": cls.description,
            "version": getattr(cls, "version", "1.0.0"),
            "requires_bars": requires_bars,
        })
    return {"success": True, "strategies": strategies}


def notify_signals(signals: list[dict], title: str = None) -> dict:
    """I1：推送信号到所有配置渠道"""
    from config.settings import NOTIFIER
    from notifier.push import Notifier
    from strategies.base import Signal

    try:
        sig_objs = [Signal(**s) for s in signals]
        notifier = Notifier(NOTIFIER)
        notifier.send_signals(sig_objs, title=title or "Alpha量化选股信号")
        return {"success": True, "count": len(signals)}
    except Exception as e:
        logger.error(f"notify_signals 失败: {e}")
        return {"success": False, "error": str(e)}


def compare_strategies(strategy_keys: list[str], start: str, end: str) -> dict:
    """
    并行回测多个策略，返回对比结果用于 Strategy Agent 选择最优组合。
    """
    results = {}
    for key in strategy_keys:
        r = run_backtest(key, start=start, end=end)
        if r["success"]:
            results[key] = r
    return {"success": True, "results": results, "compared": len(results)}
