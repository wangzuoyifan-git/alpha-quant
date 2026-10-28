"""P0-3/P1-2：组合层资金分配、再平衡、熔断与归因测试。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backtest.portfolio as pf
from backtest.portfolio import (
    PortfolioConfig,
    _apply_drawdown_guard,
    _simulate_portfolio,
    run_portfolio_backtest,
)


def _rets(periods=120, seed=7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=periods)
    return pd.DataFrame({
        "core": rng.normal(0.001, 0.01, periods),
        "etf": rng.normal(0.0015, 0.015, periods),
        "b1": rng.normal(0.0005, 0.008, periods),
        "b2": rng.normal(0.0004, 0.007, periods),
    }, index=dates)


def test_daily_mode_equals_weighted_returns():
    rets = _rets()
    cfg = PortfolioConfig(weights={"core": 0.4, "etf": 0.4, "b1": 0.1, "b2": 0.1},
                          rebalance="daily")
    eq = _simulate_portfolio(rets, cfg)
    expected = (1 + sum(rets[k] * cfg.weights[k] for k in rets.columns)
                ).cumprod() * cfg.initial_capital
    pd.testing.assert_series_equal(eq, expected, check_names=False)


def test_weights_over_one_rejected():
    cfg = PortfolioConfig(weights={"core": 0.8, "etf": 0.8})
    with pytest.raises(ValueError):
        _simulate_portfolio(_rets(), cfg)


def test_monthly_rebalance_converges_weights():
    # core 暴涨后，月度再平衡应把权重拉回目标
    dates = pd.bdate_range("2024-01-01", periods=60)
    rets = pd.DataFrame({"core": 0.02, "etf": 0.0}, index=dates)
    rets.iloc[10:20, 0] = 0.05
    cfg = PortfolioConfig(weights={"core": 0.3, "etf": 0.7},
                          rebalance="monthly", tolerance=0.0,
                          rebalance_cost_bps=0.0)
    eq = _simulate_portfolio(rets, cfg)
    assert eq.iloc[-1] > cfg.initial_capital
    assert not eq.isna().any()


def test_drawdown_guard_stops_further_losses():
    dates = pd.bdate_range("2024-01-01", periods=60)
    eq = pd.Series(1_000_000 * (1 - np.linspace(0, 0.5, 60)), index=dates)
    guarded = _apply_drawdown_guard(eq, max_drawdown=0.10, cooldown_days=10)
    # 熔断后净值不再继续按原曲线下跌
    assert guarded.iloc[-1] > eq.iloc[-1]
    assert (guarded / guarded.cummax() - 1).min() > -0.35


def test_portfolio_backtest_composes_sleeves(monkeypatch):
    rets = _rets()
    eqs = {k: (1 + rets[k]).cumprod() * 1_000_000 for k in rets.columns}

    def fake_sleeves(start, end, cfg, **kwargs):
        return {
            k: {"equity": eqs[k], "metrics": pf._metrics(eqs[k], k, 1_000_000)}
            for k in eqs
        }

    monkeypatch.setattr(pf, "_run_sleeves", fake_sleeves)
    cfg = PortfolioConfig(weights={"core": 0.4, "etf": 0.4, "b1": 0.1, "b2": 0.1},
                          rebalance="monthly")
    res = run_portfolio_backtest("2024-01-01", "2024-12-31", cfg)
    assert set(res.sleeve_metrics) == {"core", "etf", "b1", "b2"}
    assert set(res.contributions) == {"core", "etf", "b1", "b2"}
    assert res.equity.index.equals(rets.index)
    assert "depth" in res.drawdown_attribution
    assert res.to_dict()["rebalance"] == "monthly"
    assert "组合回测" in res.summary()
