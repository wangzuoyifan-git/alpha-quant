"""
test_store_meta.py — meta 域存储层单元测试
覆盖 UT-DS-01~08 通用 store 接口 + 域隔离验证
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store_meta as store_meta
import data.store_fundamental as store_fund
import data.store_factor as store_factor
import data.store_macro as store_macro
import data.store_moneyflow as store_mf


class TestDomainIsolation:
    """UT-DS-08：不同域的 store 物理路径互不重叠"""

    def test_all_domain_bases_disjoint(self):
        bases = [
            store_meta.BASE_DIR,
            store_fund.BASE_DIR,
            store_factor.BASE_DIR,
            store_macro.BASE_DIR,
            store_mf.BASE_DIR,
        ]
        resolved = [str(Path(b).resolve()) for b in bases]
        assert len(resolved) == len(set(resolved))

    def test_meta_subdirs_in_meta_root(self):
        from data import store as store_market
        assert str(store_meta.BASE_DIR.resolve()) != str(store_market.BASE_DIR.resolve())


class TestMetaSaveLoad:
    """meta 域 save/load 往返一致性"""

    @pytest.fixture
    def tmp_meta_dir(self, tmp_path, monkeypatch):
        original = store_meta.BASE_DIR
        store_meta.BASE_DIR = tmp_path / "meta"
        for _d in store_meta._DIRS.values():
            (store_meta.BASE_DIR / "sw_index").mkdir(parents=True, exist_ok=True)
        yield store_meta.BASE_DIR
        store_meta.BASE_DIR = original
        store_meta._DIRS = {
            "industry_csrc": original,
            "industry_sw": original,
            "sw_index": original / "sw_index",
            "calendar": original,
        }

    # ── UT-DS-01: save 后 load 与原始一致 ─────────────────────────
    def test_save_load_industry_csrc_roundtrip(self, tmp_meta_dir):
        df = pd.DataFrame({
            "code": ["600519", "000001"],
            "industry": ["食品饮料", "银行"],
        })
        store_meta.save("industry_csrc", df)
        loaded = store_meta.load("industry_csrc")
        assert loaded is not None
        assert len(loaded) == 2
        assert list(loaded["code"]) == ["600519", "000001"]

    def test_save_load_industry_sw_roundtrip(self, tmp_meta_dir):
        df = pd.DataFrame({
            "code": ["600519", "000001"],
            "industry": ["白酒", "银行"],
        })
        store_meta.save("industry_sw", df)
        loaded = store_meta.load("industry_sw")
        assert loaded is not None
        assert len(loaded) == 2

    def test_save_load_calendar_roundtrip(self, tmp_meta_dir):
        dates = pd.date_range("2024-01-02", "2024-01-10", freq="B")
        df = pd.DataFrame({"date": dates, "is_trading": 1})
        store_meta.save("calendar", df)
        loaded = store_meta.load("calendar")
        assert loaded is not None
        assert len(loaded) == len(dates)
        assert isinstance(loaded.index, pd.DatetimeIndex)

    def test_save_load_sw_index_roundtrip(self, tmp_meta_dir):
        dates = pd.date_range("2024-01-02", "2024-01-30", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "open": 1000.0 + np.arange(len(dates)),
            "high": 1010.0 + np.arange(len(dates)),
            "low": 990.0 + np.arange(len(dates)),
            "close": 1005.0 + np.arange(len(dates)),
        })
        store_meta.save("sw_index", df, code="801010")
        loaded = store_meta.load("sw_index", code="801010")
        assert loaded is not None
        assert len(loaded) == len(dates)
        assert isinstance(loaded.index, pd.DatetimeIndex)
        assert pytest.approx(float(loaded["close"].iloc[-1]), abs=0.01) == df["close"].iloc[-1]

    # ── UT-DS-02: 重复 save 不产生重复行 ──────────────────────────
    def test_calendar_dedup_on_resave(self, tmp_meta_dir):
        dates1 = pd.date_range("2024-01-02", "2024-01-20", freq="B")
        dates2 = pd.date_range("2024-01-15", "2024-01-31", freq="B")
        df1 = pd.DataFrame({"date": dates1, "is_trading": 1})
        df2 = pd.DataFrame({"date": dates2, "is_trading": 1})
        store_meta.save("calendar", df1)
        store_meta.save("calendar", df2)
        loaded = store_meta.load("calendar")
        assert not loaded.index.has_duplicates

    def test_sw_index_dedup_on_resave(self, tmp_meta_dir):
        dates1 = pd.date_range("2024-01-02", "2024-01-20", freq="B")
        dates2 = pd.date_range("2024-01-15", "2024-01-31", freq="B")
        df1 = pd.DataFrame({"date": dates1, "close": np.linspace(1000, 1020, len(dates1))})
        df2 = pd.DataFrame({"date": dates2, "close": np.linspace(1015, 1035, len(dates2))})
        store_meta.save("sw_index", df1, code="801010")
        store_meta.save("sw_index", df2, code="801010")
        loaded = store_meta.load("sw_index", code="801010")
        assert not loaded.index.has_duplicates

    # ── UT-DS-06/07: 日期范围查询 + last_date ────────────────────
    def test_calendar_start_end_filter(self, tmp_meta_dir):
        dates = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        df = pd.DataFrame({"date": dates, "is_trading": 1})
        store_meta.save("calendar", df)
        sub = store_meta.load("calendar", start="2024-03-01", end="2024-04-30")
        assert sub is not None
        assert sub.index.min() >= pd.Timestamp("2024-03-01")
        assert sub.index.max() <= pd.Timestamp("2024-04-30")

    def test_last_date_calendar(self, tmp_meta_dir):
        dates = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        df = pd.DataFrame({"date": dates, "is_trading": 1})
        store_meta.save("calendar", df)
        ld = store_meta.last_date("calendar")
        assert ld == "2024-06-28"

    def test_last_date_nonexistent(self, tmp_meta_dir):
        assert store_meta.last_date("calendar") is None

    # ── 辅助接口 ────────────────────────────────────────────────
    def test_exists_returns_bool(self, tmp_meta_dir):
        assert not store_meta.exists("calendar")
        store_meta.save("calendar", pd.DataFrame(
            {"date": pd.date_range("2024-01-02", "2024-01-05"), "is_trading": 1}))
        assert store_meta.exists("calendar")

    def test_list_sw_index_codes(self, tmp_meta_dir):
        d = store_meta.BASE_DIR / "sw_index"
        d.mkdir(parents=True, exist_ok=True)
        (d / "801010.parquet").touch()
        (d / "801020.parquet").touch()
        codes = store_meta.list_sw_index_codes()
        assert set(codes) == {"801010", "801020"}

    def test_list_sw_index_codes_empty(self, tmp_meta_dir):
        d = store_meta.BASE_DIR / "sw_index"
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.parquet"):
            f.unlink()
        codes = store_meta.list_sw_index_codes()
        assert codes == []

    def test_save_none_or_empty_does_nothing(self, tmp_meta_dir):
        store_meta.save("calendar", None)
        store_meta.save("calendar", pd.DataFrame())
        assert store_meta.load("calendar") is None
