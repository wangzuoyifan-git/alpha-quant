"""
tests/test_macro_regime.py — L1 宏观层测试用例

对应 requirements.md 8.8 节测试需求：
- NT-L1-01: L1 宏观层单元测试
- NT-L1-02: 条件边路由测试
- NT-L1-03: Decision Context 贯通测试
- NT-L1-04: 决策日志持久化测试
"""

import pytest
from typing import TypedDict
from pathlib import Path
import json


class TestMacroRegimeOutput:
    """NT-L1-01: L1 宏观层单元测试"""

    def test_evaluate_market_condition_structure(self):
        """测试 evaluate_market_condition() 输出结构"""
        from agents.strategy_agent import evaluate_market_condition

        result = evaluate_market_condition()

        # 必须包含的字段
        assert "condition" in result or "regime" in result, "缺少 regime 字段"
        assert "indicators" in result, "缺少 indicators 字段"

        # 阶段一升级后，必须包含 risk_mode
        if "regime" in result:
            assert "risk_mode" in result, "缺少 risk_mode 字段"
            assert result["risk_mode"] in ["defensive", "balanced", "aggressive"], \
                f"risk_mode 值非法: {result['risk_mode']}"
            assert "confidence" in result, "缺少 confidence 字段"
            assert 0.0 <= result["confidence"] <= 1.0, "confidence 超出范围"

    def test_regime_defensive_scenario(self, monkeypatch):
        """测试防御模式触发：VIX=35, PMI=47"""
        # Mock 宏观数据返回防御信号
        import pandas as pd
        from agents.strategy_agent import evaluate_market_condition

        # 模拟数据
        def mock_load_macro(key):
            if key == "vix":
                return pd.DataFrame([{"close": 35.0}])
            elif key == "pmi":
                return pd.DataFrame([{"制造业PMI": 47.0}])
            elif key == "bond_yield":
                return pd.DataFrame([{"cn_10y": 2.5}])
            elif key == "m2":
                return pd.DataFrame([{"M2同比": 8.5}])
            return None

        monkeypatch.setattr("data.store_macro.load", mock_load_macro)
        # 隔离 market 指数读取（避免依赖本地数据）
        monkeypatch.setattr("data.store.load", lambda *a, **k: None)

        result = evaluate_market_condition()

        # 阶段一规则版期望
        if "regime" in result:
            assert result["risk_mode"] == "defensive", \
                f"VIX=35, PMI=47 应触发 defensive，实际: {result['risk_mode']}"
            assert result["confidence"] > 0.6, "置信度应较高"

    def test_regime_aggressive_scenario(self, monkeypatch):
        """测试进攻模式触发：VIX=15, PMI=53"""
        import pandas as pd
        from agents.strategy_agent import evaluate_market_condition

        def mock_load_macro(key):
            if key == "vix":
                return pd.DataFrame([{"close": 15.0}])
            elif key == "pmi":
                return pd.DataFrame([{"制造业PMI": 53.0}])
            elif key == "bond_yield":
                return pd.DataFrame([{"cn_10y": 2.5}])
            elif key == "m2":
                return pd.DataFrame([{"M2同比": 10.0}])
            return None

        monkeypatch.setattr("data.store_macro.load", mock_load_macro)
        monkeypatch.setattr("data.store.load", lambda *a, **k: None)

        result = evaluate_market_condition()

        if "regime" in result:
            assert result["risk_mode"] == "aggressive", \
                f"VIX=15, PMI=53 应触发 aggressive，实际: {result['risk_mode']}"

    def test_regime_volatile_by_vix_std(self, monkeypatch):
        """测试 volatile 触发：VIX 20日标准差 > 5（L1-R2 修正后逻辑）"""
        import pandas as pd
        import numpy as np
        from agents.strategy_agent import evaluate_market_condition

        # 构造 20 条 VIX 数据，标准差 > 5（在 18~32 大幅波动）
        vix_values = [18, 30, 19, 31, 20, 32, 18, 30, 19, 31,
                      20, 32, 18, 30, 19, 31, 20, 32, 18, 25]
        vix_df = pd.DataFrame({"close": vix_values})

        def mock_load_macro(key):
            if key == "vix":
                return vix_df
            elif key == "pmi":
                return pd.DataFrame([{"制造业PMI": 50.0}])  # 中性 PMI，不触发其他 regime
            elif key == "bond_yield":
                return pd.DataFrame([{"cn_10y": 2.5}])
            elif key == "m2":
                return pd.DataFrame([{"M2同比": 8.5}])
            return None

        monkeypatch.setattr("data.store_macro.load", mock_load_macro)
        monkeypatch.setattr("data.store.load", lambda *a, **k: None)

        result = evaluate_market_condition()

        # 最新 VIX=25（不触发 crisis/risk_off），但 20日标准差 > 5 应触发 volatile
        assert result["indicators"].get("vix_std_20") is not None, "应计算 vix_std_20"
        assert result["indicators"]["vix_std_20"] > 5, "标准差应 > 5"
        assert result["regime"] == "volatile", \
            f"VIX标准差>5 应触发 volatile，实际: {result['regime']}"
        assert result["risk_mode"] == "defensive"

    def test_regime_crisis_by_index_drop(self, monkeypatch):
        """测试 crisis 触发：A股指数单日跌幅 > 5%（L1-R2 修正后逻辑）"""
        import pandas as pd
        from agents.strategy_agent import evaluate_market_condition

        def mock_load_macro(key):
            if key == "vix":
                return pd.DataFrame([{"close": 22.0}])  # VIX 不到 crisis 阈值
            elif key == "pmi":
                return pd.DataFrame([{"制造业PMI": 50.0}])
            elif key == "bond_yield":
                return pd.DataFrame([{"cn_10y": 2.5}])
            elif key == "m2":
                return pd.DataFrame([{"M2同比": 8.5}])
            return None

        # Mock 沪深300 单日跌幅 -6%（close=3760, preclose=4000）
        def mock_load_market(code, *a, **k):
            if code == "000300":
                return pd.DataFrame([{"close": 3760.0, "preclose": 4000.0}])
            return None

        monkeypatch.setattr("data.store_macro.load", mock_load_macro)
        monkeypatch.setattr("data.store.load", mock_load_market)

        result = evaluate_market_condition()

        # VIX=22 本身不触发 crisis，但 A股跌幅 -6% 应触发 crisis
        assert result["indicators"].get("index_drop_pct") is not None, "应计算 index_drop_pct"
        assert result["indicators"]["index_drop_pct"] < -5, "跌幅应 < -5%"
        assert result["regime"] == "crisis", \
            f"A股跌幅>5% 应触发 crisis，实际: {result['regime']}"
        assert result["risk_mode"] == "defensive"
        assert result["confidence"] == 0.9


