"""
tests/test_strategy_schema.py — 策略 6 维能力画像 + Signal 仓位字段测试（阶段四）

对应 requirements.md 8.10 节：
- NT-L4-07: strategy schema 6 维字段完整（D-4）
- NT-L4-08: Signal 仓位归属字段（D-5）
- NT-L4-09: select_strategies 画像匹配（L3-R1 升级）
"""

import pytest


# 6 维画像必须字段（D-4）
_PROFILE_FIELDS = [
    "applicable_regimes",
    "style",
    "applicable_scenario",
    "risk_profile",
    "time_horizon",
    "data_dependency",
]


class TestStrategyProfile:
    """NT-L4-07: 6 维能力画像完整性"""

    def test_all_builtin_strategies_have_profile(self):
        from strategies.builtin import BUILTIN_STRATEGIES

        assert BUILTIN_STRATEGIES, "内置策略注册表为空"

        for key, cls in BUILTIN_STRATEGIES.items():
            inst = cls()
            assert hasattr(inst, "profile"), f"{key} 缺少 profile() 方法"
            prof = inst.profile()
            assert isinstance(prof, dict), f"{key}.profile() 应返回 dict"

            for field in _PROFILE_FIELDS:
                assert field in prof, f"{key} 画像缺少字段 {field}"

            # applicable_regimes 非空且合法
            regimes = prof["applicable_regimes"]
            assert isinstance(regimes, list) and regimes, \
                f"{key} applicable_regimes 应为非空 list"
            valid_regimes = {"crisis", "risk_off", "balanced", "risk_on", "volatile"}
            for r in regimes:
                assert r in valid_regimes, f"{key} applicable_regimes 含非法值 {r}"

            # risk_profile 合法
            assert prof["risk_profile"] in {"defensive", "balanced", "aggressive"}, \
                f"{key} risk_profile 非法: {prof['risk_profile']}"

            # time_horizon 合法
            assert prof["time_horizon"] in {"short", "medium", "long"}, \
                f"{key} time_horizon 非法: {prof['time_horizon']}"

            # style 非空
            assert prof["style"], f"{key} style 不应为空"

            # data_dependency 非空 list
            assert isinstance(prof["data_dependency"], list) and prof["data_dependency"], \
                f"{key} data_dependency 应为非空 list"

    def test_risk_profile_consistency(self):
        """风险风格与 regime 自洽（aggressive 不应同时适用 crisis）"""
        from strategies.builtin import BUILTIN_STRATEGIES

        for key, cls in BUILTIN_STRATEGIES.items():
            prof = cls().profile()
            if prof["risk_profile"] == "aggressive":
                assert "crisis" not in prof["applicable_regimes"], \
                    f"{key} aggressive 策略不应适用 crisis"


class TestSignalPositionFields:
    """NT-L4-08: Signal 仓位归属字段"""

    def test_signal_default_position_fields(self):
        from strategies.base import Signal

        sig = Signal(code="000001", name="平安银行", signal="BUY",
                     price=10.0, reason="测试")
        assert sig.position_type == "trading", "position_type 默认应为 trading"
        assert sig.time_horizon == "short", "time_horizon 默认应为 short"

    def test_signal_config_position(self):
        from strategies.base import Signal

        sig = Signal(code="600519", name="贵州茅台", signal="WATCH",
                     price=1500.0, reason="价值",
                     position_type="config", time_horizon="long")
        assert sig.position_type == "config"
        assert sig.time_horizon == "long"


class TestSelectStrategiesProfileMatch:
    """NT-L4-09: select_strategies 画像匹配"""

    def test_select_by_regime(self):
        from agents.strategy_agent import select_strategies
        from strategies.builtin import BUILTIN_STRATEGIES

        dc = {"regime": "risk_on", "risk_mode": "aggressive"}
        keys = select_strategies(dc)

        assert isinstance(keys, list) and keys, "应返回非空策略列表"
        # 返回的每个策略其 applicable_regimes 必须包含 risk_on
        for k in keys:
            prof = BUILTIN_STRATEGIES[k]().profile()
            assert "risk_on" in prof["applicable_regimes"], \
                f"{k} 不适用 risk_on，不应被选中"

    def test_select_defensive_excludes_aggressive_only(self):
        from agents.strategy_agent import select_strategies
        from strategies.builtin import BUILTIN_STRATEGIES

        dc = {"regime": "risk_off", "risk_mode": "defensive"}
        keys = select_strategies(dc)
        assert keys, "防御模式应返回非空策略列表"
        for k in keys:
            prof = BUILTIN_STRATEGIES[k]().profile()
            assert "risk_off" in prof["applicable_regimes"], \
                f"{k} 不适用 risk_off"

    def test_backward_compat_risk_mode_kwarg(self):
        """向后兼容：旧的 risk_mode= 关键字调用仍可用"""
        from agents.strategy_agent import select_strategies

        keys = select_strategies(risk_mode="aggressive")
        assert isinstance(keys, list) and keys
