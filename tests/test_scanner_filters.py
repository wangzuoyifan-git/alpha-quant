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


def _make_df(amount: float, n: int = 40):
    """构造 n 根 K 线，最后一根成交额为 amount。"""
    import numpy as np
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    df = pd.DataFrame({
        "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0,
        "volume": 5_000_000, "amount": amount,
        "trade_status": "1",
    }, index=dates)
    df.index.name = "date"
    return df


class TestAmountFilter:
    """GAP-04: 成交额低于阈值的标的不参与扫描（流动性过滤）"""

    def _scanner_with_amount_filter(self, min_amount, with_strategy=False):
        from engine.scanner import ScanEngine
        from strategies.base import Signal
        cfg = {
            "min_volume": 0, "min_price": 2.0, "max_price": 500.0,
            "min_amount": min_amount, "top_n": 20,
        }
        scanner = ScanEngine(MagicMock(), cfg)
        if with_strategy:
            fake = MagicMock()
            fake.name = "fake"
            fake.applicable_asset_types = []
            fake.generate_signals = lambda df, code, name: [
                Signal(code=code, name=name, signal="BUY", price=10.0,
                       reason="test", score=80.0)
            ]
            scanner.strategies = {"fake": fake}
        else:
            scanner.strategies = {}
        return scanner

    def test_low_amount_excluded(self, monkeypatch):
        scanner = self._scanner_with_amount_filter(30_000_000, with_strategy=True)
        # 成交额 1000 万 < 阈值 3000 万 → 应在成交额步被拦截
        monkeypatch.setattr("data.store.load", lambda *a, **k: _make_df(10_000_000))
        result = scanner._scan_single("000001", "测试股", "2024-01-01", "2024-02-09")
        assert result == [], "成交额低于阈值应被过滤，不进入策略循环"

    def test_high_amount_passes_filter(self, monkeypatch):
        scanner = self._scanner_with_amount_filter(30_000_000, with_strategy=True)
        # 成交额 5 亿 > 阈值 → 通过过滤，策略产出信号
        monkeypatch.setattr("data.store.load", lambda *a, **k: _make_df(500_000_000))
        result = scanner._scan_single("000001", "测试股", "2024-01-01", "2024-02-09")
        assert len(result) == 1, "成交额高于阈值应通过过滤并产出信号"

    def test_zero_min_amount_disables_filter(self, monkeypatch):
        scanner = self._scanner_with_amount_filter(0, with_strategy=True)
        # min_amount=0 时即便成交额极低也不拦
        monkeypatch.setattr("data.store.load", lambda *a, **k: _make_df(1.0))
        result = scanner._scan_single("000001", "测试股", "2024-01-01", "2024-02-09")
        assert len(result) == 1, "min_amount=0 关闭过滤，信号正常产出"