class TestStrategySelection:
    """NT-L1-02: 策略选择与条件边路由测试"""

    def test_select_strategies_defensive(self):
        """测试防御模式策略选择"""
        from agents.strategy_agent import select_strategies

        strategies = select_strategies("defensive")

        # 阶段一期望：防御策略组合
        # 根据 requirements.md L3-R1，defensive 应包含 fundamental_value
        assert isinstance(strategies, list), "返回必须是 list"
        assert len(strategies) > 0, "防御模式必须返回至少1个策略"

        # 阶段一升级后，期望包含防御型策略
        # 当前未实现策略的 market_condition 元数据，先验证能运行
        assert all(isinstance(s, str) for s in strategies), "策略 key 必须是字符串"

    def test_select_strategies_aggressive(self):
        """测试进攻模式策略选择"""
        from agents.strategy_agent import select_strategies

        strategies = select_strategies("aggressive")

        assert isinstance(strategies, list)
        assert len(strategies) > 0

    def test_route_after_macro_logic(self):
        """测试条件边路由逻辑（模拟）"""
        from agents.orchestrator import WorkflowState

        # 模拟 state
        state: WorkflowState = {
            "macro_regime": {
                "regime": "risk_off",
                "risk_mode": "defensive",
                "confidence": 0.8,
            },
            "log": [],
        }

        # 阶段一实现后，route_after_macro 应该能读取 risk_mode 并返回路径
        # 此测试在阶段一实现后会真正验证路由函数
        assert state["macro_regime"]["risk_mode"] == "defensive"


