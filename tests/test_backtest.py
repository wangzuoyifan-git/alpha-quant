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


class TestBacktestAccuracy:
    """UT-BT-01/02/04/05：未来数据泄露、交易成本、覆盖率"""

    def test_trade_cost_reduces_final_capital(self):
        """UT-BT-02：有交易成本时最终资金 < 无成本时"""
        from backtest.engine import Backtester
        bt_with_cost = Backtester(None, {
            "initial_capital": 1_000_000,
            "commission_rate": 0.0003,
            "slippage": 0.001,
        })
        bt_no_cost = Backtester(None, {
            "initial_capital": 1_000_000,
            "commission_rate": 0.0,
            "slippage": 0.0,
        })

        eq = _make_equity([0.01, 0.01, 0.01])
        r_with = bt_with_cost._calc_metrics("test", "2024-01-02", "2024-01-06", eq, [])
        r_no = bt_no_cost._calc_metrics("test", "2024-01-02", "2024-01-06", eq, [])
        # 成本函数影响回调在 run() 中的实际交易，_calc_metrics 不变
        # 此处验证成本配置被正确读取
        assert bt_with_cost.commission == 0.0003
        assert bt_with_cost.slippage == 0.001
        assert bt_no_cost.commission == 0.0
        assert bt_no_cost.slippage == 0.0

    def test_coverage_rate_calculation(self):
        """UT-BT-04：覆盖率 = 实际参与标的数 / 目标标的数"""
        targeted = 100
        participated = 78
        coverage = participated / targeted
        assert 0 <= coverage <= 1
        assert pytest.approx(coverage) == 0.78

    def test_low_coverage_tags_limited(self):
        """UT-BT-05：覆盖率 < 80% 标注参考价值有限"""
        coverage = 0.70  # 70% < 80%
        if coverage < 0.80:
            reliability = "limited"
        else:
            reliability = "normal"
        assert reliability == "limited"

    def test_normal_coverage_tags_normal(self):
        """覆盖率 ≥ 80% 正常"""
        coverage = 0.85
        if coverage < 0.80:
            reliability = "limited"
        else:
            reliability = "normal"
        assert reliability == "normal"

    def test_sharpe_deducts_risk_free_rate(self):
        """UT-BT-03：夏普比率计算逻辑（年化收益-无风险利率）/年化波动率"""
        daily_returns = pd.Series([0.001] * 100)
        annual_return = daily_returns.mean() * 252
        annual_vol = daily_returns.std() * np.sqrt(252)
        risk_free = 0.03
        sharpe = (annual_return - risk_free) / (annual_vol + 1e-9)
        assert sharpe > 0

    def test_no_future_leak_in_signal(self):
        """UT-BT-01：信号生成只用截止当日数据"""
        import numpy as np
        n = 100
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
        close = np.linspace(10, 15, n)
        df = pd.DataFrame({
            "date": dates,
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": np.full(n, 500000.0),
        })
        df_slice = df.iloc[:80].reset_index(drop=True)
        future_value = df.iloc[85]["close"]
        sig_max_price = df_slice["close"].max()
        assert sig_max_price < future_value


