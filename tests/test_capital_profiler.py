"""
test_capital_profiler.py — 资金画像（Phase 1 个股级）单元测试

用 monkeypatch 隔离价格/龙虎榜/两融/事件等外部数据，覆盖：
已判定、S1(停牌)、S3(信号缺席)、S4(矛盾)、level 非 stock 占位、文本渲染。
"""
import sys
from pathlib import Path

import pandas as pd

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


class TestRender:
    def test_text(self, monkeypatch):
        eps = _episodes([("游资甲营业部", 2e8)])
        _patch(monkeypatch, eps=eps, feedback="strong_positive",
               label_map={"游资甲营业部": sma.LABEL_HOT})
        txt = cp.format_text(cp.profile("stock", "000001"))
        assert "个股资金画像" in txt
        assert "主导资金" in txt
