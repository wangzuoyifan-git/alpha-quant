"""P1-1：基于研究面板的快速回测入口。

复用 ``research.panel`` / ``research.signals`` / ``research.simulator``：
- 一次读入日期 × 代码面板，信号向量化，避免逐日逐股重算；
- 成交规则与研究框架一致（次日开盘、涨跌停、T+1、整手、印花税）；
- 结果转换为生产 ``BacktestResult``，保持 CLI / 持久化兼容。

注意：本入口用于“快速验证/参数筛选”，不是逐日事件驱动引擎。
需要逐笔事件语义时仍使用 ``backtest/engine.Backtester``。
"""
from __future__ import annotations

import pandas as pd

from backtest.engine import BacktestResult, Trade
from config.settings import EXECUTION
from research.combo import core_signal
from research.panel import Panel, eligibility, load_panel
from research.signals import (
    TechContext,
    etf_momentum,
    ma_cross,
    macd_rsi,
    vol_price_breakout,
)
from research.simulator import BTConfig, run_rotation, run_signals
from strategies.builtin import BUILTIN_STRATEGIES

STOCK_FAST_STRATEGIES = {"quality_trend", "ma_cross", "macd_rsi", "vol_price_breakout"}
ETF_FAST_STRATEGIES = {"etf_momentum"}
_FAST_STRATEGIES = STOCK_FAST_STRATEGIES | ETF_FAST_STRATEGIES


def supported(strategy_key: str) -> bool:
    return strategy_key in _FAST_STRATEGIES


def _filter_panel(panel: Panel, codes: list[str] | None) -> Panel:
    if not codes:
        return panel
    keep = [c for c in codes if c in set(panel.codes)]
    if not keep:
        raise ValueError("指定的代码不在本地研究面板中")
    return Panel(dates=panel.dates, codes=keep,
                 data={k: v[keep] for k, v in panel.data.items()})


def _cost_config(config: dict | None) -> dict:
    cfg = dict(EXECUTION)
    cfg.update(config or {})
    return cfg


def _build_signals(strategy_key: str, panel: Panel,
                   params_override: dict | None = None):
    cls = BUILTIN_STRATEGIES[strategy_key]
    params = cls(params_override or {}).params
    ctx = TechContext(panel)
    if strategy_key == "quality_trend":
        return core_signal(panel, params)
    if strategy_key == "ma_cross":
        return ma_cross(ctx, {
            "fast": params.get("fast_period", 5),
            "slow": params.get("slow_period", 20),
            "adx_min": params.get("adx_min", 20),
            "vol_ratio": params.get("volume_ratio", 1.5),
        })
    if strategy_key == "macd_rsi":
        return macd_rsi(ctx, {
            "fast": params.get("fast", 12),
            "slow": params.get("slow", 26),
            "signal": params.get("signal", 9),
            "rsi_period": params.get("rsi_period", 14),
            "rsi_oversold": params.get("rsi_oversold", 40),
        })
    if strategy_key == "vol_price_breakout":
        return vol_price_breakout(ctx, {
            "lookback": params.get("lookback", 20),
            "vol_ratio": params.get("vol_ratio", 2.0),
            "breakout_atr": params.get("breakout_atr", 0.2),
            "buildup_days": params.get("buildup_days", 5),
            "buildup_ratio": params.get("buildup_ratio", 1.0),
        })
    if strategy_key == "etf_momentum":
        return etf_momentum(ctx, {
            "lookback": params.get("lookback", 20),
            "ma_trend": params.get("ma_trend", 60),
            "abs_momentum": params.get("abs_momentum", True),
        })
    raise ValueError(f"策略 {strategy_key} 暂不支持 --fast")


def _bt_config(cfg: dict, cfg_extra: dict | None = None) -> BTConfig:
    kwargs = {
        "initial_capital": float(cfg.get("initial_capital", 1_000_000)),
        "max_positions": int(cfg.get("max_positions", 10)),
        "position_size": float(cfg.get("position_size", 0.1)),
        "commission": float(cfg.get("commission_rate", 0.0003)),
        "slippage": float(cfg.get("slippage", 0.001)),
        "stamp_duty": float(cfg.get("stamp_duty", 0.0005)),
    }
    kwargs.update(cfg_extra or {})
    return BTConfig(**kwargs)


def _to_result(br, strategy_name: str, initial_capital: float,
               coverage_rate: float) -> BacktestResult:
    trades = [
        Trade(
            code=t.code, name=t.code,
            entry_date=str(pd.Timestamp(t.entry_date).date()),
            entry_price=float(t.entry_price),
            exit_date=str(pd.Timestamp(t.exit_date).date()) if t.exit_date is not None else None,
            exit_price=float(t.exit_price) if t.exit_price is not None else None,
            shares=int(t.shares), pnl=float(t.pnl), pnl_pct=float(t.pnl_pct),
            reason_out=t.reason_out,
        )
        for t in br.trades
    ]
    return BacktestResult(
        strategy_name=strategy_name,
        start_date=br.start, end_date=br.end,
        initial_capital=initial_capital,
        final_capital=initial_capital * (1 + br.total_return),
        total_return=br.total_return,
        annual_return=br.annual_return,
        max_drawdown=br.max_drawdown,
        sharpe_ratio=br.sharpe,
        win_rate=br.win_rate,
        profit_factor=br.profit_factor,
        total_trades=br.n_trades,
        trades=trades,
        equity_curve=br.equity,
        coverage_rate=coverage_rate,
        reliability="limited" if coverage_rate < 0.80 else "normal",
        benchmark_return=br.benchmark_return,
        alpha=br.alpha,
        beta=br.beta,
    )


def run_fast(strategy_key: str, start: str, end: str,
             codes: list[str] | None = None,
             config: dict | None = None,
             params: dict | None = None) -> BacktestResult:
    """运行快速面板回测，返回与生产引擎一致的 ``BacktestResult``。"""
    if not supported(strategy_key):
        raise ValueError(f"策略 {strategy_key} 暂不支持 --fast")
    cfg = _cost_config(config)
    asset_type = "etf" if strategy_key in ETF_FAST_STRATEGIES else "stock"

    panel = load_panel(asset_type=asset_type)
    requested = len(codes) if codes else len(panel.codes)
    panel = _filter_panel(panel, codes)
    elig = eligibility(panel, min_amount=5_000_000 if asset_type == "etf"
                       else 30_000_000)
    sig = _build_signals(strategy_key, panel, params)

    if strategy_key in ETF_FAST_STRATEGIES:
        bc = _bt_config(cfg, {
            "max_positions": 5, "position_size": 0.2, "regime": "none",
        })
        br = run_rotation(panel, sig.score.where(sig.buy), elig,
                          top_n=5, rebalance_days=20, cfg=bc,
                          start=start, end=end)
    else:
        # 与研究基线保持一致：个股技术策略使用沪深300趋势闸门
        bc = _bt_config(cfg, {"regime": "trend"})
        br = run_signals(panel, sig, elig, bc, start=start, end=end)

    coverage = len(panel.codes) / max(requested, 1)
    return _to_result(br, strategy_key, float(cfg.get("initial_capital", 1_000_000)),
                      coverage)
