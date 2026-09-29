"""
test_smart_money.py — 资金主体识别与跨期画像单元测试

覆盖 agents/smart_money_agent.py 三大支柱与 archive，以及 agent_tools/smart_money_tools.py。
数据依赖（top_inst/hk_hold/margin/block_trade/K线）全部用 monkeypatch + 合成数据隔离，
不联网。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.smart_money_agent as sma
import data.store_moneyflow as store_mf


@pytest.fixture(autouse=True)
def _disable_seat_dictionary(monkeypatch):
    """隔离已构建的席位字典，避免 classify_seat 规则单测被真实字典影响。"""
    monkeypatch.setattr(sma, "_dict_lookup", lambda agency: None)

# ══════════════════════════════════════════════════════════════
# 测试替身
# ══════════════════════════════════════════════════════════════

class FakePrices:
    """价格替身：返回固定收益，并记录调用。"""

    def __init__(self, fwd=None, hold_days=7, window_ret=0.05):
        self.fwd = fwd or {5: 0.03, 10: -0.02, 20: 0.10}
        self.hold_days = hold_days
        self.window_ret = window_ret

    def forward_return(self, code, date, horizon):
        return self.fwd.get(horizon)

    def trading_days_between(self, code, d1, d2):
        return self.hold_days

    def window_return(self, code, start, end):
        return self.window_ret


def _top_inst_shards(rows: pd.DataFrame) -> dict:
    """把扁平 top_inst 记录按日期拆成 {date: df(index=date)}。"""
    shards = {}
    for d, g in rows.groupby("date"):
        df = g.drop(columns=["date"]).copy()
        df.index = pd.to_datetime([d] * len(df))
        df.index.name = "date"
        shards[str(d)] = df
    return shards


@pytest.fixture
def fake_store(monkeypatch):
    """
    安装假 moneyflow store。返回可写入数据的句柄：
    handle.top_inst = DataFrame(扁平)；handle.hk_hold = {code: df}；
    handle.margin = {code: df}；handle.block_trade = DataFrame(扁平)
    """
    handle = type("H", (), {})()
    handle.top_inst = pd.DataFrame(columns=["date", "ts_code", "exalter", "buy", "sell", "reason", "side"])
    handle.hk_hold = {}
    handle.margin = {}
    handle.block_trade = pd.DataFrame(columns=["date", "ts_code", "price", "vol", "amount", "buyer", "seller"])

    def list_dates(dt, **kw):
        if dt == "top_inst":
            return sorted(handle.top_inst["date"].unique()) if not handle.top_inst.empty else []
        if dt == "block_trade":
            return sorted(handle.block_trade["date"].unique()) if not handle.block_trade.empty else []
        return []

    def load(dt, code=None, date=None, start=None, end=None, **kw):
        if dt == "top_inst":
            if handle.top_inst.empty:
                return None
            shards = _top_inst_shards(handle.top_inst)
            return shards.get(date)
        if dt == "block_trade":
            if handle.block_trade.empty:
                return None
            d = handle.block_trade[handle.block_trade["date"] == date]
            if d.empty:
                return None
            out = d.drop(columns=["date"]).copy()
            out.index = pd.to_datetime([date] * len(out))
            out.index.name = "date"
            return out
        if dt == "hk_hold":
            df = handle.hk_hold.get(code)
            if df is None:
                return None
            if start:
                df = df[df.index >= pd.Timestamp(start)]
            if end:
                df = df[df.index <= pd.Timestamp(end)]
            return df if not df.empty else None
        if dt == "margin":
            df = handle.margin.get(code)
            if df is None:
                return None
            if start:
                df = df[df.index >= pd.Timestamp(start)]
            if end:
                df = df[df.index <= pd.Timestamp(end)]
            return df if not df.empty else None
        return None

    monkeypatch.setattr(store_mf, "list_dates", list_dates)
    monkeypatch.setattr(store_mf, "load", load)
    return handle


# ══════════════════════════════════════════════════════════════
# 席位身份
# ══════════════════════════════════════════════════════════════

class TestClassifySeat:
    def test_institution(self):
        assert sma.classify_seat("机构专用")["label"] == sma.LABEL_INST

    def test_northbound_channel(self):
        assert sma.classify_seat("深股通专用")["label"] == sma.LABEL_NORTH
        assert sma.classify_seat("沪股通专用")["label"] == sma.LABEL_NORTH

    def test_branch_default(self):
        info = sma.classify_seat("华泰证券股份有限公司南京民智路证券营业部")
        assert info["label"] == sma.LABEL_BRANCH
        assert info["label_basis"] == "规则匹配"

    def test_quant_alias_is_inference(self):
        info = sma.classify_seat("东方财富证券股份有限公司拉萨东环路第二证券营业部")
        assert info["label"] == sma.LABEL_QUANT
        assert info["label_basis"] == "推断"

    def test_aggregate_rows(self):
        assert "个人投资者" in sma.classify_seat("自然人")["label"]
        assert "机构投资者" in sma.classify_seat("机构投资者")["label"]

    def test_unknown(self):
        assert sma.classify_seat("某某资金")["label"] == sma.LABEL_UNKNOWN

    def test_empty(self):
        assert sma.classify_seat("")["label"] == sma.LABEL_UNKNOWN


# ══════════════════════════════════════════════════════════════
# 数据加载与聚合
# ══════════════════════════════════════════════════════════════

class TestLoadSeat:
    def _sample(self):
        return pd.DataFrame({
            "date": ["2026-08-01"] * 4 + ["2026-08-15"] * 4,
            "ts_code": ["000001.SZ"] * 8,
            "exalter": ["机构专用", "机构专用", "华泰证券股份有限公司南京民智路证券营业部",
                        "华泰证券股份有限公司南京民智路证券营业部"] * 2,
            "buy": [1e8, 1e8, 2e7, 2e7, 5e7, 5e7, 1e7, 1e7],
            "sell": [4e7, 4e7, 3e7, 3e7, 1e7, 1e7, 2e7, 2e7],
            "reason": ["涨幅"] * 8,
            "side": [0, 1, 0, 1, 0, 1, 0, 1],
        })

    def test_mirror_dedup(self, fake_store):
        fake_store.top_inst = self._sample()
        rows = sma.load_seat_rows()
        # 每个日期每个席位只剩一行（去镜像）
        assert len(rows) == 4
        assert set(rows["code"]) == {"000001"}

    def test_episode_aggregation(self, fake_store):
        fake_store.top_inst = self._sample()
        eps = sma.load_seat_episodes()
        inst = eps[eps["agency"] == "机构专用"]
        assert len(inst) == 2
        assert inst[inst["date"] == "2026-08-01"].iloc[0]["net"] == pytest.approx(6e7)

    def test_code_filter(self, fake_store):
        fake_store.top_inst = self._sample()
        eps = sma.load_seat_episodes(code="000002")
        assert eps.empty


# ══════════════════════════════════════════════════════════════
# 席位画像
# ══════════════════════════════════════════════════════════════

class TestSeatProfile:
    def _episodes(self):
        return pd.DataFrame({
            "date": ["2026-08-01", "2026-08-01", "2026-08-15", "2026-08-20", "2026-08-25"],
            "code": ["000001", "000001", "000001", "600519", "000001"],
            "agency": ["机构专用"] * 5,
            "buy": [1.5e8, 6e7, 5e7, 1e8, 1e7],
            "sell": [5e7, 5e7, 1e7, 2e7, 6e7],
            "net": [1e8, 1e7, 4e7, 8e7, -5e7],
            "reasons": [["涨幅"], ["换手"], ["涨停"], ["涨幅"], ["跌停"]],
        })

    def test_metrics(self, monkeypatch):
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        prices = FakePrices(fwd={5: 0.03, 10: -0.02, 20: 0.10}, hold_days=7)
        card = sma.compute_seat_profile("机构专用", self._episodes(), prices=prices)
        assert card["label"] == sma.LABEL_INST
        assert card["appearance_count"]["buy"] == 4
        assert card["appearance_count"]["sell"] == 1
        assert card["cum_net_value"] == pytest.approx(1.8e8)
        assert card["win_rate"]["d5"] == 1.0
        assert card["win_rate"]["d10"] == 0.0
        assert card["win_rate"]["d5_n"] == 4
        assert card["avg_hold_days"] == 7
        assert card["fav_sectors"][0]["sector"] == "测试行业"

    def test_empty(self):
        card = sma.compute_seat_profile("机构专用", pd.DataFrame(), prices=FakePrices())
        assert card["appearance_count"]["total"] == 0
        assert card["cum_net_value"] == 0.0

    def test_stock_view(self, fake_store, monkeypatch):
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        eps = pd.DataFrame({
            "date": ["2026-08-01", "2026-08-01", "2026-08-15"],
            "code": ["000001", "000001", "000002"],
            "agency": ["机构专用", "华泰证券股份有限公司南京民智路证券营业部", "机构专用"],
            "buy": [1e8, 2e7, 5e7], "sell": [1e7, 3e7, 1e7],
            "net": [9e7, -1e7, 4e7],
            "reasons": [["涨幅"], ["换手"], ["涨幅"]],
        })
        out = sma.profile_stock_seats("000001", prices=FakePrices(), episodes=eps)
        assert out["available"] is True
        agencies = {s["agency"] for s in out["seats"]}
        assert "机构专用" in agencies
        # 本股净买卖只统计该股
        inst = next(s for s in out["seats"] if s["agency"] == "机构专用")
        assert inst["this_stock"]["net"] == pytest.approx(9e7)

    def test_stock_view_empty(self, fake_store):
        out = sma.profile_stock_seats("000001", prices=FakePrices(), episodes=pd.DataFrame())
        assert out["available"] is False
        assert out["notes"]


# ══════════════════════════════════════════════════════════════
# 北向
# ══════════════════════════════════════════════════════════════

class TestNorthbound:
    def _hk(self, ratios, start="2026-01-01"):
        idx = pd.date_range(start, periods=len(ratios), freq="QS")
        return pd.DataFrame({"ratio": ratios, "vol": [1e8] * len(ratios)}, index=idx)

    def test_streak_and_divergence(self, fake_store, monkeypatch):
        fake_store.hk_hold["000001"] = self._hk([4.0, 4.3, 4.6, 4.9], start="2025-07-01")
        # 价格下跌 → 加仓背离
        out = sma.profile_northbound("000001", window_months=24,
                                     prices=FakePrices(window_ret=-0.1))
        assert out["available"] is True
        assert out["streak"]["direction"] == "加仓"
        assert out["streak"]["length"] == 3
        assert out["divergence"] is not None

    def test_no_data(self, fake_store):
        out = sma.profile_northbound("999999", prices=FakePrices())
        assert out["available"] is False

    def test_streak_helper(self):
        assert sma._streak([1, 1, 1, -1])["length"] == 3
        assert sma._streak([-1, -1, 2])["direction"] == "减仓"
        assert sma._streak([])["length"] == 0


# ══════════════════════════════════════════════════════════════
# 合力 / 分歧
# ══════════════════════════════════════════════════════════════

class TestConsensus:
    def test_rules(self):
        r = sma.classify_consensus({
            "北向": {"direction": "buy"}, "机构席位": {"direction": "buy"},
            "融资盘": {"direction": "buy"}, "大宗买方": {"direction": "no_data"},
        })
        assert r["verdict"] == "资金合力（同向买入）"

        r = sma.classify_consensus({
            "北向": {"direction": "buy"}, "机构席位": {"direction": "sell"},
            "融资盘": {"direction": "no_data"}, "大宗买方": {"direction": "no_data"},
        })
        assert r["verdict"] == "证据不足"

        r = sma.classify_consensus({
            "北向": {"direction": "buy"}, "机构席位": {"direction": "sell"},
            "融资盘": {"direction": "buy"}, "大宗买方": {"direction": "buy"},
        })
        assert r["verdict"] == "资金分歧（对打）"

    def test_assess_routes(self, fake_store, monkeypatch):
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        fake_store.hk_hold["000001"] = pd.DataFrame(
            {"ratio": [4.0, 4.5]}, index=pd.date_range("2026-05-01", periods=2, freq="MS"))
        fake_store.top_inst = pd.DataFrame({
            "date": ["2026-08-01"], "ts_code": ["000001.SZ"], "exalter": ["机构专用"],
            "buy": [1e8], "sell": [0.0], "reason": ["涨幅"], "side": [0],
        })
        fake_store.margin["000001"] = pd.DataFrame(
            {"rzye": [1e8, 1.5e8]}, index=pd.date_range("2026-04-01", periods=2, freq="D"))
        fake_store.block_trade = pd.DataFrame({
            "date": ["2026-08-01"], "ts_code": ["000001.SZ"], "price": [10.0],
            "vol": [100.0], "amount": [2e8], "buyer": ["机构专用"], "seller": ["某营业部"],
        })
        out = sma.assess_consensus("000001", prices=FakePrices())
        assert out["routes"]["机构席位"]["direction"] == "buy"
        assert out["routes"]["融资盘"]["direction"] == "buy"
        assert out["routes"]["大宗买方"]["direction"] == "buy"
        assert out["routes"]["北向"]["direction"] == "buy"
        assert out["verdict"] == "资金合力（同向买入）"


# ══════════════════════════════════════════════════════════════
# Archive
# ══════════════════════════════════════════════════════════════

class TestArchive:
    def _episodes(self):
        return pd.DataFrame({
            "date": ["2026-08-01", "2026-08-15"],
            "code": ["000001", "000001"],
            "agency": ["机构专用", "机构专用"],
            "buy": [1e8, 5e7], "sell": [1e7, 1e7],
            "net": [9e7, 4e7], "reasons": [["涨幅"], ["换手"]],
        })

    def test_update_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sma, "ARCHIVE_PATH", tmp_path / "seats.json")
        prices = FakePrices()
        sma.update_archive(self._episodes(), prices=prices)
        sma.update_archive(self._episodes(), prices=prices)  # 重复合并
        archive = sma.load_archive()
        assert len(archive["seats"]) == 1
        rec = archive["seats"][0]
        assert rec["appearance_count"]["total"] == 2
        assert rec["cum_net_value"] == pytest.approx(1.3e8)
        assert "avg_hold_days" in rec

    def test_accumulates_new_episode(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sma, "ARCHIVE_PATH", tmp_path / "seats.json")
        sma.update_archive(self._episodes(), prices=FakePrices())
        more = pd.DataFrame({
            "date": ["2026-09-01"], "code": ["600519"], "agency": ["机构专用"],
            "buy": [2e8], "sell": [1e8], "net": [1e8], "reasons": [["涨幅"]],
        })
        sma.update_archive(more, prices=FakePrices())
        rec = sma.load_archive()["seats"][0]
        assert rec["appearance_count"]["total"] == 3
        assert rec["data_window"]["end"] == "2026-09-01"


# ══════════════════════════════════════════════════════════════
# 主入口 & 报告
# ══════════════════════════════════════════════════════════════

class TestBuildProfile:
    def test_seat_mode(self, fake_store, tmp_path, monkeypatch):
        monkeypatch.setattr(sma, "ARCHIVE_PATH", tmp_path / "seats.json")
        fake_store.top_inst = pd.DataFrame({
            "date": ["2026-08-01"], "ts_code": ["000001.SZ"], "exalter": ["机构专用"],
            "buy": [1e8], "sell": [1e7], "reason": ["涨幅"], "side": [0],
        })
        monkeypatch.setattr(sma, "PriceCache", lambda: FakePrices())
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        report = sma.build_profile("机构专用", target_type="seat",
                                   window_months=12, update_seat_archive=False)
        assert report["subject"]["type"] == "seat"
        assert report["seat_profile"]["available"] is True
        assert report["seat_profile"]["label"] == sma.LABEL_INST

    def test_auto_detect_seat(self):
        assert sma._looks_like_seat("机构专用") is True
        assert sma._looks_like_seat("000001") is False

    def test_markdown_has_disclaimer(self, fake_store, monkeypatch):
        monkeypatch.setattr(sma, "PriceCache", lambda: FakePrices())
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        report = sma.build_profile("000001", window_months=3, update_seat_archive=False)
        md = sma.report_to_markdown(report)
        assert sma.DISCLAIMER in md
        assert "# 资金主体行为画像" in md

    def test_markdown_includes_hold_days_column(self, fake_store, monkeypatch):
        monkeypatch.setattr(sma, "PriceCache", lambda: FakePrices())
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        fake_store.top_inst = pd.DataFrame({
            "date": ["2026-08-01"], "ts_code": ["000001.SZ"], "exalter": ["机构专用"],
            "buy": [1e8], "sell": [1e7], "reason": ["涨幅"], "side": [0],
        })
        report = sma.build_profile("000001", mode="seat", window_months=3,
                                   update_seat_archive=False)
        md = sma.report_to_markdown(report)
        assert "平均持有(交易日)" in md

    def test_parse_horizons(self):
        assert sma.parse_horizons("5,10") == (5, 10)
        assert sma.parse_horizons([20, 5, 5]) == (20, 5)
        assert sma.parse_horizons(None) == sma.HORIZONS
        assert sma.parse_horizons("abc") == sma.HORIZONS


# ══════════════════════════════════════════════════════════════
# 盘前龙虎榜扫描
# ══════════════════════════════════════════════════════════════

class TestRecentLhb:
    def _rows(self):
        return pd.DataFrame({
            "date": ["2026-08-01", "2026-08-01", "2026-08-15", "2026-08-20", "2026-08-20"],
            "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ", "000001.SZ", "000002.SZ"],
            "exalter": ["机构专用", "华泰证券股份有限公司南京民智路证券营业部",
                        "机构专用", "机构专用", "机构专用"],
            "buy": [1e8, 2e7, 5e7, 4e7, 6e7],
            "sell": [2e7, 3e7, 1e7, 1e7, 1e7],
            "reason": ["涨幅", "换手", "涨停", "涨幅", "涨幅"],
            "side": [0, 0, 0, 0, 0],
        })

    def test_days1(self, fake_store, monkeypatch):
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        fake_store.top_inst = self._rows()
        r = sma.scan_recent_lhb(days=1, history_months=0, top_n=5,
                                prices=FakePrices())
        assert r["available"] is True
        assert r["dates"] == ["2026-08-20"]
        assert r["target_count"] == 2
        t = next(x for x in r["targets"] if x["code"] == "000001")
        s = t["seats"][0]
        assert s["agency"] == "机构专用"
        assert s["this_net"] == pytest.approx(3e7)
        # 本股历史其他上榜（排除当日）
        assert [e["date"] for e in s["stock_history"]] == ["2026-08-01", "2026-08-15"]
        # 跨股历史画像覆盖 4 次上榜（000001 三次 + 000002 一次）
        assert s["history"]["appearance_count"]["total"] == 4
        assert "d5" in s["history"]["win_rate"]

    def test_days2(self, fake_store, monkeypatch):
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        fake_store.top_inst = self._rows()
        r = sma.scan_recent_lhb(days=2, top_n=5, prices=FakePrices())
        assert r["dates"] == ["2026-08-15", "2026-08-20"]
        assert r["target_count"] == 3

    def test_empty(self, fake_store):
        r = sma.scan_recent_lhb(days=1, prices=FakePrices())
        assert r["available"] is False
        assert r["notes"]

    def test_markdown(self, fake_store, monkeypatch):
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        fake_store.top_inst = self._rows()
        r = sma.scan_recent_lhb(days=1, prices=FakePrices())
        md = sma.recent_lhb_to_markdown(r)
        assert "# 盘前龙虎榜标的与席位画像" in md
        assert "平均持有(日)" in md


# ══════════════════════════════════════════════════════════════
# Tool 封装
# ══════════════════════════════════════════════════════════════

class TestTools:
    def test_identity(self):
        from agent_tools.smart_money_tools import get_seat_identity
        r = get_seat_identity("机构专用")
        assert r["success"] is True
        assert r["label"] == sma.LABEL_INST

    def test_tools_success(self, fake_store, tmp_path, monkeypatch):
        from agent_tools import smart_money_tools as tools
        monkeypatch.setattr(sma, "ARCHIVE_PATH", tmp_path / "seats.json")
        monkeypatch.setattr(sma, "PriceCache", lambda: FakePrices())
        monkeypatch.setattr(sma, "_sector_of", lambda c: "测试行业")
        fake_store.top_inst = pd.DataFrame({
            "date": ["2026-08-01"], "ts_code": ["000001.SZ"], "exalter": ["机构专用"],
            "buy": [1e8], "sell": [1e7], "reason": ["涨幅"], "side": [0],
        })
        assert tools.get_seat_profile("机构专用")["success"] is True
        assert tools.get_capital_consensus("000001")["success"] is True
        r = tools.get_stock_smart_money("000001", mode="seat", window_months=3)
        assert r["success"] is True
        assert "markdown" in r
        rt = tools.get_recent_lhb_smart_money(days=1)
        assert rt["success"] is True
        assert "markdown" in rt

    def test_tool_error_captured(self, monkeypatch):
        from agent_tools.smart_money_tools import get_seat_profile

        def _boom(*a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(sma, "profile_seat", _boom)
        r = get_seat_profile("机构专用")
        assert r["success"] is False
        assert "boom" in r["error"]
