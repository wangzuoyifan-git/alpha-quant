"""P0-2/P0-4：point-in-time 数据与 TTM ROE 口径测试（不联网）。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store_fundamental as sf
import data.store_moneyflow as sm
from strategies.base import ttm_roe, ttm_sum


def _profit_df():
    idx = pd.to_datetime([
        "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
        "2025-03-31", "2025-06-30",
    ])
    return pd.DataFrame({
        "pubDate": pd.to_datetime([
            "2024-04-30", "2024-08-30", "2024-10-30", "2025-04-10",
            "2025-04-30", "2025-08-30",
        ]),
        "roeAvg": [0.02, 0.04, 0.06, 0.08, 0.03, 0.06],
        "netProfit": [20.0, 40.0, 60.0, 80.0, 30.0, 60.0],
    }, index=idx)


class TestFundamentalAsof:
    def test_future_report_invisible(self, monkeypatch):
        df = _profit_df()
        monkeypatch.setattr(sf, "load", lambda *a, **k: df)
        # 2024-09-30 报告 2024-10-30 才公告
        out = sf.load_asof("000001", "profit", as_of="2024-10-29")
        assert out.index[-1] == pd.Timestamp("2024-06-30")
        out2 = sf.load_asof("000001", "profit", as_of="2024-10-30")
        assert out2.index[-1] == pd.Timestamp("2024-09-30")

    def test_history_returns_only_visible_rows(self, monkeypatch):
        df = _profit_df()
        monkeypatch.setattr(sf, "load", lambda *a, **k: df)
        out = sf.load_asof("000001", "profit", as_of="2025-05-01", history=8)
        # 2025Q1（4/30 公告）可见；2025H1（8/30）不可见
        assert list(out.index[-2:]) == [
            pd.Timestamp("2024-12-31"), pd.Timestamp("2025-03-31")
        ]
        assert len(out) == 5

    def test_latest_snapshot_when_asof_none(self, monkeypatch):
        df = _profit_df()
        monkeypatch.setattr(sf, "load", lambda *a, **k: df)
        out = sf.load_asof("000001", "profit", as_of=None)
        assert len(out) == 1
        assert out.index[-1] == pd.Timestamp("2025-06-30")

    def test_missing_pubdate_uses_conservative_lag(self):
        idx = pd.to_datetime(["2024-03-31"])
        df = pd.DataFrame({"roeAvg": [0.05]}, index=idx)  # 无 pubDate
        import data.store_fundamental as store_mod
        original = store_mod.load
        try:
            store_mod.load = lambda *a, **k: df
            assert sf.load_asof("000001", "profit", as_of="2024-06-28") is None
            assert sf.load_asof("000001", "profit", as_of="2024-07-01") is not None
        finally:
            store_mod.load = original


class TestMoneyflowAsof:
    def test_future_moneyflow_invisible(self, monkeypatch):
        dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
        df = pd.DataFrame({"net_mf_amount": [1.0, 2.0, 999.0]}, index=dates)
        monkeypatch.setattr(sm, "load", lambda *a, **k: df)
        out = sm.load_asof("stock", code="000001", as_of="2024-01-03")
        assert out.index.max() == pd.Timestamp("2024-01-03")
        assert out["net_mf_amount"].tolist() == [1.0, 2.0]
        last2 = sm.load_asof("stock", code="000001", as_of="2024-01-03", lookback=2)
        assert last2["net_mf_amount"].tolist() == [1.0, 2.0]
        assert 999.0 not in out["net_mf_amount"].tolist()


class TestTtmRoe:
    def test_ttm_sum_legacy(self):
        s = pd.Series([0.01, 0.02, 0.03, 0.04])
        assert ttm_sum(s) == pytest.approx(0.10)

    def test_ytd_roll_forward(self):
        idx = pd.to_datetime([
            "2024-12-31", "2025-03-31", "2025-06-30",
            "2025-09-30", "2025-12-31", "2026-03-31",
        ])
        df = pd.DataFrame({
            "roeAvg": [0.10, 0.03, 0.06, 0.09, 0.12, 0.04],
            "netProfit": [100, 30, 60, 90, 120, 40],
        }, index=idx)
        # FY2025 + 2026Q1 - 2025Q1 = 0.12 + 0.04 - 0.03
        assert ttm_roe(df, cumulative=True) == pytest.approx(0.13, abs=1e-9)
        # 自动识别同样得到 0.13
        assert ttm_roe(df) == pytest.approx(0.13, abs=1e-9)

    def test_single_quarter_series_auto_detected(self):
        idx = pd.to_datetime([
            "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
        ])
        df = pd.DataFrame({"roeAvg": [0.045] * 4}, index=idx)
        assert ttm_roe(df) == pytest.approx(0.18)
        assert ttm_roe(df, cumulative=False) == pytest.approx(0.18)

    def test_insufficient_history_annualizes_without_future_data(self):
        idx = pd.to_datetime(["2025-03-31"])
        df = pd.DataFrame({"roeAvg": [0.03], "netProfit": [30.0]}, index=idx)
        # 仅 Q1：0.03 * 12/3 = 0.12
        assert ttm_roe(df, cumulative=True) == pytest.approx(0.12)


class TestStrategyPit:
    def _kline(self):
        dates = pd.date_range("2024-01-02", periods=30, freq="B")
        close = np.linspace(20, 25, len(dates))
        return pd.DataFrame({
            "date": dates, "open": close, "high": close * 1.02,
            "low": close * 0.98, "close": close, "volume": 1e6,
            "pe_ttm": 10.0, "pb_mrq": 1.2,
        })

    def test_b1_uses_only_published_reports(self, monkeypatch):
        from strategies.builtin.fundamental_value import FundamentalValueStrategy

        idx = pd.to_datetime(["2024-12-31", "2025-12-31"])
        profit = pd.DataFrame({
            "pubDate": pd.to_datetime(["2025-04-10", "2026-04-10"]),
            "roeAvg": [0.20, 0.02],
            "netProfit": [200.0, 20.0],
        }, index=idx)
        growth = pd.DataFrame({
            "pubDate": pd.to_datetime(["2025-04-10", "2026-04-10"]),
            "YOYNI": [0.30, 0.30],
            "YOYEquity": [0.20, 0.20],
        }, index=idx)
        store = {"profit": profit, "growth": growth}
        monkeypatch.setattr(sf, "load",
                            lambda code, data_type, **kw: store.get(data_type))
        s = FundamentalValueStrategy()

        # 2025-06：2025 年报尚未公告，应看到 2024 年报 ROE=20%
        assert len(s.generate_signals(self._kline(), "600519", "x",
                                      as_of="2025-06-01")) == 1
        # 2026-04-20：2025 年报已公告 ROE=2%，应被过滤
        assert s.generate_signals(self._kline(), "600519", "x",
                                 as_of="2026-04-20") == []

    def test_moneyflow_strategy_ignores_future_rows(self, monkeypatch):
        from strategies.builtin.money_flow import MoneyFlowStrategy

        dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
        mf = pd.DataFrame({"net_mf_amount": [-5.0, -5.0, 100.0]}, index=dates)
        monkeypatch.setattr(sm, "load", lambda *a, **k: mf)
        df = self._kline()
        s = MoneyFlowStrategy({"lookback": 2})
        # as_of 2024-01-03：只看到两笔净流出 → 无信号
        assert s.generate_signals(df, "000001", "x", as_of="2024-01-03") == []
        # 1-04 的大额流入不可提前看到
        assert s.generate_signals(df, "000001", "x", as_of="2024-01-04") != []
