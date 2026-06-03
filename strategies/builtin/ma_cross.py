"""
内置策略一：均线金叉策略
MA5 上穿 MA20，且量能放大
"""
from typing import List
import pandas as pd
from ..base import BaseStrategy, Signal


class MACrossStrategy(BaseStrategy):
    """
    均线金叉/死叉策略
    参数：
      fast_period: 快线周期（默认5）
      slow_period: 慢线周期（默认20）
      volume_ratio: 放量倍数（默认1.5）
    """
    name = "均线金叉"
    description = "快均线上穿慢均线，同时成交量放大"
    version = "1.0.0"
    market_condition = "range"  # 趋势启动初期的震荡转势

    def requires_bars(self) -> int:
        return max(self.params.get("slow_period", 20) * 2, 60)

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        fast = self.params.get("fast_period", 5)
        slow = self.params.get("slow_period", 20)
        vol_ratio = self.params.get("volume_ratio", 1.5)

        if len(df) < slow + 2:
            return []

        df = df.copy()
        df["ma_fast"] = df["close"].rolling(fast).mean()
        df["ma_slow"] = df["close"].rolling(slow).mean()
        df["vol_ma"] = df["volume"].rolling(20).mean()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        signals = []

        # 金叉：快线由下方穿越慢线
        if (prev["ma_fast"] <= prev["ma_slow"] and
                last["ma_fast"] > last["ma_slow"]):
            # 量能放大校验
            vol_ok = last["volume"] >= last["vol_ma"] * vol_ratio
            score = 60.0 + (20.0 if vol_ok else 0.0)
            # 趋势加分（快线斜率）
            slope = (last["ma_fast"] - df.iloc[-3]["ma_fast"]) / df.iloc[-3]["ma_fast"] * 100
            score += min(slope * 5, 20.0)

            reason = f"MA{fast}上穿MA{slow}"
            if vol_ok:
                reason += f"，量能放大{last['volume']/last['vol_ma']:.1f}x"
            signals.append(Signal(
                code=code, name=name,
                signal="BUY",
                price=float(last["close"]),
                reason=reason,
                score=min(score, 100.0),
            ))

        # 死叉：快线由上方跌破慢线
        elif (prev["ma_fast"] >= prev["ma_slow"] and
              last["ma_fast"] < last["ma_slow"]):
            signals.append(Signal(
                code=code, name=name,
                signal="SELL",
                price=float(last["close"]),
                reason=f"MA{fast}下穿MA{slow}，死叉出现",
                score=65.0,
            ))

        return signals
