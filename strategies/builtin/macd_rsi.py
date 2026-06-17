"""
内置策略：均值回归（超跌反弹）

正交维度：均值回归（Mean Reversion）— 与趋势跟踪对立，由 L1 regime 控制激活
升级自原 macd_rsi（v1.0），改进：
  - RSI 阈值放宽至 40（原 35 在 A 股恐慌时太紧，信号频率极低）
  - 新增 RSI 背离判断（价格创新低但 RSI 不创新低）作为更强的底部信号
  - 背离信号优先，阈值穿越信号作为次级入场
"""
import pandas as pd

from ..base import BaseStrategy, Signal


class MACDRSIStrategy(BaseStrategy):
    """
    均值回归策略

    参数：
      fast / slow / signal : MACD 参数（默认 12/26/9）
      rsi_period           : RSI 周期（默认 14）
      rsi_oversold         : RSI 超卖阈值（默认 40，放宽自原 35）
      rsi_overbought       : RSI 超买阈值（默认 70）
      divergence_lookback  : RSI 背离检测回看窗口（默认 10，0=禁用）
    """
    name = "均值回归"
    description = "MACD金叉+RSI超卖/背离，超跌反弹底部信号，与趋势策略正交"
    version = "2.0.0"
    applicable_regimes = ["risk_off", "volatile"]
    style = "reversal"
    applicable_scenario = "超跌反弹，熊市底部或高波动寻底；risk_on 时由 L1 关闭"
    risk_profile = "defensive"
    time_horizon = "short"
    data_dependency = ["technical"]
    market_condition = "bear"  # @deprecated

    def requires_bars(self) -> int:
        return 60

    def _calc_macd(self, close, fast=12, slow=26, signal=9):
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        macd = (dif - dea) * 2
        return dif, dea, macd

    def _calc_rsi(self, close, period=14):
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / (loss + 1e-9)
        return 100 - 100 / (1 + rs)

    def _has_rsi_divergence(self, close: pd.Series, rsi: pd.Series, lookback: int) -> bool:
        """
        检测底背离：近 lookback 根 K 线内，价格创新低但 RSI 不创新低。
        这是比阈值穿越更可靠的底部信号。
        """
        if len(close) < lookback + 1:
            return False
        window_close = close.iloc[-lookback:]
        window_rsi = rsi.iloc[-lookback:]
        # 当前价格是窗口最低点，但当前 RSI 不是窗口最低点
        price_new_low = float(close.iloc[-1]) <= float(window_close.min())
        rsi_no_new_low = float(rsi.iloc[-1]) > float(window_rsi.min()) + 2.0
        return price_new_low and rsi_no_new_low

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> list[Signal]:
        p = self.params
        fast = p.get("fast", 12)
        slow = p.get("slow", 26)
        sig = p.get("signal", 9)
        rsi_period = p.get("rsi_period", 14)
        oversold = p.get("rsi_oversold", 40)
        overbought = p.get("rsi_overbought", 70)
        div_lookback = p.get("divergence_lookback", 10)

        if len(df) < slow + sig + 5:
            return []

        df = df.copy()
        close = pd.to_numeric(df["close"], errors="coerce")
        dif, dea, _ = self._calc_macd(close, fast, slow, sig)
        rsi = self._calc_rsi(close, rsi_period)

        last_dif, prev_dif = float(dif.iloc[-1]), float(dif.iloc[-2])
        last_dea, prev_dea = float(dea.iloc[-1]), float(dea.iloc[-2])
        last_rsi, prev_rsi = float(rsi.iloc[-1]), float(rsi.iloc[-2])
        last_close = float(close.iloc[-1])

        signals = []

        macd_cross_up = prev_dif <= prev_dea and last_dif > last_dea
        if macd_cross_up:
            reasons = ["MACD金叉"]
            score = 50.0
            confirmed = False  # 必须有背离或超卖至少一个确认才发信号

            # 底背离（优先，更强信号）
            divergence = div_lookback > 0 and self._has_rsi_divergence(close, rsi, div_lookback)
            if divergence:
                score += 25.0
                reasons.append("RSI底背离")
                confirmed = True
            # RSI 超卖（次级，放宽至 40）
            elif prev_rsi < oversold and last_rsi >= oversold - 5:
                score += 15.0
                reasons.append(f"RSI({last_rsi:.1f})超卖回升")
                confirmed = True

            if not confirmed:
                return []  # 无超卖/背离确认，不发信号

            if close.iloc[-1] > close.rolling(20).mean().iloc[-1]:
                score += 10.0
                reasons.append("站上MA20")

            signals.append(Signal(
                code=code, name=name, signal="BUY",
                price=last_close,
                reason="，".join(reasons),
                score=min(score, 100.0),
            ))

        # 卖出：MACD 死叉
        macd_cross_down = prev_dif >= prev_dea and last_dif < last_dea
        if macd_cross_down:
            rsi_note = f"，RSI({last_rsi:.1f})" if last_rsi > 50 else ""
            signals.append(Signal(
                code=code, name=name, signal="SELL",
                price=last_close,
                reason=f"MACD死叉{rsi_note}",
                score=60.0 + (10.0 if last_rsi > overbought else 0.0),
            ))

        return signals
