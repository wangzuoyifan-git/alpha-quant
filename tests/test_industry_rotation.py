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
    """L2-R1/R2/R3: 行业轮动评估输出"""

    def _mock_moneyflow(self):
        """构造模拟行业资金流数据（东财格式）"""
        return pd.DataFrame([
            {"name": "电子", "net_amount": 5e9, "pct_change": 2.1, "content_type": "行业"},
            {"name": "银行", "net_amount": 3e9, "pct_change": 0.5, "content_type": "行业"},
            {"name": "医药生物", "net_amount": 2e9, "pct_change": 1.2, "content_type": "行业"},
            {"name": "食品饮料", "net_amount": 1.5e9, "pct_change": 0.8, "content_type": "行业"},
            {"name": "有色金属", "net_amount": 1e9, "pct_change": 1.5, "content_type": "行业"},
            {"name": "计算机", "net_amount": 8e8, "pct_change": 2.5, "content_type": "行业"},
            {"name": "通信", "net_amount": 5e8, "pct_change": 1.0, "content_type": "行业"},
            {"name": "汽车", "net_amount": -2e8, "pct_change": -0.5, "content_type": "行业"},
            {"name": "钢铁", "net_amount": -5e8, "pct_change": -1.0, "content_type": "行业"},
            {"name": "房地产", "net_amount": -1e9, "pct_change": -2.0, "content_type": "行业"},
            # 东财细分板块（非申万一级名，应被忽略）
            {"name": "半导体", "net_amount": 4e9, "pct_change": 3.0, "content_type": "行业"},
            {"name": "光学光电子", "net_amount": 2e9, "pct_change": 2.0, "content_type": "行业"},
        ])

    def _mock_industry_sw(self):
        """构造模拟申万行业分类"""
        return pd.DataFrame([
            {"stock_code": "000001", "stock_name": "平安银行", "industry_code": "801780", "industry_name": "银行"},
            {"stock_code": "600519", "stock_name": "贵州茅台", "industry_code": "801120", "industry_name": "食品饮料"},
            {"stock_code": "002415", "stock_name": "海康威视", "industry_code": "801080", "industry_name": "电子"},
            {"stock_code": "600276", "stock_name": "恒瑞医药", "industry_code": "801150", "industry_name": "医药生物"},
            {"stock_code": "601899", "stock_name": "紫金矿业", "industry_code": "801050", "industry_name": "有色金属"},
            {"stock_code": "000002", "stock_name": "万科A", "industry_code": "801180", "industry_name": "房地产"},
        ])

    def test_evaluate_industry_rotation_structure(self, monkeypatch):
        """L2-R3: 输出结构验证"""
        from agents import industry_agent

        monkeypatch.setattr(industry_agent, "_load_moneyflow_window",
                            lambda risk_mode, date=None: self._mock_moneyflow())
        monkeypatch.setattr(
            "data.store_meta.load",
            lambda dt, **k: self._mock_industry_sw() if dt == "industry_sw" else None
        )

        result = industry_agent.evaluate_industry_rotation(risk_mode="balanced")

        assert "success" in result
        assert "allowed_industries" in result
        assert "industry_scores" in result
        assert isinstance(result["allowed_industries"], list)
        # 输出的应是申万行业代码（801xxx）
        for code in result["allowed_industries"]:
            assert code.startswith("801"), f"应输出申万代码，实际: {code}"

    def test_defensive_fewer_industries_than_aggressive(self, monkeypatch):
        """L2-R3: defensive 行业池应窄于 aggressive"""
        from agents import industry_agent

        monkeypatch.setattr(industry_agent, "_load_moneyflow_window",
                            lambda risk_mode, date=None: self._mock_moneyflow())
        monkeypatch.setattr(
            "data.store_meta.load",
            lambda dt, **k: self._mock_industry_sw() if dt == "industry_sw" else None
        )

        defensive = industry_agent.evaluate_industry_rotation(risk_mode="defensive")
        aggressive = industry_agent.evaluate_industry_rotation(risk_mode="aggressive")

        # defensive 池 <= aggressive 池
        assert len(defensive["allowed_industries"]) <= len(aggressive["allowed_industries"]), \
            "defensive 行业池应不宽于 aggressive"

    def test_high_inflow_industries_ranked_first(self, monkeypatch):
        """L2-R2: 资金净流入高的行业排名靠前"""
        from agents import industry_agent

        monkeypatch.setattr(industry_agent, "_load_moneyflow_window",
                            lambda risk_mode, date=None: self._mock_moneyflow())
        monkeypatch.setattr(
            "data.store_meta.load",
            lambda dt, **k: self._mock_industry_sw() if dt == "industry_sw" else None
        )

        result = industry_agent.evaluate_industry_rotation(risk_mode="aggressive")

        # 电子（净流入最高 5e9）应在行业池中
        # 电子对应申万代码 801080
        assert "801080" in result["allowed_industries"], "电子（最高净流入）应入选"

    def test_empty_data_returns_empty_pool(self, monkeypatch):
        """L2-R3: 数据缺失时返回空池（容错，不中断）"""
        from agents import industry_agent

        monkeypatch.setattr(industry_agent, "_load_moneyflow_window",
                            lambda risk_mode, date=None: None)
        monkeypatch.setattr(
            "data.store_meta.load",
            lambda dt, **k: self._mock_industry_sw() if dt == "industry_sw" else None
        )

        result = industry_agent.evaluate_industry_rotation(risk_mode="balanced")

        # 数据缺失时应返回空 allowed_industries（L3 退化为全市场）
        assert result["allowed_industries"] == [], "数据缺失应返回空池"
        assert result["success"] is False or result.get("fallback") is True


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
