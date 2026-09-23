"""
test_store_moneyflow.py — moneyflow 域存储层单元测试
覆盖 UT-DS-01/02/06/07：save/load 往返、去重、区间查询、last_date

类型映射（新域）：
  stock      个股资金流（按 code）← tushare/moneyflow
  margin     融资融券（按 code）  ← tushare/margin
  industry   行业资金流（按 date）← tushare/moneyflow_ind
  top_list   龙虎榜（按 date）   ← tushare/top_list
  market     大盘资金流（单文件）← tushare/moneyflow_mkt
  northbound 北向资金（单文件）  ← factor/northbound
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store_moneyflow as smf


@pytest.fixture
def tmp_mf_dir(tmp_path, monkeypatch):
    original = smf.BASE_DIR
    smf.BASE_DIR = tmp_path / "moneyflow"
    smf.BASE_DIR.mkdir(parents=True, exist_ok=True)
    for sub in ("stock", "margin", "industry", "top_list"):
        (smf.BASE_DIR / sub).mkdir(parents=True, exist_ok=True)
    yield smf.BASE_DIR
    smf.BASE_DIR = original


class TestMoneyflowRoundtrip:
    """UT-DS-01：save/load 往返"""

    def test_stock_code_type_roundtrip(self, tmp_mf_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "net_mf_amount": np.random.randn(len(dates)) * 1e7,
        })
        smf.save("stock", df, code="600519")
        loaded = smf.load("stock", code="600519")
        assert loaded is not None
        assert len(loaded) == len(dates)
        assert isinstance(loaded.index, pd.DatetimeIndex)
        assert loaded.index.name == "date"

    def test_margin_code_type_roundtrip(self, tmp_mf_dir):
        """margin 字段完整性"""
        dates = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "rzye": np.random.uniform(1e8, 1e9, len(dates)),
            "rzmre": np.random.uniform(1e7, 1e8, len(dates)),
            "rqyl": np.random.uniform(1e6, 1e7, len(dates)),
            "rqchl": np.random.uniform(1e5, 1e6, len(dates)),
        })
        smf.save("margin", df, code="600519")
        loaded = smf.load("margin", code="600519")
        assert loaded is not None
        assert len(loaded) == len(dates)
        for col in ("rzye", "rzmre", "rqyl", "rqchl"):
            assert col in loaded.columns

    def test_industry_date_type_roundtrip(self, tmp_mf_dir):
        df = pd.DataFrame({
            "date": [pd.Timestamp("2024-06-20")],
            "name": ["食品饮料"],
            "net_amount": [1e8],
        })
        smf.save("industry", df, date="2024-06-20")
        loaded = smf.load("industry", date="2024-06-20")
        assert loaded is not None
        assert len(loaded) == 1

    def test_top_list_date_type_roundtrip(self, tmp_mf_dir):
        df = pd.DataFrame({
            "date": [pd.Timestamp("2024-06-20")],
            "code": ["600519"],
            "reason": ["涨幅偏离值"],
        })
        smf.save("top_list", df, date="2024-06-20")
        loaded = smf.load("top_list", date="2024-06-20")
        assert loaded is not None
        assert len(loaded) == 1

    def test_market_single_type_roundtrip(self, tmp_mf_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "net_mf_amount": np.random.randn(len(dates)) * 1e9,
        })
        smf.save("market", df)
        loaded = smf.load("market")
        assert loaded is not None
        assert len(loaded) == len(dates)

    def test_northbound_single_type_roundtrip(self, tmp_mf_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "net_buy": np.random.randn(len(dates)) * 1e9,
        })
        smf.save("northbound", df)
        loaded = smf.load("northbound")
        assert loaded is not None
        assert len(loaded) == len(dates)

    def test_rename_trade_date_column(self, tmp_mf_dir):
        """Tushare trade_date 列自动重命名为 date"""
        df = pd.DataFrame({
            "trade_date": ["20240102", "20240103", "20240104"],
            "net_mf_amount": [1e7, 2e7, -1e7],
        })
        smf.save("stock", df, code="000001")
        loaded = smf.load("stock", code="000001")
        assert loaded is not None
        assert loaded.index.name == "date"

    def test_chinese_date_column(self, tmp_mf_dir):
        """AKShare 日期列（日期）自动识别"""
        df = pd.DataFrame({
            "日期": pd.date_range("2024-01-02", "2024-01-05", freq="B"),
            "net_buy": [1e9, 2e9, 3e9, 4e9],
        })
        smf.save("northbound", df)
        loaded = smf.load("northbound")
        assert loaded is not None
        assert loaded.index.name == "date"


class TestMoneyflowDedup:
    """UT-DS-02：重复 save 自动去重"""

    def test_dedup_stock_overlapping(self, tmp_mf_dir):
        dates1 = pd.date_range("2024-01-02", "2024-01-31", freq="B")
        dates2 = pd.date_range("2024-01-15", "2024-02-28", freq="B")
        df1 = pd.DataFrame({"date": dates1, "net_mf_amount": 100.0})
        df2 = pd.DataFrame({"date": dates2, "net_mf_amount": 200.0})
        smf.save("stock", df1, code="600519")
        smf.save("stock", df2, code="600519")
        loaded = smf.load("stock", code="600519")
        assert not loaded.index.has_duplicates

    def test_dedup_single_file(self, tmp_mf_dir):
        dates1 = pd.date_range("2024-01-02", "2024-01-20", freq="B")
        dates2 = pd.date_range("2024-01-15", "2024-01-31", freq="B")
        df1 = pd.DataFrame({"date": dates1, "net_buy": 100.0})
        df2 = pd.DataFrame({"date": dates2, "net_buy": 200.0})
        smf.save("northbound", df1)
        smf.save("northbound", df2)
        loaded = smf.load("northbound")
        assert not loaded.index.has_duplicates


class TestMoneyflowQuery:
    """日期范围查询 + last_date"""

    def test_start_end_filter(self, tmp_mf_dir):
        dates = pd.date_range("2024-01-02", "2024-12-31", freq="B")
        df = pd.DataFrame({"date": dates, "net_mf_amount": 1.0})
        smf.save("stock", df, code="600519")
        sub = smf.load("stock", code="600519", start="2024-06-01", end="2024-08-31")
        assert sub is not None
        assert sub.index.min() >= pd.Timestamp("2024-06-01")
        assert sub.index.max() <= pd.Timestamp("2024-08-31")

    def test_last_date_code_type(self, tmp_mf_dir):
        dates = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        df = pd.DataFrame({"date": dates, "net_mf_amount": 1.0})
        smf.save("stock", df, code="600519")
        assert smf.last_date("stock", code="600519") == "2024-06-28"

    def test_last_date_single_type(self, tmp_mf_dir):
        dates = pd.date_range("2024-01-02", "2024-05-31", freq="B")
        df = pd.DataFrame({"date": dates, "net_mf_amount": 100.0})
        smf.save("market", df)
        assert smf.last_date("market") == "2024-05-31"

    def test_last_date_nonexistent(self, tmp_mf_dir):
        assert smf.last_date("stock", code="000000") is None


class TestMoneyflowAux:
    """辅助接口"""

    def test_exists(self, tmp_mf_dir):
        assert not smf.exists("margin", code="600519")
        df = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "rzye": [1e8]})
        smf.save("margin", df, code="600519")
        assert smf.exists("margin", code="600519")

    def test_list_codes(self, tmp_mf_dir):
        for code in ["600519", "000001"]:
            df = pd.DataFrame({"date": pd.date_range("2024-01-02", "2024-01-10"),
                               "net_mf_amount": 1.0})
            smf.save("stock", df, code=code)
        codes = smf.list_codes("stock")
        assert "600519" in codes
        assert "000001" in codes

    def test_list_codes_empty(self, tmp_mf_dir):
        assert smf.list_codes("margin") == []

    def test_list_dates(self, tmp_mf_dir):
        d = tmp_mf_dir / "industry"
        (d / "2024-06-20.parquet").touch()
        (d / "2024-06-21.parquet").touch()
        dates = smf.list_dates("industry")
        assert "2024-06-20" in dates
        assert "2024-06-21" in dates

    def test_code_type_missing_code_raises(self, tmp_mf_dir):
        with pytest.raises(ValueError):
            smf.save("stock", pd.DataFrame({"date": [pd.Timestamp("2024-01-02")]}), code=None)

    def test_date_type_missing_date_raises(self, tmp_mf_dir):
        with pytest.raises(ValueError):
            smf.save("industry", pd.DataFrame({"date": [pd.Timestamp("2024-01-02")]}), date=None)

    def test_stats_structure(self, tmp_mf_dir):
        df = pd.DataFrame({"date": pd.date_range("2024-01-02", "2024-01-10"),
                           "net_mf_amount": 1.0})
        smf.save("stock", df, code="600519")
        s = smf.stats()
        assert "stock" in s
        assert isinstance(s["stock"]["count"], int)

    def test_save_none_or_empty_noop(self, tmp_mf_dir):
        smf.save("stock", None, code="600519")
        smf.save("stock", pd.DataFrame(), code="600519")
        assert smf.load("stock", code="600519") is None


class TestMoneyflowMarket:
    """market 维度：hk 落在 BASE_DIR/hk/ 下，与 cn 同名代码互不干扰"""

    def test_hk_market_path_and_roundtrip(self, tmp_mf_dir):
        d = tmp_mf_dir / "hk" / "stock"
        d.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({
            "date": pd.date_range("2026-09-01", periods=3, freq="B"),
            "main_net": [1.0, 2.0, 3.0],
        })
        smf.save("stock", df, code="00700", market="hk")
        loaded = smf.load("stock", code="00700", market="hk")
        assert loaded is not None and len(loaded) == 3
        assert (d / "00700.parquet").exists()
        assert smf.load("stock", code="00700") is None
        assert smf.list_codes("stock", market="hk") == ["00700"]
