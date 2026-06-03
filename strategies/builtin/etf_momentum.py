"""
ETF 动量轮动策略

逻辑：
  计算每只 ETF 近 lookback 日的价格动量（涨幅），返回动量评分。
  扫描引擎汇总所有 ETF 的评分后，取 Top K 作为持仓候选（BUY/WATCH）。

评分设计：
  - 基础分：50 + momentum * 2（动量每涨 1% 加 2 分，上限 100）
  - 趋势加分：价格在 MA20 之上 +5，在 MA60 之上再 +5
  - 波动惩罚：近 20 日波动率（年化）超过 40% 时扣分，避免选到暴涨暴跌品种
  - 流动性过滤：近 5 日均成交额 < 1000 万则跳过（防止流动性陷阱）

信号类型：
  - WATCH：动量为正，进入候选池（由引擎 Top K 筛选后决定是否买入）
  - SELL ：动量为负（近期下跌），提示减仓/离场
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal

# 最低流动性门槛：近 5 日均成交额（元）
_MIN_AMOUNT_5D = 1_000_000  # 100 万，ETF 流动性普遍较好，设低一点


class ETFMomentumStrategy(BaseStrategy):
    """
    ETF 动量轮动策略

    参数：
      lookback   : 动量计算周期，默认 20 个交易日
      ma_trend   : 趋势均线周期，默认 60 日（用于趋势加分）
      vol_penalty: 是否启用波动率惩罚，默认 True
      min_amount : 最低日均成交额（元），默认 100 万
    """

    name = "ETF动量轮动"
    description = "计算近期价格动量，选出涨幅最强的 ETF 作为轮动候选"
    version = "1.0.0"
    market_condition = "bull"  # 动量策略依赖趋势延续

    def requires_bars(self) -> int:
        lookback = self.params.get("lookback", 20)
        ma_trend = self.params.get("ma_trend", 60)
        # 需要足够的历史数据计算趋势均线和波动率
        return max(lookback + 5, ma_trend + 5)

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        lookback  = self.params.get("lookback", 20)
        ma_trend  = self.params.get("ma_trend", 60)
        vol_pen   = self.params.get("vol_penalty", True)
        min_amt   = self.params.get("min_amount", _MIN_AMOUNT_5D)

        if len(df) < lookback + 1:
            return []

        df = df.copy()

        # ── 流动性过滤 ──────────────────────────────────────────────
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

        # ── 核心动量：近 lookback 日涨幅（%）──────────────────────
        momentum = (last_close / base_close - 1) * 100

        # ── 基础评分：50 分为中性，动量每 +1% 加 2 分 ─────────────
        score = 50.0 + momentum * 2.0
        score = max(0.0, min(100.0, score))

        reason_parts = [f"{lookback}日动量 {momentum:+.1f}%"]

        # ── 趋势加分：价格在均线之上 ────────────────────────────────
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

        # ── 波动率惩罚：年化波动率 > 40% 扣分 ──────────────────────
        if vol_pen and len(df) >= 21:
            daily_ret = close.pct_change().iloc[-20:]
            vol_annual = float(daily_ret.std() * np.sqrt(252) * 100)  # 年化波动率 %
            if vol_annual > 40:
                penalty = min((vol_annual - 40) * 0.5, 15.0)
                score = max(0.0, score - penalty)
                reason_parts.append(f"波动率{vol_annual:.0f}%↓")

        reason = "，".join(reason_parts)

        # ── 动量为负：发出 SELL 信号 ────────────────────────────────
        if momentum < 0:
            return [Signal(
                code=code, name=name,
                signal="SELL",
                price=last_close,
                reason=reason,
                score=max(0.0, 50.0 - abs(momentum) * 2.0),
            )]

        # ── 动量为正：发出 WATCH 信号，进入 Top K 候选池 ────────────
        return [Signal(
            code=code, name=name,
            signal="WATCH",
            price=last_close,
            reason=reason,
            score=score,
        )]
