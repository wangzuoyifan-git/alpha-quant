"""
tests/test_industry_rotation.py — L2 行业轮动层测试用例

对应 requirements.md 8.4 节 L2 需求（阶段二）：
- L2-R1/R1a: 数据源与行业分类对齐
- L2-R2: 行业评分维度
- L2-R3: 行业池输出约束
- L2-R4: 扫描范围前置收窄（引擎层穿透）
"""

import pytest
import pandas as pd


class TestIndustryRotationOutput:
    """L2-R1/R2/R3: 行业轮动评估输出（台阶二三维评分）"""

    def _mock_member(self):
        """mock _load_member_map: (name_map, code_to_ind)"""
        name_map = {
            "801080": "电子", "801780": "银行", "801150": "医药生物",
            "801120": "食品饮料", "801050": "有色金属", "801180": "房地产",
        }
        code_to_ind = {
            "002415": "801080", "000001": "801780", "600276": "801150",
            "600519": "801120", "601899": "801050", "000002": "801180",
        }
        return name_map, code_to_ind

    def _patch_all(self, monkeypatch, mf=None, pros=None, val=None):
        """统一 mock 三维聚合 + 成分股映射"""
        from agents import industry_agent
        name_map, code_to_ind = self._mock_member()
        # 默认资金流：电子最高
        mf = mf if mf is not None else {
            "801080": 5e9, "801780": 3e9, "801150": 2e9,
            "801120": 1.5e9, "801050": 1e9, "801180": -1e9,
        }
        pros = pros if pros is not None else {
            "801080": 0.10, "801780": 0.05, "801150": 0.25,
            "801120": 0.15, "801050": 0.12, "801180": -0.05,
        }
        val = val if val is not None else {
            "801080": 0.3, "801780": 0.7, "801150": 0.5,
            "801120": 0.4, "801050": 0.6, "801180": 0.8,
        }
        monkeypatch.setattr(industry_agent, "_load_member_map",
                            lambda level: (name_map, code_to_ind))
        monkeypatch.setattr(industry_agent, "_aggregate_raw_dims_cached",
                            lambda c, level, date=None: (mf, pros, val))

    def test_evaluate_industry_rotation_structure(self, monkeypatch):
        """L2-R3: 输出结构验证"""
        from agents import industry_agent
        self._patch_all(monkeypatch)

        result = industry_agent.evaluate_industry_rotation(risk_mode="balanced")

        assert "success" in result
        assert "allowed_industries" in result
        assert "industry_scores" in result
        assert isinstance(result["allowed_industries"], list)
        for code in result["allowed_industries"]:
            assert code.startswith("801"), f"应输出申万代码，实际: {code}"

    def test_defensive_fewer_industries_than_aggressive(self, monkeypatch):
        """L2-R3: defensive 行业池应窄于 aggressive"""
        from agents import industry_agent
        self._patch_all(monkeypatch)

        defensive = industry_agent.evaluate_industry_rotation(risk_mode="defensive")
        aggressive = industry_agent.evaluate_industry_rotation(risk_mode="aggressive")

        assert len(defensive["allowed_industries"]) <= len(aggressive["allowed_industries"]), \
            "defensive 行业池应不宽于 aggressive"

    def test_high_inflow_industries_ranked_first(self, monkeypatch):
        """L2-R2: 资金流主导时净流入高的行业排名靠前"""
        from agents import industry_agent
        self._patch_all(monkeypatch)

        # 资金流权重 100% → 电子（最高净流入）入选
        result = industry_agent.evaluate_industry_rotation(
            risk_mode="aggressive", weights={"w1": 0.0, "w2": 0.0, "w3": 1.0})

        assert "801080" in result["allowed_industries"], "电子（最高净流入）应入选"

    def test_empty_data_returns_empty_pool(self, monkeypatch):
        """L2-R3: 数据缺失时返回空池（容错，不中断）"""
        from agents import industry_agent
        # 成分股映射为空 → fallback
        monkeypatch.setattr(industry_agent, "_load_member_map",
                            lambda level: ({}, {}))

        result = industry_agent.evaluate_industry_rotation(risk_mode="balanced")

        assert result["allowed_industries"] == [], "数据缺失应返回空池"
        assert result["success"] is False or result.get("fallback") is True

    def test_prosperity_valuation_not_pending(self, monkeypatch):
        """L2-R2: 三维评分接入后景气度/估值不再是 pending"""
        from agents import industry_agent
        self._patch_all(monkeypatch)

        result = industry_agent.evaluate_industry_rotation(
            risk_mode="balanced", weights={"w1": 0.3, "w2": 0.25, "w3": 0.45})
        detail = result["industry_detail"]
        any_code = next(iter(detail))
        assert detail[any_code]["prosperity"] != "pending"
        assert detail[any_code]["valuation"] != "pending"


