"""
tests/test_decision_context_fields.py — decision_context 多周期字段测试（台阶一）

- build_decision_context 含 allocation/l2_weight_config/allowed_styles/
  allowed_time_horizons/industry_detail/industry_horizon_map
- industry_rotation_node 回填这些字段
- scan_node 落盘 WATCH 配置仓候选到 watch_pool
"""
import pytest


class TestBuildDecisionContextFields:
    """L1-R3 decision_context 多周期字段完整性"""

    def _env(self, regime="balanced", risk_mode="balanced"):
        return {
            "regime": regime, "risk_mode": risk_mode,
            "confidence": 0.6, "indicators": {},
            "trigger_reason": "test",
        }

    def test_allocation_present(self):
        from agents.strategy_agent import build_decision_context
        ctx = build_decision_context(self._env("risk_on", "aggressive"))
        assert "allocation" in ctx
        assert "position_pct" in ctx["allocation"]
        assert "trading_pct" in ctx["allocation"]
        assert abs(ctx["allocation"]["position_pct"] + ctx["allocation"]["trading_pct"] - 1.0) < 1e-9

    def test_l2_weight_config_present(self):
        from agents.strategy_agent import build_decision_context
        ctx = build_decision_context(self._env("risk_off", "defensive"))
        assert "l2_weight_config" in ctx
        wc = ctx["l2_weight_config"]
        assert all(k in wc for k in ("w1", "w2", "w3"))
        # 台阶二景气度接入后 w1>0（防御市重基本面）
        assert wc["w1"] > 0.0
        assert abs(wc["w1"] + wc["w2"] + wc["w3"] - 1.0) < 1e-9

    def test_allowed_styles_present_empty_by_default(self):
        from agents.strategy_agent import build_decision_context
        ctx = build_decision_context(self._env())
        assert "allowed_styles" in ctx
        assert isinstance(ctx["allowed_styles"], list)

    def test_allowed_time_horizons_default_short(self):
        from agents.strategy_agent import build_decision_context
        ctx = build_decision_context(self._env())
        assert "allowed_time_horizons" in ctx
        assert "short" in ctx["allowed_time_horizons"]

    def test_industry_detail_and_horizon_map_present(self):
        from agents.strategy_agent import build_decision_context
        ctx = build_decision_context(self._env())
        assert "industry_detail" in ctx
        assert "industry_horizon_map" in ctx

    def test_allocation_varies_by_risk_mode(self):
        from agents.strategy_agent import build_decision_context
        def_ctx = build_decision_context(self._env("risk_off", "defensive"))
        agg_ctx = build_decision_context(self._env("risk_on", "aggressive"))
        # defensive：配置仓比例 > 进攻模式
        assert def_ctx["allocation"]["position_pct"] > agg_ctx["allocation"]["position_pct"]

    def test_l2_weight_varies_by_regime(self):
        from agents.strategy_agent import build_decision_context
        bull = build_decision_context(self._env("risk_on", "aggressive"))
        bear = build_decision_context(self._env("risk_off", "defensive"))
        # risk_on 资金流权重更大
        assert bull["l2_weight_config"]["w3"] >= bear["l2_weight_config"]["w3"]


