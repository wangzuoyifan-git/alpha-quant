"""
内置策略三：量价突破策略
收盘价突破近N日高点，成交量明显放大
"""
from typing import List
import pandas as pd
from ..base import BaseStrategy, Signal


class VolPriceBreakoutStrategy(BaseStrategy):
    """
    量价突破策略
    参数：
      lookback: 突破参考周期（默认20日）
      vol_ratio: 成交量放大倍数（默认2.0）
      breakout_pct: 突破幅度下限（默认0.5%）
    """
    name = "量价突破"
    description = "价格突破近N日高点，配合明显放量确认突破有效性"
    version = "1.0.0"

    def requires_bars(self) -> int:
        return self.params.get("lookback", 20) + 10

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        lookback = self.params.get("lookback", 20)
        vol_ratio = self.params.get("vol_ratio", 2.0)
        breakout_pct = self.params.get("breakout_pct", 0.005)

        if len(df) < lookback + 2:
            return []

        df = df.copy()
        # 用昨日收盘前 lookback 天的最高价作为突破位
        prev_high = df["high"].iloc[-(lookback + 1):-1].max()
        last = df.iloc[-1]
        vol_ma20 = df["volume"].iloc[-21:-1].mean()

        signals = []

        # 突破确认
        if (last["close"] > prev_high * (1 + breakout_pct) and
                last["volume"] >= vol_ma20 * vol_ratio):
            breakout_pct_real = (last["close"] / prev_high - 1) * 100
            actual_vol_ratio = last["volume"] / vol_ma20
            score = 50.0 + min(breakout_pct_real * 10, 25.0) + min((actual_vol_ratio - vol_ratio) * 5, 25.0)

            signals.append(Signal(
                code=code, name=name,
                signal="BUY",
                price=float(last["close"]),
                reason=(f"突破{lookback}日高点¥{prev_high:.2f}"
                        f"（+{breakout_pct_real:.1f}%），"
                        f"量比{actual_vol_ratio:.1f}x"),
                score=min(score, 100.0),
            ))

        # 跌破近 N 日低点作为止盈/止损卖出信号
        prev_low = df["low"].iloc[-(lookback + 1):-1].min()
        if last["close"] < prev_low * (1 - breakout_pct):
            drop_pct = (prev_low / last["close"] - 1) * 100
            signals.append(Signal(
                code=code, name=name,
                signal="SELL",
                price=float(last["close"]),
                reason=f"跌破{lookback}日低点¥{prev_low:.2f}（-{drop_pct:.1f}%）",
                score=60.0,
            ))

        return signals
