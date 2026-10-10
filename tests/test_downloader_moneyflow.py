"""
test_downloader_moneyflow.py — moneyflow 下载器单元测试

覆盖按 trade_date 批量更新（stock/margin）：
  - 全市场 DataFrame 按 ts_code 拆分落盘（stock 带 code 列，margin 不带）
  - 仅拉取缺失交易日、已最新则跳过
  - _recent_floor 忽略长期陈旧文件
  - 本地无数据时转全量初始化
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.downloader_moneyflow as mfl
from data.downloader_moneyflow import MoneyflowDownloader


def _stock_df():
    return pd.DataFrame({
        "ts_code": ["000001.SZ", "600519.SH"],
        "trade_date": ["20261009", "20261009"],
        "buy_sm_amount": [1.0, 2.0],
        "net_mf_amount": [10.0, 20.0],
    })


def _margin_df():
    return pd.DataFrame({
        "trade_date": ["20261009", "20261009"],
        "ts_code": ["000001.SZ", "600519.SH"],
        "rzye": [100.0, 200.0],
    })


class _FakePro:
    def __init__(self, df):
        self._df = df
        self.calls = []

    def moneyflow(self, trade_date=None, **kw):
        self.calls.append(("moneyflow", trade_date))
        return self._df.copy()

    def margin_detail(self, trade_date=None, **kw):
        self.calls.append(("margin_detail", trade_date))
        return self._df.copy()


def _patch_common(monkeypatch, saved):
    monkeypatch.setattr(mfl, "_retry", lambda fn, **kw: fn())
    monkeypatch.setattr(mfl.time, "sleep", lambda *a, **k: None)

    def fake_save(data_type, df, code=None, date=None, market="cn"):
        saved.append({"data_type": data_type, "code": code, "df": df})

    monkeypatch.setattr(mfl.store, "save", fake_save)


class TestSaveGrouped:

    def test_stock_adds_code(self, monkeypatch):
        saved = []
        _patch_common(monkeypatch, saved)

        n = MoneyflowDownloader()._save_grouped(_stock_df(), "stock")
        assert n == 2
        assert {s["code"] for s in saved} == {"000001", "600519"}
        assert all(s["data_type"] == "stock" for s in saved)
        for s in saved:
            assert "code" in s["df"].columns
            assert s["df"]["code"].iloc[0] == s["code"]

    def test_margin_no_code_column(self, monkeypatch):
        saved = []
        _patch_common(monkeypatch, saved)

        n = MoneyflowDownloader()._save_grouped(_margin_df(), "margin")
        assert n == 2
        assert all("code" not in s["df"].columns for s in saved)


class TestRecentFloor:

    def test_ignores_stale(self):
        last = {"a": "2026-10-09", "b": "2026-04-01"}
        assert mfl._recent_floor(last, "2026-10-10") == "2026-10-09"

    def test_all_stale_returns_none(self):
        last = {"a": "2026-04-01", "b": "2026-06-01"}
        assert mfl._recent_floor(last, "2026-10-10") is None

    def test_picks_min_recent(self):
        last = {"a": "2026-10-09", "b": "2026-10-07", "c": "2026-09-01"}
        assert mfl._recent_floor(last, "2026-10-10") == "2026-10-07"


class TestUpdateStockByDate:

    def test_fetches_missing_dates(self, monkeypatch):
        saved = []
        _patch_common(monkeypatch, saved)
        pro = _FakePro(_stock_df())

        monkeypatch.setattr(mfl, "_latest_trade_date", lambda p: "2026-10-09")
        monkeypatch.setattr(mfl, "_preload_last_dates",
                            lambda d: {"000001": "2026-10-08", "600519": "2026-10-08"})
        monkeypatch.setattr(mfl, "_get_trade_dates",
                            lambda p, s, e: ["20261009"])

        dl = MoneyflowDownloader()
        dl._pro = pro
        dl._update_stock_by_date()

        assert pro.calls == [("moneyflow", "20261009")]
        assert {s["code"] for s in saved} == {"000001", "600519"}

    def test_skips_when_latest(self, monkeypatch):
        saved = []
        _patch_common(monkeypatch, saved)
        pro = _FakePro(_stock_df())

        monkeypatch.setattr(mfl, "_latest_trade_date", lambda p: "2026-10-09")
        monkeypatch.setattr(mfl, "_preload_last_dates",
                            lambda d: {"000001": "2026-10-09"})

        dl = MoneyflowDownloader()
        dl._pro = pro
        dl._update_stock_by_date()

        assert pro.calls == []
        assert saved == []

    def test_no_local_data_delegates_to_init(self, monkeypatch):
        saved = []
        _patch_common(monkeypatch, saved)
        called = {"init": 0}

        monkeypatch.setattr(mfl, "_latest_trade_date", lambda p: "2026-10-09")
        monkeypatch.setattr(mfl, "_preload_last_dates", lambda d: {})

        dl = MoneyflowDownloader()
        dl._pro = _FakePro(_stock_df())
        monkeypatch.setattr(dl, "init_stock",
                            lambda *a, **k: called.__setitem__("init", 1))
        dl._update_stock_by_date()

        assert called["init"] == 1

    def test_multiple_dates(self, monkeypatch):
        saved = []
        _patch_common(monkeypatch, saved)
        pro = _FakePro(_stock_df())

        monkeypatch.setattr(mfl, "_latest_trade_date", lambda p: "2026-10-09")
        monkeypatch.setattr(mfl, "_preload_last_dates",
                            lambda d: {"000001": "2026-10-07"})
        monkeypatch.setattr(mfl, "_get_trade_dates",
                            lambda p, s, e: ["20261008", "20261009"])

        dl = MoneyflowDownloader()
        dl._pro = pro
        dl._update_stock_by_date()

        assert pro.calls == [("moneyflow", "20261008"), ("moneyflow", "20261009")]
        assert len(saved) == 4


class TestUpdateMarginByDate:

    def test_fetches_missing_dates(self, monkeypatch):
        saved = []
        _patch_common(monkeypatch, saved)
        pro = _FakePro(_margin_df())

        monkeypatch.setattr(mfl, "_latest_trade_date", lambda p: "2026-10-09")
        monkeypatch.setattr(mfl, "_preload_last_dates",
                            lambda d: {"000001": "2026-10-08"})
        monkeypatch.setattr(mfl, "_get_trade_dates",
                            lambda p, s, e: ["20261009"])

        dl = MoneyflowDownloader()
        dl._pro = pro
        dl._update_margin_by_date()

        assert pro.calls == [("margin_detail", "20261009")]
        assert all(s["data_type"] == "margin" for s in saved)
        assert all("code" not in s["df"].columns for s in saved)


class TestLatestTradeDate:

    def test_picks_max(self, monkeypatch):
        monkeypatch.setattr(mfl, "_get_trade_dates",
                            lambda p, s, e: ["20261008", "20261009"])
        assert mfl._latest_trade_date(None) == "2026-10-09"

    def test_none_when_empty(self, monkeypatch):
        monkeypatch.setattr(mfl, "_get_trade_dates", lambda p, s, e: [])
        assert mfl._latest_trade_date(None) is None
