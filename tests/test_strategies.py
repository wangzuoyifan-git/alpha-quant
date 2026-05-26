"""
test_strategies.py — 三个内置策略单元测试
MA均线交叉 / MACD+RSI共振 / 量价突破
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.builtin.ma_cross import MACrossStrategy
from strategies.builtin.macd_rsi import MACDRSIStrategy
from strategies.builtin.vol_price_breakout import VolPriceBreakoutStrategy
from strategies.base import Signal


def make_cross_df(n=100, seed=42):
    """构造价格上行后回落的 DataFrame，适合测试均线交叉"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    base = np.linspace(10, 20, n // 2).tolist() + np.linspace(20, 15, n - n // 2).tolist()
    close = np.array(base) + rng.normal(0, 0.3, n).cumsum() * 0.5
    data = {
        "date": dates,
        "open": close + rng.uniform(-0.3, 0.1, n),
        "high": close + rng.uniform(0, 0.5, n),
        "low": close - rng.uniform(0, 0.5, n),
        "close": close,
        "volume": rng.integers(500000, 2000000, n).astype(float),
    }
    return pd.DataFrame(data)


def make_flat_df(n=100, seed=42):
    """构造平稳行情 DataFrame（无交叉）"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    close = 10.0 + rng.normal(0, 0.1, n).cumsum() * 0.1
    data = {
        "date": dates,
        "open": close + rng.uniform(-0.1, 0.05, n),
        "high": close + rng.uniform(0, 0.2, n),
        "low": close - rng.uniform(0, 0.2, n),
        "close": close,
        "volume": rng.integers(500000, 2000000, n).astype(float),
    }
    return pd.DataFrame(data)


class TestMACrossStrategy:

    def test_insufficient_data_returns_empty(self):
        df = make_cross_df(n=10)
        sigs = MACrossStrategy().generate_signals(df, "000001", "测试")
        assert sigs == []

    def test_golden_cross_produces_buy(self):
        """构造精确的金叉数据：倒数第二天 MA5<=MA20，最后一天 MA5>MA20"""
        n = 100
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        close = np.full(n, 10.0)
        close[95:99] = [9.1, 9.1, 9.1, 9.1]
        close[99] = 15.0
        volume = np.full(n, 500000.0)
        volume[-1] = 3000000.0
        df = pd.DataFrame({
            "date": dates,
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": volume,
        })
        sigs = MACrossStrategy().generate_signals(df, "000001", "测试")
        buy_sigs = [s for s in sigs if s.signal == "BUY"]
        assert len(buy_sigs) >= 1, f"close[-1]={close[-1]:.2f}, should trigger golden cross"
        assert 0 <= buy_sigs[0].score <= 100

    def test_reason_no_literal_format(self):
        """验证 reason 不含字面量 {:.1f}x"""
        rng = np.random.default_rng(99)
        n = 80
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        close = np.linspace(10, 15, n) + rng.normal(0, 0.05, n)
        volume = np.full(n, 500000.0)
        volume[-1] = 2000000.0
        df = pd.DataFrame({
            "date": dates,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": volume,
        })
        sigs = MACrossStrategy().generate_signals(df, "000001", "测试")
        buy_sigs = [s for s in sigs if s.signal == "BUY"]
        if buy_sigs:
            assert "{:.1f}x" not in buy_sigs[0].reason

    def test_flat_market_no_signal(self):
        df = make_flat_df(n=80)
        sigs = MACrossStrategy().generate_signals(df, "000001", "测试")
        assert len(sigs) == 0

    def test_score_in_range(self):
        rng = np.random.default_rng(99)
        n = 80
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        close = np.linspace(10, 15, n) + rng.normal(0, 0.05, n)
        volume = np.full(n, 500000.0)
        volume[-1] = 2000000.0
        df = pd.DataFrame({
            "date": dates,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": volume,
        })
        sigs = MACrossStrategy().generate_signals(df, "000001", "测试")
        for s in sigs:
            assert 0 <= s.score <= 100


class TestMACDRSIStrategy:

    def test_insufficient_data_returns_empty(self):
        df = make_cross_df(n=10)
        sigs = MACDRSIStrategy().generate_signals(df, "000001", "测试")
        assert sigs == []

    def test_flat_market_no_signal(self):
        df = make_flat_df(n=80)
        sigs = MACDRSIStrategy().generate_signals(df, "000001", "测试")
        assert len(sigs) == 0

    def test_score_in_range(self):
        rng = np.random.default_rng(42)
        n = 100
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        close = np.linspace(8, 15, n) + rng.normal(0, 0.2, n).cumsum() * 0.3
        df = pd.DataFrame({
            "date": dates,
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
        })
        sigs = MACDRSIStrategy().generate_signals(df, "000001", "测试")
        for s in sigs:
            assert 0 <= s.score <= 100

    def test_macd_cross_without_rsi_no_buy(self):
        """MACD金叉但RSI不满足超卖条件时不应产生BUY"""
        rng = np.random.default_rng(12)
        n = 80
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        close = np.linspace(10, 18, n) + rng.normal(0, 0.1, n)
        df = pd.DataFrame({
            "date": dates,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
        })
        strategy = MACDRSIStrategy({"rsi_oversold": 30, "rsi_overbought": 70})
        df_copy = df.copy()
        df_copy["dif"], df_copy["dea"], _ = strategy._calc_macd(df_copy["close"])
        df_copy["rsi"] = strategy._calc_rsi(df_copy["close"])
        macd_cross = (df_copy["dif"].iloc[-2] <= df_copy["dea"].iloc[-2] and
                      df_copy["dif"].iloc[-1] > df_copy["dea"].iloc[-1])
        rsi_recovering = (df_copy["rsi"].iloc[-2] < 30 and
                          df_copy["rsi"].iloc[-1] >= 25)
        if macd_cross and not rsi_recovering:
            sigs = strategy.generate_signals(df, "000001", "测试")
            buy_sigs = [s for s in sigs if s.signal == "BUY"]
            assert len(buy_sigs) == 0, "MACD金叉但RSI不满足超卖时不应产生BUY"


class TestVolPriceBreakoutStrategy:

    def test_insufficient_data_returns_empty(self):
        df = make_cross_df(n=10)
        sigs = VolPriceBreakoutStrategy({"lookback": 20}).generate_signals(df, "000001", "测试")
        assert sigs == []

    def test_breakout_produces_buy(self):
        rng = np.random.default_rng(88)
        n = 60
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        close = np.zeros(n)
        close[:40] = np.linspace(10.0, 11.0, 40)
        close[40:55] = np.linspace(11.0, 12.0, 15)
        close[55:] = np.linspace(16.0, 17.0, 5)
        close += rng.normal(0, 0.05, n)
        high = close + rng.uniform(0.1, 0.3, n)
        low = close - rng.uniform(0.1, 0.3, n)
        volume = np.full(n, 500000.0)
        volume[-1] = 5000000.0

        df = pd.DataFrame({
            "date": dates,
            "open": close - 0.1,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
        sigs = VolPriceBreakoutStrategy({"lookback": 20, "vol_ratio": 1.5}).generate_signals(df, "000001", "测试")
        buy_sigs = [s for s in sigs if s.signal == "BUY"]
        assert len(buy_sigs) >= 1, f"close[-1]={close[-1]:.2f}, should breakout above prev high"
        assert 0 <= buy_sigs[0].score <= 100

    def test_no_breakout_no_signal(self):
        df = make_flat_df(n=50)
        sigs = VolPriceBreakoutStrategy({"lookback": 20}).generate_signals(df, "000001", "测试")
        assert len(sigs) == 0

    def test_has_sell_signal(self):
        """验证跌破N日低点卖出信号：高位平台后暴跌"""
        n = 60
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        close = np.zeros(n)
        close[:50] = np.linspace(15, 16, 50)
        close[50:] = np.linspace(10, 8, 10)
        high = np.full(n, 17.0)
        low = np.full(n, 14.5)
        low[50:] = close[50:] - 0.1
        df = pd.DataFrame({
            "date": dates,
            "open": close + 0.05,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 500000.0),
        })
        sigs = VolPriceBreakoutStrategy({"lookback": 20, "breakout_pct": 0.005}).generate_signals(df, "000001", "测试")
        sell_sigs = [s for s in sigs if s.signal == "SELL"]
        assert len(sell_sigs) >= 1, f"close[-1]={close[-1]:.2f}, prev_low~14.5, should trigger SELL"

    def test_score_in_range(self):
        rng = np.random.default_rng(88)
        n = 50
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        close = np.linspace(10, 16, n) + rng.normal(0, 0.1, n)
        df = pd.DataFrame({
            "date": dates,
            "open": close - 0.1,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "volume": np.full(n, 500000.0),
        })
        df.at[n - 1, "volume"] = 3000000.0
        sigs = VolPriceBreakoutStrategy({"lookback": 20, "vol_ratio": 1.0}).generate_signals(df, "000001", "测试")
        for s in sigs:
            assert 0 <= s.score <= 100
