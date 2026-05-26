"""
test_store.py — 本地行情存储层单元测试
覆盖 save/load/exists/list_codes/stats/ipo 核心接口
"""
import json
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store as store


@pytest.fixture
def temp_market_dir(tmp_path, monkeypatch):
    original = store.BASE_DIR
    store.BASE_DIR = tmp_path / "market"
    store.BASE_DIR.mkdir(parents=True, exist_ok=True)
    store.IPO_FILE = store.BASE_DIR / "ipo_dates.json"
    for market, atypes in store.MARKET_ASSET_TYPES.items():
        for atype in atypes:
            (store.BASE_DIR / market / atype).mkdir(parents=True, exist_ok=True)
    yield store.BASE_DIR
    store.BASE_DIR = original
    store.IPO_FILE = original / "ipo_dates.json"


def _make_df(dates, close_start=10.0, stock=True):
    n = len(dates)
    rng = np.random.default_rng(42)
    data = {
        "date": pd.to_datetime(dates),
        "open": [close_start + rng.uniform(-0.5, 0.5) for _ in range(n)],
        "high": [close_start + rng.uniform(0, 1) for _ in range(n)],
        "low": [close_start - rng.uniform(0, 1) for _ in range(n)],
        "close": close_start + np.cumsum(rng.uniform(-0.3, 0.3, n)),
        "preclose": close_start + np.cumsum(rng.uniform(-0.3, 0.3, n)),
        "volume": rng.integers(100000, 1000000, n).astype(float),
        "amount": rng.integers(1000000, 10000000, n).astype(float),
        "pct_chg": rng.uniform(-5, 5, n),
    }
    if stock:
        data["turn"] = rng.uniform(0.5, 5, n)
        data["trade_status"] = "1"
        data["pe_ttm"] = rng.uniform(10, 50, n)
        data["pb_mrq"] = rng.uniform(1, 5, n)
        data["ps_ttm"] = rng.uniform(1, 10, n)
        data["pcf_ttm"] = rng.uniform(5, 30, n)
        data["is_st"] = "0"
    return pd.DataFrame(data)