class TestIndustryRotationNodeFillsContext:
    """industry_rotation_node 回填 allowed_styles/industry_detail/horizon_map"""

    def test_node_fills_industry_detail(self, monkeypatch):
        from agents import orchestrator
        from agents import industry_agent

        monkeypatch.setattr(industry_agent, "evaluate_industry_rotation", lambda **kw: {
            "success": True,
            "allowed_industries": ["801080", "801770"],
            "industry_scores": [
                {"industry_code": "801080", "industry_name": "电子", "score": 75.0, "net_amount_sum": 1e8},
                {"industry_code": "801770", "industry_name": "通信", "score": 45.0, "net_amount_sum": 5e7},
            ],
            "industry_detail": {
                "801080": {"industry_name": "电子", "score": 75.0, "time_horizon": "short",
                           "moneyflow": 1e8, "prosperity": "pending", "valuation": "pending"},
                "801770": {"industry_name": "通信", "score": 45.0, "time_horizon": "medium",
                           "moneyflow": 5e7, "prosperity": "pending", "valuation": "pending"},
            },
            "industry_horizon_map": {"801080": "short", "801770": "medium"},
            "allowed_styles": [],
            "fallback": False,
            "risk_mode": "balanced",
        })

        state = {"decision_context": {"regime": "balanced", "risk_mode": "balanced",
                                       "allowed_industries": [], "strategy_keys": [],
                                       "allowed_styles": [], "allowed_time_horizons": ["short"],
                                       "industry_detail": {}, "industry_horizon_map": {}}}
        out = orchestrator.industry_rotation_node(state)
        ctx = out["decision_context"]

        assert "801080" in ctx["allowed_industries"]
        assert "industry_detail" in ctx and "801080" in ctx["industry_detail"]
        assert "industry_horizon_map" in ctx
        assert "medium" in ctx["allowed_time_horizons"]
        assert "short" in ctx["allowed_time_horizons"]

    def test_node_recalculates_strategy_keys_after_l2(self, monkeypatch):
        """L2 回填后重算 strategy_keys（画像匹配用新的 allowed_styles）"""
        from agents import orchestrator, industry_agent

        monkeypatch.setattr(industry_agent, "evaluate_industry_rotation", lambda **kw: {
            "success": True,
            "allowed_industries": ["801080"],
            "industry_scores": [
                {"industry_code": "801080", "industry_name": "电子", "score": 80.0, "net_amount_sum": 1e8},
            ],
            "industry_detail": {"801080": {"industry_name": "电子", "score": 80.0,
                                            "time_horizon": "short", "moneyflow": 1e8,
                                            "prosperity": "pending", "valuation": "pending"}},
            "industry_horizon_map": {"801080": "short"},
            "allowed_styles": [],
            "fallback": False,
            "risk_mode": "risk_on",
        })

        state = {"decision_context": {"regime": "risk_on", "risk_mode": "aggressive",
                                       "allowed_industries": [], "strategy_keys": [],
                                       "allowed_styles": [], "allowed_time_horizons": ["short"],
                                       "industry_detail": {}, "industry_horizon_map": {}}}
        out = orchestrator.industry_rotation_node(state)
        # strategy_keys 必须被重算（非空）
        assert out["decision_context"]["strategy_keys"]
        assert out.get("strategy_keys")


class TestScanNodeWatchPool:
    """scan_node 落盘 WATCH 信号到 watch_pool（D-7 L3-B 候选链路）"""

    def test_watch_signals_separated_to_watch_pool(self, monkeypatch):
        from agents import orchestrator
        from agent_tools import strategy_tools

        monkeypatch.setattr(
            "agent_tools.run_scan",
            lambda **kw: {
                "success": True, "count": 3, "elapsed": 0.1,
                "signals": [
                    {"code": "000001", "signal": "BUY", "score": 80.0, "reason": "ma cross"},
                    {"code": "600519", "signal": "WATCH", "score": 70.0, "reason": "value",
                     "position_type": "config"},
                    {"code": "000002", "signal": "WATCH", "score": 60.0, "reason": "value2",
                     "position_type": "config"},
                ],
            }
        )

        state = {"decision_context": {"allowed_industries": []}, "strategy_keys": []}
        out = orchestrator.scan_node(state)

        assert "watch_pool" in out
        assert len(out["watch_pool"]) == 2
        assert all(s["signal"] == "WATCH" for s in out["watch_pool"])

    def test_no_watch_signals_no_watch_pool(self, monkeypatch):
        from agents import orchestrator

        monkeypatch.setattr(
            "agent_tools.run_scan",
            lambda **kw: {
                "success": True, "count": 1, "elapsed": 0.1,
                "signals": [{"code": "000001", "signal": "BUY", "score": 80.0, "reason": "x"}],
            }
        )

        state = {"decision_context": {"allowed_industries": []}, "strategy_keys": []}
        out = orchestrator.scan_node(state)
        assert out.get("watch_pool") is None or out.get("watch_pool") == []
