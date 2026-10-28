"""内置策略：低波动质量趋势（L3-A 交易仓）

研究来源：research/rounds/ 第 3~5 轮（2024-07~2026-10 全市场回测）。
正交定位：趋势跟踪的“质量增强版”。与 ma_cross 的区别：
  - 只做已确认上升趋势（close > MA60 且 MA20 > MA60）
  - 剔除高波动（ATR/close < 5%）与过热（偏离 MA20 < 8%、20日动量 < 25%）
  - 要求流动性（5日平均成交额 ≥ 1 亿）
  - 趋势中回踩 MA20 后重新站上时入场（避免追涨停），跌破 MA20 离场

核心发现（事件研究）：在上升趋势中，低波动 + 温和动量的股票 20 日前瞻
收益显著高于高波动/高动量组；追高（ext>20%、mom>50%）是主要亏损来源。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal


class QualityTrendStrategy(BaseStrategy):
    """低波动质量趋势（趋势跟踪 + 低波动 + 不过热）"""

    name = "低波动质量趋势"
    description = "MA60上升趋势 + ATR波动率过滤 + 过热过滤 + 回踩MA20再站上入场，跌破MA20离场"
    version = "1.0.0"

    applicable_regimes = ["risk_on", "balanced"]
    style = "momentum"
    applicable_scenario = "指数处于MA60上方、个股温和上涨且波动较低时表现最佳；高波动/追高标的自动过滤"
    risk_profile = "balanced"
    time_horizon = "short"
    data_dependency = ["technical"]
    applicable_asset_types = ["stock"]
    market_condition = "bull"  # @deprecated

    def requires_bars(self) -> int:
        return int(self.params.get("min_bars", 80))

    def _features(self, df: pd.DataFrame) -> dict:
        close = pd.to_numeric(df["close"], errors="coerce")
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        amount = pd.to_numeric(df.get("amount"), errors="coerce")

        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        mom20 = close / close.shift(20) - 1
        ext = close / ma20 - 1
        amt5 = amount.rolling(5).mean() if amount is not None else pd.Series(np.nan, index=close.index)

        last = len(close) - 1
        if last < 1:
            return {}
        i, j = last, last - 1
        return {
            "close": close,
            "low": low,
            "ma20": ma20,
            "ma60": ma60,
            "atr": atr,
            "vol": atr / close,
            "mom20": mom20,
            "ext": ext,
            "amt5": amt5,
            "i": i,
            "j": j,
        }

    def _condition(self, f: dict, idx: int, p: dict) -> bool:
        if idx < 60:
            return False
        close = f["close"].iloc[idx]
        ma20 = f["ma20"].iloc[idx]
        ma60 = f["ma60"].iloc[idx]
        vol = f["vol"].iloc[idx]
        mom20 = f["mom20"].iloc[idx]
        ext = f["ext"].iloc[idx]
        amt5 = f["amt5"].iloc[idx]
        if any(pd.isna(x) for x in (close, ma20, ma60, vol, mom20, ext)):
            return False
        if not (close > ma60 and ma20 > ma60):
            return False
        if vol >= p["vol_max"]:
            return False
        if not (0 <= ext < p["ext_max"]):
            return False
        if not (0 < mom20 < p["mom_max"]):
            return False
        return not (pd.notna(amt5) and amt5 < p["min_amount"])

    def generate_signals(self, df: pd.DataFrame, code: str, name: str,
                         as_of=None) -> list[Signal]:
        if df is None or df.empty or len(df) < self.requires_bars():
            return []
        p = {
            "vol_max": float(self.params.get("vol_max", 0.05)),
            "ext_max": float(self.params.get("ext_max", 0.08)),
            "mom_max": float(self.params.get("mom_max", 0.25)),
            "min_amount": float(self.params.get("min_amount", 1e8)),
        }
        if "is_st" in df.columns:
            st = pd.to_numeric(df["is_st"].iloc[-1], errors="coerce")
            if pd.notna(st) and int(st) == 1:
                return []

        f = self._features(df)
        if not f:
            return []
        i = f["i"]
        last_close = float(f["close"].iloc[i])
        ma20 = float(f["ma20"].iloc[i])

        # 卖出：跌破 MA20（仅交叉当日发一次，避免连续刷屏）
        if pd.notna(ma20) and last_close < ma20 and f["i"] >= 1:
            prev_ma20 = f["ma20"].iloc[i - 1]
            prev_close = float(f["close"].iloc[i - 1])
            if pd.notna(prev_ma20) and prev_close >= prev_ma20:
                return [Signal(
                    code=code, name=name, signal="SELL", price=last_close,
                    reason=f"跌破MA20({ma20:.2f})", score=65.0,
                )]
            return []

        # 买入：趋势中回踩 MA20 后重新站上（touch20 入场，避免追涨停）
        if not self._condition(f, i, p):
            return []
        low = f["low"].iloc[i]
        prev_close = float(f["close"].iloc[i - 1])
        prev_ma20 = float(f["ma20"].iloc[i - 1])
        touched = pd.notna(low) and low <= ma20
        reclaimed = last_close > ma20 and pd.notna(prev_ma20) and prev_close <= prev_ma20
        if not (touched and reclaimed):
            return []

        vol = float(f["vol"].iloc[i])
        ext = float(f["ext"].iloc[i])
        mom = float(f["mom20"].iloc[i])
        score = (
            20.0
            + max(0.0, 1 - vol / p["vol_max"]) * 40.0
            + max(0.0, 1 - ext / p["ext_max"]) * 20.0
            + max(0.0, 1 - mom / p["mom_max"]) * 20.0
        )
        reason = (
            f"MA20>MA60趋势，ATR/价{vol*100:.2f}%，"
            f"偏离MA20{ext*100:+.1f}%，20日动量{mom*100:+.1f}%"
        )
        return [Signal(
            code=code, name=name, signal="BUY", price=last_close,
            reason=reason, score=round(min(score, 99.0), 1),
        )]

    def compute_exit_levels(self, df, entry_price, fundamental_data=None):
        """3×ATR 硬止损 + 3×ATR 移动止损，MA20 作为结构支撑。"""
        from config.settings import RISK_CONTROL
        from strategies.base import ExitLevels, ScaleLevel
        from strategies.position_manager import (
            build_scale_levels,
            calc_atr,
            calc_swing_high,
        )

        atr = calc_atr(df)
        close = pd.to_numeric(df["close"], errors="coerce")
        ma20 = float(close.rolling(20).mean().iloc[-1])
        stop_loss = round(max(entry_price - atr * 3.0, ma20 - atr * 0.5), 2)
        if stop_loss >= entry_price:
            stop_loss = round(entry_price - atr * 3.0, 2)
        resistance = calc_swing_high(df, n=20)
        take_profit = round(entry_price + atr * 6.0, 2)

        scale_cfg = RISK_CONTROL.get("scale_out", {})
        levels_raw = build_scale_levels(
            entry_price, stop_loss, take_profit,
            support=round(ma20, 2), resistance=resistance,
            atr=atr, scale_cfg=scale_cfg,
        )
        for lv in levels_raw:
            if lv["level_type"] == "hard_stop":
                lv["reason"] = "3×ATR/MA20下沿止损"
            elif lv["level_type"] == "soft_stop":
                lv["reason"] = "跌破MA20趋势破坏"
        levels = [ScaleLevel(**lv) for lv in levels_raw]
        return ExitLevels(
            stop_loss=stop_loss, take_profit=take_profit, atr=atr,
            support=round(ma20, 2), resistance=resistance,
            exit_logic="trail_ma20", scale_levels=levels,
        )
