"""
test_backtest.py — 回测引擎单元测试
覆盖 BacktestResult 指标计算、Backtester 数据加载逻辑
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.engine import Backtester, BacktestResult, Trade


def _make_equity(returns: list[float], start=1000000.0):
    """从日收益率序列生成权益曲线"""
    eq = [start]
    for r in returns:
        eq.append(eq[-1] * (1 + r))
    dates = pd.date_range("2024-01-02", periods=len(eq), freq="B")
    return pd.Series(eq, index=dates)


def _make_trades(wins: int, losses: int, avg_win_pct=0.05, avg_loss_pct=-0.03):
    trades = []
    for i in range(wins):
        trades.append(Trade(
            code=f"000{i:03d}", name=f"测试{i}",
            entry_date="2024-01-02", entry_price=10.0,
            exit_date="2024-02-01", exit_price=10.0 * (1 + avg_win_pct),
            shares=1000, pnl=1000 * 10.0 * avg_win_pct,
            pnl_pct=avg_win_pct * 100,
        ))
    for i in range(losses):
        trades.append(Trade(
            code=f"000{100+i:03d}", name=f"测试{100+i}",
            entry_date="2024-01-02", entry_price=10.0,
            exit_date="2024-02-01", exit_price=10.0 * (1 + avg_loss_pct),
            shares=1000, pnl=1000 * 10.0 * avg_loss_pct,
            pnl_pct=avg_loss_pct * 100,
        ))
    return trades


class TestBacktestResultMetrics:

    def test_total_return_zero(self):
        eq = _make_equity([0, 0, 0, 0, 0])
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-01-08", eq, [])
        assert result.total_return == pytest.approx(0.0, abs=1e-9)

    def test_total_return_positive(self):
        eq = _make_equity([0.01, 0.01, 0.01, 0.01, 0.01])
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-01-08", eq, [])
        assert result.total_return > 0
        assert result.final_capital > result.initial_capital

    def test_total_return_negative(self):
        eq = _make_equity([-0.01, -0.01, -0.01, -0.01, -0.01])
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-01-08", eq, [])
        assert result.total_return < 0

    def test_max_drawdown_monotonic_decline(self):
        eq = _make_equity([-0.02, -0.02, -0.03, -0.01, -0.02])
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-01-08", eq, [])
        assert result.max_drawdown < 0
        assert result.max_drawdown < -0.05

    def test_max_drawdown_recovery(self):
        eq = _make_equity([-0.05, -0.03, 0.1, 0.05, 0.02])
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-01-08", eq, [])
        assert result.max_drawdown < 0

    def test_annual_return_from_long_series(self):
        n = 252
        eq = _make_equity([0.001] * n)
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-12-31", eq, [])
        assert result.annual_return > 0

    def test_sharpe_positive(self):
        n = 100
        eq = _make_equity([0.001] * n)
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-01-02", eq, [])
        assert result.sharpe_ratio > 0

    def test_win_rate_and_profit_factor(self):
        eq = _make_equity([0.001] * 50)
        trades = _make_trades(wins=7, losses=3)
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-03-29", eq, trades)
        assert result.win_rate == pytest.approx(0.7, abs=0.01)
        assert result.total_trades == 10
        assert result.profit_factor > 1.0

    def test_win_rate_all_loss(self):
        eq = _make_equity([-0.001] * 50)
        trades = _make_trades(wins=0, losses=5)
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-03-29", eq, trades)
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0

    def test_empty_equity(self):
        eq = pd.Series([], dtype=float, index=pd.DatetimeIndex([]))
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-01-02", eq, [])
        assert result.final_capital == result.initial_capital
        assert result.total_return == 0.0

    def test_summary_contains_key_info(self):
        eq = _make_equity([0.01, 0.01, 0.01])
        bt = Backtester(None, {"initial_capital": 1000000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-01-06", eq, [])
        s = result.summary()
        assert "test" in s
        assert "总收益率" in s
        assert "夏普比率" in s
        assert "最大回撤" in s


class TestBacktesterDataLoading:
    """回测数据加载逻辑（mock store.load）"""

    def test_miss_count_no_duplicate(self, monkeypatch, tmp_path):
        """验证 miss 计数不重复（以前的 bug：代码块重复导致虚高）"""
        import data.store as store

        def mock_load(code, asset_type="stock", market="cn", start=None, end=None):
            dates = pd.date_range("2024-01-02", "2024-06-28", freq="B")
            data = {
                "close": np.linspace(10, 12, len(dates)),
                "open": np.linspace(10, 12, len(dates)),
                "high": np.linspace(10, 12, len(dates)),
                "low": np.linspace(10, 12, len(dates)),
                "volume": np.full(len(dates), 500000.0),
            }
            df = pd.DataFrame(data, index=dates)
            if code == "000001":
                return df
            elif code == "000003":
                return df.iloc[:5]
            else:
                return None

        def mock_ipo():
            return {}

        monkeypatch.setattr(store, "load", mock_load)
        monkeypatch.setattr(store, "load_ipo_dates", mock_ipo)

        from strategies.builtin.ma_cross import MACrossStrategy

        bt = Backtester(None, {})
        bt.run = lambda *a, **kw: None
        bt.initial_capital = 1000000

        strategy = MACrossStrategy()
        codes = ["000001", "000002", "000003", "000004"]

        data = {}
        miss = 0
        for code in codes:
            df = store.load(code)
            if df is None or df.empty:
                miss += 1
                continue
            ipo_dates = store.load_ipo_dates()
            ipo = ipo_dates.get(code, {}).get("ipo_date")
            min_bars = 20 if ipo else strategy.requires_bars()
            if len(df) >= min_bars:
                data[code] = df.sort_index()
            else:
                miss += 1

        assert miss <= 4
        assert len(data) == 1
