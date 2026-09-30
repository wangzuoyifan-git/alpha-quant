"""
test_downloader_events.py — events 下载器单元测试（离线）

覆盖 Tushare repurchase（回购）解析与日期区间过滤。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.downloader_events as de


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
