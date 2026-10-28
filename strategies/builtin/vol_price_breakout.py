"""
内置策略：突破（Breakout）

正交维度：突破（Breakout）— 结构突破后的趋势加速
修复自原 v1.0，改进：
  - 新增蓄力确认：突破前 N 日成交量低于均值（筹码锁定后的有效突破）
  - SELL 语义明确：跌破 N 日低点为持仓止损信号，与 BUY 扫描独立（通过 params 控制）
  - ATR 辅助：突破幅度相对 ATR 标准化，避免固定百分比在高/低波动品种失效
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal


def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    """计算当日 ATR（真实波幅均值）。"""
    if len(df) < period + 1:
        return float("nan")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


class VolPriceBreakoutStrategy(BaseStrategy):
    """
    突破策略

    参数：
      lookback      : 突破参考周期（默认 20 日）
      vol_ratio     : 突破当日放量倍数（默认 2.0x 均量）
      breakout_atr  : 突破幅度下限（ATR 倍数，默认 0.2，约 0.2 个真实波幅）
      buildup_days  : 蓄力确认天数（突破前该天数内均量低于均值，默认 5，0=禁用）
      buildup_ratio : 蓄力期成交量与均量的比值上限（默认 1.0，即不超过均量；0.9则要求比均量低10%）
      scan_sell     : 是否在扫描时输出 SELL 信号（默认 False，SELL 仅用于持仓管理）
    """
    name = "突破"
    description = "N日高点突破+放量+蓄力确认，结构突破后趋势加速"
    version = "2.0.0"
    applicable_regimes = ["risk_on"]
    style = "breakout"
    applicable_scenario = "牛市趋势确立期有效突破；熊市假突破多，由 L1 regime 过滤"
    risk_profile = "aggressive"
    time_horizon = "short"
    data_dependency = ["technical"]
    market_condition = "bull"  # @deprecated

    def requires_bars(self) -> int:
        return self.params.get("lookback", 20) + 20

    def generate_signals(self, df: pd.DataFrame, code: str, name: str,
                         as_of=None) -> List[Signal]:
        lookback = self.params.get("lookback", 20)
        vol_ratio = self.params.get("vol_ratio", 2.0)
        breakout_atr = self.params.get("breakout_atr", 0.2)
        buildup_days = self.params.get("buildup_days", 5)
        buildup_ratio = self.params.get("buildup_ratio", 1.0)
        scan_sell = self.params.get("scan_sell", False)

        if len(df) < lookback + 10:
            return []

        df = df.copy()
        close = pd.to_numeric(df["close"], errors="coerce")
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        volume = pd.to_numeric(df["volume"], errors="coerce")

        last = df.iloc[-1]
        last_close = float(close.iloc[-1])
        vol_ma20 = float(volume.iloc[-21:-1].mean())

        # 参考窗口（不含当日）用收盘价——避免日内假突破被 high 列触发
        ref_high = float(close.iloc[-(lookback + 1):-1].max())
        atr = _calc_atr(df)

        # ATR 标准化突破幅度下限
        min_breakout = atr * breakout_atr if (pd.notna(atr) and atr > 0) else ref_high * 0.005

        signals = []

        # ── BUY：突破 N 日高点 + 放量 + 蓄力 ────────────────────────
        price_break = last_close > ref_high + min_breakout
        vol_surge = float(volume.iloc[-1]) >= vol_ma20 * vol_ratio

        if price_break and vol_surge:
            # 蓄力确认：突破前 buildup_days 内成交量萎缩（数据不足时静默跳过）
            buildup_ok = True
            if buildup_days > 0:
                buildup_start = -(lookback + buildup_days + 1)
                buildup_end = -(lookback + 1)
                if abs(buildup_start) <= len(df) and abs(buildup_end) < len(df):
                    buildup_vol = volume.iloc[buildup_start:buildup_end]
                    if len(buildup_vol) >= buildup_days:
                        buildup_ok = float(buildup_vol.mean()) <= vol_ma20 * buildup_ratio

            if buildup_ok:
                breakout_pct = (last_close / ref_high - 1) * 100
                actual_vol_ratio = float(volume.iloc[-1]) / vol_ma20
                score = 50.0 + min(breakout_pct * 8, 25.0) + min((actual_vol_ratio - vol_ratio) * 5, 20.0)
                if buildup_days > 0:
                    score = min(score + 5.0, 100.0)  # 蓄力确认加分

                reason = (f"突破{lookback}日高点¥{ref_high:.2f}"
                          f"（+{breakout_pct:.1f}%），"
                          f"量比{actual_vol_ratio:.1f}x")
                if buildup_days > 0:
                    reason += "，蓄力确认"

                signals.append(Signal(
                    code=code, name=name, signal="BUY",
                    price=last_close, reason=reason,
                    score=min(score, 100.0),
                ))

        # ── SELL：跌破 N 日低点（持仓止损，默认不在扫描中输出）────────
        # scan_sell=False：SELL 信号仅在回测引擎持仓检查时生效（通过 scan_sell=True 显式开启）
        if scan_sell:
            ref_low = float(close.iloc[-(lookback + 1):-1].min())
            if last_close < ref_low:
                drop_pct = (ref_low - last_close) / ref_low * 100
                signals.append(Signal(
                    code=code, name=name, signal="SELL",
                    price=last_close,
                    reason=f"跌破{lookback}日低点¥{ref_low:.2f}（-{drop_pct:.1f}%）",
                    score=65.0,
                ))

        return signals

    def compute_exit_levels(self, df, entry_price, fundamental_data=None):
        """突破出场：突破点下方1×ATR紧止损 + 箱体高度目标止盈"""
        from strategies.position_manager import (
            calc_atr, calc_swing_low, calc_swing_high, build_scale_levels,
        )
        from strategies.base import ExitLevels, ScaleLevel
        from config.settings import RISK_CONTROL

        lookback = self.params.get("lookback", 20)
        atr = calc_atr(df)
        # 止损：突破前的近期低点（swing_low of lookback window）下方 0.5×ATR
        # 假突破要快出，止损比趋势策略更紧
        swing_lo = calc_swing_low(df, n=lookback)
        stop_loss = round(swing_lo - atr * 0.5, 2)
        stop_loss = max(stop_loss, entry_price - atr * 2.0)  # 上限 2×ATR

        # 止盈：箱体高度上移（突破目标 = 突破位 + 突破前箱体高度）
        swing_hi = calc_swing_high(df, n=lookback)
        box_height = swing_hi - swing_lo
        take_profit = round(entry_price + box_height, 2)
        resistance = calc_swing_high(df, n=5)  # 近期压力（5日高点）

        scale_cfg = RISK_CONTROL.get("scale_out", {})
        levels_raw = build_scale_levels(entry_price, stop_loss, take_profit,
                                        support=swing_lo, resistance=resistance,
                                        atr=atr, scale_cfg=scale_cfg)
        for l in levels_raw:
            if l["level_type"] == "hard_stop":
                l["reason"] = f"突破失败，跌回箱体（swing_low-0.5×ATR）"
            elif l["level_type"] == "take_1":
                l["reason"] = f"箱体高度目标（+{box_height:.2f}）"
        levels = [ScaleLevel(**l) for l in levels_raw]
        return ExitLevels(
            stop_loss=stop_loss, take_profit=take_profit, atr=atr,
            support=swing_lo, resistance=resistance,
            exit_logic="swing_low", scale_levels=levels,
        )
