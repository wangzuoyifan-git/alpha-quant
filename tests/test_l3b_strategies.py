"""
test_l3b_strategies.py — L3-B 基本面策略单元测试（台阶二）
B1 质量价值(GARP) / B2 深度价值 / B3 成长筛选 + select_strategies 画像匹配

数据量纲：fundamental 域来自 BaoStock，财务比率均为小数（ROE 0.15=15%）。
估值 pe_ttm/pb_mrq 来自 K线 df（market 域）。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.builtin.fundamental_value import FundamentalValueStrategy
from strategies.builtin.deep_value import DeepValueStrategy
from strategies.builtin.growth_pick import GrowthPickStrategy
from strategies.base import Signal


# ── 测试夹具：构造 K线 df（含 PE/PB）和财务 df ──

def make_kline(pe=20.0, pb=3.0, price=50.0, n=10):
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": price, "high": price * 1.02, "low": price * 0.98,
        "close": price, "volume": 1e6,
        "pe_ttm": pe, "pb_mrq": pb,
    })


def make_profit(roe=0.18, npmargin=0.2, gpmargin=0.4, n=4):
    """profit 域：roeAvg/npMargin/gpMargin（小数）

    roe 参数语义为 TTM（年化）ROE，按 4 季均分写入 roeAvg（单季），
    策略内部累加近 4 季还原 TTM。
    """
    dates = pd.date_range("2023-03-31", periods=n, freq="QE")
    q_roe = roe / 4  # 单季 = TTM / 4
    return pd.DataFrame({
        "roeAvg": [q_roe] * n, "npMargin": [npmargin] * n,
        "gpMargin": [gpmargin] * n, "netProfit": [1e9] * n,
    }, index=dates)


def make_growth(yoy_ni=0.30, yoy_equity=0.20, n=4):
    """growth 域：YOYNI/YOYEquity（小数）"""
    dates = pd.date_range("2023-03-31", periods=n, freq="QE")
    return pd.DataFrame({
        "YOYNI": [yoy_ni] * n, "YOYEquity": [yoy_equity] * n,
    }, index=dates)


def make_balance(debt=0.4, n=4):
    """balance 域：liabilityToAsset（小数）"""
    dates = pd.date_range("2023-03-31", periods=n, freq="QE")
    return pd.DataFrame({
        "liabilityToAsset": [debt] * n, "currentRatio": [2.0] * n,
    }, index=dates)


@pytest.fixture
def patch_fund(monkeypatch):
    """统一 mock 三个策略模块里的 load_fund"""
    store = {"profit": make_profit(), "growth": make_growth(), "balance": make_balance()}

    def fake_load(code, data_type, start=None, end=None):
        return store.get(data_type)

    import strategies.builtin.fundamental_value as b1
    import strategies.builtin.deep_value as b2
    import strategies.builtin.growth_pick as b3
    monkeypatch.setattr(b1, "load_fund", fake_load)
    monkeypatch.setattr(b2, "load_fund", fake_load)
    monkeypatch.setattr(b3, "load_fund", fake_load)
    # deep_value 行业排除依赖 meta 反查；默认 mock 为非金融地产行业（食品饮料 801120）
    import data.store_meta as meta
    monkeypatch.setattr(meta, "get_stock_l1_industry", lambda code: "801120")
    return store


# ════════════════════════════════════════════════════════════
# B1 质量价值（GARP）
# ════════════════════════════════════════════════════════════

class TestB1QualityValue:

    def test_profile_is_config_quality(self):
        s = FundamentalValueStrategy()
        assert s.style == "quality"
        prof = s.profile()
        assert prof["time_horizon"] == "medium"
        # 全 5 种 regime 适用
        assert len(s.applicable_regimes) == 5

    def test_high_quality_produces_watch_config(self, patch_fund):
        """高ROE + 合理PE/PB + 盈利增长 → WATCH/config"""
        s = FundamentalValueStrategy()
        df = make_kline(pe=20.0, pb=3.0)
        sigs = s.generate_signals(df, "600519", "贵州茅台")
        assert len(sigs) == 1
        assert sigs[0].signal == "WATCH"
        assert sigs[0].position_type == "config"
        assert sigs[0].time_horizon == "medium"

    def test_low_roe_rejected(self, patch_fund):
        patch_fund["profit"] = make_profit(roe=0.05)  # ROE 5% < 15%
        s = FundamentalValueStrategy()
        assert s.generate_signals(make_kline(), "000001", "x") == []

    def test_high_pe_rejected(self, patch_fund):
        s = FundamentalValueStrategy()
        # PE 50 > max_pe 30
        assert s.generate_signals(make_kline(pe=50.0), "000001", "x") == []

    def test_negative_pe_rejected(self, patch_fund):
        """亏损股（PE<=0）排除"""
        s = FundamentalValueStrategy()
        assert s.generate_signals(make_kline(pe=-10.0), "000001", "x") == []

    def test_params_configurable(self, patch_fund):
        """阈值可配置：放宽 max_pe 后 PE45 通过"""
        patch_fund["profit"] = make_profit(roe=0.18)
        s = FundamentalValueStrategy({"max_pe": 50.0})
        sigs = s.generate_signals(make_kline(pe=45.0), "600519", "x")
        assert len(sigs) == 1

    def test_score_in_range(self, patch_fund):
        s = FundamentalValueStrategy()
        sigs = s.generate_signals(make_kline(), "600519", "x")
        for sig in sigs:
            assert 0 <= sig.score <= 100

    def test_no_fundamental_data_returns_empty(self, monkeypatch):
        import strategies.builtin.fundamental_value as b1
        monkeypatch.setattr(b1, "load_fund", lambda c, t, **k: None)
        assert FundamentalValueStrategy().generate_signals(make_kline(), "x", "x") == []


# ════════════════════════════════════════════════════════════
# B2 深度价值
# ════════════════════════════════════════════════════════════

class TestB2DeepValue:

    def test_profile_is_config_value_long(self):
        s = DeepValueStrategy()
        assert s.style == "value"
        assert s.profile()["time_horizon"] == "long"
        assert len(s.applicable_regimes) == 5

    def test_deep_value_produces_watch(self, patch_fund):
        """低PB + 低PE + 盈利为正 + 负债可控 → WATCH/config/long"""
        s = DeepValueStrategy()
        df = make_kline(pe=10.0, pb=1.2)
        sigs = s.generate_signals(df, "600519", "贵州茅台")
        assert len(sigs) == 1
        assert sigs[0].signal == "WATCH"
        assert sigs[0].position_type == "config"
        assert sigs[0].time_horizon == "long"

    def test_finance_industry_excluded(self, patch_fund, monkeypatch):
        """金融/地产行业即便低PB低PE也应被排除（防伪低估）"""
        import data.store_meta as meta
        monkeypatch.setattr(meta, "get_stock_l1_industry", lambda code: "801780")  # 银行
        s = DeepValueStrategy()
        assert s.generate_signals(make_kline(pe=8.0, pb=0.8), "601398", "工商银行") == []

    def test_exclude_finance_disabled(self, patch_fund, monkeypatch):
        """exclude_finance=False 时金融股不被行业排除"""
        import data.store_meta as meta
        monkeypatch.setattr(meta, "get_stock_l1_industry", lambda code: "801780")
        s = DeepValueStrategy({"exclude_finance": False})
        sigs = s.generate_signals(make_kline(pe=8.0, pb=0.8), "601398", "工商银行")
        assert len(sigs) == 1

    def test_high_pb_rejected(self, patch_fund):
        s = DeepValueStrategy()
        # PB 3 > max_pb 1.5
        assert s.generate_signals(make_kline(pe=10.0, pb=3.0), "x", "x") == []

    def test_high_pe_rejected(self, patch_fund):
        s = DeepValueStrategy()
        assert s.generate_signals(make_kline(pe=20.0, pb=1.2), "x", "x") == []

    def test_negative_pe_rejected(self, patch_fund):
        s = DeepValueStrategy()
        assert s.generate_signals(make_kline(pe=-5.0, pb=1.0), "x", "x") == []

    def test_high_debt_rejected(self, patch_fund):
        patch_fund["balance"] = make_balance(debt=0.8)  # 80% > 60%
        s = DeepValueStrategy()
        assert s.generate_signals(make_kline(pe=10.0, pb=1.2), "x", "x") == []

    def test_params_configurable(self, patch_fund):
        s = DeepValueStrategy({"max_pb": 4.0})
        sigs = s.generate_signals(make_kline(pe=10.0, pb=3.0), "x", "x")
        assert len(sigs) == 1

    def test_score_in_range(self, patch_fund):
        s = DeepValueStrategy()
        for sig in s.generate_signals(make_kline(pe=10.0, pb=1.2), "x", "x"):
            assert 0 <= sig.score <= 100


# ════════════════════════════════════════════════════════════
# B3 成长筛选
# ════════════════════════════════════════════════════════════

class TestB3GrowthPick:

    def test_profile_is_config_growth(self):
        s = GrowthPickStrategy()
        assert s.style == "growth"
        assert s.profile()["time_horizon"] == "medium"
        assert len(s.applicable_regimes) == 5

    def test_high_growth_produces_watch(self, patch_fund):
        """高净利增长 + 高净资产增长 + ROE达标 → WATCH/config/medium"""
        s = GrowthPickStrategy()
        sigs = s.generate_signals(make_kline(), "300750", "宁德时代")
        assert len(sigs) == 1
        assert sigs[0].signal == "WATCH"
        assert sigs[0].position_type == "config"
        assert sigs[0].time_horizon == "medium"

    def test_low_ni_growth_rejected(self, patch_fund):
        patch_fund["growth"] = make_growth(yoy_ni=0.10)  # 10% < 25%
        s = GrowthPickStrategy()
        assert s.generate_signals(make_kline(), "x", "x") == []

    def test_low_equity_growth_rejected(self, patch_fund):
        patch_fund["growth"] = make_growth(yoy_ni=0.30, yoy_equity=0.05)
        s = GrowthPickStrategy()
        assert s.generate_signals(make_kline(), "x", "x") == []

    def test_low_roe_rejected(self, patch_fund):
        patch_fund["profit"] = make_profit(roe=0.05)
        s = GrowthPickStrategy()
        assert s.generate_signals(make_kline(), "x", "x") == []

    def test_params_configurable(self, patch_fund):
        patch_fund["growth"] = make_growth(yoy_ni=0.15, yoy_equity=0.20)
        s = GrowthPickStrategy({"min_ni_growth": 0.10})
        assert len(s.generate_signals(make_kline(), "x", "x")) == 1

    def test_score_in_range(self, patch_fund):
        s = GrowthPickStrategy()
        for sig in s.generate_signals(make_kline(), "x", "x"):
            assert 0 <= sig.score <= 100

    def test_no_data_returns_empty(self, monkeypatch):
        import strategies.builtin.growth_pick as b3
        monkeypatch.setattr(b3, "load_fund", lambda c, t, **k: None)
        assert GrowthPickStrategy().generate_signals(make_kline(), "x", "x") == []


# ════════════════════════════════════════════════════════════
# select_strategies 画像匹配（style + time_horizon 收窄）
# ════════════════════════════════════════════════════════════

class TestSelectStrategiesProfileMatch:

    def test_b_strategies_registered(self):
        from strategies.builtin import BUILTIN_STRATEGIES
        assert "fundamental_value" in BUILTIN_STRATEGIES
        assert "deep_value" in BUILTIN_STRATEGIES
        assert "growth_pick" in BUILTIN_STRATEGIES

    def test_medium_horizon_selects_b1_b3_not_b2(self):
        """allowed_time_horizons=[medium] 选 B1/B3（medium），不选 B2（long）"""
        from agents.strategy_agent import select_strategies
        keys = select_strategies({
            "regime": "balanced",
            "allowed_styles": [],
            "allowed_time_horizons": ["medium"],
        })
        assert "fundamental_value" in keys
        assert "growth_pick" in keys
        assert "deep_value" not in keys

    def test_long_horizon_selects_b2(self):
        from agents.strategy_agent import select_strategies
        keys = select_strategies({
            "regime": "balanced",
            "allowed_styles": [],
            "allowed_time_horizons": ["long"],
        })
        assert "deep_value" in keys

    def test_style_narrowing_value_only(self):
        """allowed_styles=[value] 只选 value 风格（B2 deep_value）"""
        from agents.strategy_agent import select_strategies
        keys = select_strategies({
            "regime": "balanced",
            "allowed_styles": ["value"],
            "allowed_time_horizons": ["short", "medium", "long"],
        })
        assert "deep_value" in keys
        assert "fundamental_value" not in keys  # quality 风格被排除
        assert "growth_pick" not in keys         # growth 风格被排除

    def test_quality_style_selects_b1(self):
        from agents.strategy_agent import select_strategies
        keys = select_strategies({
            "regime": "crisis",
            "allowed_styles": ["quality"],
            "allowed_time_horizons": ["medium"],
        })
        assert "fundamental_value" in keys
