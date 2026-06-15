"""
tests/test_sw_classify.py — 申万二/三级行业支持测试（L2-R5）

- store_meta.get_member_stocks 按层级取成分股
- store_meta.list_industry_codes 按层级列行业
- industry_agent._score_industries_by_aggregation 个股资金流聚合
- scanner._resolve_industry_members 二三级 join + 回退
"""
import pytest
import pandas as pd


# ── 构造 mock industry_member / industry_classify ──

def _mock_member_df():
    return pd.DataFrame([
        {"l1_code": "801080.SI", "l1_name": "电子", "l2_code": "801081.SI",
         "l2_name": "半导体", "l3_code": "850811.SI", "l3_name": "集成电路",
         "ts_code": "000001.SZ", "name": "A"},
        {"l1_code": "801080.SI", "l1_name": "电子", "l2_code": "801081.SI",
         "l2_name": "半导体", "l3_code": "850812.SI", "l3_name": "分立器件",
         "ts_code": "600519.SH", "name": "B"},
        {"l1_code": "801770.SI", "l1_name": "通信", "l2_code": "801102.SI",
         "l2_name": "通信设备", "l3_code": "851021.SI", "l3_name": "通信网络",
         "ts_code": "000002.SZ", "name": "C"},
    ])


def _mock_classify_df():
    return pd.DataFrame([
        {"index_code": "801080.SI", "industry_name": "电子", "level": "L1",
         "industry_code": "270000", "parent_code": "0"},
        {"index_code": "801081.SI", "industry_name": "半导体", "level": "L2",
         "industry_code": "270100", "parent_code": "270000"},
        {"index_code": "850811.SI", "industry_name": "集成电路", "level": "L3",
         "industry_code": "270101", "parent_code": "270100"},
    ])


class TestStoreMemberQuery:
    """store_meta 三级成分股查询"""

    def test_get_member_stocks_l2(self, monkeypatch):
        import data.store_meta as sm
        monkeypatch.setattr(sm, "load",
                            lambda dt, **k: _mock_member_df() if dt == "industry_member" else None)
        codes = sm.get_member_stocks(["801081.SI"], level="L2")
        assert set(codes) == {"000001", "600519"}

    def test_get_member_stocks_l3(self, monkeypatch):
        import data.store_meta as sm
        monkeypatch.setattr(sm, "load",
                            lambda dt, **k: _mock_member_df() if dt == "industry_member" else None)
        codes = sm.get_member_stocks(["850811.SI"], level="L3")
        assert codes == ["000001"]

    def test_get_member_stocks_code_without_suffix(self, monkeypatch):
        """传不带 .SI 后缀的代码也能匹配"""
        import data.store_meta as sm
        monkeypatch.setattr(sm, "load",
                            lambda dt, **k: _mock_member_df() if dt == "industry_member" else None)
        codes = sm.get_member_stocks(["801081"], level="L2")
        assert set(codes) == {"000001", "600519"}

    def test_get_member_stocks_empty_when_no_data(self, monkeypatch):
        import data.store_meta as sm
        monkeypatch.setattr(sm, "load", lambda dt, **k: None)
        assert sm.get_member_stocks(["801081.SI"], level="L2") == []

    def test_list_industry_codes_by_level(self, monkeypatch):
        import data.store_meta as sm
        monkeypatch.setattr(sm, "load",
                            lambda dt, **k: _mock_classify_df() if dt == "industry_classify" else None)
        l2 = sm.list_industry_codes(level="L2")
        assert len(l2) == 1
        assert l2[0]["index_code"] == "801081.SI"


class TestScannerResolveMembers:
    """scanner 二三级 join + 回退逻辑"""

    def _make_scanner(self):
        from engine.scanner import ScanEngine
        from unittest.mock import MagicMock
        return ScanEngine(MagicMock(), {"exclude_st": True})

    def test_resolve_l2_uses_member(self, monkeypatch):
        import data.store_meta as sm
        monkeypatch.setattr(sm, "get_member_stocks",
                            lambda codes, level: ["000001", "600519"] if level == "L2" else [])
        scanner = self._make_scanner()
        result = scanner._resolve_industry_members(["801081.SI"], "L2")
        assert result == {"000001", "600519"}

    def test_resolve_l1_fallback_to_industry_sw(self, monkeypatch):
        """L1 且新表空时回退 industry_sw"""
        import data.store_meta as sm
        monkeypatch.setattr(sm, "get_member_stocks", lambda codes, level: [])
        old_sw = pd.DataFrame([
            {"stock_code": "000001", "industry_code": "801080"},
            {"stock_code": "600519", "industry_code": "801080"},
            {"stock_code": "000002", "industry_code": "801770"},
        ])
        monkeypatch.setattr(sm, "load",
                            lambda dt, **k: old_sw if dt == "industry_sw" else None)
        scanner = self._make_scanner()
        result = scanner._resolve_industry_members(["801080"], "L1")
        assert result == {"000001", "600519"}

    def test_resolve_l3_no_fallback(self, monkeypatch):
        """L3 新表空时不回退（旧表只有一级），返回空"""
        import data.store_meta as sm
        monkeypatch.setattr(sm, "get_member_stocks", lambda codes, level: [])
        monkeypatch.setattr(sm, "load", lambda dt, **k: None)
        scanner = self._make_scanner()
        result = scanner._resolve_industry_members(["850811.SI"], "L3")
        assert result == set()


class TestIndustryAggregation:
    """L2 个股资金流聚合评分"""

    def test_aggregation_level_passed_through(self, monkeypatch):
        """evaluate_industry_rotation(level=L2) 调用聚合评分而非东财评分"""
        from agents import industry_agent

        called = {}
        def fake_agg(level, date=None):
            called["level"] = level
            return pd.DataFrame([
                {"industry_code": "801081.SI", "industry_name": "半导体",
                 "score": 80.0, "net_amount_sum": 1e8},
            ])
        monkeypatch.setattr(industry_agent, "_score_industries_by_aggregation", fake_agg)

        r = industry_agent.evaluate_industry_rotation(risk_mode="balanced", level="L2")
        assert called["level"] == "L2"
        assert r["success"]
        assert r["level"] == "L2"
        assert "801081.SI" in r["allowed_industries"]

    def test_l1_still_uses_eastmoney(self, monkeypatch):
        """level=L1 仍走东财板块评分（向后兼容）"""
        from agents import industry_agent

        called = {}
        def fake_score(risk_mode, date=None):
            called["used"] = "eastmoney"
            return pd.DataFrame([
                {"industry_code": "801080", "industry_name": "电子",
                 "score": 75.0, "net_amount_sum": 1e8},
            ])
        monkeypatch.setattr(industry_agent, "_score_industries", fake_score)

        r = industry_agent.evaluate_industry_rotation(risk_mode="balanced", level="L1")
        assert called.get("used") == "eastmoney"
        assert r["level"] == "L1"
