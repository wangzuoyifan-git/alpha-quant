"""
test_sector_aggregator.py — Phase 2 板块/ETF 聚合与画像单元测试（离线）
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.capital_profiler as cp
import agents.sector_aggregator as sa
import agents.smart_money_agent as sma
import data.store_fund as store_fund


@pytest.fixture(autouse=True)
def _clear_caches():
    sa._stock_sector_map.cache_clear()
    yield
    sa._stock_sector_map.cache_clear()


class TestMemberMapping:
    def test_sector_members(self, monkeypatch):
        df = pd.DataFrame({
            "ts_code": ["000001.SZ", "600000.SH", "000002.SZ"],
            "l1_code": ["801780.SI", "801780.SI", "801080.SI"],
            "l2_code": ["801781.SI", "801782.SI", "801081.SI"],
        })
        monkeypatch.setattr(sa, "load_meta", lambda t: df)
        sa._stock_sector_map.cache_clear()
        assert sa.sector_members("801780", "L1") == ["000001", "600000"]
        assert sa.stock_sector("600000", "L1") == "801780"

    def test_etf_members_fallback(self, monkeypatch):
        monkeypatch.setattr(store_fund, "load_etf_index_map", lambda: {})
        monkeypatch.setattr("agents.etf_agent.get_etf_meta",
                            lambda c: {"sw_l2_codes": ["801081.SI"]})
        monkeypatch.setattr("data.store_meta.get_member_stocks",
                            lambda codes, level="L2": ["000002", "000003"])
        m = sa.etf_members("512760")
        assert set(m) == {"000002", "000003"}
        assert sum(m.values()) == pytest.approx(1.0)


class TestAggregation:
    def test_aggregate_lhb(self, monkeypatch):
        eps = pd.DataFrame({"code": ["000001", "000001"],
                            "agency": ["机构专用", "某营业部"],
                            "net": [1e8, 2e7]})
        monkeypatch.setattr(sa, "load_seat_episodes", lambda start, end: eps)
        monkeypatch.setattr(sa, "classify_seat",
                            lambda a: {"label": sma.LABEL_INST if "机构" in a else sma.LABEL_BRANCH})
        r = sa.aggregate_lhb({"000001"}, "2026-01-01", "2026-09-01")
        assert r["institutional"] == pytest.approx(1e8)
        assert r["lhb_total"] == pytest.approx(1.2e8)

    def test_etf_flow(self, monkeypatch):
        idx = pd.date_range("2026-01-01", periods=3, freq="B")
        share = pd.DataFrame({"fd_share": [1e8, 1.1e8, 1.2e8]}, index=idx)
        nav = pd.DataFrame({"adj_nav": [2.0, 2.0, 2.0]}, index=idx)
        monkeypatch.setattr(store_fund, "load",
                            lambda dt, code=None, start=None, end=None:
                            {"share": share, "nav": nav}.get(dt))
        r = sa.etf_flow("510300", "2026-01-01", "2026-02-01")
        assert r["net_amount"] == pytest.approx((0.1e8 + 0.1e8) * 2.0)


class TestSectorProfile:
    def _patch(self, monkeypatch, lhb_inst=2e9):
        monkeypatch.setattr(sa, "sector_members", lambda c, level="L1": ["000001", "000002"])
        monkeypatch.setattr(sa, "sector_name", lambda c: "测试板块")
        monkeypatch.setattr(sa, "aggregate_lhb", lambda m, s, e: {
            "hot_money": 0, "quant": 0, "institutional": lhb_inst,
            "northbound": 0, "lhb_total": lhb_inst, "n": 10})
        monkeypatch.setattr(sa, "aggregate_margin", lambda m, s, e: {"change_rate": 0.0, "members": 2})
        monkeypatch.setattr(sa, "aggregate_block", lambda m, s, e: {"inst_net": None, "amount": 0, "n": 0})
        monkeypatch.setattr(sa, "aggregate_industrial", lambda m, s, e: {"amount": 0.0, "n": 0})
        monkeypatch.setattr(sa, "aggregate_northbound", lambda m, e: {"ratio_change": None, "members": 0})
        monkeypatch.setattr(sa, "etf_flow", lambda c, s, e: {"net_amount": None, "days": 0})
        idx = pd.date_range("2026-06-01", periods=60, freq="B")
        monkeypatch.setattr(cp, "_sector_price", lambda c, is_etf, date:
                            pd.DataFrame({"close": [10 + i * 0.1 for i in range(60)]}, index=idx))
        monkeypatch.setattr(cp, "_sector_feedback", lambda *a: "neutral")

    def test_resolved(self, monkeypatch):
        self._patch(monkeypatch, lhb_inst=2e9)
        r = cp.profile("sector_etf", "801780", lookback_days=60)
        assert r["meta"]["level"] == "sector_etf"
        assert r["answer"]["status"] == "resolved"
        assert any(k in r["answer"]["dominant_capital"] for k in ("机构", "龙虎榜"))
        assert r["structure_score"] is not None

    def test_undetermined_when_all_stable(self, monkeypatch):
        self._patch(monkeypatch, lhb_inst=1e6)  # 低于阈值
        r = cp.profile("sector_etf", "801780", lookback_days=60)
        assert r["answer"]["status"] == "undetermined"
        assert "缺席" in r["answer"]["undetermined_reason"] or "稳定" in r["answer"]["undetermined_reason"]

    def test_sector_macro_stub(self):
        r = cp.profile("sector_macro", "tech")
        assert r["answer"]["status"] == "undetermined"
        assert "Phase 3" in r["answer"]["undetermined_reason"]

    def test_fetch_triggers_on_demand(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(cp, "_ensure_etf_data", lambda code: called.__setitem__("n", called["n"] + 1))
        monkeypatch.setattr(sa, "etf_members", lambda c: {})
        cp.profile("sector_etf", "510300", fetch=True, lookback_days=60)
        assert called["n"] == 1
        called["n"] = 0
        cp.profile("sector_etf", "510300", fetch=False, lookback_days=60)
        assert called["n"] == 0
