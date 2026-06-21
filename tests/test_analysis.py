"""
tests/test_analysis.py — A2 辅助分析聚合测试（阶段C）

覆盖：
- 技术面摘要：量比计算（IT-AA-04）、近20日价格区间
- 资金流摘要：近5日主力净流入（IT-AA-05）
- 基本面摘要：ROE(TTM)/净利同比/毛利率/资产负债率/PE/PB
- A3：数据缺失标注 available=False
- 推送/看板摘要格式化
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_tools import analysis as A


def _kline(n=25, base=10.0, last_vol=2_000_000, prev_vol=1_000_000):
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    vol = [prev_vol] * (n - 1) + [last_vol]
    df = pd.DataFrame({
        "open": base, "high": base * 1.1, "low": base * 0.9, "close": base,
        "volume": vol, "amount": [v * base for v in vol],
        "turn": 3.5, "pct_chg": 2.0, "pe_ttm": 15.0, "pb_mrq": 1.8,
    }, index=dates)
    df.index.name = "date"
    return df


class TestTechnicalSummary:

    def test_volume_ratio_computed(self, monkeypatch):
        """量比 = 当日量 / 近5日均量（IT-AA-04）"""
        import data.store as store
        # 近5日均量 = 1_000_000，当日 = 2_000_000 → 量比 2.0
        monkeypatch.setattr(store, "load", lambda *a, **k: _kline(last_vol=2_000_000, prev_vol=1_000_000))
        r = A._technical_summary("600519")
        assert r["available"] is True
        assert r["volume_ratio"] == 2.0

    def test_price_range_20d(self, monkeypatch):
        import data.store as store
        monkeypatch.setattr(store, "load", lambda *a, **k: _kline(base=10.0))
        r = A._technical_summary("600519")
        assert r["price_low_20d"] == 9.0
        assert r["price_high_20d"] == 11.0

    def test_unavailable_when_no_data(self, monkeypatch):
        import data.store as store
        monkeypatch.setattr(store, "load", lambda *a, **k: None)
        r = A._technical_summary("600519")
        assert r["available"] is False


class TestMoneyflowSummary:

    def _mf(self, n=5, lg=100.0, elg=50.0, sell_lg=30.0, sell_elg=20.0):
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        return pd.DataFrame({
            "buy_lg_amount": lg, "buy_elg_amount": elg,
            "sell_lg_amount": sell_lg, "sell_elg_amount": sell_elg,
            "net_mf_amount": (lg + elg - sell_lg - sell_elg),
        }, index=dates)

    def test_main_net_inflow_5d_sum(self, monkeypatch):
        """近5日主力净流入 = 近5条记录之和（IT-AA-05）"""
        import data.store_moneyflow as mf
        # 单日主力净 = (100+50)-(30+20)=100，5日合计 = 500
        monkeypatch.setattr(mf, "load", lambda *a, **k: self._mf())
        r = A._moneyflow_summary("600519")
        assert r["available"] is True
        assert r["main_net_inflow_5d"] == 500.0
        assert r["days_counted"] == 5

    def test_unavailable_when_no_data(self, monkeypatch):
        import data.store_moneyflow as mf
        monkeypatch.setattr(mf, "load", lambda *a, **k: None)
        r = A._moneyflow_summary("600519")
        assert r["available"] is False


class TestFundamentalSummary:

    def test_roe_ttm_and_fields(self, monkeypatch):
        import data.store_fundamental as sf
        import data.store as store

        def fake_load(code, dtype, **k):
            if dtype == "profit":
                return pd.DataFrame({"roeAvg": [0.04, 0.04, 0.04, 0.04],  # TTM=0.16→16%
                                     "gpMargin": [0.35] * 4})
            if dtype == "growth":
                return pd.DataFrame({"YOYNI": [0.20, 0.25]})  # 最新25%
            if dtype == "balance":
                return pd.DataFrame({"liabilityToAsset": [0.45, 0.50]})  # 50%
            return None

        monkeypatch.setattr(sf, "load", fake_load)
        monkeypatch.setattr(store, "load", lambda *a, **k: _kline())
        r = A._fundamental_summary("600519")
        assert r["available"] is True
        assert r["roe_ttm"] == 16.0
        assert r["net_profit_yoy"] == 25.0
        assert r["gross_margin"] == 35.0
        assert r["debt_ratio"] == 50.0
        assert r["pe_ttm"] == 15.0
        assert r["pb_mrq"] == 1.8

    def test_unavailable_when_all_missing(self, monkeypatch):
        import data.store_fundamental as sf
        import data.store as store
        monkeypatch.setattr(sf, "load", lambda *a, **k: None)
        monkeypatch.setattr(store, "load", lambda *a, **k: None)
        r = A._fundamental_summary("600519")
        assert r["available"] is False


class TestBuildAndEnrich:

    def test_build_structure(self, monkeypatch):
        import data.store as store
        import data.store_moneyflow as mf
        import data.store_fundamental as sf
        monkeypatch.setattr(store, "load", lambda *a, **k: _kline())
        monkeypatch.setattr(mf, "load", lambda *a, **k: None)
        monkeypatch.setattr(sf, "load", lambda *a, **k: None)
        r = A.build_auxiliary_analysis("600519")
        assert set(r.keys()) == {"technical", "moneyflow", "fundamental", "northbound"}
        assert all("available" in v for v in r.values())

    def test_etf_skips_stock_only_blocks(self, monkeypatch):
        import data.store as store
        import data.store_moneyflow as mf
        monkeypatch.setattr(store, "load", lambda *a, **k: _kline())
        monkeypatch.setattr(mf, "load", lambda *a, **k: None)
        r = A.build_auxiliary_analysis("510300", asset_type="etf")
        assert r["fundamental"]["available"] is False
        assert r["moneyflow"]["available"] is False

    def test_enrich_signal_objects(self, monkeypatch):
        from strategies.base import Signal
        import data.store as store
        import data.store_moneyflow as mf
        import data.store_fundamental as sf
        monkeypatch.setattr(store, "load", lambda *a, **k: _kline())
        monkeypatch.setattr(mf, "load", lambda *a, **k: None)
        monkeypatch.setattr(sf, "load", lambda *a, **k: None)
        sig = Signal(code="600519", name="茅台", signal="BUY", price=10.0,
                     reason="t", score=80.0)
        A.enrich_signal_objects([sig])
        assert "analysis" in sig.extra
        assert sig.extra["analysis"]["technical"]["available"] is True


class TestSummaryFormatters:

    def _full_extra(self):
        return {"analysis": {
            "technical": {"available": True, "pct_chg": 2.3, "volume_ratio": 1.45, "turnover": 3.2},
            "moneyflow": {"available": True, "main_net_inflow_5d": 52000.0},
            "fundamental": {"available": True, "roe_ttm": 18.5, "net_profit_yoy": 25.0,
                            "pe_ttm": 22.1, "pb_mrq": 3.4},
        }}

    def test_web_summary(self):
        import main
        s = main._build_aux_summary(self._full_extra())
        assert "ROE18.5%" in s
        assert "主力5日+5.20亿" in s
        assert "PE22.1" in s

    def test_web_summary_empty(self):
        import main
        assert main._build_aux_summary({}) == ""

    def test_push_aux_line(self):
        from notifier.push import Notifier
        from strategies.base import Signal
        s = Signal(code="600519", name="茅台", signal="BUY", price=100,
                   reason="t", score=85, extra=self._full_extra())
        line = Notifier._format_aux_line(s)
        assert "涨跌+2.3%" in line
        assert "ROE18.5%" in line

    def test_push_aux_line_no_analysis(self):
        from notifier.push import Notifier
        from strategies.base import Signal
        s = Signal(code="600519", name="茅台", signal="BUY", price=100, reason="t", score=85)
        assert Notifier._format_aux_line(s) == ""
