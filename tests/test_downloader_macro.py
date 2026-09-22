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


class TestParseDxySina:
    """新浪 DINIW 美元指数日K解析"""

    def test_parses_rows(self):
        text = ("/*<script>location.href='//sina.com';</script>*/ var _DINIW=("
                '"2026-09-21,100.2407,100.1793,100.4517,100.4130,|'
                '2026-09-22,100.4109,100.3091,100.6667,100.3867");')
        out = MacroDownloader._parse_dxy_sina(text)
        assert out is not None
        assert list(out.columns) == ["date", "open", "low", "high", "close"]
        assert len(out) == 2
        last = out.iloc[-1]
        assert last["date"] == "2026-09-22"
        assert float(last["high"]) == 100.6667

    def test_bad_text_returns_none(self):
        assert MacroDownloader._parse_dxy_sina("") is None
        assert MacroDownloader._parse_dxy_sina("no data") is None


class TestParseFedRate:
    """美联储利率：东财指标记录 → date + target_lower/upper 宽表"""

    def test_merges_lower_upper(self):
        rows = {
            "target_lower": [
                {"PUBLISH_DATE": "2026-09-17 00:00:00", "VALUE": 3.75},
                {"PUBLISH_DATE": "2026-10-29 00:00:00", "VALUE": None},
                {"PUBLISH_DATE": None, "REPORT_DATE": "2008-01-01 00:00:00",
                 "VALUE": 3.0},
            ],
            "target_upper": [
                {"PUBLISH_DATE": "2026-09-17 00:00:00", "VALUE": 4.0},
            ],
        }
        out = MacroDownloader._parse_fed_rate(rows)
        assert out is not None
        assert list(out.columns) == ["target_lower", "target_upper"]
        assert len(out) == 2
        assert out.loc["2026-09-17", "target_upper"] == 4.0
        assert out.loc["2008-01-01", "target_lower"] == 3.0

    def test_returns_none_for_empty(self):
        assert MacroDownloader._parse_fed_rate({}) is None
        assert MacroDownloader._parse_fed_rate({"target_lower": []}) is None
