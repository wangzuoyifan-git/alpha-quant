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


class TestSettledEnd:
    """未收盘时目标日回退（当日数据尚不可用）"""

    def test_before_close_rolls_back_to_yesterday(self):
        from datetime import datetime as dt
        assert dhk._settled_end("2026-09-23", now=dt(2026, 9, 23, 8, 47)) == "2026-09-22"

    def test_after_close_keeps_today(self):
        from datetime import datetime as dt
        assert dhk._settled_end("2026-09-23", now=dt(2026, 9, 23, 17, 30)) == "2026-09-23"

    def test_past_end_unchanged(self):
        from datetime import datetime as dt
        assert dhk._settled_end("2026-09-01", now=dt(2026, 9, 23, 8, 47)) == "2026-09-01"

    def test_future_end_capped_to_today(self):
        from datetime import datetime as dt
        assert dhk._settled_end("2026-10-01", now=dt(2026, 9, 23, 17, 30)) == "2026-09-23"


class TestDownloadOneSemantics:
    """空窗口→跳过（不再回落限频的 Tushare）；异常→失败"""

    def test_empty_window_returns_skip(self, monkeypatch):
        dl = HKDownloader.__new__(HKDownloader)
        dl._pro = None
        monkeypatch.setattr(dl, "_download_stock_ak", lambda c, s, e: pd.DataFrame())
        assert dl._download_one("00700", "2026-09-23", "2026-09-23") == "skip"

    def test_source_error_returns_fail(self, monkeypatch):
        dl = HKDownloader.__new__(HKDownloader)
        dl._pro = None
        monkeypatch.setattr(dl, "_download_stock_ak", lambda c, s, e: None)
        assert dl._download_one("00700", "2026-09-23", "2026-09-23") == "fail"

    def test_counts_skip_status(self, monkeypatch):
        dl = HKDownloader.__new__(HKDownloader)
        monkeypatch.setattr(dl, "_download_one_timed",
                            lambda code, s, e, force=False: "skip")
        monkeypatch.setattr(dhk.time, "sleep", lambda *_: None)
        res = dl._batch_download([("00700", "腾讯控股")], end="2026-09-22",
                                 start_map={"00700": "2026-09-01"})
        assert res["skip"] == 1
        assert res["fail"] == 0


class TestHkMoneyflowParsing:
    """东财 fflow klines 解析"""

    def test_parse_fflow(self):
        line = ("2026-09-22,3208242096.0,-821992464.0,439920128.0,163809584.0,"
                "3044432512.0,9.97,-2.56,1.37,0.51,9.46,451.600,5.02,0.00,0.00")
        out = HKDownloader._parse_fflow([line])
        assert out is not None
        assert len(out) == 1
        assert out.iloc[0]["date"] == "2026-09-22"
        assert out.iloc[0]["main_net"] == 3208242096.0
        assert out.iloc[0]["close"] == 451.6

    def test_parse_fflow_bad(self):
        assert HKDownloader._parse_fflow([]) is None
        assert HKDownloader._parse_fflow(["short,line"]) is None


class TestHkFinancialsParsing:
    """东财港股报表长表 → 宽表；主要指标拆分"""

    def test_pivot_statement(self):
        raw = pd.DataFrame({
            "REPORT_DATE": ["2025-12-31", "2025-12-31", "2025-06-30"],
            "STD_ITEM_NAME": ["营业额", "净利润", "营业额"],
            "AMOUNT": [743689000000.0, 100.0, 300.0],
        })
        wide = HKDownloader._pivot_hk_statement(raw)
        assert wide is not None
        assert list(wide.index) == [pd.Timestamp("2025-06-30"),
                                    pd.Timestamp("2025-12-31")]
        assert wide.loc["2025-12-31", "营业额"] == 743689000000.0

    def test_pivot_statement_bad(self):
        assert HKDownloader._pivot_hk_statement(None) is None
        assert HKDownloader._pivot_hk_statement(pd.DataFrame({"x": [1]})) is None

    def test_split_indicator(self):
        ind = pd.DataFrame({
            "REPORT_DATE": ["2025-12-31"],
            "OPERATE_INCOME_YOY": [10.0],
            "GROSS_PROFIT_YOY": [5.0],
            "ROE_AVG": [12.0],
            "BPS": [100.0],
            "SECUCODE": ["00700.HK"],
        })
        growth, dupont = HKDownloader._split_hk_indicator(ind)
        assert list(growth.columns) == ["OPERATE_INCOME_YOY", "GROSS_PROFIT_YOY"]
        assert "ROE_AVG" in dupont.columns and "BPS" in dupont.columns


class TestHkDividendEvents:
    """分红派息 → E2 事件"""

    def test_build_events(self):
        df = pd.DataFrame({
            "最新公告日期": ["2026-05-13"],
            "财政年度": ["2025"],
            "分红方案": ["每股派港币5.3元"],
            "分配类型": ["年度分配"],
            "除净日": ["2026-05-15"],
            "截至过户日": ["2026/05/19-2026/05/20"],
            "发放日": ["2026-06-01"],
        })
        ev = HKDownloader._build_hk_dividend_events(df, "00700")
        assert len(ev) == 1
        e = ev[0]
        assert e["event_id"] == "hk_dividend_00700_20260513"
        assert e["event_type"] == "dividend"
        assert e["date"] == "2026-05-13"
        assert e["end_date"] == "2026-05-15"
        assert e["source"] == "akshare"
        assert e["detail"]["pay_date"] == "2026-06-01"

    def test_skip_missing_ann_date(self):
        df = pd.DataFrame({"最新公告日期": [None], "除净日": ["2026-05-15"]})
        assert HKDownloader._build_hk_dividend_events(df, "00700") == []
        assert HKDownloader._build_hk_dividend_events(pd.DataFrame(), "00700") == []


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

    def test_build_valuation_df_with_pcf(self):
        idx = pd.to_datetime(["2026-09-22"])
        pe = pd.DataFrame({"value": [15.0]}, index=idx)
        pb = pd.DataFrame({"value": [3.0]}, index=idx)
        pcf = pd.DataFrame({"value": [12.0]}, index=idx)
        out = HKDownloader._build_valuation_df(pe, pb, pcf)
        assert list(out.columns) == ["pe_ttm", "pb_mrq", "pcf_ttm"]
        assert out.iloc[0]["pcf_ttm"] == 12.0


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