class TestSaveLoad:
    """核心存取接口"""

    def test_save_then_load_consistent(self, temp_market_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = _make_df(dates)
        store.save("600519", df, "stock", "cn")
        loaded = store.load("600519", "stock", "cn")
        assert loaded is not None
        assert len(loaded) == len(dates)
        assert list(loaded.columns) == store.COLUMNS["cn"]["stock"]
        pd.testing.assert_series_equal(
            loaded["close"].reset_index(drop=True),
            df.set_index("date")["close"].reset_index(drop=True),
            check_names=False,
        )

    def test_save_duplicate_dedup(self, temp_market_dir):
        dates1 = pd.date_range("2024-01-02", "2024-01-31", freq="B")
        dates2 = pd.date_range("2024-01-15", "2024-02-28", freq="B")
        df1 = _make_df(dates1)
        df2 = _make_df(dates2)
        store.save("000001", df1, "stock", "cn")
        store.save("000001", df2, "stock", "cn")
        loaded = store.load("000001", "stock", "cn")
        assert loaded is not None
        assert loaded.index.is_monotonic_increasing
        assert not loaded.index.has_duplicates
        assert loaded.index.min() == pd.Timestamp("2024-01-02")

    def test_start_end_filter(self, temp_market_dir):
        dates = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        df = _make_df(dates)
        store.save("300750", df, "stock", "cn")
        sub = store.load("300750", "stock", "cn", start="2024-03-01", end="2024-04-30")
        assert sub is not None
        assert sub.index.min() >= pd.Timestamp("2024-03-01")
        assert sub.index.max() <= pd.Timestamp("2024-04-30")

    def test_load_nonexistent_returns_none(self, temp_market_dir):
        assert store.load("000000", "stock", "cn") is None


class TestExistsLastDateListCodes:
    """辅助查询接口"""

    def test_exists(self, temp_market_dir):
        assert not store.exists("000001", "stock", "cn")
        df = _make_df(pd.date_range("2024-01-02", "2024-01-31", freq="B"))
        store.save("000001", df, "stock", "cn")
        assert store.exists("000001", "stock", "cn")

    def test_last_date(self, temp_market_dir):
        df = _make_df(pd.date_range("2024-01-02", "2024-03-29", freq="B"))
        store.save("600519", df, "stock", "cn")
        ld = store.last_date("600519", "stock", "cn")
        assert ld is not None
        assert datetime.strptime(ld, "%Y-%m-%d")

    def test_last_date_nonexistent(self, temp_market_dir):
        assert store.last_date("000000", "stock", "cn") is None

    def test_list_codes(self, temp_market_dir):
        codes = ["000001", "000002", "600519"]
        for c in codes:
            df = _make_df(pd.date_range("2024-01-02", "2024-01-31", freq="B"))
            store.save(c, df, "stock", "cn")
        listed = store.list_codes("stock", "cn")
        assert set(listed) == set(codes)

    def test_list_codes_empty_dir(self, temp_market_dir):
        assert store.list_codes("stock", "hk") == []


class TestStats:
    """统计接口"""

    def test_stats_structure(self, temp_market_dir):
        df = _make_df(pd.date_range("2024-01-02", "2024-03-29", freq="B"))
        store.save("600519", df, "stock", "cn")
        s = store.stats()
        assert "total" in s
        assert "cn" in s
        assert "stock" in s["cn"]
        assert s["cn"]["stock"]["count"] == 1
        assert s["cn"]["stock"]["rows"] >= 0
        assert isinstance(s["cn"]["stock"]["size_mb"], float)
        assert s["total"]["count"] >= 1

    def test_stats_empty(self, temp_market_dir):
        s = store.stats()
        assert s["total"]["count"] == 0
        assert s["total"]["rows"] == 0


class TestIPO:
    """上市日期索引"""

    def test_save_and_load_ipo(self, temp_market_dir):
        ipo_data = {
            "cn:600519": {"ipo_date": "2001-08-27", "market": "cn", "type": "stock", "name": "贵州茅台", "full_code": "sh.600519"},
            "cn:000001": {"ipo_date": "1991-04-03", "market": "cn", "type": "stock", "name": "平安银行", "full_code": "sz.000001"},
        }
        store.save_ipo_dates(ipo_data)
        loaded = store.load_ipo_dates()
        assert loaded["cn:600519"]["ipo_date"] == "2001-08-27"
        assert "cn:000001" in loaded

    def test_save_ipo_merge(self, temp_market_dir):
        store.save_ipo_dates({"cn:600519": {"ipo_date": "2001-08-27", "market": "cn", "type": "stock", "name": "贵州茅台", "full_code": "sh.600519"}})
        store.save_ipo_dates({"cn:000001": {"ipo_date": "1991-04-03", "market": "cn", "type": "stock", "name": "平安银行", "full_code": "sz.000001"}})
        loaded = store.load_ipo_dates()
        assert len(loaded) == 2

    def test_get_ipo_date_with_market_prefix(self, temp_market_dir):
        store.save_ipo_dates({"cn:600519": {"ipo_date": "2001-08-27", "market": "cn", "type": "stock", "name": "贵州茅台", "full_code": "sh.600519"}})
        assert store.get_ipo_date("600519") == "2001-08-27"

    def test_get_ipo_date_compat_old_format(self, temp_market_dir):
        data = {"600519": {"ipo_date": "2001-08-27", "market": "cn", "type": "stock", "name": "贵州茅台", "full_code": "sh.600519"}}
        store.save_ipo_dates(data)
        assert store.get_ipo_date("600519") == "2001-08-27"

    def test_get_ipo_date_not_found(self, temp_market_dir):
        assert store.get_ipo_date("000000") is None

    def test_trading_days_since_ipo(self, temp_market_dir):
        store.save_ipo_dates({"cn:600519": {"ipo_date": "2001-08-27", "market": "cn", "type": "stock", "name": "贵州茅台", "full_code": "sh.600519"}})
        days = store.trading_days_since_ipo("600519", as_of="2024-01-02")
        assert days is not None
        assert days > 7000

    def test_trading_days_since_ipo_no_data(self, temp_market_dir):
        assert store.trading_days_since_ipo("000000") is None
