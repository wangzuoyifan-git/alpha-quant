"""
test_downloader_events.py — events 下载器单元测试（离线）

覆盖 Tushare repurchase（回购）解析与日期区间过滤，
以及 E2 按日期批量下载（减持/回购/质押/分红）事件构造、分段、聚合与派发。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.downloader_events as de
import data.downloader_events as dev


class _FakePro:
    def repurchase(self, ts_code=None, start_date=None, end_date=None):
        return pd.DataFrame([
            {"ts_code": ts_code, "ann_date": "20260901", "end_date": "20260830",
             "proc": "完成", "vol": 1e6, "amount": 2e8, "high_limit": 10.0, "low_limit": 9.0},
            {"ts_code": ts_code, "ann_date": "20260101", "end_date": "20251230",
             "proc": "实施", "vol": 1e6, "amount": 1e8, "high_limit": 9.0, "low_limit": 8.0},
        ])


def test_fetch_repurchase_filters_range(monkeypatch):
    monkeypatch.setattr(de, "_get_pro", lambda: _FakePro())
    ev = de._fetch_repurchase("000001.SZ", "2026-08-01", "2026-09-30")
    assert len(ev) == 1
    e = ev[0]
    assert e["event_type"] == "buyback"
    assert e["code"] == "000001"
    assert e["date"] == "2026-09-01"
    assert e["detail"]["amount"] == 2e8
    assert e["detail"]["proc"] == "完成"


def test_fetch_repurchase_empty(monkeypatch):
    class _Empty:
        def repurchase(self, **k):
            return pd.DataFrame()
    monkeypatch.setattr(de, "_get_pro", lambda: _Empty())
    assert de._fetch_repurchase("000001.SZ", "2026-08-01", "2026-09-30") == []


def test_update_events_codes_normalize(monkeypatch):
    captured = {}
    monkeypatch.setattr(de, "build_calendar", lambda year=None: [])
    monkeypatch.setattr(de, "download_all_corp_events",
                        lambda codes=None, start=None, end=None: captured.update(codes=codes) or {})
    de.update_events(codes=["000001", "600519"])
    assert captured["codes"] == ["000001.SZ", "600519.SH"]


def _patch_no_throttle(monkeypatch):
    monkeypatch.setattr(dev, "_api_call", lambda fn, key, **kw: fn(**kw))


class _BatchPro:
    def __init__(self):
        self.calls = []

    def stk_holdertrade(self, start_date=None, end_date=None, ts_code=None):
        self.calls.append(("stk_holdertrade", ts_code, start_date, end_date))
        return pd.DataFrame({
            "ts_code": ["000001.SZ", "600519.SH"],
            "ann_date": ["20260901", "20260902"],
            "holder_name": ["张三", "李四"],
            "in_de": ["D", "I"],
            "change_vol": [100.0, 200.0],
        })

    def repurchase(self, start_date=None, end_date=None, ts_code=None):
        self.calls.append(("repurchase", ts_code, start_date, end_date))
        return pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "ann_date": ["20260901"],
            "end_date": ["20260910"],
            "proc": ["实施"],
            "vol": [1000.0],
        })

    def pledge_stat(self, ts_code=None, end_date=None):
        self.calls.append(("pledge_stat", ts_code, end_date))
        if end_date == "20260902":
            return pd.DataFrame({
                "ts_code": ["000001.SZ"],
                "end_date": ["20260902"],
                "pledge_count": [5],
                "pledge_ratio": [1.5],
            })
        return pd.DataFrame()

    def dividend(self, ts_code=None, ann_date=None):
        self.calls.append(("dividend", ts_code, ann_date))
        if ann_date == "20260901":
            return pd.DataFrame({
                "ts_code": ["600519.SH"],
                "ann_date": ["20260901"],
                "ex_date": ["20260905"],
                "cash_div": [1.0],
            })
        return pd.DataFrame()

    def trade_cal(self, **kw):
        return pd.DataFrame({"cal_date": ["20260901", "20260902", "20260903"]})


class TestEventBuilders:

    def test_reduce(self):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "600519.SH"],
            "ann_date": ["20260901", "20260902"],
            "holder_name": ["张三", "李四"],
            "in_de": ["D", "I"],
        })
        evs = dev._reduce_events_from_df(df)
        assert len(evs) == 2
        assert evs[0]["event_type"] == "reduce"
        assert evs[1]["event_type"] == "increase"
        assert evs[0]["code"] == "000001"
        assert evs[0]["event_id"] == "reduce_000001.SZ_20260901_张三"

    def test_repurchase_filters_dates(self):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "600519.SH"],
            "ann_date": ["20260901", "20261101"],
            "end_date": ["20260910", "20261110"],
            "proc": ["实施", "实施"],
        })
        evs = dev._repurchase_events_from_df(df, "2026-09-01", "2026-09-30")
        assert len(evs) == 1
        assert evs[0]["event_type"] == "buyback"
        assert evs[0]["date"] == "2026-09-01"

    def test_pledge(self):
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20260902"],
                           "pledge_count": [5], "pledge_ratio": [1.5]})
        evs = dev._pledge_events_from_df(df)
        assert len(evs) == 1
        assert evs[0]["event_type"] == "pledge"
        assert evs[0]["date"] == "2026-09-02"

    def test_dividend(self):
        df = pd.DataFrame({"ts_code": ["600519.SH"], "ann_date": ["20260901"],
                           "ex_date": ["20260905"], "cash_div": [1.0]})
        evs = dev._dividend_events_from_df(df)
        assert len(evs) == 1
        assert evs[0]["event_type"] == "dividend"
        assert evs[0]["end_date"] == "2026-09-05"

    def test_limit_per_code(self):
        evs = [{"code": "A", "date": f"2026-09-0{i}"} for i in range(1, 5)]
        evs += [{"code": "B", "date": "2026-09-01"}]
        out = dev._limit_per_code(evs, 2)
        assert len([e for e in out if e["code"] == "A"]) == 2
        assert len([e for e in out if e["code"] == "B"]) == 1


class TestSegments:

    def test_week_segments(self):
        segs = list(dev._week_segments("2026-09-01", "2026-09-20"))
        assert segs[0] == ("2026-09-01", "2026-09-07")
        assert segs[-1][1] == "2026-09-20"

    def test_month_segments(self):
        segs = list(dev._month_segments("2026-08-15", "2026-10-02"))
        assert segs == [("2026-08-15", "2026-08-31"),
                        ("2026-09-01", "2026-09-30"),
                        ("2026-10-01", "2026-10-02")]


class TestBatchCollector:

    def test_collect_and_group(self, monkeypatch):
        _patch_no_throttle(monkeypatch)
        by_code, stats = dev._collect_corp_events_batch(
            _BatchPro(), "2026-09-01", "2026-09-03",
            pledge_limit=26, dividend_limit=10, repurchase_limit=10)

        assert stats["fail"] == 0
        assert stats["reduce"] == 2
        assert stats["buyback"] == 1
        assert stats["pledge"] == 1
        assert stats["dividend"] == 1
        assert set(by_code) == {"000001", "600519"}
        assert any(e["event_type"] == "pledge" for e in by_code["000001"])


class TestDownloadDispatch:

    def test_batch_path(self, monkeypatch):
        _patch_no_throttle(monkeypatch)
        monkeypatch.setattr(dev, "_get_pro", lambda: _BatchPro())
        saved = {}
        monkeypatch.setattr("data.store_events.save_corp_events",
                            lambda code, evs: saved.__setitem__(code, evs))

        res = dev.download_all_corp_events(codes=None,
                                           start="2026-09-01", end="2026-09-03")
        assert res["reduce"] == 2
        assert set(saved) == {"000001", "600519"}

    def test_codes_path(self, monkeypatch):
        _patch_no_throttle(monkeypatch)
        pro = _BatchPro()
        monkeypatch.setattr(dev, "_get_pro", lambda: pro)
        saved = {}
        monkeypatch.setattr("data.store_events.save_corp_events",
                            lambda code, evs: saved.__setitem__(code, evs))

        res = dev.download_all_corp_events(codes=["000001.SZ"],
                                           start="2026-09-01", end="2026-09-03")
        keys = {c[0] for c in pro.calls}
        assert {"stk_holdertrade", "repurchase", "pledge_stat", "dividend"} <= keys
        assert res["reduce"] >= 1
        assert "000001" in saved
