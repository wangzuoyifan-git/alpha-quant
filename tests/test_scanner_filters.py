"""
tests/test_scanner_filters.py — 扫描引擎 S4 过滤测试（台阶一）

- GAP-02: 北交所标的不参与扫描
- GAP-03: 上市不足60日新股不参与扫描
- GAP-04: 成交额过滤（低流动性标的）
"""
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta


def _make_scanner():
    from engine.scanner import ScanEngine
    cfg = {
        "exclude_st": True,
        "exclude_new": True,
        "min_volume": 1_000_000,
        "min_price": 2.0,
        "max_price": 500.0,
        "top_n": 20,
    }
    fetcher = MagicMock()
    return ScanEngine(fetcher, cfg)


class TestNorthExchangeFilter:
    """GAP-02: 北交所标的（8开头/4开头）不参与扫描"""

    def test_north_exchange_codes_excluded(self, monkeypatch):
        scanner = _make_scanner()

        # mock store 返回含北交所代码的列表
        stock_list = pd.DataFrame([
            {"code": "000001", "name": "平安银行"},
            {"code": "830799", "name": "北交所股票A"},   # 8开头
            {"code": "430047", "name": "北交所股票B"},   # 4开头
            {"code": "600519", "name": "贵州茅台"},
        ])

        monkeypatch.setattr("data.store.list_codes", lambda *a, **k: ["000001", "830799", "430047", "600519"])
        monkeypatch.setattr("data.store.load_ipo_dates", lambda: {
            "cn:000001": {"name": "平安银行", "ipo_date": "2000-01-01"},
            "cn:830799": {"name": "北交所股票A", "ipo_date": "2020-01-01"},
            "cn:430047": {"name": "北交所股票B", "ipo_date": "2020-01-01"},
            "cn:600519": {"name": "贵州茅台", "ipo_date": "2001-01-01"},
        })
        monkeypatch.setattr("data.store_meta.load", lambda *a, **k: None)

        result = scanner._get_filtered_stock_list()
        codes = result["code"].tolist()

        assert "830799" not in codes, "北交所 8 开头应被过滤"
        assert "430047" not in codes, "北交所 4 开头应被过滤"
        assert "000001" in codes
        assert "600519" in codes

    def test_north_exchange_count_logged(self, monkeypatch, capsys):
        scanner = _make_scanner()
        monkeypatch.setattr("data.store.list_codes", lambda *a, **k: ["000001", "830001"])
        monkeypatch.setattr("data.store.load_ipo_dates", lambda: {
            "cn:000001": {"name": "A股", "ipo_date": "2000-01-01"},
            "cn:830001": {"name": "北交", "ipo_date": "2020-01-01"},
        })
        monkeypatch.setattr("data.store_meta.load", lambda *a, **k: None)
        scanner._get_filtered_stock_list()
        captured = capsys.readouterr()
        assert "北交所" in captured.out


class TestNewStockFilter:
    """GAP-03: 上市不足60日新股不参与扫描"""

    def test_new_stock_under_60days_excluded(self, monkeypatch):
        scanner = _make_scanner()
        recent_ipo = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        old_ipo = "2020-01-01"

        monkeypatch.setattr("data.store.list_codes", lambda *a, **k: ["000001", "688001"])
        monkeypatch.setattr("data.store.load_ipo_dates", lambda: {
            "cn:000001": {"name": "老股", "ipo_date": old_ipo},
            "cn:688001": {"name": "新股", "ipo_date": recent_ipo},
        })
        monkeypatch.setattr("data.store_meta.load", lambda *a, **k: None)

        result = scanner._get_filtered_stock_list()
        codes = result["code"].tolist()

        assert "688001" not in codes, "上市不足60天新股应被过滤"
        assert "000001" in codes

    def test_stock_exactly_60days_kept(self, monkeypatch):
        scanner = _make_scanner()
        ipo_60 = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

        monkeypatch.setattr("data.store.list_codes", lambda *a, **k: ["000001"])
        monkeypatch.setattr("data.store.load_ipo_dates", lambda: {
            "cn:000001": {"name": "恰好60天", "ipo_date": ipo_60},
        })
        monkeypatch.setattr("data.store_meta.load", lambda *a, **k: None)

        result = scanner._get_filtered_stock_list()
        assert "000001" in result["code"].tolist(), "恰好60天应保留"

    def test_missing_ipo_date_kept(self, monkeypatch):
        """无上市日期数据的标的默认保留（容错）"""
        scanner = _make_scanner()
        monkeypatch.setattr("data.store.list_codes", lambda *a, **k: ["000001"])
        monkeypatch.setattr("data.store.load_ipo_dates", lambda: {
            "cn:000001": {"name": "无上市日期"},  # 无 ipo_date
        })
        monkeypatch.setattr("data.store_meta.load", lambda *a, **k: None)

        result = scanner._get_filtered_stock_list()
        assert "000001" in result["code"].tolist()


class TestSTFilter:
    """S4: ST / 退市标的不参与扫描（既有功能，补测试）"""

    def test_st_stocks_excluded(self, monkeypatch):
        scanner = _make_scanner()
        monkeypatch.setattr("data.store.list_codes", lambda *a, **k: ["000001", "000002"])
        monkeypatch.setattr("data.store.load_ipo_dates", lambda: {
            "cn:000001": {"name": "正常股", "ipo_date": "2000-01-01"},
            "cn:000002": {"name": "*ST公司", "ipo_date": "2000-01-01"},
        })
        monkeypatch.setattr("data.store_meta.load", lambda *a, **k: None)

        result = scanner._get_filtered_stock_list()
        codes = result["code"].tolist()
        assert "000002" not in codes
        assert "000001" in codes