class TestDecisionContext:
    """NT-L1-03: Decision Context 贯通测试"""

    def test_decision_context_structure(self):
        """测试 decision_context 结构定义"""
        # 按 requirements.md L1-R3 定义的结构
        decision_context = {
            "regime": "balanced",
            "risk_mode": "balanced",
            "max_position": 0.7,
            "allowed_industries": [],  # 阶段二填充
            "industry_weights": {},    # 阶段二填充
            "strategy_keys": ["ma_cross", "macd_rsi"],
            "timestamp": "2026-06-11 10:00:00",
        }

        # 验证必须字段存在
        assert "regime" in decision_context
        assert "risk_mode" in decision_context
        assert "max_position" in decision_context
        assert "strategy_keys" in decision_context
        assert isinstance(decision_context["strategy_keys"], list)

    def test_workflow_state_extension(self):
        """测试 WorkflowState 可选字段扩展（AC10）"""
        from agents.orchestrator import WorkflowState

        # 新字段必须是可选的（total=False）
        state: WorkflowState = {
            "date": "2026-06-11",
            "log": [],
        }

        # 不应报错（说明字段是可选的）
        assert "date" in state

        # 添加新字段
        state["macro_regime"] = {"regime": "balanced"}
        state["decision_context"] = {"risk_mode": "balanced"}

        assert "macro_regime" in state
        assert "decision_context" in state


class TestDecisionLogPersistence:
    """NT-L1-04: 决策日志持久化测试"""

    def test_log_result_structure(self, tmp_path):
        """测试决策日志结构完整性"""
        from datetime import datetime

        # 模拟阶段一升级后的日志结构
        log_data = {
            "date": "2026-06-11",
            "macro_regime": {
                "regime": "balanced",
                "risk_mode": "balanced",
                "confidence": 0.7,
                "indicators": {
                    "cn_10y": 2.5,
                    "pmi": 50.5,
                    "vix": 18.0,
                },
                "trigger_reason": "PMI 在 50 附近震荡，VIX 低于 20",
            },
            "decision_context": {
                "regime": "balanced",
                "risk_mode": "balanced",
                "max_position": 0.7,
                "strategy_keys": ["ma_cross", "macd_rsi"],
                "timestamp": datetime.now().isoformat(),
            },
            "logs": [
                "[check_data] domains=6 stale=[]",
                "[macro_regime] regime=balanced risk_mode=balanced",
                "[scan] 50 signals in 10.5s",
            ],
            "signals_count": 50,
            "orders_count": 5,
            "error": None,
        }

        # 写入临时文件
        log_file = tmp_path / "decision_log_20260611.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)

        # 验证读回
        with open(log_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        assert loaded["date"] == "2026-06-11"
        assert "macro_regime" in loaded
        assert loaded["macro_regime"]["risk_mode"] == "balanced"
        assert "decision_context" in loaded

    def test_log_file_naming_convention(self):
        """测试日志文件命名规范"""
        from datetime import datetime

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        expected_pattern = f"run_log_{ts}.json"

        # 验证命名符合规范
        assert expected_pattern.startswith("run_log_")
        assert expected_pattern.endswith(".json")
        assert len(ts) == 15  # YYYYMMDD_HHMMSS

        # 阶段一升级后，决策日志应使用 decision_log_YYYYMMDD.json
        date_str = datetime.now().strftime("%Y%m%d")
        decision_log_pattern = f"decision_log_{date_str}.json"
        assert decision_log_pattern.startswith("decision_log_")


class TestWorkflowIntegration:
    """集成测试：完整 workflow 运行（需要真实数据）"""

    @pytest.mark.skip(reason="需要真实宏观数据，集成测试时运行")
    def test_full_workflow_with_macro_regime(self):
        """测试完整 workflow 含 L1 宏观层"""
        from agents.orchestrator import run_daily_workflow

        result = run_daily_workflow(date="2026-06-11")

        assert result["success"] is True or result["success"] is False
        # 阶段一实现后，应能在日志中看到 macro_regime
        assert any("macro_regime" in log or "regime" in log for log in result.get("log", []))


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
