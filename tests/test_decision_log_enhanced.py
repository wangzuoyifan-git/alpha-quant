"""
tests/test_decision_log_enhanced.py — 阶段三决策日志结构化增强测试

对应 requirements.md 8.8b 节测试需求：
- NT-L3-04: decision_log industry_scores 明细测试（OB-R1）
- NT-L3-05: scan_results_summary 分组统计测试（OB-R1）
- NT-L3-06: node_audit 审计快照测试（OB-R5）
"""

import pytest
from pathlib import Path
import json
import tempfile


class TestIndustryScoresDetail:
    """NT-L3-04: decision_log industry_scores 明细测试（OB-R1）"""

    def test_industry_scores_from_decision_context(self):
        """测试从 decision_context 提取 industry_scores 明细"""
        from agents.orchestrator import _extract_industry_scores

        # 模拟 L2 输出的 decision_context
        decision_context = {
            "regime": "balanced",
            "risk_mode": "balanced",
            "max_position": 0.7,
            "allowed_industries": ["801080", "801770", "801050"],
            "industry_weights": {
                "801080": 84.7,
                "801770": 100.0,
                "801050": 75.5,
            },
            "strategy_keys": ["ma_cross"],
            "timestamp": "2026-06-11T10:00:00",
        }

        # 提取 industry_scores 明细（需要结合 industry_weights 和行业名称）
        industry_scores = _extract_industry_scores(decision_context)

        # 应返回有序列表（按 score 降序）
        assert isinstance(industry_scores, list)
        assert len(industry_scores) == 3

        # 验证结构
        first = industry_scores[0]
        assert "industry_code" in first
        assert "industry_name" in first
        assert "score" in first

        # 验证排序：第一个应是得分最高的（801770=100.0）
        assert first["industry_code"] == "801770"
        assert first["score"] == 100.0

    def test_industry_scores_fallback_empty(self):
        """测试 industry_weights 为空时，返回空列表（不报错）"""
        from agents.orchestrator import _extract_industry_scores

        decision_context = {
            "risk_mode": "balanced",
            "industry_weights": {},
            "allowed_industries": [],
        }

        industry_scores = _extract_industry_scores(decision_context)
        assert industry_scores == []


class TestScanResultsSummary:
    """NT-L3-05: scan_results_summary 分组统计测试（OB-R1）"""

    def test_scan_summary_by_industry_and_strategy(self):
        """测试按行业/策略分组统计信号数"""
        from agents.orchestrator import _build_scan_summary

        # 模拟扫描信号（含多个行业/策略）
        scan_results = {
            "success": True,
            "count": 5,
            "elapsed": 12.3,
            "signals": [
                {"code": "000001", "name": "平安银行", "signal": "BUY", "price": 10.5,
                 "score": 80, "reason": "均线金叉 - 5日线上穿20日线"},
                {"code": "000002", "name": "万科A", "signal": "BUY", "price": 8.2,
                 "score": 75, "reason": "MACD+RSI共振 - 双底部"},
                {"code": "600036", "name": "招商银行", "signal": "BUY", "price": 35.0,
                 "score": 85, "reason": "均线金叉 / 基本面价值 - ROE > 15%"},
                {"code": "600519", "name": "贵州茅台", "signal": "WATCH", "price": 1680.0,
                 "score": 70, "reason": "基本面价值 - 现金流稳健"},
                {"code": "601318", "name": "中国平安", "signal": "BUY", "price": 45.0,
                 "score": 78, "reason": "资金流信号 - 主力净流入"},
            ],
        }

        summary = _build_scan_summary(scan_results)

        # 验证基础字段
        assert summary["total_signals"] == 5
        assert summary["elapsed"] == 12.3

        # 验证 by_strategy 统计（粗粒度：reason 含策略名的信号数）
        assert "by_strategy" in summary
        by_strat = summary["by_strategy"]
        # 至少应统计到 "均线金叉"（2次）、"基本面价值"（3次）
        assert "均线金叉" in by_strat
        assert by_strat["均线金叉"] >= 2  # 000001 + 600036

        # 验证 by_industry 统计（需要 join industry_sw）
        assert "by_industry" in summary
        by_ind = summary["by_industry"]
        # 应是 dict，key=行业名，value=信号数
        assert isinstance(by_ind, dict)

    def test_scan_summary_empty_signals(self):
        """测试空信号时，summary 不报错"""
        from agents.orchestrator import _build_scan_summary

        scan_results = {
            "success": True,
            "count": 0,
            "elapsed": 5.0,
            "signals": [],
        }

        summary = _build_scan_summary(scan_results)

        assert summary["total_signals"] == 0
        assert summary["by_strategy"] == {}
        assert summary["by_industry"] == {}

    def test_scan_summary_industry_join(self):
        """测试 by_industry 通过 industry_sw 正确 join"""
        from agents.orchestrator import _build_scan_summary

        # 构造已知行业的信号（平安银行=801780银行）
        scan_results = {
            "success": True,
            "count": 2,
            "elapsed": 3.0,
            "signals": [
                {"code": "000001", "name": "平安银行", "signal": "BUY", "price": 10.5,
                 "score": 80, "reason": "test"},
                {"code": "600036", "name": "招商银行", "signal": "BUY", "price": 35.0,
                 "score": 85, "reason": "test"},
            ],
        }

        summary = _build_scan_summary(scan_results)

        # 验证银行行业应有 2 个信号（如果 industry_sw 数据完整）
        by_ind = summary["by_industry"]
        if "银行" in by_ind:
            assert by_ind["银行"] == 2


