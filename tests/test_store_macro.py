"""
test_store_macro.py — macro 域存储层单元测试
覆盖 UT-DS-01/02/06/07：save/load 往返、去重、区间查询、last_date
对应 GAP-15/16/17：D5a/D5b 字段完整性
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store_macro as smacro


@pytest.fixture
def tmp_macro_dir(tmp_path, monkeypatch):
    original = smacro.BASE_DIR
    smacro.BASE_DIR = tmp_path / "macro"
    smacro.BASE_DIR.mkdir(parents=True, exist_ok=True)
    yield smacro.BASE_DIR
    smacro.BASE_DIR = original


class TestMacroRoundtrip:
    """UT-DS-01：save/load 往返 + GAP-15/16/17 字段完整性"""

    def test_bond_yield_roundtrip(self, tmp_macro_dir):
        """GAP-16：国债收益率日频字段完整性"""
        dates = pd.date_range("2024-01-02", "2024-12-31", freq="B")
        df = pd.DataFrame({
            "date": dates,
            "yield_2y": np.linspace(2.0, 1.5, len(dates)),
            "yield_10y": np.linspace(2.5, 2.0, len(dates)),
            "spread_10_2": np.linspace(0.5, 0.5, len(dates)),
        })
        smacro.save("bond_yield", df)
        loaded = smacro.load("bond_yield")
        assert loaded is not None
        assert len(loaded) == len(dates)
        assert "yield_2y" in loaded.columns
        assert "yield_10y" in loaded.columns
        assert isinstance(loaded.index, pd.DatetimeIndex)

    def test_m2_roundtrip(self, tmp_macro_dir):
        """GAP-15：M2月度字段完整性"""
        dates = pd.date_range("2024-01-01", "2025-03-01", freq="MS")
        vals = [300.0 + i for i in range(len(dates))]
        df = pd.DataFrame({
            "date": dates,
            "m2_value": vals,
            "m2_yoy": [10.0, 10.5, 11.0, 11.5, 12.0, 11.0, 10.5,
                        10.0, 9.5, 9.0, 9.5, 10.0, 9.5, 9.0, 9.5],
        })
        smacro.save("m2", df)
        loaded = smacro.load("m2")
        assert loaded is not None
        assert len(loaded) == len(dates)

    def test_cpi_ppi_roundtrip(self, tmp_macro_dir):
        dates = pd.date_range("2024-01-01", "2025-03-01", freq="MS")
        df = pd.DataFrame({
            "date": dates,
            "cpi_yoy": [0.5, 0.3, 0.2, 0.1, 0.0, -0.1, 0.0, 0.2,
                         0.3, 0.4, 0.2, 0.1, 0.2, 0.3, 0.4],
            "ppi_yoy": [-2.0, -1.5, -1.2, -1.0, -0.8, -0.5, -0.3, -0.1,
                          0.0, 0.2, 0.1, -0.1, 0.0, 0.1, 0.2],
        })
        smacro.save("cpi_ppi", df)
        loaded = smacro.load("cpi_ppi")
        assert loaded is not None
        assert "cpi_yoy" in loaded.columns
        assert "ppi_yoy" in loaded.columns

    def test_pmi_roundtrip(self, tmp_macro_dir):
        dates = pd.date_range("2024-01-31", "2025-03-31", freq="ME")
        df = pd.DataFrame({
            "date": dates,
            "pmi_manufacturing": [50.1, 49.8, 50.0, 50.2, 49.5, 49.0, 49.5, 50.0,
                                   50.3, 50.5, 50.2, 50.0, 50.3, 50.1, 50.4],
        })
        smacro.save("pmi", df)
        loaded = smacro.load("pmi")
        assert loaded is not None
        assert len(loaded) == 15


class TestMacroDedup:
    """UT-DS-02：重复 save 自动去重"""

    def test_dedup_overlapping_dates(self, tmp_macro_dir):
        dates1 = pd.date_range("2024-01-02", "2024-06-28", freq="B")
        dates2 = pd.date_range("2024-04-01", "2024-12-31", freq="B")
        df1 = pd.DataFrame({"date": dates1, "yield_2y": 2.0})
        df2 = pd.DataFrame({"date": dates2, "yield_2y": 1.5})
        smacro.save("bond_yield", df1)
        smacro.save("bond_yield", df2)
        loaded = smacro.load("bond_yield")
        assert not loaded.index.has_duplicates


class TestMacroQuery:
    """日期范围查询 + last_date"""

    def test_start_end_filter(self, tmp_macro_dir):
        dates = pd.date_range("2020-01-02", "2025-06-01", freq="B")
        df = pd.DataFrame({"date": dates, "yield_10y": 3.0})
        smacro.save("bond_yield", df)
        sub = smacro.load("bond_yield", start="2024-01-01", end="2024-12-31")
        assert sub is not None
        assert sub.index.min() >= pd.Timestamp("2024-01-01")
        assert sub.index.max() <= pd.Timestamp("2024-12-31")

    def test_last_date(self, tmp_macro_dir):
        dates = pd.date_range("2024-01-01", "2024-12-01", freq="MS")
        df = pd.DataFrame({"date": dates, "m2_value": range(1, len(dates) + 1)})
        smacro.save("m2", df)
        ld = smacro.last_date("m2")
        assert ld == "2024-12-01"

    def test_last_date_nonexistent(self, tmp_macro_dir):
        assert smacro.last_date("bond_yield") is None


class TestMacroAux:
    """辅助接口"""

    def test_exists(self, tmp_macro_dir):
        assert not smacro.exists("bond_yield")
        df = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "yield_2y": [2.0]})
        smacro.save("bond_yield", df)
        assert smacro.exists("bond_yield")

    def test_invalid_type_raises(self, tmp_macro_dir):
        with pytest.raises(ValueError):
            smacro.save("invalid", pd.DataFrame({"date": [pd.Timestamp.now()], "x": [1]}))

    def test_save_none_or_empty_noop(self, tmp_macro_dir):
        smacro.save("bond_yield", None)
        smacro.save("bond_yield", pd.DataFrame())
        assert smacro.load("bond_yield") is None

    def test_load_nonexistent_type(self, tmp_macro_dir):
        assert smacro.load("pmi") is None
