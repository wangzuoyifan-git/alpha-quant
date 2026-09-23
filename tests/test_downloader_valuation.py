"""
test_downloader_valuation.py — A股 PCF 补充单测
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.downloader_valuation import _merge_pcf


class TestMergePcf:

    def test_merge_keeps_other_columns(self):
        idx = pd.to_datetime(["2026-09-21", "2026-09-22"])
        kline = pd.DataFrame({"close": [1.0, 2.0], "pcf_ttm": [None, None]},
                             index=idx)
        val = pd.DataFrame({"pcf_ttm": [25.45, 25.48]}, index=idx)
        out = _merge_pcf(kline, val)
        assert list(out.columns) == ["close", "pcf_ttm"]
        assert out.loc["2026-09-22", "pcf_ttm"] == 25.48
        assert out.loc["2026-09-22", "close"] == 2.0

    def test_val_dates_not_in_kline_are_dropped(self):
        idx = pd.to_datetime(["2026-09-22"])
        kline = pd.DataFrame({"close": [2.0]}, index=idx)
        val = pd.DataFrame({"pcf_ttm": [25.0, 99.0]},
                           index=pd.to_datetime(["2026-09-22", "2026-09-23"]))
        out = _merge_pcf(kline, val)
        assert list(out.index) == [pd.Timestamp("2026-09-22")]
        assert out.iloc[0]["pcf_ttm"] == 25.0

    def test_missing_val_keeps_existing(self):
        idx = pd.to_datetime(["2026-09-21", "2026-09-22"])
        kline = pd.DataFrame({"pcf_ttm": [1.0, 2.0]}, index=idx)
        val = pd.DataFrame({"pcf_ttm": [9.9]}, index=pd.to_datetime(["2026-09-22"]))
        out = _merge_pcf(kline, val)
        assert out.loc["2026-09-21", "pcf_ttm"] == 1.0
        assert out.loc["2026-09-22", "pcf_ttm"] == 9.9