class TestScannerIndustryFilter:
    """L2-R4: 扫描范围前置收窄（引擎层穿透，关键）"""

    def test_get_filtered_stock_list_with_industry(self, monkeypatch):
        """_get_filtered_stock_list 接受 industry_filter 并收窄标的池"""
        from engine.scanner import ScanEngine
        from data.fetcher import DataFetcher

        # 模拟全市场股票
        all_codes = ["000001", "600519", "002415", "600276", "601899", "000002"]

        industry_sw = pd.DataFrame([
            {"stock_code": "000001", "stock_name": "平安银行", "industry_code": "801780", "industry_name": "银行"},
            {"stock_code": "600519", "stock_name": "贵州茅台", "industry_code": "801120", "industry_name": "食品饮料"},
            {"stock_code": "002415", "stock_name": "海康威视", "industry_code": "801080", "industry_name": "电子"},
            {"stock_code": "600276", "stock_name": "恒瑞医药", "industry_code": "801150", "industry_name": "医药生物"},
            {"stock_code": "601899", "stock_name": "紫金矿业", "industry_code": "801050", "industry_name": "有色金属"},
            {"stock_code": "000002", "stock_name": "万科A", "industry_code": "801180", "industry_name": "房地产"},
        ])

        monkeypatch.setattr("data.store.list_codes", lambda *a, **k: all_codes)
        monkeypatch.setattr("data.store.load_ipo_dates", lambda: {
            f"cn:{c}": {"name": c, "ipo_date": "2010-01-01"} for c in all_codes
        })
        monkeypatch.setattr(
            "data.store_meta.load",
            lambda dt, **k: industry_sw if dt == "industry_sw" else None
        )

        engine = ScanEngine(DataFetcher({}), {"exclude_st": False, "exclude_new": False})

        # 只允许电子(801080)和银行(801780)
        result = engine._get_filtered_stock_list(industry_filter=["801080", "801780"])

        result_codes = set(result["code"].tolist())
        # 应只剩电子和银行的标的
        assert result_codes == {"002415", "000001"}, \
            f"行业过滤后应只剩电子+银行，实际: {result_codes}"

    def test_industry_filter_none_keeps_all(self, monkeypatch):
        """industry_filter=None 时保持全市场（向后兼容 AC13）"""
        from engine.scanner import ScanEngine
        from data.fetcher import DataFetcher

        all_codes = ["000001", "600519", "002415"]
        monkeypatch.setattr("data.store.list_codes", lambda *a, **k: all_codes)
        monkeypatch.setattr("data.store.load_ipo_dates", lambda: {
            f"cn:{c}": {"name": c, "ipo_date": "2010-01-01"} for c in all_codes
        })

        engine = ScanEngine(DataFetcher({}), {"exclude_st": False, "exclude_new": False})

        result = engine._get_filtered_stock_list(industry_filter=None)

        # 全部保留
        assert len(result) == 3, "industry_filter=None 应保留全部标的"

    def test_scan_passes_industry_filter(self, monkeypatch):
        """scan() 将 industry_filter 透传到 _get_filtered_stock_list"""
        from engine.scanner import ScanEngine
        from data.fetcher import DataFetcher
        from strategies.base import BaseStrategy, Signal

        received = {}

        class DummyStrategy(BaseStrategy):
            name = "dummy"
            def generate_signals(self, df, code, name):
                return []
            def requires_bars(self):
                return 30

        engine = ScanEngine(DataFetcher({}), {})
        engine.register_strategy("dummy", DummyStrategy())

        def mock_get_list(industry_filter=None, industry_level="L1"):
            received["industry_filter"] = industry_filter
            received["industry_level"] = industry_level
            return pd.DataFrame(columns=["code", "name"])

        monkeypatch.setattr(engine, "_get_filtered_stock_list", mock_get_list)
        monkeypatch.setattr(engine, "_check_data_freshness", lambda *a, **k: [])

        engine.scan(date="2026-06-03", industry_filter=["801080"])

        assert received.get("industry_filter") == ["801080"], \
            "scan 应将 industry_filter 透传给 _get_filtered_stock_list"


class TestRunScanIndustryFilter:
    """L2-R4: agent_tools.run_scan 透传 industry_filter"""

    def test_run_scan_accepts_industry_filter(self, monkeypatch):
        """run_scan 接受 industry_filter 参数并透传给引擎"""
        from agent_tools import strategy_tools

        received = {}

        class FakeEngine:
            def __init__(self, *a, **k):
                pass
            def register_strategy(self, key, strat):
                pass
            def scan(self, date=None, industry_filter=None, **kwargs):
                received["date"] = date
                received["industry_filter"] = industry_filter
                return []

        monkeypatch.setattr(strategy_tools, "ScanEngine", FakeEngine)
        monkeypatch.setattr(strategy_tools, "DataFetcher", lambda *a, **k: None)

        result = strategy_tools.run_scan(
            date="2026-06-03",
            strategies=["ma_cross"],
            industry_filter=["801080", "801780"]
        )

        assert result["success"] is True
        assert received["industry_filter"] == ["801080", "801780"], \
            "run_scan 应将 industry_filter 透传给 ScanEngine.scan"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