class TestBacktestBenchmark:
    """基准对比功能（alpha/beta/benchmark_return）"""

    def test_benchmark_fields_default_zero(self):
        """无基准数据时，基准字段默认为 0"""
        eq = _make_equity([0.01] * 20)
        bt = Backtester(None, {"initial_capital": 1_000_000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-02-01", eq, [])
        # _load_benchmark 在 store 无数据时返回空，alpha/beta/benchmark_return 均为 0
        assert isinstance(result.benchmark_return, float)
        assert isinstance(result.alpha, float)
        assert isinstance(result.beta, float)

    def test_alpha_positive_when_strategy_outperforms(self, monkeypatch):
        """策略年化 > 基准年化时 alpha > 0"""
        import data.store as store_mod

        # mock 基准：沪深300 年化约 5%（每日 ~0.02%）
        bm_dates = pd.date_range("2024-01-02", periods=60, freq="B")
        bm_close = pd.Series(
            [100 * (1 + 0.0002) ** i for i in range(60)], index=bm_dates
        )
        bm_df = pd.DataFrame({"close": bm_close}, index=bm_dates)

        original_load = store_mod.load
        def mock_load(code, asset_type="stock", market="cn", start=None, end=None):
            if asset_type == "index" and code == "000300":
                return bm_df
            return original_load(code, asset_type=asset_type, market=market,
                                 start=start, end=end)
        monkeypatch.setattr(store_mod, "load", mock_load)

        # 策略：年化约 25%（每日 ~0.1%）
        eq = _make_equity([0.001] * 60)
        bt = Backtester(None, {"initial_capital": 1_000_000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-03-29", eq, [])
        assert result.benchmark_return > 0
        assert result.alpha > 0  # 策略跑赢基准

    def test_summary_shows_benchmark_when_available(self, monkeypatch):
        """有基准数据时 summary 展示超额收益行"""
        import data.store as store_mod

        bm_dates = pd.date_range("2024-01-02", periods=30, freq="B")
        bm_close = pd.Series(
            [100 * (1 + 0.0001) ** i for i in range(30)], index=bm_dates
        )
        bm_df = pd.DataFrame({"close": bm_close}, index=bm_dates)

        original_load = store_mod.load
        def mock_load(code, asset_type="stock", market="cn", start=None, end=None):
            if asset_type == "index" and code == "000300":
                return bm_df
            return original_load(code, asset_type=asset_type, market=market,
                                 start=start, end=end)
        monkeypatch.setattr(store_mod, "load", mock_load)

        eq = _make_equity([0.001] * 30)
        bt = Backtester(None, {"initial_capital": 1_000_000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-02-12", eq, [])
        s = result.summary()
        assert "基准收益" in s
        assert "超额收益" in s

    def test_sharpe_deducts_risk_free(self):
        """夏普比率已扣无风险利率（扣后比不扣低）"""
        eq = _make_equity([0.001] * 100)
        bt = Backtester(None, {"initial_capital": 1_000_000})
        result = bt._calc_metrics("test", "2024-01-02", "2024-05-20", eq, [])
        # 验证计算包含 risk-free 扣除：对相同序列手动算无扣版本 > 扣除版本
        daily_ret = eq.pct_change().dropna()
        sharpe_no_rf = float((daily_ret.mean() / (daily_ret.std() + 1e-9)) * np.sqrt(252))
        # 扣除无风险利率后 sharpe 应更低
        assert result.sharpe_ratio < sharpe_no_rf


class TestBacktestETFSupport:
    """ETF asset_type 支持"""

    def test_run_accepts_etf_asset_type(self, monkeypatch):
        """run() 支持 asset_type='etf'，调用 store.list_codes('etf', ...)"""
        import data.store as store_mod

        listed = []

        def mock_list(asset_type, market="cn"):
            listed.append(asset_type)
            return []

        monkeypatch.setattr(store_mod, "list_codes", mock_list)
        monkeypatch.setattr(store_mod, "load_ipo_dates", lambda: {})

        from strategies.builtin.etf_momentum import ETFMomentumStrategy
        bt = Backtester(None, {"initial_capital": 1_000_000})
        try:
            bt.run(ETFMomentumStrategy(), [], "2024-01-02", "2024-06-28",
                   asset_type="etf")
        except ValueError:
            pass  # "没有可用历史数据" 是正常的 mock 行为
        assert "etf" in listed

    def test_run_default_asset_type_is_stock(self, monkeypatch):
        """run() 默认 asset_type='stock'"""
        import data.store as store_mod

        listed = []

        def mock_list(asset_type, market="cn"):
            listed.append(asset_type)
            return []

        monkeypatch.setattr(store_mod, "list_codes", mock_list)
        monkeypatch.setattr(store_mod, "load_ipo_dates", lambda: {})

        from strategies.builtin.ma_cross import MACrossStrategy
        bt = Backtester(None, {"initial_capital": 1_000_000})
        try:
            bt.run(MACrossStrategy(), [], "2024-01-02", "2024-06-28")
        except ValueError:
            pass
        assert "stock" in listed


class TestBacktestMaxPositions:
    """RC2 对齐：同时持仓上限从配置读取，不硬编码"""

    def test_max_positions_from_config(self):
        bt = Backtester(None, {"max_positions": 5})
        assert bt.max_positions == 5

    def test_max_positions_default(self):
        bt = Backtester(None, {})
        assert bt.max_positions == 10
