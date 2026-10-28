"""
ETF 动量轮动策略

正交维度：ETF 轮动（Trend-ETF）— 专用于 ETF 的相对+绝对动量
升级自 v1.0，新增：
  - 绝对动量过滤：策略动量低于无风险利率（年化2%折算为周期收益）时不发信号
    避免"市场整体下跌但相对强势的ETF"被误选
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal

_MIN_AMOUNT_5D = 1_000_000
_RISK_FREE_ANNUAL = 0.02  # 年化无风险利率，绝对动量基准


class ETFMomentumStrategy(BaseStrategy):
    """
    ETF 动量轮动策略

    参数：
      lookback   : 动量计算周期，默认 20 个交易日
      ma_trend   : 趋势均线周期，默认 60 日
      vol_penalty: 是否启用波动率惩罚，默认 True
      min_amount : 最低日均成交额（元），默认 100 万
      abs_momentum: 是否启用绝对动量过滤，默认 True
    """

    name = "ETF动量轮动"
    description = "相对动量+绝对动量过滤，选出跑赢无风险利率的强势ETF"
    version = "2.0.0"
    applicable_regimes = ["risk_on", "balanced"]
    style = "momentum"
    applicable_scenario = "趋势市ETF板块轮动；绝对动量为负时自动停发（市场整体熊市保护）"
    risk_profile = "balanced"
    time_horizon = "short"
    data_dependency = ["technical"]
    applicable_asset_types = ["etf"]   # 仅适用于 ETF，扫描引擎自动过滤
    market_condition = "bull"  # @deprecated

    def requires_bars(self) -> int:
        lookback = self.params.get("lookback", 20)
        ma_trend = self.params.get("ma_trend", 60)
        return max(lookback + 5, ma_trend + 5)

    def generate_signals(self, df: pd.DataFrame, code: str, name: str,
                         as_of=None) -> List[Signal]:
        lookback = self.params.get("lookback", 20)
        ma_trend = self.params.get("ma_trend", 60)
        vol_pen = self.params.get("vol_penalty", True)
        min_amt = self.params.get("min_amount", _MIN_AMOUNT_5D)
        abs_momentum = self.params.get("abs_momentum", True)

        if len(df) < lookback + 1:
            return []

        df = df.copy()

        if "amount" in df.columns:
            amt_5d = pd.to_numeric(df["amount"], errors="coerce").iloc[-5:].mean()
            if pd.notna(amt_5d) and amt_5d < min_amt:
                return []

        close = pd.to_numeric(df["close"], errors="coerce")
        if close.isna().any():
            return []

        last_close = float(close.iloc[-1])
        base_close = float(close.iloc[-lookback])
        if base_close <= 0:
            return []

        momentum = (last_close / base_close - 1) * 100

        # ── 绝对动量过滤：跑不赢无风险利率则不发 WATCH 信号（SELL 不受限）────
        if abs_momentum and momentum >= 0:
            rf_period = _RISK_FREE_ANNUAL * lookback / 252 * 100
            if momentum < rf_period:
                return []

        score = 50.0 + momentum * 2.0
        score = max(0.0, min(100.0, score))
        reason_parts = [f"{lookback}日动量 {momentum:+.1f}%"]

        if len(df) >= 20:
            ma20 = float(close.rolling(20).mean().iloc[-1])
            if last_close > ma20:
                score = min(score + 5.0, 100.0)
                reason_parts.append("MA20↑")

        if len(df) >= ma_trend:
            ma_long = float(close.rolling(ma_trend).mean().iloc[-1])
            if last_close > ma_long:
                score = min(score + 5.0, 100.0)
                reason_parts.append(f"MA{ma_trend}↑")

        if vol_pen and len(df) >= 21:
            daily_ret = close.pct_change().iloc[-20:]
            vol_annual = float(daily_ret.std() * np.sqrt(252) * 100)
            if vol_annual > 40:
                penalty = min((vol_annual - 40) * 0.5, 15.0)
                score = max(0.0, score - penalty)
                reason_parts.append(f"波动率{vol_annual:.0f}%↓")

        reason = "，".join(reason_parts)

        # 动量为负（已被绝对动量过滤拦截，但 abs_momentum=False 时可能到达此处）
        if momentum < 0:
            return [Signal(
                code=code, name=name, signal="SELL",
                price=last_close, reason=reason,
                score=max(0.0, 50.0 - abs(momentum) * 2.0),
            )]

        return [Signal(
            code=code, name=name, signal="WATCH",
            price=last_close, reason=reason, score=score,
        )]

    def compute_exit_levels(self, df, entry_price, fundamental_data=None):
        """ETF轮动出场：ATR宽止损（ETF波动小）+ 相对动量出场逻辑标注"""
        from strategies.position_manager import (
            calc_atr, calc_swing_low, calc_swing_high, build_scale_levels,
        )
        from strategies.base import ExitLevels, ScaleLevel
        from config.settings import RISK_CONTROL

        atr = calc_atr(df)
        # ETF波动小，用 2.5×ATR
        stop_loss = round(entry_price - atr * 2.5, 2)
        support = calc_swing_low(df, n=20)
        resistance = calc_swing_high(df, n=20)
        # 止盈：ETF轮动以相对动量为主，价格止盈设宽（3×ATR）
        take_profit = round(entry_price + atr * 5.0, 2)

        scale_cfg = RISK_CONTROL.get("scale_out", {})
        levels_raw = build_scale_levels(entry_price, stop_loss, take_profit,
                                        support=support, resistance=resistance,
                                        atr=atr, scale_cfg=scale_cfg)
        levels = [ScaleLevel(**l) for l in levels_raw]
        return ExitLevels(
            stop_loss=stop_loss, take_profit=take_profit, atr=atr,
            support=support, resistance=resistance,
            exit_logic="momentum_relative", scale_levels=levels,
            fundamental_exit="相对动量排名跌出 Top N 时轮出（需扫描时重新评估）",
        )
