"""
test_downloader_macro.py — macro 下载器纯函数单测

覆盖 us_bond_yield 派生逻辑（美债字段从 bond_yield 截取，作为独立文件）。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.downloader_macro import MacroDownloader


class TestDeriveUsBondYield:

    def test_extracts_us_cols_and_drops_all_nan_rows(self):
        idx = pd.to_datetime(["2026-09-17", "2026-09-18", "2026-09-21"])
        df = pd.DataFrame({
            "cn_10y": [1.80, 1.81, 1.82],
            "us_2y": [4.70, 4.76, None],
            "us_10y": [4.90, 5.01, None],
            "us_gdp_yoy": [None, None, None],
        }, index=idx)
        out = MacroDownloader._derive_us_bond_yield(df)
        assert out is not None
        assert list(out.columns) == ["us_2y", "us_10y", "us_gdp_yoy"]
        assert len(out) == 2
        assert out.index.max() == pd.Timestamp("2026-09-18")

    def test_returns_none_without_us_cols(self):
        df = pd.DataFrame({"cn_10y": [1.80]},
                          index=pd.to_datetime(["2026-09-18"]))
        assert MacroDownloader._derive_us_bond_yield(df) is None

    def test_returns_none_for_empty(self):
        assert MacroDownloader._derive_us_bond_yield(None) is None
        assert MacroDownloader._derive_us_bond_yield(pd.DataFrame()) is None
