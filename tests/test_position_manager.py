"""
test_position_manager.py — 持仓管理计算器单元测试
覆盖：ATR/支撑压力/Supertrend、仓位计算、R倍数、分级操作表、出场触发、OCA、移动止损
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.position_manager import (
    calc_atr, calc_swing_low, calc_swing_high, calc_supertrend, calc_bollinger,
    calc_position_size, calc_r_ratio, build_scale_levels,
    check_exit_triggered, update_trail_stop,
)
from strategies.base import BaseStrategy, ExitLevels, ScaleLevel, Signal


def make_df(n=100, seed=1, base=100.0, trend=0.2):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    close = base + np.arange(n) * trend + rng.normal(0, 1, n).cumsum() * 0.3
    close = np.maximum(close, 1.0)
    high = close + rng.uniform(0.2, 1.0, n)
    low = close - rng.uniform(0.2, 1.0, n)
    return pd.DataFrame({
        "date": dates, "open": close, "high": high, "low": low,
        "close": close, "volume": rng.uniform(1e6, 5e6, n),
    })


# ── 底层价位计算 ──────────────────────────────────────────────

def test_calc_atr_positive():
    df = make_df()
    atr = calc_atr(df)
    assert atr > 0

def test_calc_atr_insufficient_data():
    df = make_df(n=5)
    atr = calc_atr(df)
    assert atr > 0  # 不足周期用 high-low 均值兜底，不崩

def test_swing_low_high_relationship():
    df = make_df()
    lo = calc_swing_low(df, n=20)
    hi = calc_swing_high(df, n=20)
    assert lo < hi

def test_supertrend_bands():
    df = make_df()
    up, dn = calc_supertrend(df)
    assert up > dn  # 上轨 > 下轨

def test_bollinger_order():
    df = make_df()
    up, mid, lo = calc_bollinger(df)
    assert up > mid > lo


# ── 仓位计算 ──────────────────────────────────────────────────

def test_position_size_basic():
    # 风险距=10, 资金100万, 风险1% → 风险额1万 / 10 = 1000股，但受max_position约束
    size = calc_position_size(entry_price=100, stop_loss=90, capital=1_000_000,
                              risk_per_trade=0.01, max_position_pct=0.10)
    # max: 100万*10%/100 = 1000股；by_risk: 1万/10=1000股 → 1000
    assert size == 1000

def test_position_size_round_to_100():
    size = calc_position_size(entry_price=33.3, stop_loss=30.0, capital=1_000_000,
                              risk_per_trade=0.01)
    assert size % 100 == 0

def test_position_size_zero_stop():
    # 配置仓 stop_loss=0 → 返回 0
    assert calc_position_size(100, 0, 1_000_000) == 0

def test_position_size_stop_above_entry():
    # 止损价 >= 进场价 → 返回 0，不崩
    assert calc_position_size(100, 100, 1_000_000) == 0
    assert calc_position_size(100, 110, 1_000_000) == 0

def test_position_size_max_constraint():
    # 风险距极小，by_risk 会很大，但受 max_position 约束
    size = calc_position_size(entry_price=100, stop_loss=99.9, capital=1_000_000,
                              risk_per_trade=0.01, max_position_pct=0.10)
    assert size <= 1000  # max: 100万*10%/100=1000股

def test_r_ratio():
    assert calc_r_ratio(100, 90, 120) == 2.0
    assert calc_r_ratio(100, 90, 110) == 1.0

def test_r_ratio_zero_stop():
    assert calc_r_ratio(100, 0, 120) == 0.0


# ── 分级操作表 ────────────────────────────────────────────────

def test_build_scale_levels_structure():
    levels = build_scale_levels(entry_price=100, stop_loss=90, take_profit=120,
                                support=95, resistance=115, atr=5)
    types = [l["level_type"] for l in levels]
    assert "take_1" in types and "take_2" in types
    assert "soft_stop" in types and "hard_stop" in types

def test_build_scale_levels_oca_group():
    levels = build_scale_levels(100, 90, 120, 95, 115, 5)
    stops = [l for l in levels if l["cancels_group"] == "stop"]
    assert len(stops) == 2  # soft_stop + hard_stop 同组

def test_build_scale_levels_hard_stop_is_stop_loss():
    levels = build_scale_levels(100, 90, 120, 95, 115, 5)
    hard = [l for l in levels if l["level_type"] == "hard_stop"][0]
    assert hard["price"] == 90
    assert hard["size_pct"] == -1.0


# ── 出场触发检查 ──────────────────────────────────────────────

def _sample_exit_dict():
    return {
        "exit_logic": "atr",
        "stop_loss": 90.0,
        "scale_levels": build_scale_levels(100, 90, 120, 95, 115, 5),
    }

def test_check_exit_stop_triggered():
    el = _sample_exit_dict()
    # 价格跌破 hard_stop 90
    trig = check_exit_triggered(89, el)
    assert trig is not None
    assert trig["action"] in ("stop_loss", "reduce")

def test_check_exit_profit_triggered():
    el = _sample_exit_dict()
    # 价格涨过 take_1（resistance=115）
    trig = check_exit_triggered(116, el)
    assert trig is not None
    assert trig["action"] == "take_profit"

def test_check_exit_none():
    el = _sample_exit_dict()
    # 价格在区间内，不触发
    trig = check_exit_triggered(105, el)
    assert trig is None


# ── 移动止损 ──────────────────────────────────────────────────

def test_update_trail_stop_supertrend_only_up():
    df = make_df(trend=0.5)  # 上行趋势
    el = {"exit_logic": "trail_supertrend", "stop_loss": 50.0,
          "scale_levels": build_scale_levels(100, 50, 150, 95, 120, 5)}
    new_el = update_trail_stop(el, df)
    # 止损只能上移
    assert new_el["stop_loss"] >= 50.0

def test_update_trail_stop_ma20():
    df = make_df(trend=0.5)
    el = {"exit_logic": "trail_ma20", "stop_loss": 10.0,
          "scale_levels": build_scale_levels(100, 10, 150, 95, 120, 5)}
    new_el = update_trail_stop(el, df)
    assert new_el["stop_loss"] >= 10.0

def test_update_trail_stop_atr_unchanged():
    # 非 trail 逻辑不更新
    df = make_df()
    el = {"exit_logic": "atr", "stop_loss": 90.0, "scale_levels": []}
    new_el = update_trail_stop(el, df)
    assert new_el["stop_loss"] == 90.0


# ── BaseStrategy.compute_exit_levels 默认实现 ────────────────────

class _DummyStrategy(BaseStrategy):
    def generate_signals(self, df, code, name):
        return []

def test_default_compute_exit_levels():
    df = make_df()
    s = _DummyStrategy()
    price = float(df.iloc[-1]["close"])
    el = s.compute_exit_levels(df, entry_price=price)
    assert isinstance(el, ExitLevels)
    assert el.stop_loss > 0
    assert el.take_profit > price
    assert el.atr > 0
    assert el.exit_logic == "atr"
    assert len(el.scale_levels) == 4

def test_exit_levels_to_dict():
    df = make_df()
    s = _DummyStrategy()
    el = s.compute_exit_levels(df, entry_price=float(df.iloc[-1]["close"]))
    d = el.to_dict()
    assert "stop_loss" in d and "scale_levels" in d
    assert isinstance(d["scale_levels"], list)
    assert all("price" in l for l in d["scale_levels"])