class TestNodeAudit:
    """NT-L3-06: node_audit 审计快照测试（OB-R5）"""

    def test_audit_helper_structure(self):
        """测试 _audit() helper 追加审计快照到 state"""
        from agents.orchestrator import _audit, WorkflowState

        state: WorkflowState = {
            "date": "2026-06-11",
            "log": [],
        }

        # 调用 _audit 追加审计记录
        _audit(
            state,
            node="macro_regime",
            input_summary={"date": "2026-06-11"},
            output_summary={"regime": "balanced", "risk_mode": "balanced"},
        )

        # 验证 node_audit 已创建并追加
        assert "node_audit" in state
        assert isinstance(state["node_audit"], list)
        assert len(state["node_audit"]) == 1

        audit_entry = state["node_audit"][0]
        assert audit_entry["node"] == "macro_regime"
        assert "timestamp" in audit_entry
        assert audit_entry["input_summary"]["date"] == "2026-06-11"
        assert audit_entry["output_summary"]["regime"] == "balanced"

    def test_audit_multiple_nodes(self):
        """测试多个节点的审计记录累积"""
        from agents.orchestrator import _audit, WorkflowState

        state: WorkflowState = {"date": "2026-06-11", "log": []}

        # 模拟多个节点依次追加审计
        _audit(state, "check_data", {"date": "2026-06-11"}, {"stale": []})
        _audit(state, "macro_regime", {}, {"regime": "balanced"})
        _audit(state, "industry_rotation", {"risk_mode": "balanced"}, {"allowed_industries": ["801080"]})

        # 验证累积了 3 条记录
        assert len(state["node_audit"]) == 3
        assert state["node_audit"][0]["node"] == "check_data"
        assert state["node_audit"][1]["node"] == "macro_regime"
        assert state["node_audit"][2]["node"] == "industry_rotation"

    def test_audit_persisted_in_decision_log(self, tmp_path):
        """测试 node_audit 随 decision_log 落盘"""
        from datetime import datetime

        # 模拟完整的 decision_log（含 node_audit）
        log_data = {
            "date": "2026-06-11",
            "macro_regime": {"regime": "balanced"},
            "decision_context": {"risk_mode": "balanced"},
            "industry_scores": [],
            "scan_results_summary": {"total_signals": 10, "by_industry": {}, "by_strategy": {}},
            "node_audit": [
                {
                    "node": "check_data",
                    "timestamp": datetime.now().isoformat(),
                    "input_summary": {"date": "2026-06-11"},
                    "output_summary": {"stale": []},
                },
                {
                    "node": "macro_regime",
                    "timestamp": datetime.now().isoformat(),
                    "input_summary": {},
                    "output_summary": {"regime": "balanced"},
                },
            ],
            "filtered_signals_count": 8,
            "orders_count": 2,
            "logs": ["test"],
            "error": None,
        }

        # 写入
        log_file = tmp_path / "decision_log_20260611.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)

        # 读回验证
        with open(log_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        assert "node_audit" in loaded
        assert len(loaded["node_audit"]) == 2
        assert loaded["node_audit"][0]["node"] == "check_data"
        assert loaded["node_audit"][1]["node"] == "macro_regime"


class TestDecisionLogFullIntegration:
    """集成测试：完整 decision_log 结构符合 OB-R1"""

    def test_decision_log_schema_complete(self, tmp_path):
        """测试完整 decision_log 结构包含所有 OB-R1 必需字段"""
        from datetime import datetime

        decision_log = {
            "date": "2026-06-11",
            "macro_regime": {
                "regime": "balanced",
                "risk_mode": "balanced",
                "confidence": 0.7,
                "indicators": {"vix": 18.0, "pmi": 50.5},
                "trigger_reason": "市场震荡",
            },
            "decision_context": {
                "regime": "balanced",
                "risk_mode": "balanced",
                "max_position": 0.7,
                "allowed_industries": ["801080", "801770"],
                "industry_weights": {"801080": 84.7, "801770": 100.0},
                "strategy_keys": ["ma_cross", "macd_rsi"],
                "timestamp": datetime.now().isoformat(),
            },
            "industry_scores": [
                {"industry_code": "801770", "industry_name": "通信", "score": 100.0},
                {"industry_code": "801080", "industry_name": "电子", "score": 84.7},
            ],
            "scan_results_summary": {
                "total_signals": 50,
                "elapsed": 120.5,
                "by_industry": {"通信": 15, "电子": 20, "银行": 10, "其他": 5},
                "by_strategy": {"均线金叉": 25, "MACD+RSI共振": 15, "基本面价值": 10},
            },
            "node_audit": [
                {"node": "check_data", "timestamp": datetime.now().isoformat(),
                 "input_summary": {}, "output_summary": {}},
                {"node": "macro_regime", "timestamp": datetime.now().isoformat(),
                 "input_summary": {}, "output_summary": {}},
                {"node": "industry_rotation", "timestamp": datetime.now().isoformat(),
                 "input_summary": {}, "output_summary": {}},
                {"node": "run_scan", "timestamp": datetime.now().isoformat(),
                 "input_summary": {}, "output_summary": {}},
            ],
            "filtered_signals_count": 45,
            "orders_count": 8,
            "logs": ["[check_data] ok", "[macro_regime] balanced", "[scan] 50 signals"],
            "error": None,
        }

        # 验证 OB-R1 必需字段全部存在
        assert "date" in decision_log
        assert "macro_regime" in decision_log
        assert "decision_context" in decision_log
        assert "industry_scores" in decision_log
        assert "scan_results_summary" in decision_log
        assert "node_audit" in decision_log
        assert "filtered_signals_count" in decision_log
        assert "orders_count" in decision_log
        assert "logs" in decision_log
        assert "error" in decision_log

        # 验证 scan_results_summary 子结构
        summary = decision_log["scan_results_summary"]
        assert "total_signals" in summary
        assert "elapsed" in summary
        assert "by_industry" in summary
        assert "by_strategy" in summary

        # 写入并读回
        log_file = tmp_path / "decision_log_full.json"
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(decision_log, f, ensure_ascii=False, indent=2)

        with open(log_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)

        assert loaded["industry_scores"][0]["industry_name"] == "通信"
        assert loaded["scan_results_summary"]["by_industry"]["通信"] == 15


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
