"""
test_capital_profiler.py — 资金画像（Phase 1 个股级）单元测试

用 monkeypatch 隔离价格/龙虎榜/两融/事件等外部数据，覆盖：
已判定、S1(停牌)、S3(信号缺席)、S4(矛盾)、level 非 stock 占位、文本渲染。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.capital_profiler as cp
import agents.smart_money_agent as sma


def _prices(n=60):
    idx = pd.date_range("2026-06-01", periods=n, freq="B")
    return pd.DataFrame({"close": [10 + i * 0.05 for i in range(n)]}, index=idx)


def _episodes(labels_net):
    """labels_net: list of (agency, net)。"""
    rows = []
    for a, net in labels_net:
        rows.append({"date": "2026-09-20", "code": "000001", "agency": a,
                     "buy": max(net, 0) + 1e7, "sell": max(-net, 0) + 1e7,
                     "net": net, "reasons": ["涨幅"]})
    return pd.DataFrame(rows)


def _patch(monkeypatch, prices=None, eps=None, margin_rate=0.0, feedback="neutral",
           industrial=(0.0, False, False), label_map=None):
    monkeypatch.setattr(cp.store, "load",
                        lambda code, asset_type="stock", market="cn", end=None:
                        (prices if prices is not None else _prices()))
    monkeypatch.setattr(cp, "load_seat_episodes", lambda **k: (eps if eps is not None else pd.DataFrame()))
    monkeypatch.setattr(cp, "classify_seat",
                        lambda a: {"label": (label_map or {}).get(a, sma.LABEL_BRANCH)})
    monkeypatch.setattr(cp, "_detect_feedback", lambda code, p: feedback)
    monkeypatch.setattr(cp, "_industrial", lambda *a: industrial)

    def _load(dt, code=None, start=None, end=None, **k):
        if dt == "margin":
            idx = pd.date_range("2026-06-01", periods=3, freq="B")
            base = 1e9
            vals = [base, base, base * (1 + margin_rate)]
            return pd.DataFrame({"rzye": vals}, index=idx)
        if dt == "stock":
            return None  # 反馈已 mock
        if dt == "hk_hold":
            return None
        return None
    monkeypatch.setattr(cp.store_mf, "load", _load)


class TestResolved:
    def test_hot_money_dominant(self, monkeypatch):
        eps = _episodes([("游资甲营业部", 2e8)])
        _patch(monkeypatch, eps=eps, feedback="strong_positive",
               label_map={"游资甲营业部": sma.LABEL_HOT})
        r = cp.profile("stock", "000001", lookback_days=60)
        assert r["answer"]["status"] == "resolved"
        assert "游资" in r["answer"]["dominant_capital"]
        assert r["answer"]["persistence"] == "极弱（1日）"
        assert r["structure_score"] is not None
        assert r["capital_details"]["hot_money"]["direction"] == "buy"

    def test_margin_dominant(self, monkeypatch):
        _patch(monkeypatch, eps=pd.DataFrame(), margin_rate=-0.09)
        r = cp.profile("stock", "000001")
        assert r["answer"]["status"] == "resolved"
        assert "两融" in r["answer"]["dominant_capital"]


class TestUndetermined:
    def test_s1_no_price(self, monkeypatch):
        _patch(monkeypatch, prices=pd.DataFrame())
        r = cp.profile("stock", "000001")
        assert r["answer"]["status"] == "undetermined"
        assert "停牌" in r["answer"]["undetermined_reason"]

    def test_s3_all_absent(self, monkeypatch):
        _patch(monkeypatch, eps=pd.DataFrame(), margin_rate=0.0)
        r = cp.profile("stock", "000001")
        assert r["answer"]["status"] == "undetermined"
        assert "缺席" in r["answer"]["undetermined_reason"] or "稳定" in r["answer"]["undetermined_reason"]

    def test_s4_conflict(self, monkeypatch):
        eps = _episodes([("游资甲营业部", 2e8)])
        _patch(monkeypatch, eps=eps, margin_rate=-0.09,
               label_map={"游资甲营业部": sma.LABEL_HOT})
        r = cp.profile("stock", "000001")
        assert r["answer"]["status"] == "undetermined"
        assert "矛盾" in r["answer"]["undetermined_reason"]

    def test_level_stub(self):
        r = cp.profile("sector_etf", "512760")
        assert r["answer"]["status"] == "undetermined"
        assert "尚未实现" in r["answer"]["undetermined_reason"]


class TestFeedback:
    def _prices_and_flow(self):
        idx = pd.date_range("2026-01-01", periods=30, freq="B")
        closes, base = [], 10.0
        for i in range(30):
            base *= (1 + (0.01 if i % 2 == 0 else -0.01))
            closes.append(base)
        price = pd.DataFrame({"close": closes}, index=idx)
        ret = pd.Series(closes, index=idx).pct_change()
        flow = pd.Series(np.where(ret > 0, 1000.0, -1000.0), index=idx)
        return price, flow

    def test_prefers_lg_elg(self, monkeypatch):
        price, flow = self._prices_and_flow()
        mf = pd.DataFrame({
            "buy_lg_amount": flow.clip(lower=0), "buy_elg_amount": 0.0,
            "sell_lg_amount": (-flow).clip(lower=0), "sell_elg_amount": 0.0,
            "net_mf_amount": -flow,  # 与主力口径相反：若误用 net_mf 会得到负相关
        })
        monkeypatch.setattr(cp.store_mf, "load", lambda *a, **k: mf)
        assert cp._detect_feedback("000001", price) == "strong_positive"

    def test_fallback_net_mf(self, monkeypatch):
        price, flow = self._prices_and_flow()
        mf = pd.DataFrame({"net_mf_amount": flow})  # 无 lg/elg 列
        monkeypatch.setattr(cp.store_mf, "load", lambda *a, **k: mf)
        assert cp._detect_feedback("000001", price) == "strong_positive"


class TestIndustrial:
    def test_buyback_and_increase(self, monkeypatch):
        import data.store_events as se
        monkeypatch.setattr(se, "load_corp_events", lambda code: [
            {"event_type": "buyback", "date": "2026-09-01",
             "detail": {"amount": 2e8}},
            {"event_type": "increase", "date": "2026-09-02",
             "detail": {"change_vol": 1e6, "avg_price": 50.0}},
        ])
        amount, inc, red = cp._industrial("000001", "2026-08-01", "2026-09-30")
        assert inc and not red and amount == pytest.approx(2e8 + 5e7)

    def test_reduce_only(self, monkeypatch):
        import data.store_events as se
        monkeypatch.setattr(se, "load_corp_events", lambda code: [
            {"event_type": "reduce", "date": "2026-09-01", "detail": {}}])
        _, inc, red = cp._industrial("000001", "2026-08-01", "2026-09-30")
        assert red and not inc


class TestMainFlowCross:
    def test_main_flow_daily_streak(self):
        idx = pd.date_range("2026-01-01", periods=5, freq="B")
        d = cp._main_flow_daily(pd.Series([1, 2, 3, 4, 5.0], index=idx))
        assert d["available"] and d["streak_days"] == 5 and d["streak_dir"] == "inflow"
        d2 = cp._main_flow_daily(pd.Series([1, -1, 2, 3, 4.0], index=idx))
        assert d2["streak_days"] == 3 and d2["streak_dir"] == "inflow"

    def test_cross_verdicts(self):
        details = {"hot_money": {"net_buy": 1e8}, "quant": {"net_buy": None},
                   "institutional": {"net_buy": None}, "northbound": {"net_buy": None}}
        assert cp._cross_seat_main(details, {"available": True, "sum_net_wan": 5000})["verdict"] == "合力向上"
        v = cp._cross_seat_main(details, {"available": True, "sum_net_wan": -5000})["verdict"]
        assert "对倒" in v
        assert cp._cross_seat_main({"hot_money": {"net_buy": None}},
                                   {"available": True, "sum_net_wan": 1}) is None

    def test_hidden_main_toggle(self, monkeypatch):
        flow = pd.Series([1e6] * 60, index=pd.date_range("2026-06-01", periods=60, freq="B"))
        monkeypatch.setattr(cp, "_main_flow_series", lambda code, p: flow.reindex(p.index))
        _patch(monkeypatch, eps=pd.DataFrame(), margin_rate=0.0)
        r1 = cp.profile("stock", "000001", allow_hidden_main=False)
        assert r1["answer"]["status"] == "undetermined"
        r2 = cp.profile("stock", "000001", allow_hidden_main=True)
        assert r2["answer"]["status"] == "resolved"
        assert "隐性主力" in r2["answer"]["dominant_capital"]
        assert "日频主力" in cp.format_text(r2)


class TestRender:
    def test_text(self, monkeypatch):
        eps = _episodes([("游资甲营业部", 2e8)])
        _patch(monkeypatch, eps=eps, feedback="strong_positive",
               label_map={"游资甲营业部": sma.LABEL_HOT})
        txt = cp.format_text(cp.profile("stock", "000001"))
        assert "个股资金画像" in txt
        assert "主导资金" in txt
