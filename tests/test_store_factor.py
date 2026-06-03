"""
test_store_factor.py — factor 域存储层单元测试
覆盖 UT-DS-01/02/06/07：save/load 往返、去重、区间查询、last_date
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store_factor as sfact


@pytest.fixture
def tmp_factor_dir(tmp_path, monkeypatch):
    original = sfact.BASE_DIR
    sfact.BASE_DIR = tmp_path / "factor"
    (sfact.BASE_DIR / "moneyflow").mkdir(parents=True, exist_ok=True)
    sfact.BASE_DIR.mkdir(parents=True, exist_ok=True)
    yield sfact.BASE_DIR
    sfact.BASE_DIR = original


class TestFactorRoundtrip:
    """UT-DS-01：save/load 往返一致性"""

    def test_moneyflow_save_load_roundtrip(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "net_mf_amount": np.random.randn(len(dates)) * 1e7,
            "buy_elg_amount": np.random.rand(len(dates)) * 1e8,
        })
        sfact.save("moneyflow", df, code="600519")
        loaded = sfact.load("moneyflow", code="600519")
        assert loaded is not None
        assert len(loaded) == len(dates)
        assert isinstance(loaded.index, pd.DatetimeIndex)
        assert loaded.index.name == "date"

    def test_moneyflow_sector_save_load(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "net_mf_amount": np.random.randn(len(dates)) * 1e8,
        })
        sfact.save("moneyflow_sector", df)
        loaded = sfact.load("moneyflow_sector")
        assert loaded is not None
        assert len(loaded) == len(dates)

    def test_moneyflow_market_save_load(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "net_mf_amount": np.random.randn(len(dates)) * 1e9,
        })
        sfact.save("moneyflow_market", df)
        loaded = sfact.load("moneyflow_market")
        assert loaded is not None
        assert len(loaded) == len(dates)

    def test_northbound_save_load(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "net_buy": np.random.randn(len(dates)) * 1e9,
        })
        sfact.save("northbound", df)
        loaded = sfact.load("northbound")
        assert loaded is not None
        assert len(loaded) == len(dates)


class TestFactorDedup:
    """UT-DS-02：重复 save 自动去重"""

    def test_dedup_moneyflow_overlapping(self, tmp_factor_dir):
        dates1 = pd.date_range("2024-01-02", "2024-01-31", freq="B")
        dates2 = pd.date_range("2024-01-15", "2024-02-28", freq="B")
        df1 = pd.DataFrame({"date": dates1, "net_mf_amount": 100.0})
        df2 = pd.DataFrame({"date": dates2, "net_mf_amount": 200.0})
        sfact.save("moneyflow", df1, code="600519")
        sfact.save("moneyflow", df2, code="600519")
        loaded = sfact.load("moneyflow", code="600519")
        assert not loaded.index.has_duplicates

    def test_dedup_single_file_type(self, tmp_factor_dir):
        dates1 = pd.date_range("2024-01-02", "2024-01-20", freq="B")
        dates2 = pd.date_range("2024-01-15", "2024-01-31", freq="B")
        df1 = pd.DataFrame({"date": dates1, "net_buy": 100.0})
        df2 = pd.DataFrame({"date": dates2, "net_buy": 200.0})
        sfact.save("northbound", df1)
        sfact.save("northbound", df2)
        loaded = sfact.load("northbound")
        assert not loaded.index.has_duplicates


class TestFactorQuery:
    """日期范围查询 + last_date"""

    def test_start_end_filter_moneyflow(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-12-31", freq="B")
        df = pd.DataFrame({"date": dates, "net_mf_amount": 100.0})
        sfact.save("moneyflow", df, code="600519")
        sub = sfact.load("moneyflow", code="600519", start="2024-06-01", end="2024-08-31")
        assert sub is not None
        assert sub.index.min() >= pd.Timestamp("2024-06-01")
        assert sub.index.max() <= pd.Timestamp("2024-08-31")

    def test_start_end_filter_northbound(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-12-31", freq="B")
        df = pd.DataFrame({"date": dates, "net_buy": 100.0})
        sfact.save("northbound", df)
        sub = sfact.load("northbound", start="2024-03-01", end="2024-06-30")
        assert sub is not None
        assert sub.index.min() >= pd.Timestamp("2024-03-01")

    def test_last_date_moneyflow(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        df = pd.DataFrame({"date": dates, "net_mf_amount": 100.0})
        sfact.save("moneyflow", df, code="600519")
        ld = sfact.last_date("moneyflow", code="600519")
        assert ld == "2024-06-28"

    def test_last_date_single_type(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-05-31", freq="B")
        df = pd.DataFrame({"date": dates, "net_buy": 100.0})
        sfact.save("northbound", df)
        ld = sfact.last_date("northbound")
        assert ld == "2024-05-31"

    def test_last_date_nonexistent(self, tmp_factor_dir):
        assert sfact.last_date("moneyflow", code="000000") is None


class TestFactorAux:
    """辅助接口"""

    def test_exists(self, tmp_factor_dir):
        assert not sfact.exists("moneyflow", code="600519")
        df = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "net_mf_amount": [100.0]})
        sfact.save("moneyflow", df, code="600519")
        assert sfact.exists("moneyflow", code="600519")

    def test_list_moneyflow_codes(self, tmp_factor_dir):
        for code in ["600519", "000001"]:
            df = pd.DataFrame({"date": pd.date_range("2024-01-02", "2024-01-10"), "net_mf_amount": 100.0})
            sfact.save("moneyflow", df, code=code)
        assert sorted(sfact.list_moneyflow_codes()) == ["000001", "600519"]

    def test_list_codes_empty(self, tmp_factor_dir):
        assert sfact.list_moneyflow_codes() == []

    def test_moneyflow_missing_code_raises(self, tmp_factor_dir):
        with pytest.raises(ValueError):
            sfact.save("moneyflow", pd.DataFrame({"date": [pd.Timestamp("2024-01-02")]}), code=None)

    def test_save_none_or_empty_noop(self, tmp_factor_dir):
        sfact.save("moneyflow", None, code="600519")
        sfact.save("moneyflow", pd.DataFrame(), code="600519")
        assert sfact.load("moneyflow", code="600519") is None

    def test_load_nonexistent(self, tmp_factor_dir):
        assert sfact.load("moneyflow", code="000000") is None
