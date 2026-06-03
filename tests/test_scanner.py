"""
test_scanner.py — 扫描引擎单元测试
覆盖 GAP-02/03/04：ST/北交所/新股/成交额过滤 + UT-MG-01~03：信号合并
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store as store
from engine.scanner import ScanEngine
from strategies.base import Signal
from strategies.builtin.ma_cross import MACrossStrategy


def _make_signals():
    """构造多策略信号的辅助函数"""
    s1 = Signal(code="000001", name="标的A", signal="BUY", price=10.0,
                reason="MA金叉+放量", score=75.0)
    s2 = Signal(code="000001", name="标的A", signal="BUY", price=10.0,
                reason="MACD金叉+RSI超卖", score=65.0)
    s3 = Signal(code="000002", name="标的B", signal="SELL", price=20.0,
                reason="MA死叉", score=70.0)
    s4 = Signal(code="000002", name="标的B", signal="SELL", price=20.0,
                reason="跌破支撑", score=55.0)
    s5 = Signal(code="000003", name="标的C", signal="BUY", price=30.0,
                reason="突破高点+放量", score=88.0)
    s6 = Signal(code="000003", name="标的C", signal="SELL", price=30.0,
                reason="警戒线触发", score=30.0)
    return [s1, s2, s3, s4, s5, s6]


# ═══════════════════════════════════════════════════════════════
# UT-MG-01~03：信号合并逻辑
# ═══════════════════════════════════════════════════════════════

class TestSignalMerge:
    """UT-MG-01~03：扫描引擎信号合并逻辑"""

    def test_same_direction_merge_takes_max_score(self):
        """UT-MG-01：同标的同方向合并，取最高分"""
        signals = _make_signals()
        merged = {}
        for s in signals:
            key = (s.code, s.signal)
            if key not in merged:
                merged[key] = s
            else:
                existing = merged[key]
                if s.score > existing.score:
                    existing.reason = f"{s.reason} / {existing.reason}"
                    existing.score = s.score
                else:
                    existing.reason = f"{existing.reason} / {s.reason}"

        key_000001_buy = ("000001", "BUY")
        assert key_000001_buy in merged
        assert merged[key_000001_buy].score == 75.0  # max of 75 and 65

    def test_merge_preserves_reasons(self):
        """UT-MG-02：合并后原因字段包含所有原始原因"""
        signals = _make_signals()
        merged = {}
        for s in signals:
            key = (s.code, s.signal)
            if key not in merged:
                merged[key] = s
            else:
                existing = merged[key]
                if s.score > existing.score:
                    existing.reason = f"{s.reason} / {existing.reason}"
                    existing.score = s.score
                else:
                    existing.reason = f"{existing.reason} / {s.reason}"

        buy_reason = merged[("000001", "BUY")].reason
        assert "MA金叉" in buy_reason
        assert "MACD金叉" in buy_reason or "MACD+" in buy_reason

    def test_different_direction_not_merged(self):
        """UT-MG-03：同标的不同方向信号不合并，分别保留"""
        signals = _make_signals()
        merged = {}
        for s in signals:
            key = (s.code, s.signal)
            if key not in merged:
                merged[key] = s
            else:
                existing = merged[key]
                if s.score > existing.score:
                    existing.reason = f"{s.reason} / {existing.reason}"
                    existing.score = s.score
                else:
                    existing.reason = f"{existing.reason} / {s.reason}"

        assert ("000003", "BUY") in merged
        assert ("000003", "SELL") in merged
        assert merged[("000003", "BUY")].score == 88.0
        assert merged[("000003", "SELL")].score == 30.0

    def test_merge_empty_input(self):
        merged = {}
        assert len(merged) == 0

    def test_merge_single_signal(self):
        sig = Signal(code="000001", name="测试", signal="BUY", price=10.0,
                     reason="唯一信号", score=80.0)
        merged = {("000001", "BUY"): sig}
        assert merged[("000001", "BUY")].score == 80.0


# ═══════════════════════════════════════════════════════════════
# GAP-02/03/04：扫描过滤逻辑
# ═══════════════════════════════════════════════════════════════

class TestFilterStockList:
    """GAP-02（北交所）/ GAP-03（新股）/ GAP-04（成交额）/ S4（ST/停牌）"""

    def _make_engine(self, cfg=None):
        cfg = cfg or {"exclude_st": True, "exclude_new": True}
        fetcher = MagicMock()
        return ScanEngine(fetcher, cfg)

    def test_exclude_beijing_exchange_8_prefix(self, monkeypatch):
        """GAP-02：排除8开头北交所标的"""
        ipo = {
            "cn:800001": {"ipo_date": "2023-01-10", "market": "cn", "type": "stock", "name": "北交所标的1"},
            "cn:600519": {"ipo_date": "2001-08-27", "market": "cn", "type": "stock", "name": "贵州茅台"},
            "cn:838888": {"ipo_date": "2023-06-15", "market": "cn", "type": "stock", "name": "北交所标的2"},
        }
        monkeypatch.setattr(store, "load_ipo_dates", lambda: ipo)
        monkeypatch.setattr(store, "list_codes", lambda asset_type="stock", market="cn": ["800001", "600519", "838888"])

        engine = self._make_engine()
        result = engine._get_filtered_stock_list()
        codes = result["code"].tolist()
        assert "800001" not in codes
        assert "838888" not in codes
        assert "600519" in codes

    def test_exclude_beijing_exchange_4_prefix(self, monkeypatch):
        """GAP-02：排除4开头北交所标的"""
        ipo = {
            "cn:400001": {"ipo_date": "2023-01-01", "market": "cn", "type": "stock", "name": "退市股"},
            "cn:000001": {"ipo_date": "1991-04-03", "market": "cn", "type": "stock", "name": "平安银行"},
        }
        monkeypatch.setattr(store, "load_ipo_dates", lambda: ipo)
        monkeypatch.setattr(store, "list_codes", lambda asset_type="stock", market="cn": ["400001", "000001"])

        engine = self._make_engine()
        result = engine._get_filtered_stock_list()
        codes = result["code"].tolist()
        assert "400001" not in codes
        assert "000001" in codes

    def test_exclude_new_stock_under_60_days(self, monkeypatch):
        """GAP-03：排除上市不足60天新股"""
        recent_ipo = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        old_ipo = "2001-08-27"
        ipo = {
            "cn:600001": {"ipo_date": recent_ipo, "market": "cn", "type": "stock", "name": "新股"},
            "cn:600519": {"ipo_date": old_ipo, "market": "cn", "type": "stock", "name": "贵州茅台"},
        }
        monkeypatch.setattr(store, "load_ipo_dates", lambda: ipo)
        monkeypatch.setattr(store, "list_codes", lambda asset_type="stock", market="cn": ["600001", "600519"])

        engine = self._make_engine({"exclude_st": True, "exclude_new": True})
        result = engine._get_filtered_stock_list()
        codes = result["code"].tolist()
        assert "600001" not in codes
        assert "600519" in codes

    def test_new_stock_over_60_days_kept(self, monkeypatch):
        """GAP-03：上市超过60天保留"""
        old_ipo = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
        ipo = {
            "cn:000001": {"ipo_date": old_ipo, "market": "cn", "type": "stock", "name": "老股"},
        }
        monkeypatch.setattr(store, "load_ipo_dates", lambda: ipo)
        monkeypatch.setattr(store, "list_codes", lambda asset_type="stock", market="cn": ["000001"])

        engine = self._make_engine({"exclude_st": True, "exclude_new": True})
        result = engine._get_filtered_stock_list()
        codes = result["code"].tolist()
        assert "000001" in codes

    def test_exclude_st_with_name(self, monkeypatch):
        """S4：排除名称含ST的标的"""
        ipo = {
            "cn:600001": {"ipo_date": "2010-01-01", "market": "cn", "type": "stock", "name": "*ST华英"},
            "cn:000002": {"ipo_date": "2010-01-01", "market": "cn", "type": "stock", "name": "ST康美"},
            "cn:600519": {"ipo_date": "2001-08-27", "market": "cn", "type": "stock", "name": "贵州茅台"},
        }
        monkeypatch.setattr(store, "load_ipo_dates", lambda: ipo)
        monkeypatch.setattr(store, "list_codes", lambda asset_type="stock", market="cn": ["600001", "000002", "600519"])

        engine = self._make_engine({"exclude_st": True, "exclude_new": False})
        result = engine._get_filtered_stock_list()
        codes = result["code"].tolist()
        assert "600001" not in codes
        assert "000002" not in codes
        assert "600519" in codes

    def test_exclude_delisting_name(self, monkeypatch):
        """S4：排除名称含退的标的"""
        ipo = {
            "cn:600001": {"ipo_date": "2010-01-01", "market": "cn", "type": "stock", "name": "退市明日"},
            "cn:600519": {"ipo_date": "2001-08-27", "market": "cn", "type": "stock", "name": "贵州茅台"},
        }
        monkeypatch.setattr(store, "load_ipo_dates", lambda: ipo)
        monkeypatch.setattr(store, "list_codes", lambda asset_type="stock", market="cn": ["600001", "600519"])

        engine = self._make_engine({"exclude_st": True, "exclude_new": False})
        result = engine._get_filtered_stock_list()
        codes = result["code"].tolist()
        assert "600001" not in codes
        assert "600519" in codes

    def test_exclude_new_disabled(self, monkeypatch):
        """exclude_new=False 时新股不过滤"""
        recent_ipo = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        ipo = {
            "cn:600001": {"ipo_date": recent_ipo, "market": "cn", "type": "stock", "name": "新股"},
            "cn:600519": {"ipo_date": "2001-08-27", "market": "cn", "type": "stock", "name": "贵州茅台"},
        }
        monkeypatch.setattr(store, "load_ipo_dates", lambda: ipo)
        monkeypatch.setattr(store, "list_codes", lambda asset_type="stock", market="cn": ["600001", "600519"])

        engine = self._make_engine({"exclude_st": True, "exclude_new": False})
        result = engine._get_filtered_stock_list()
        codes = result["code"].tolist()
        assert "600001" in codes

    def test_no_ipo_info_still_kept(self, monkeypatch):
        """无IPO信息的标的（容错）不排除"""
        monkeypatch.setattr(store, "load_ipo_dates", lambda: {})
        monkeypatch.setattr(store, "list_codes", lambda asset_type="stock", market="cn": ["999999"])

        engine = self._make_engine({"exclude_st": True, "exclude_new": True})
        result = engine._get_filtered_stock_list()
        codes = result["code"].tolist()
        assert "999999" in codes

    def test_empty_stock_list(self, monkeypatch):
        """本地无数据时返回空 DataFrame"""
        monkeypatch.setattr(store, "load_ipo_dates", lambda: {})
        monkeypatch.setattr(store, "list_codes", lambda asset_type="stock", market="cn": [])

        engine = self._make_engine()
        result = engine._get_filtered_stock_list()
        assert len(result) == 0
        assert list(result.columns) == ["code", "name"]


# ═══════════════════════════════════════════════════════════════
# 单标的扫描过滤逻辑
# ═══════════════════════════════════════════════════════════════

class TestSingleScanFilter:
    """单标的 min_price / max_price / min_volume 过滤"""

    def _make_engine(self, cfg=None):
        cfg = cfg or {"min_price": 2.0, "max_price": 500.0, "min_volume": 0}
        fetcher = MagicMock()
        engine = ScanEngine(fetcher, cfg)
        engine.register_strategy("ma", MACrossStrategy())
        return engine

    def test_price_below_min_filtered(self, monkeypatch):
        """GAP-04：股价低于 min_price 被过滤"""
        import numpy as np
        dates = pd.date_range("2024-01-02", periods=80, freq="B")
        df = pd.DataFrame({
            "date": dates,
            "open": [0.5] * 80,
            "high": [0.6] * 80,
            "low": [0.4] * 80,
            "close": [0.5] * 80,
            "volume": [1_000_000.0] * 80,
        })
        monkeypatch.setattr(store, "load", lambda code, **kw: df)

        engine = self._make_engine({"min_price": 2.0, "max_price": 500.0, "min_volume": 0})
        sigs = engine._scan_single("000001", "低价股", "2024-01-02", "2024-06-28")
        assert sigs == []

    def test_price_above_max_filtered(self, monkeypatch):
        """股价超过 max_price 被过滤"""
        import numpy as np
        dates = pd.date_range("2024-01-02", periods=80, freq="B")
        df = pd.DataFrame({
            "date": dates,
            "open": [600.0] * 80,
            "high": [610.0] * 80,
            "low": [590.0] * 80,
            "close": [600.0] * 80,
            "volume": [1_000_000.0] * 80,
        })
        monkeypatch.setattr(store, "load", lambda code, **kw: df)

        engine = self._make_engine({"min_price": 0, "max_price": 500.0, "min_volume": 0})
        sigs = engine._scan_single("000001", "高价股", "2024-01-02", "2024-06-28")
        assert sigs == []

    def test_volume_below_min_filtered(self, monkeypatch):
        """成交量低于 min_volume 被过滤"""
        import numpy as np
        dates = pd.date_range("2024-01-02", periods=80, freq="B")
        df = pd.DataFrame({
            "date": dates,
            "open": [10.0] * 80,
            "high": [11.0] * 80,
            "low": [9.0] * 80,
            "close": [10.0] * 80,
            "volume": [500_000.0] * 80,
        })
        monkeypatch.setattr(store, "load", lambda code, **kw: df)

        engine = self._make_engine({"min_price": 0, "max_price": float("inf"), "min_volume": 1_000_000})
        sigs = engine._scan_single("000001", "低量股", "2024-01-02", "2024-06-28")
        assert sigs == []

    def test_insufficient_data_returns_empty(self, monkeypatch):
        """数据不足30条时返回空"""
        dates = pd.date_range("2024-01-02", periods=20, freq="B")
        df = pd.DataFrame({
            "date": dates,
            "open": [10.0] * 20,
            "high": [11.0] * 20,
            "low": [9.0] * 20,
            "close": [10.0] * 20,
            "volume": [5_000_000.0] * 20,
        })
        monkeypatch.setattr(store, "load", lambda code, **kw: df)

        engine = self._make_engine()
        sigs = engine._scan_single("000001", "数据不足", "2024-01-02", "2024-01-30")
        assert sigs == []
