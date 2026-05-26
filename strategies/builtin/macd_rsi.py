"""
内置策略二：MACD + RSI 共振策略
MACD 金叉同时 RSI 在超卖区域回升
"""

import pandas as pd

from ..base import BaseStrategy, Signal


class MACDRSIStrategy(BaseStrategy):
    """
    MACD + RSI 共振策略
    参数：
      fast: MACD 快线（默认12）
      slow: MACD 慢线（默认26）
      signal: MACD 信号线（默认9）
      rsi_period: RSI 周期（默认14）
      rsi_oversold: RSI 超卖阈值（默认35）
      rsi_overbought: RSI 超买阈值（默认70）
    """
    name = "MACD+RSI共振"
    description = "MACD金叉配合RSI超卖回升，双重过滤减少假信号"
    version = "1.0.0"

    def requires_bars(self) -> int:
        return 60

    def _calc_macd(self, close: pd.Series, fast=12, slow=26, signal=9):
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        macd = (dif - dea) * 2
        return dif, dea, macd

    def _calc_rsi(self, close: pd.Series, period=14):
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / (loss + 1e-9)
        return 100 - 100 / (1 + rs)

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> list[Signal]:
        p = self.params
        fast = p.get("fast", 12)
        slow = p.get("slow", 26)
        sig = p.get("signal", 9)
        rsi_period = p.get("rsi_period", 14)
        oversold = p.get("rsi_oversold", 35)
        overbought = p.get("rsi_overbought", 70)

        if len(df) < slow + sig + 5:
            return []

        df = df.copy()
        df["dif"], df["dea"], df["macd"] = self._calc_macd(df["close"], fast, slow, sig)
        df["rsi"] = self._calc_rsi(df["close"], rsi_period)

        last = df.iloc[-1]
        prev = df.iloc[-2]
        signals = []

        # 买入：MACD 金叉 + RSI 从超卖回升（双重过滤，两者都必须满足）
        macd_cross_up = prev["dif"] <= prev["dea"] and last["dif"] > last["dea"]
        rsi_recovering = prev["rsi"] < oversold and last["rsi"] >= oversold - 5

        if macd_cross_up and rsi_recovering:
            score = 55.0
            reasons = ["MACD金叉"]
            reasons.append(f"RSI({last['rsi']:.1f})超卖回升")
            if last["macd"] > 0:
                score += 10.0
                reasons.append("MACD柱翻红")
            if last["close"] > df["close"].rolling(20).mean().iloc[-1]:
                score += 10.0
                reasons.append("站上MA20")
            signals.append(Signal(
                code=code, name=name,
                signal="BUY",
                price=float(last["close"]),
                reason="，".join(reasons),
                score=min(score, 100.0),
            ))

        # 卖出：MACD 死叉（RSI 不作强制要求，死叉本身已是趋势转弱信号）
        macd_cross_down = prev["dif"] >= prev["dea"] and last["dif"] < last["dea"]
        if macd_cross_down:
            rsi_note = f"，RSI({last['rsi']:.1f})" if last["rsi"] > 50 else ""
            signals.append(Signal(
                code=code, name=name,
                signal="SELL",
                price=float(last["close"]),
                reason=f"MACD死叉{rsi_note}",
                score=60.0 + (10.0 if last["rsi"] > overbought else 0.0),
            ))

        return signals
