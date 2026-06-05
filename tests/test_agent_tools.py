"""
tests/test_agent_tools.py — Tool Layer 单元测试

聚焦本次新增/修复的 data_tools 工具，不走网络：
  - get_active_event_windows：E1 窗口期查询（AC1 dict 返回）
  - check_reduce_window：RC7 减持窗口期
  - update_domain：非法域校验 + 各域路由（mock downloader）
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import data.store_events as se
from agent_tools import (
    get_active_event_windows,
    check_reduce_window,
    update_domain,
    query_kline,
    validate_data,
    get_pending_orders,
    cancel_order,
    confirm_order,
)


@pytest.fixture
def tmp_events(tmp_path, monkeypatch):
    """把 store_events 的存储目录重定向到临时目录，隔离真实数据"""
    base = tmp_path / "events"
    corp = base / "corporate"
    shock = base / "market_shock"
    for d in (base, corp, shock):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(se, "BASE_DIR", base)
    monkeypatch.setattr(se, "E1_CALENDAR_FILE", base / "calendar.json")
    monkeypatch.setattr(se, "E2_CORP_DIR", corp)
    monkeypatch.setattr(se, "E3_SHOCK_DIR", shock)
    return base


# ─────────────────────────────────────────────────────────────
# get_active_event_windows
# ─────────────────────────────────────────────────────────────

class TestActiveEventWindows:

    def test_returns_active_window_with_days_to_event(self, tmp_events):
        se.save_event_calendar([{
            "event_id": "fomc_test",
            "event_type": "macro",
            "event_name": "FOMC议息",
            "date": "2026-05-20",
            "end_date": "2026-05-21",
            "impact_scope": "global",
            "pre_window": 5,
            "post_window": 3,
        }])
        # 2026-05-18 处于 pre_window(5天) 内
        r = get_active_event_windows("2026-05-18")
        assert r["success"] is True
        assert r["date"] == "2026-05-18"
        assert len(r["windows"]) == 1
        w = r["windows"][0]
        assert w["event_name"] == "FOMC议息"
        assert w["event_type"] == "macro"
        assert w["impact_scope"] == "global"
        # 距事件 2 天（05-20 - 05-18）
        assert w["days_to_event"] == 2

    def test_empty_outside_window(self, tmp_events):
        se.save_event_calendar([{
            "event_id": "earnings_test",
            "event_type": "earnings",
            "event_name": "年报窗口",
            "date": "2026-04-01",
            "end_date": "2026-04-30",
            "impact_scope": "cn",
            "pre_window": 0,
            "post_window": 0,
        }])
        r = get_active_event_windows("2026-06-01")
        assert r["success"] is True
        assert r["windows"] == []

    def test_no_calendar_returns_empty(self, tmp_events):
        r = get_active_event_windows("2026-05-18")
        assert r["success"] is True
        assert r["windows"] == []


# ─────────────────────────────────────────────────────────────
# check_reduce_window (RC7)
# ─────────────────────────────────────────────────────────────

class TestReduceWindow:

    def test_in_reduce_window_with_detail(self, tmp_events):
        se.save_corp_events("000001", [{
            "event_id": "reduce_000001_20260510",
            "code": "000001",
            "event_type": "reduce",
            "date": "2026-05-10",
            "end_date": "2026-05-30",
            "detail": {"holder_name": "大股东A"},
        }])
        r = check_reduce_window("000001", "2026-05-15")
        assert r["success"] is True
        assert r["in_window"] is True
        assert r["detail"]["holder"] == "大股东A"
        assert r["detail"]["end_date"] == "2026-05-30"

    def test_not_in_window(self, tmp_events):
        se.save_corp_events("000001", [{
            "event_id": "reduce_000001_20260510",
            "code": "000001",
            "event_type": "reduce",
            "date": "2026-05-10",
            "end_date": "2026-05-30",
            "detail": {"holder_name": "大股东A"},
        }])
        r = check_reduce_window("000001", "2026-06-15")
        assert r["success"] is True
        assert r["in_window"] is False
        assert r["detail"] == {}

    def test_no_events_returns_false(self, tmp_events):
        r = check_reduce_window("999999", "2026-05-15")
        assert r["success"] is True
        assert r["in_window"] is False


# ─────────────────────────────────────────────────────────────
# update_domain
# ─────────────────────────────────────────────────────────────

class TestUpdateDomain:

    def test_unknown_domain_rejected(self):
        r = update_domain("not_a_domain")
        assert r["success"] is False
        assert "未知域" in r["error"]

    def test_meta_domain_routes_to_downloader(self):
        with mock.patch("data.downloader_meta.MetaDownloader") as MD:
            r = update_domain("meta")
        assert r["success"] is True
        assert r["domain"] == "meta"
        assert "end_date" in r
        MD.return_value.update_db.assert_called_once()

    def test_events_domain_routes(self):
        with mock.patch("data.downloader_events.update_events") as ue:
            r = update_domain("events")
        assert r["success"] is True
        assert r["domain"] == "events"
        ue.assert_called_once()

    def test_downloader_exception_captured(self):
        with mock.patch("data.downloader_macro.MacroDownloader",
                        side_effect=RuntimeError("boom")):
            r = update_domain("macro")
        assert r["success"] is False
        assert "boom" in r["error"]


# ─────────────────────────────────────────────────────────────
# query_kline
# ─────────────────────────────────────────────────────────────

class TestQueryKline:

    def test_returns_summary_with_sample(self):
        import pandas as pd
        idx = pd.to_datetime(["2026-05-18", "2026-05-19", "2026-05-20"])
        df = pd.DataFrame(
            {"open": [10, 11, 12], "close": [10.5, 11.5, 12.5],
             "volume": [100, 200, 300]}, index=idx)
        with mock.patch("data.store.load", return_value=df):
            r = query_kline("000001", "2026-05-01", "2026-05-31")
        assert r["success"] is True
        assert r["rows"] == 3
        assert r["start"] == "2026-05-18"
        assert r["end"] == "2026-05-20"
        assert "close" in r["columns"]
        assert len(r["sample"]) == 3
        assert r["sample"][-1]["close"] == 12.5

    def test_no_data(self):
        with mock.patch("data.store.load", return_value=None):
            r = query_kline("999999")
        assert r["success"] is False
        assert r["rows"] == 0


# ─────────────────────────────────────────────────────────────
# validate_data
# ─────────────────────────────────────────────────────────────

class TestValidateData:

    def test_non_market_domain_rejected(self):
        r = validate_data("fundamental")
        assert r["success"] is False

    def test_market_validation_flags_empty(self):
        with mock.patch("data.store.load", return_value=None):
            r = validate_data("market", sample_codes=["000001"])
        assert r["success"] is True
        assert r["checked"] == 0
        assert any("无数据" in i["issue"] for i in r["issues"])

    def test_market_validation_clean(self):
        import pandas as pd
        idx = pd.to_datetime(["2026-05-18", "2026-05-19"])
        df = pd.DataFrame(
            {"open": [10, 11], "high": [10.6, 11.6], "low": [9.9, 10.9],
             "close": [10.5, 11.5], "preclose": [10.0, 10.5],
             "volume": [100, 200]}, index=idx)
        with mock.patch("data.store.load", return_value=df):
            r = validate_data("market", sample_codes=["000001"])
        assert r["success"] is True
        assert r["checked"] == 1


# ─────────────────────────────────────────────────────────────
# trade_tools: get_pending_orders / cancel_order / confirm_order
# ─────────────────────────────────────────────────────────────

class TestTradeOrderTools:

    def test_get_pending_orders(self):
        with mock.patch("agent_tools.trade_tools.ta.list_pending_orders",
                        return_value=[{"order_id": "X1"}, {"order_id": "X2"}]):
            r = get_pending_orders()
        assert r["success"] is True
        assert r["count"] == 2

    def test_cancel_order(self):
        with mock.patch("agent_tools.trade_tools.ta.cancel_order",
                        return_value=True) as cx:
            r = cancel_order("X1", reason="风控否决")
        assert r["success"] is True
        assert r["order_id"] == "X1"
        assert r["reason"] == "风控否决"
        cx.assert_called_once_with("X1")

    def test_confirm_order_missing(self):
        with mock.patch("agent_tools.trade_tools.ta.confirm_order",
                        return_value=False):
            r = confirm_order("nope")
        assert r["success"] is False
        assert r["order_id"] == "nope"
