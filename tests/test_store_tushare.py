"""
test_store_tushare.py — tushare 域存储层单元测试
覆盖 UT-DS-01/02/06/07：save/load 往返、去重、区间查询、last_date
对应 GAP-12/13：D7 margin 字段完整性验证
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store_tushare as sts


@pytest.fixture
def tmp_ts_dir(tmp_path, monkeypatch):
    original = sts.BASE_DIR
    sts.BASE_DIR = tmp_path / "tushare"
    sts.BASE_DIR.mkdir(parents=True, exist_ok=True)
    for sub in ("moneyflow", "daily", "daily_basic", "top_list", "margin", "moneyflow_ind"):
        (sts.BASE_DIR / sub).mkdir(parents=True, exist_ok=True)
    yield sts.BASE_DIR
    sts.BASE_DIR = original


class TestTushareRoundtrip:
    """UT-DS-01：save/load 往返"""

    def test_moneyflow_code_type_roundtrip(self, tmp_ts_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "net_mf_amount": np.random.randn(len(dates)) * 1e7,
        })
        sts.save("moneyflow", df, code="600519")
        loaded = sts.load("moneyflow", code="600519")
        assert loaded is not None
        assert len(loaded) == len(dates)
        assert isinstance(loaded.index, pd.DatetimeIndex)

    def test_daily_code_type_roundtrip(self, tmp_ts_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "open": np.linspace(10, 15, len(dates)),
            "close": np.linspace(10, 15, len(dates)),
        })
        sts.save("daily", df, code="600519")
        loaded = sts.load("daily", code="600519")
        assert loaded is not None
        assert len(loaded) == len(dates)

    def test_daily_basic_code_type_roundtrip(self, tmp_ts_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "pe_ttm": np.random.uniform(10, 50, len(dates)),
            "pb": np.random.uniform(1, 5, len(dates)),
        })
        sts.save("daily_basic", df, code="000001")
        loaded = sts.load("daily_basic", code="000001")
        assert loaded is not None

    # ── GAP-12：D7 个股 margin 字段完整性 ────────────────────────
    def test_margin_code_type_roundtrip(self, tmp_ts_dir):
        """GAP-12：margin 个股字段 save/load 往返"""
        dates = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "rzye": np.random.uniform(1e8, 1e9, len(dates)),   # 融资余额
            "rzmre": np.random.uniform(1e7, 1e8, len(dates)),  # 融资买入额
            "rqyl": np.random.uniform(1e6, 1e7, len(dates)),   # 融券余量
            "rqchl": np.random.uniform(1e5, 1e6, len(dates)),  # 融券卖出量
        })
        sts.save("margin", df, code="600519")
        loaded = sts.load("margin", code="600519")
        assert loaded is not None
        assert len(loaded) == len(dates)
        assert "rzye" in loaded.columns
        assert "rzmre" in loaded.columns
        assert "rqyl" in loaded.columns
        assert "rqchl" in loaded.columns

    def test_top_list_date_type_roundtrip(self, tmp_ts_dir):
        df = pd.DataFrame({
            "date": [pd.Timestamp("2024-06-20")],
            "code": ["600519"],
            "reason": ["涨幅偏离值"],
        })
        sts.save("top_list", df, date="2024-06-20")
        loaded = sts.load("top_list", date="2024-06-20")
        assert loaded is not None
        assert len(loaded) == 1

    def test_moneyflow_ind_date_type_roundtrip(self, tmp_ts_dir):
        df = pd.DataFrame({
            "date": [pd.Timestamp("2024-06-20")],
            "industry": ["白酒"],
            "net_amount": [1e8],
        })
        sts.save("moneyflow_ind", df, date="2024-06-20")
        loaded = sts.load("moneyflow_ind", date="2024-06-20")
        assert loaded is not None
        assert len(loaded) == 1

    def test_moneyflow_mkt_single_type_roundtrip(self, tmp_ts_dir):
        """GAP-13：市场级别字段完整性"""
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "net_mf_amount": np.random.randn(len(dates)) * 1e9,
        })
        sts.save("moneyflow_mkt", df)
        loaded = sts.load("moneyflow_mkt")
        assert loaded is not None
        assert len(loaded) == len(dates)

    def test_rename_trade_date_column(self, tmp_ts_dir):
        """Tushare trade_date 列自动重命名为 date"""
        df = pd.DataFrame({
            "trade_date": ["20240102", "20240103", "20240104"],
            "close": [10.0, 10.5, 10.3],
        })
        sts.save("daily", df, code="000001")
        loaded = sts.load("daily", code="000001")
        assert loaded is not None
        assert "date" not in loaded.columns  # date 应该是 index
        assert loaded.index.name == "date"


class TestTushareDedup:
    """UT-DS-02：重复 save 自动去重"""

    def test_dedup_code_type_overlapping(self, tmp_ts_dir):
        dates1 = pd.date_range("2024-01-02", "2024-01-31", freq="B")
        dates2 = pd.date_range("2024-01-15", "2024-02-28", freq="B")
        df1 = pd.DataFrame({"date": dates1, "close": 10.0})
        df2 = pd.DataFrame({"date": dates2, "close": 12.0})
        sts.save("daily", df1, code="600519")
        sts.save("daily", df2, code="600519")
        loaded = sts.load("daily", code="600519")
        assert not loaded.index.has_duplicates


class TestTushareQuery:
    """日期范围查询 + last_date"""

    def test_start_end_filter(self, tmp_ts_dir):
        dates = pd.date_range("2024-01-02", "2024-12-31", freq="B")
        df = pd.DataFrame({"date": dates, "close": np.linspace(10, 20, len(dates))})
        sts.save("daily", df, code="600519")
        sub = sts.load("daily", code="600519", start="2024-06-01", end="2024-08-31")
        assert sub is not None
        assert sub.index.min() >= pd.Timestamp("2024-06-01")
        assert sub.index.max() <= pd.Timestamp("2024-08-31")

    def test_last_date(self, tmp_ts_dir):
        dates = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        df = pd.DataFrame({"date": dates, "close": 10.0})
        sts.save("daily", df, code="600519")
        ld = sts.last_date("daily", code="600519")
        assert ld == "2024-06-28"

    def test_last_date_nonexistent(self, tmp_ts_dir):
        assert sts.last_date("daily", code="000000") is None

    def test_last_date_single_type(self, tmp_ts_dir):
        dates = pd.date_range("2024-01-02", "2024-05-31", freq="B")
        df = pd.DataFrame({"date": dates, "net_mf_amount": 100.0})
        sts.save("moneyflow_mkt", df)
        ld = sts.last_date("moneyflow_mkt")
        assert ld == "2024-05-31"


class TestTushareAux:
    """辅助接口"""

    def test_exists(self, tmp_ts_dir):
        assert not sts.exists("margin", code="600519")
        df = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "rzye": [1e8]})
        sts.save("margin", df, code="600519")
        assert sts.exists("margin", code="600519")

    def test_list_codes(self, tmp_ts_dir):
        for code in ["600519", "000001"]:
            df = pd.DataFrame({"date": pd.date_range("2024-01-02", "2024-01-10"), "close": 10.0})
            sts.save("daily", df, code=code)
        codes = sts.list_codes("daily")
        assert "600519" in codes
        assert "000001" in codes

    def test_list_codes_empty(self, tmp_ts_dir):
        assert sts.list_codes("margin") == []

    def test_list_dates(self, tmp_ts_dir):
        d = tmp_ts_dir / "top_list"
        (d / "2024-06-20.parquet").touch()
        (d / "2024-06-21.parquet").touch()
        dates = sts.list_dates("top_list")
        assert "2024-06-20" in dates
        assert "2024-06-21" in dates

    def test_code_type_missing_code_raises(self, tmp_ts_dir):
        with pytest.raises(ValueError):
            sts.save("daily", pd.DataFrame({"date": [pd.Timestamp("2024-01-02")]}), code=None)

    def test_date_type_missing_date_raises(self, tmp_ts_dir):
        with pytest.raises(ValueError):
            sts.save("top_list", pd.DataFrame({"date": [pd.Timestamp("2024-01-02")]}), date=None)

    def test_stats_structure(self, tmp_ts_dir):
        df = pd.DataFrame({"date": pd.date_range("2024-01-02", "2024-01-10"), "close": 10.0})
        sts.save("daily", df, code="600519")
        s = sts.stats()
        assert "daily" in s
        assert isinstance(s["daily"]["count"], int)

    def test_save_none_or_empty_noop(self, tmp_ts_dir):
        sts.save("daily", None, code="600519")
        sts.save("daily", pd.DataFrame(), code="600519")
        assert sts.load("daily", code="600519") is None
