"""P0-3/P1-1：快速引擎与研究信号的一致性测试（不联网）。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.fast import supported
from research.combo import core_signal
from research.panel import Panel
from strategies.builtin.quality_trend import QualityTrendStrategy


def _panel_and_df(n=200):
    dates = pd.bdate_range("2024-01-02", periods=n)
    i = np.arange(n)
    close = np.empty(n)
    for k in range(n):
        if k < 140:
            close[k] = 10 * (1.0015 ** k)
        elif k < 144:
            close[k] = close[k - 1] * 0.99
        else:
            close[k] = close[k - 1] * 1.005
    open_ = close * (1 + 0.001 * np.cos(i))
    high = np.maximum(open_, close) * 1.02
    low = np.minimum(open_, close) * 0.98
    amount = np.full(n, 2e8)
    volume = np.full(n, 1e6)
    data = {
        "open": pd.DataFrame({"600519": open_}, index=dates),
        "high": pd.DataFrame({"600519": high}, index=dates),
        "low": pd.DataFrame({"600519": low}, index=dates),
        "close": pd.DataFrame({"600519": close}, index=dates),
        "volume": pd.DataFrame({"600519": volume}, index=dates),
        "amount": pd.DataFrame({"600519": amount}, index=dates),
        "is_st": pd.DataFrame({"600519": np.zeros(n)}, index=dates),
    }
    panel = Panel(dates=pd.DatetimeIndex(dates), codes=["600519"], data=data)
    df = pd.DataFrame({
        "date": dates, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume, "amount": amount,
    })
    return panel, df


def test_fast_strategy_support():
    assert supported("quality_trend")
    assert supported("etf_momentum")
    assert not supported("chan_theory")


def test_quality_trend_buy_signals_match_research():
    panel, df = _panel_and_df()
    sig = core_signal(panel)
    strat = QualityTrendStrategy()

    prod_buys = res_buys = 0
    for i in range(90, len(df)):
        signals = strat.generate_signals(df.iloc[:i + 1].copy(), "600519", "x")
        prod_buy = any(s.signal == "BUY" for s in signals)
        prod_sell = any(s.signal == "SELL" for s in signals)
        res_buy = bool(sig.buy.iloc[i, 0])
        res_sell = bool(sig.sell.iloc[i, 0])
        assert prod_buy == res_buy, f"buy mismatch at {df.iloc[i]['date']}"
        if prod_sell:
            # 生产版只在下穿当日发一次 SELL；研究版为“收盘低于 MA20”
            assert res_sell
        prod_buys += int(prod_buy)
        res_buys += int(res_buy)

    assert prod_buys == res_buys
    assert prod_buys > 0  # 合成序列应至少产生一次回踩买入


def test_research_simulator_skips_zero_volume_execution(monkeypatch):
    import research.simulator as sim
    from research.signals import SignalSet

    dates = pd.bdate_range("2024-01-02", periods=5)
    close = np.array([10.0, 10.1, 10.2, 10.3, 10.4])
    volume = np.array([1e6, 1e6, 0.0, 1e6, 1e6])  # day2 停牌残留
    data = {
        "open": pd.DataFrame({"000001": close}, index=dates),
        "high": pd.DataFrame({"000001": close * 1.01}, index=dates),
        "low": pd.DataFrame({"000001": close * 0.99}, index=dates),
        "close": pd.DataFrame({"000001": close}, index=dates),
        "volume": pd.DataFrame({"000001": volume}, index=dates),
        "amount": pd.DataFrame({"000001": np.where(volume > 0, 2e8, 0.0)},
                               index=dates),
        "is_st": pd.DataFrame({"000001": np.zeros(len(dates))}, index=dates),
    }
    panel = Panel(dates=pd.DatetimeIndex(dates), codes=["000001"], data=data)
    buy = pd.DataFrame(False, index=dates, columns=["000001"])
    buy.iloc[1, 0] = True  # day1 收盘信号 → day2 开盘执行，但 day2 无量
    sig = SignalSet("t", buy, pd.DataFrame(False, index=dates,
                                           columns=["000001"]),
                    pd.DataFrame(1.0, index=dates, columns=["000001"]))
    monkeypatch.setattr(sim, "_benchmark", lambda panel: pd.Series(dtype=float))
    from research.panel import eligibility
    res = sim.run_signals(panel, sig, eligibility(panel, min_amount=0),
                          sim.BTConfig(regime="none", commission=0,
                                       slippage=0, stamp_duty=0))
    assert res.n_trades == 0
