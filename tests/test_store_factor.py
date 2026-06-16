"""
test_store_factor.py — factor 域存储层单元测试（重构后：计算型因子）
原资金流测试已迁往 test_store_moneyflow.py
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store_factor as sfact


@pytest.fixture
def tmp_factor_dir(tmp_path, monkeypatch):
    original = sfact.BASE_DIR
    sfact.BASE_DIR = tmp_path / "factor"
    sfact.BASE_DIR.mkdir(parents=True, exist_ok=True)
    yield sfact.BASE_DIR
    sfact.BASE_DIR = original


class TestFactorRoundtrip:
    def test_single_file_save_load(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({"date": dates, "value": 1.0})
        sfact.save("momentum", df)
        loaded = sfact.load("momentum")
        assert loaded is not None
        assert len(loaded) == len(dates)
        assert isinstance(loaded.index, pd.DatetimeIndex)

    def test_per_code_save_load(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-03-29", freq="B")
        df = pd.DataFrame({"date": dates, "value": 1.0})
        sfact.save("quality", df, code="600519")
        loaded = sfact.load("quality", code="600519")
        assert loaded is not None
        assert len(loaded) == len(dates)

    def test_dedup(self, tmp_factor_dir):
        dates1 = pd.date_range("2024-01-02", "2024-01-20", freq="B")
        dates2 = pd.date_range("2024-01-15", "2024-01-31", freq="B")
        df1 = pd.DataFrame({"date": dates1, "value": 1.0})
        df2 = pd.DataFrame({"date": dates2, "value": 2.0})
        sfact.save("momentum", df1)
        sfact.save("momentum", df2)
        loaded = sfact.load("momentum")
        assert not loaded.index.has_duplicates

    def test_last_date(self, tmp_factor_dir):
        dates = pd.date_range("2024-01-02", "2024-05-31", freq="B")
        df = pd.DataFrame({"date": dates, "value": 1.0})
        sfact.save("momentum", df)
        assert sfact.last_date("momentum") == "2024-05-31"

    def test_exists(self, tmp_factor_dir):
        assert not sfact.exists("momentum")
        df = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "value": [1.0]})
        sfact.save("momentum", df)
        assert sfact.exists("momentum")

    def test_load_nonexistent(self, tmp_factor_dir):
        assert sfact.load("nonexistent") is None

    def test_save_none_noop(self, tmp_factor_dir):
        sfact.save("momentum", None)
        assert sfact.load("momentum") is None
