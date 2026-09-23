"""
test_store_fundamental.py — fundamental 域存储层单元测试
覆盖 UT-DS-01/02/06/07：save/load 往返、去重、区间查询、last_date
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store_fundamental as sf


@pytest.fixture
def tmp_fund_dir(tmp_path, monkeypatch):
    original = sf.BASE_DIR
    sf.BASE_DIR = tmp_path / "fundamental"
    for t in sf.VALID_TYPES:
        (sf.BASE_DIR / t).mkdir(parents=True, exist_ok=True)
    yield sf.BASE_DIR
    sf.BASE_DIR = original


class TestFundamentalRoundtrip:
    """UT-DS-01：save/load 往返一致性"""

    def test_profit_save_load_roundtrip(self, tmp_fund_dir):
        df = pd.DataFrame({
            "statDate": pd.date_range("2024-03-31", "2025-09-30", freq="QE"),
            "roew": [15.0, 16.0, 17.0, 16.5, 15.5, 14.0, 13.5],
            "netprofit_yoy": [10.0, 12.0, 15.0, 13.0, 11.0, 9.0, 8.0],
        })
        assert len(df) == 7  # 7 quarters
        sf.save("600519", df, "profit")
        loaded = sf.load("600519", "profit")
        assert loaded is not None
        assert len(loaded) == 7
        assert isinstance(loaded.index, pd.DatetimeIndex)
        assert loaded.index.name == "stat_date"

    def test_balance_save_load_roundtrip(self, tmp_fund_dir):
        df = pd.DataFrame({
            "statDate": pd.date_range("2024-03-31", "2024-12-31", freq="QE"),
            "total_assets": [1e10, 1.05e10, 1.1e10, 1.15e10],
        })
        sf.save("000001", df, "balance")
        loaded = sf.load("000001", "balance")
        assert loaded is not None
        assert len(loaded) == 4

    def test_cashflow_save_load_roundtrip(self, tmp_fund_dir):
        df = pd.DataFrame({
            "statDate": pd.date_range("2024-03-31", "2024-12-31", freq="QE"),
            "n_cashflow_act": [5e8, 6e8, 7e8, 8e8],
        })
        sf.save("600036", df, "cashflow")
        loaded = sf.load("600036", "cashflow")
        assert loaded is not None
        assert len(loaded) == 4

    def test_growth_save_load_roundtrip(self, tmp_fund_dir):
        df = pd.DataFrame({
            "statDate": pd.date_range("2024-03-31", "2024-09-30", freq="QE"),
            "or_yoy": [8.0, 10.0, 12.0],
        })
        sf.save("000858", df, "growth")
        loaded = sf.load("000858", "growth")
        assert loaded is not None
        assert len(loaded) == 3

    def test_dupont_save_load_roundtrip(self, tmp_fund_dir):
        df = pd.DataFrame({
            "statDate": pd.date_range("2024-03-31", "2024-09-30", freq="QE"),
            "roe": [12.0, 13.0, 14.0],
        })
        sf.save("601318", df, "dupont")
        loaded = sf.load("601318", "dupont")
        assert loaded is not None
        assert len(loaded) == 3


class TestFundamentalDedup:
    """UT-DS-02：重复 save 自动去重"""

    def test_dedup_overlapping_dates(self, tmp_fund_dir):
        df1 = pd.DataFrame({
            "statDate": pd.date_range("2024-03-31", "2024-09-30", freq="QE"),
            "roew": [10.0, 12.0, 14.0],
        })
        df2 = pd.DataFrame({
            "statDate": pd.date_range("2024-06-30", "2025-03-31", freq="QE"),
            "roew": [13.0, 15.0, 16.0, 17.0],
        })
        sf.save("000001", df1, "profit")
        sf.save("000001", df2, "profit")
        loaded = sf.load("000001", "profit")
        assert not loaded.index.has_duplicates

    def test_dedup_same_date_keep_last(self, tmp_fund_dir):
        df1 = pd.DataFrame({
            "statDate": pd.to_datetime(["2024-03-31"]),
            "roew": [10.0],
        })
        df2 = pd.DataFrame({
            "statDate": pd.to_datetime(["2024-03-31"]),
            "roew": [12.0],
        })
        sf.save("000001", df1, "profit")
        sf.save("000001", df2, "profit")
        loaded = sf.load("000001", "profit")
        assert len(loaded) == 1
        assert float(loaded.iloc[0]["roew"]) == 12.0


class TestFundamentalQuery:
    """日期范围查询 + last_date"""

    def test_start_end_filter(self, tmp_fund_dir):
        df = pd.DataFrame({
            "statDate": pd.date_range("2020-03-31", "2025-03-31", freq="QE"),
            "roew": [float(i) for i in range(21)],
        })
        sf.save("600519", df, "profit")
        sub = sf.load("600519", "profit", start="2023-01-01", end="2024-12-31")
        assert sub is not None
        assert sub.index.min() >= pd.Timestamp("2023-01-01")
        assert sub.index.max() <= pd.Timestamp("2024-12-31")

    def test_last_date(self, tmp_fund_dir):
        df = pd.DataFrame({
            "statDate": pd.date_range("2024-03-31", "2025-03-31", freq="QE"),
            "roew": [10.0, 12.0, 14.0, 13.0, 15.0],
        })
        sf.save("600519", df, "profit")
        ld = sf.last_date("600519", "profit")
        assert ld == "2025-03-31"

    def test_last_date_nonexistent(self, tmp_fund_dir):
        assert sf.last_date("000000", "profit") is None


class TestFundamentalAux:
    """辅助接口"""

    def test_exists(self, tmp_fund_dir):
        assert not sf.exists("600519", "profit")
        df = pd.DataFrame({"statDate": [pd.Timestamp("2024-03-31")], "roew": [10.0]})
        sf.save("600519", df, "profit")
        assert sf.exists("600519", "profit")

    def test_load_nonexistent(self, tmp_fund_dir):
        assert sf.load("000000", "profit") is None

    def test_invalid_type_raises(self, tmp_fund_dir):
        df = pd.DataFrame({"statDate": [pd.Timestamp("2024-03-31")], "x": [1]})
        with pytest.raises(ValueError):
            sf.save("600519", df, "invalid")

    def test_list_codes(self, tmp_fund_dir):
        for code in ["600519", "000001", "000002"]:
            df = pd.DataFrame({"statDate": [pd.Timestamp("2024-03-31")], "roew": [10.0]})
            sf.save(code, df, "profit")
        codes = sf.list_codes("profit")
        assert sorted(codes) == ["000001", "000002", "600519"]

    def test_list_codes_empty(self, tmp_fund_dir):
        assert sf.list_codes("growth") == []

    def test_stats_structure(self, tmp_fund_dir):
        df = pd.DataFrame({"statDate": [pd.Timestamp("2024-03-31")], "roew": [10.0]})
        sf.save("600519", df, "profit")
        s = sf.stats()
        assert "profit" in s
        assert s["profit"]["count"] >= 1
        assert isinstance(s["profit"]["size_mb"], float)

    def test_save_none_or_empty_noop(self, tmp_fund_dir):
        sf.save("600519", None, "profit")
        sf.save("600519", pd.DataFrame(), "profit")
        assert sf.load("600519", "profit") is None


class TestFundamentalMarket:
    """market 维度：hk 落在 BASE_DIR/hk/ 下"""

    def test_hk_market_path_and_roundtrip(self, tmp_fund_dir):
        d = tmp_fund_dir / "hk" / "profit"
        d.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({"REPORT_DATE": ["2025-12-31"], "营业额": [100.0]})
        sf.save("00700", df, "profit", market="hk")
        loaded = sf.load("00700", "profit", market="hk")
        assert loaded is not None and len(loaded) == 1
        assert (d / "00700.parquet").exists()
        assert sf.load("00700", "profit") is None
