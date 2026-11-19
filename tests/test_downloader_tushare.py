"""
test_downloader_tushare.py — Tushare 下载器维护逻辑单测

覆盖：ETF fund_adj 后复权、现有复权基准缩放、代码后缀、ETF 存量修复。
"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store as store
from data.downloader_tushare import (
    TushareKlineDownloader,
    _apply_fund_adj,
    _etf_ts_code,
    _index_ts_code,
    _rescale_price_cols,
)


@pytest.fixture
def temp_market_dir(tmp_path, monkeypatch):
    original = store.BASE_DIR
    store.BASE_DIR = tmp_path / "market"
    store.BASE_DIR.mkdir(parents=True, exist_ok=True)
    store.IPO_FILE = store.BASE_DIR / "ipo_dates.json"
    for market, atypes in store.MARKET_ASSET_TYPES.items():
        for atype in atypes:
            (store.BASE_DIR / market / atype).mkdir(parents=True, exist_ok=True)
    yield store.BASE_DIR
    store.BASE_DIR = original
    store.IPO_FILE = original / "ipo_dates.json"


def _factors():
    return pd.Series(
        [1.0, 1.0, 2.0],
        index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
    )


class TestCodeSuffix:

    def test_etf_suffix(self):
        assert _etf_ts_code("510300") == "510300.SH"
        assert _etf_ts_code("159516") == "159516.SZ"

    def test_index_suffix(self):
        assert _index_ts_code("000300") == "000300.SH"
        assert _index_ts_code("399006") == "399006.SZ"


class TestFundAdj:

    def test_split_is_continuous(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "open": [10.0, 10.0, 5.0],
            "high": [10.0, 10.0, 5.0],
            "low": [10.0, 10.0, 5.0],
            "close": [10.0, 10.0, 5.0],
            "preclose": [10.0, 10.0, 10.0],
        })
        out = _apply_fund_adj(df, _factors(), latest=2.0)
        assert out["close"].tolist() == pytest.approx([5.0, 5.0, 5.0])
        assert out["adj_factor"].tolist() == pytest.approx([1.0, 1.0, 2.0])

    def test_missing_factor_filled(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-04", "2026-01-07"]),
            "close": [10.0, 5.0],
            "preclose": [10.0, 10.0],
        })
        out = _apply_fund_adj(df, _factors(), latest=2.0)
        assert out["close"].tolist() == pytest.approx([5.0, 5.0])

    def test_rescale_to_new_latest(self):
        df = pd.DataFrame({"open": [5.0], "close": [5.0], "preclose": [5.0]})
        out = _rescale_price_cols(df, 1.0 / 2.0)
        assert out["close"].iloc[0] == pytest.approx(2.5)


class FakePro:

    def __init__(self, factors):
        self._factors = factors

    def fund_adj(self, ts_code):
        return pd.DataFrame({
            "ts_code": ts_code,
            "trade_date": self._factors.index.strftime("%Y%m%d"),
            "adj_factor": self._factors.to_numpy(),
        })


class TestRepairEtf:

    def test_raw_history_is_adjusted(self, temp_market_dir):
        idx = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        raw = pd.DataFrame({
            "open": [10.0, 10.0, 5.0], "high": [10.0, 10.0, 5.0],
            "low": [10.0, 10.0, 5.0], "close": [10.0, 10.0, 5.0],
            "preclose": [10.0, 10.0, 10.0], "volume": [1, 1, 1],
            "amount": [1, 1, 1], "pct_chg": [0.0, 0.0, -50.0],
        }, index=idx)
        store.replace("159516", raw, asset_type="etf", market="cn")

        dl = TushareKlineDownloader.__new__(TushareKlineDownloader)
        dl._pro = FakePro(_factors())
        res = dl.repair_etf_adjust(codes=["159516"], sleep=0)

        assert res["ok"] == 1
        out = store.load("159516", asset_type="etf", market="cn")
        assert out["close"].tolist() == pytest.approx([5.0, 5.0, 5.0])
        assert out["adj_factor"].tolist() == pytest.approx([1.0, 1.0, 2.0])

    def test_already_adjusted_same_latest_is_noop(self, temp_market_dir):
        idx = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
        adjusted = pd.DataFrame({
            "open": [5.0, 5.0, 5.0], "high": [5.0, 5.0, 5.0],
            "low": [5.0, 5.0, 5.0], "close": [5.0, 5.0, 5.0],
            "preclose": [5.0, 5.0, 5.0], "volume": [1, 1, 1],
            "amount": [1, 1, 1], "pct_chg": [0.0, 0.0, 0.0],
            "adj_factor": [1.0, 1.0, 2.0],
        }, index=idx)
        store.replace("159516", adjusted, asset_type="etf", market="cn")

        dl = TushareKlineDownloader.__new__(TushareKlineDownloader)
        dl._pro = FakePro(_factors())
        res = dl.repair_etf_adjust(codes=["159516"], sleep=0)

        assert res["ok"] == 1
        out = store.load("159516", asset_type="etf", market="cn")
        assert out["close"].tolist() == pytest.approx([5.0, 5.0, 5.0])


class TestTushareEmptyGuard:
    """Tushare HTTP 异常时静默返回空表，必须重试并最终报错，不能当成 0 更新。"""

    class EmptyPro:
        def stock_basic(self, **kwargs):
            return pd.DataFrame()

    class RecoveringPro:
        def __init__(self):
            self.calls = 0

        def stock_basic(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                return pd.DataFrame()
            return pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"]})

    def test_call_nonempty_retries_then_succeeds(self, monkeypatch):
        import data.tushare_utils as tu

        monkeypatch.setattr(tu.time, "sleep", lambda *_: None)
        pro = self.RecoveringPro()
        df = tu.call_nonempty(pro.stock_basic, label="stock_basic")
        assert len(df) == 1
        assert pro.calls == 3

    def test_empty_stock_list_raises(self, monkeypatch):
        import data.tushare_utils as tu
        from data.tushare_utils import TushareEmptyError

        monkeypatch.setattr(tu.time, "sleep", lambda *_: None)
        dl = TushareKlineDownloader.__new__(TushareKlineDownloader)
        dl._pro = self.EmptyPro()

        with pytest.raises(TushareEmptyError):
            dl._get_security_list(["stock"])


class TestSettledEnd:
    class CalPro:
        def trade_cal(self, **kwargs):
            return pd.DataFrame({"cal_date": ["20261118"]})

    def _dl(self):
        dl = TushareKlineDownloader.__new__(TushareKlineDownloader)
        dl._pro = self.CalPro()
        return dl

    def test_past_end_untouched(self):
        assert self._dl()._settled_end("2026-11-17") == "2026-11-17"

    def test_before_settle_rolls_back(self, monkeypatch):
        import data.downloader_tushare as dlt

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 19, 10, 0)

        monkeypatch.setattr(dlt, "datetime", FakeDatetime)
        assert self._dl()._settled_end("2026-11-19") == "2026-11-18"

    def test_after_settle_keeps_today(self, monkeypatch):
        import data.downloader_tushare as dlt

        class FakeDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 19, 17, 30)

        monkeypatch.setattr(dlt, "datetime", FakeDatetime)
        assert self._dl()._settled_end("2026-11-19") == "2026-11-19"


class TestRefreshRecent:
    def test_force_and_range(self, monkeypatch):
        dl = TushareKlineDownloader.__new__(TushareKlineDownloader)
        calls = []
        monkeypatch.setattr(dl, "_get_security_list",
                            lambda types: {"stock": [("000001", "平安")]})
        monkeypatch.setattr(dl, "_settled_end", lambda end: "2026-11-19")
        monkeypatch.setattr(dl, "_save_ipo_dates", lambda: None)
        monkeypatch.setattr(
            dl, "_batch_download",
            lambda secs, atype, **kw: (calls.append(kw) or
                                       {"ok": 1, "skip": 0, "fail": 0, "total": 1}))
        monkeypatch.setattr(dl, "_print_stats", lambda r: r)

        dl.refresh_recent(days=3, types=["stock"], codes=["000001"])
        assert calls[0]["force"] is True
        assert calls[0]["start"] == "2026-11-17"
        assert calls[0]["end"] == "2026-11-19"
