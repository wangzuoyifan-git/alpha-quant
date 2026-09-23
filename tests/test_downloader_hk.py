"""
test_downloader_hk.py — 港股下载器单元测试

覆盖：港股通列表解析/映射、缓存兜底策略、批量下载签名回归
（回归：update_db 调用 _batch_download 时未传 start 导致 TypeError）
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.downloader_hk as dhk
from data.downloader_hk import HKDownloader, DEFAULT_START


class TestParseConnect:

    def test_parse_chinese_columns(self):
        df = pd.DataFrame({"代码": ["00700", "00001"], "名称": ["腾讯控股", "长和"]})
        codes = HKDownloader._parse_connect_df(df)
        assert codes == {"00700": "腾讯控股", "00001": "长和"}

    def test_parse_zfill_and_suffix(self):
        df = pd.DataFrame({"code": ["700.HK", "1"], "name": ["腾讯", "长和"]})
        codes = HKDownloader._parse_connect_df(df)
        assert set(codes) == {"00700", "00001"}

    def test_parse_no_code_col(self):
        assert HKDownloader._parse_connect_df(pd.DataFrame({"x": [1]})) == {}


class TestApplyConnect:

    def test_filters_full_list(self):
        full = [("00700", "腾讯控股"), ("00001", "长和"), ("09999", "网易")]
        connect = {"00700": "腾讯", "09999": "網易"}
        out = HKDownloader._apply_connect(full, connect)
        assert out == [("00700", "腾讯控股"), ("09999", "网易")]

    def test_accepts_suffixed_keys(self):
        full = [("00700", "腾讯控股")]
        out = HKDownloader._apply_connect(full, {"00700.HK": "腾讯"})
        assert out == [("00700", "腾讯控股")]


class TestConnectCache:

    def test_today_cache(self, tmp_path, monkeypatch):
        f = tmp_path / "hk_connect.json"
        f.write_text(json.dumps({"date": "2026-09-22",
                                 "codes": {"00700": "腾讯"}}), encoding="utf-8")
        monkeypatch.setattr(dhk, "_CONNECT_FILE", f)
        monkeypatch.setattr(dhk, "_today", lambda: "2026-09-22")
        dl = HKDownloader.__new__(HKDownloader)
        assert dl._load_connect_cache() == {"00700": "腾讯"}
        assert dl._load_connect_cache(allow_stale=False) == {"00700": "腾讯"}

    def test_recent_cache_fallback(self, tmp_path, monkeypatch):
        f = tmp_path / "hk_connect.json"
        f.write_text(json.dumps({"date": "2026-09-01",
                                 "codes": {"00700": "腾讯"}}), encoding="utf-8")
        monkeypatch.setattr(dhk, "_CONNECT_FILE", f)
        monkeypatch.setattr(dhk, "_today", lambda: "2026-09-22")
        dl = HKDownloader.__new__(HKDownloader)
        assert dl._load_connect_cache(allow_stale=True) == {"00700": "腾讯"}
        assert dl._load_connect_cache(allow_stale=False) == {}

    def test_expired_cache_ignored(self, tmp_path, monkeypatch):
        f = tmp_path / "hk_connect.json"
        f.write_text(json.dumps({"date": "2026-06-01",
                                 "codes": {"00700": "腾讯"}}), encoding="utf-8")
        monkeypatch.setattr(dhk, "_CONNECT_FILE", f)
        monkeypatch.setattr(dhk, "_today", lambda: "2026-09-22")
        assert HKDownloader.__new__(HKDownloader)._load_connect_cache() == {}


class TestConnectPriority:
    """列表优先级：当日缓存 → 东财 → 近期缓存 → Tushare"""

    def test_em_beats_stale_cache(self, tmp_path, monkeypatch):
        import akshare as ak
        f = tmp_path / "hk_connect.json"
        f.write_text(json.dumps({"date": "2026-09-01",
                                 "codes": {"00700": "腾讯"}}), encoding="utf-8")
        monkeypatch.setattr(dhk, "_CONNECT_FILE", f)
        monkeypatch.setattr(dhk, "_today", lambda: "2026-09-22")
        monkeypatch.setattr(ak, "stock_hk_ggt_components_em",
                            lambda: pd.DataFrame({"代码": ["00005"], "名称": ["汇丰控股"]}))
        codes = HKDownloader.__new__(HKDownloader)._get_connect_codes()
        assert codes == {"00005": "汇丰控股"}
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["date"] == "2026-09-22" and "00005" in data["codes"]

    def test_stale_cache_used_when_em_fails(self, tmp_path, monkeypatch):
        import akshare as ak
        f = tmp_path / "hk_connect.json"
        f.write_text(json.dumps({"date": "2026-09-01",
                                 "codes": {"00700": "腾讯"}}), encoding="utf-8")
        monkeypatch.setattr(dhk, "_CONNECT_FILE", f)
        monkeypatch.setattr(dhk, "_today", lambda: "2026-09-22")
        monkeypatch.setattr(dhk.time, "sleep", lambda *_: None)
        monkeypatch.setattr(ak, "stock_hk_ggt_components_em",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert HKDownloader.__new__(HKDownloader)._get_connect_codes() == {"00700": "腾讯"}

    def test_ts_fallback_when_em_fails_and_no_cache(self, tmp_path, monkeypatch):
        import akshare as ak

        class FakePro:
            def hk_hold(self):
                return pd.DataFrame({"ts_code": ["00700.HK"], "name": ["腾讯控股"]})

        monkeypatch.setattr(dhk, "_CONNECT_FILE", tmp_path / "missing.json")
        monkeypatch.setattr(dhk, "_today", lambda: "2026-09-22")
        monkeypatch.setattr(dhk.time, "sleep", lambda *_: None)
        monkeypatch.setattr(ak, "stock_hk_ggt_components_em",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        dl = HKDownloader.__new__(HKDownloader)
        dl._pro = FakePro()
        assert dl._get_connect_codes() == {"00700": "腾讯控股"}


class TestBatchDownload:

    def test_without_start_arg_uses_start_map(self, monkeypatch):
        """回归：update_db 路径只传 end/start_map，不应再 TypeError"""
        dl = HKDownloader.__new__(HKDownloader)
        calls = {}

        def fake_one(code, s, e, force=False):
            calls[code] = (s, e)
            return "ok"

        monkeypatch.setattr(dl, "_download_one_timed", fake_one)
        monkeypatch.setattr(dhk.time, "sleep", lambda *_: None)

        res = dl._batch_download([("00700", "腾讯控股")], end="2026-09-22",
                                 start_map={"00700": "2026-09-01"})
        assert res["ok"] == 1
        assert calls["00700"] == ("2026-09-01", "2026-09-22")

    def test_without_start_map_falls_back_to_default(self, monkeypatch):
        dl = HKDownloader.__new__(HKDownloader)
        calls = {}

        def fake_one(code, s, e, force=False):
            calls[code] = (s, e)
            return "ok"

        monkeypatch.setattr(dl, "_download_one_timed", fake_one)
        monkeypatch.setattr(dhk.time, "sleep", lambda *_: None)

        res = dl._batch_download([("00001", "长和")], end="2026-09-22")
        assert res["ok"] == 1
        assert calls["00001"] == (DEFAULT_START, "2026-09-22")


class TestValuationParsing:
    """百度股市通估值序列解析 + 合并进 K 线"""

    def test_parse_baidu(self):
        body = [["2026-09-21", "14.42"], ["2026-09-22", "15.15"],
                ["2026-09-23", None]]
        out = HKDownloader._parse_baidu_valuation(body)
        assert out is not None
        assert list(out.columns) == ["value"]
        assert len(out) == 2
        assert out.index.max() == pd.Timestamp("2026-09-22")

    def test_parse_bad_body(self):
        assert HKDownloader._parse_baidu_valuation([]) is None
        assert HKDownloader._parse_baidu_valuation([["bad"]]) is None

    def test_merge_valuation_keeps_ohlc_and_updates_pe(self):
        idx = pd.to_datetime(["2026-09-21", "2026-09-22"])
        kline = pd.DataFrame({"close": [430.0, 451.6], "pe_ttm": [14.0, None]},
                             index=idx)
        val = pd.DataFrame({"pe_ttm": [15.15, 99.9], "pb_mrq": [3.14, 9.9]},
                           index=pd.to_datetime(["2026-09-22", "2026-09-23"]))
        out = HKDownloader._merge_valuation(kline, val)
        assert list(out.columns) == ["close", "pe_ttm", "pb_mrq"]
        assert out.loc["2026-09-22", "close"] == 451.6
        assert out.loc["2026-09-22", "pe_ttm"] == 15.15
        assert out.loc["2026-09-22", "pb_mrq"] == 3.14
        assert out.loc["2026-09-21", "pe_ttm"] == 14.0
        assert pd.Timestamp("2026-09-23") not in out.index


class TestConnectTsFallback:

    def test_hk_hold_parsed(self):
        class FakePro:
            def hk_hold(self):
                return pd.DataFrame({
                    "ts_code": ["700.HK", "00001.HK"],
                    "name": ["腾讯控股", "长和"],
                })

        dl = HKDownloader.__new__(HKDownloader)
        dl._pro = FakePro()
        assert dl._get_connect_codes_ts() == {"00700": "腾讯控股", "00001": "长和"}

    def test_no_pro_returns_empty(self):
        dl = HKDownloader.__new__(HKDownloader)
        dl._pro = None
        assert dl._get_connect_codes_ts() == {}
