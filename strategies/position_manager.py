"""
持仓管理计算器 — 无状态工具模块

提供：
  底层价位计算：calc_atr / calc_swing_low / calc_swing_high / calc_supertrend
  仓位管理：calc_position_size / calc_r_ratio
  分级操作表构建：build_scale_levels
  出场触发检查：check_exit_triggered（供回测引擎逐根K线调用）
  移动止损更新：update_trail_stop
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from strategies.base import ExitLevels, ScaleLevel


# ══════════════════════════════════════════════════════════════
# 底层价位计算
# ══════════════════════════════════════════════════════════════

def calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """ATR(period)，返回最新一根K线的值。df 需含 high/low/close 列。"""
    if len(df) < period + 1:
        # 数据不足：用 high-low 均值估算
        return float((df["high"].astype(float) - df["low"].astype(float)).mean())
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return float(atr.iloc[-1])


def calc_swing_low(df: pd.DataFrame, n: int = 20) -> float:
    """近 n 根K线的最低 low，作为近支撑位。"""
    return float(df["low"].astype(float).iloc[-n:].min())


def calc_swing_high(df: pd.DataFrame, n: int = 20) -> float:
    """近 n 根K线的最高 high，作为近压力位。"""
    return float(df["high"].astype(float).iloc[-n:].max())


def calc_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """
    计算 Supertrend 指标。
    返回 (upper_band, lower_band) — 当前K线的上下轨。
    上轨：趋势向上时的阻力线（止盈参考）
    下轨：趋势向上时跟随的移动止损线
    """
    if len(df) < period + 2:
        close = float(df["close"].astype(float).iloc[-1])
        atr = calc_atr(df, period)
        return close + multiplier * atr, close - multiplier * atr

    high = df["high"].astype(float).reset_index(drop=True)
    low = df["low"].astype(float).reset_index(drop=True)
    close = df["close"].astype(float).reset_index(drop=True)

    hl2 = (high + low) / 2
    atr_s = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1).ewm(span=period, adjust=False).mean()

    upper = hl2 + multiplier * atr_s
    lower = hl2 - multiplier * atr_s

    # 修正上下轨（不允许反向收窄）
    for i in range(1, len(df)):
        upper.iloc[i] = min(upper.iloc[i], upper.iloc[i-1]) if close.iloc[i-1] <= upper.iloc[i-1] else upper.iloc[i]
        lower.iloc[i] = max(lower.iloc[i], lower.iloc[i-1]) if close.iloc[i-1] >= lower.iloc[i-1] else lower.iloc[i]

    return float(upper.iloc[-1]), float(lower.iloc[-1])


def calc_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0):
    """布林带。返回 (upper, mid, lower)。"""
    close = df["close"].astype(float)
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return float((mid + std_dev * std).iloc[-1]), float(mid.iloc[-1]), float((mid - std_dev * std).iloc[-1])


# ══════════════════════════════════════════════════════════════
# 仓位管理
# ══════════════════════════════════════════════════════════════

def calc_position_size(
    entry_price: float,
    stop_loss: float,
    capital: float,
    risk_per_trade: float = 0.01,
    max_position_pct: float = 0.10,
) -> int:
    """
    基于风险的仓位计算（Fixed Risk sizing）。
    建议股数 = floor( capital × risk_per_trade / (entry_price - stop_loss) / 100 ) × 100
    同时受 max_position_pct 约束（不超过总资金的此比例）。
    stop_loss=0 时（配置仓无价格止损）返回 0，调用方按固定比例处理。
    """
    if stop_loss <= 0 or entry_price <= stop_loss:
        return 0
    risk_amount = capital * risk_per_trade
    risk_per_share = entry_price - stop_loss
    shares_by_risk = math.floor(risk_amount / risk_per_share / 100) * 100
    max_shares = math.floor(capital * max_position_pct / entry_price / 100) * 100
    return max(min(shares_by_risk, max_shares), 0)


def calc_r_ratio(entry_price: float, stop_loss: float, take_profit: float) -> float:
    """
    R 倍数 = (take_profit - entry_price) / (entry_price - stop_loss)
    止损=0（配置仓）时返回 0.0。
    """
    if stop_loss <= 0 or entry_price <= stop_loss:
        return 0.0
    return round((take_profit - entry_price) / (entry_price - stop_loss), 2)


# ══════════════════════════════════════════════════════════════
# 分级操作表构建
# ══════════════════════════════════════════════════════════════

def build_scale_levels(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    support: float = 0.0,
    resistance: float = 0.0,
    atr: float = 0.0,
    scale_cfg: dict = None,
) -> list:
    """
    按标准模板构建技术仓分级操作表（4档）。
    配置仓（stop_loss=0）不调用此函数，由各配置仓策略自行构建基本面档位。

    返回 List[dict]（避免循环依赖，不直接引用 ScaleLevel dataclass）
    """
    cfg = scale_cfg or {}
    take1_pct = cfg.get("take_1_pct", 0.33)
    take2_pct = cfg.get("take_2_pct", 0.50)
    soft_pct  = cfg.get("soft_stop_pct", 0.50)

    # 止盈档：优先用 resistance，没有时用 take_profit
    t1_price = resistance if resistance > entry_price else take_profit
    t2_price = round(entry_price + (take_profit - entry_price) * 1.5, 2) if atr > 0 else take_profit

    # 近支撑止损档：优先用 support，没有时用 entry - 1×ATR
    soft_price = support if 0 < support < entry_price else (
        round(entry_price - atr, 2) if atr > 0 else round(entry_price * 0.97, 2)
    )
    # soft_stop 不能低于 hard_stop
    soft_price = max(soft_price, stop_loss + 0.01) if stop_loss > 0 else soft_price

    return [
        {"price": round(t1_price, 2),   "level_type": "take_1",    "action": "take_profit",
         "size_pct": -take1_pct, "reason": "近压力位/止盈目标1", "cancels_group": ""},
        {"price": round(t2_price, 2),   "level_type": "take_2",    "action": "take_profit",
         "size_pct": -take2_pct, "reason": "延伸止盈目标2",     "cancels_group": ""},
        {"price": round(soft_price, 2), "level_type": "soft_stop", "action": "reduce",
         "size_pct": -soft_pct,  "reason": "跌破近支撑减仓",    "cancels_group": "stop"},
        {"price": round(stop_loss, 2),  "level_type": "hard_stop", "action": "stop_loss",
         "size_pct": -1.0,       "reason": f"ATR动态止损",       "cancels_group": "stop"},
    ]


# ══════════════════════════════════════════════════════════════
# 回测引擎工具
# ══════════════════════════════════════════════════════════════

def check_exit_triggered(
    current_price: float,
    exit_levels_dict: dict,
    triggered_groups: set = None,
) -> dict | None:
    """
    检查当前价是否触发分级操作表中的某一档。
    triggered_groups: 已触发的 cancels_group 集合（OCA语义：同组只触发一次）
    返回触发的档位 dict，或 None。
    止盈档：current_price >= price 触发
    止损档：current_price <= price 触发
    """
    cancelled = triggered_groups or set()
    levels = exit_levels_dict.get("scale_levels", [])

    def _available(lv):
        grp = lv.get("cancels_group", "")
        return grp == "" or grp not in cancelled

    stop_levels   = [l for l in levels if l["action"] in ("stop_loss", "reduce") and _available(l)]
    profit_levels = [l for l in levels if l["action"] == "take_profit" and _available(l)]

    # 止损：价格 <= 档位价触发，从高到低检查（最先跌破的先触发）
    for lv in sorted(stop_levels, key=lambda x: -x["price"]):
        if current_price <= lv["price"]:
            return lv
    # 止盈：价格 >= 档位价触发，从低到高检查（最先到达的先触发）
    for lv in sorted(profit_levels, key=lambda x: x["price"]):
        if current_price >= lv["price"]:
            return lv
    return None


def update_trail_stop(
    exit_levels_dict: dict,
    df: pd.DataFrame,
    supertrend_period: int = 10,
    supertrend_mult: float = 3.0,
) -> dict:
    """
    更新移动止损位（止损只能上移，不能下移）。
    exit_logic=trail_supertrend: 重算Supertrend下轨，取 max(old, new)
    exit_logic=trail_ma20:       取 max(old, 当日MA20)
    返回更新后的 exit_levels_dict（浅拷贝）。
    """
    import copy
    el = copy.copy(exit_levels_dict)
    logic = el.get("exit_logic", "atr")
    old_stop = el.get("stop_loss", 0.0)

    if logic == "trail_supertrend":
        _, lower = calc_supertrend(df, supertrend_period, supertrend_mult)
        el["stop_loss"] = max(old_stop, lower)
    elif logic == "trail_ma20":
        ma20 = float(df["close"].astype(float).rolling(20).mean().iloc[-1])
        el["stop_loss"] = max(old_stop, ma20)

    # 同步更新 scale_levels 里的 hard_stop 价位
    new_stop = el["stop_loss"]
    levels = el.get("scale_levels", [])
    for lv in levels:
        if lv.get("level_type") == "hard_stop":
            lv["price"] = round(new_stop, 2)
            break
    return el
