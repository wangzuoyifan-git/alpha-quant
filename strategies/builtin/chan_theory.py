"""
内置策略：缠论三买/三卖（L3-A 交易仓）

正交定位：突破/趋势延续。与 vol_price_breakout 的区别在于确认方式：
  - vol_price_breakout：量价突破
  - chan_theory：离开中枢后回踩不重新进入中枢（结构确认）

默认只做三买/三卖；一买/二买可通过 params 打开，但建议先作为其他策略的确认层。
周线确认：日线三买要求周线趋势不向下（周线数据不足时放行并标注 unknown）。
"""
from __future__ import annotations

import pandas as pd

from chan.engine import ChanEngine
from chan.multi_level import analyze_multi_level

from ..base import BaseStrategy, Signal


class ChanTheoryStrategy(BaseStrategy):
    name = "缠论三买三卖"
    description = "日线中枢结构三买/三卖 + 周线方向确认 + 中枢上下沿出场"
    version = "1.0.0"

    applicable_regimes = ["balanced", "risk_on"]
    style = "breakout"
    applicable_scenario = "日线离开中枢后回踩不破、周线同向时表现最佳；震荡市假突破被中枢与周线过滤"
    risk_profile = "balanced"
    time_horizon = "short"
    data_dependency = ["technical"]
    applicable_asset_types = ["stock"]

    market_condition = "range"  # @deprecated

    def requires_bars(self) -> int:
        return max(int(self.params.get("min_bars", 300)), 120)

    def generate_signals(self, df: pd.DataFrame, code: str, name: str,
                         as_of=None) -> list[Signal]:
        if len(df) < self.requires_bars():
            return []

        buy_points = set(self.params.get("buy_points", ["3buy"]))
        sell_points = set(self.params.get("sell_points", ["3sell"]))
        max_age = int(self.params.get("max_signal_age_bars", 1))
        weekly_confirm = bool(self.params.get("weekly_confirm", True))
        allow_unconfirmed = bool(self.params.get("allow_unconfirmed", True))

        engine = ChanEngine()
        result = engine.analyze(df, level="daily", code=code)
        if not result.points or not result.bars:
            return []

        last_idx = len(result.bars) - 1
        allowed = buy_points | sell_points
        candidates = [
            p for p in result.points
            if p.kind in allowed and (last_idx - p.orig_idx) <= max_age
        ]
        if not allow_unconfirmed:
            candidates = [p for p in candidates if p.confirmed]
        if not candidates:
            return []

        point = max(candidates, key=lambda p: (p.orig_idx, p.strength))
        weekly_trend = "unknown"
        if weekly_confirm:
            ml = analyze_multi_level(df, engine, code)
            weekly_trend = ml["higher_level_trend"]

        last_close = float(pd.to_numeric(df["close"], errors="coerce").iloc[-1])

        # 三买后价格应已从回踩低点转强；三卖反之
        if point.kind == "3buy" and last_close < point.price:
            return []
        if point.kind == "3sell" and last_close > point.price:
            return []

        # 日线三买要求周线不向下；周线数据不足时放行
        if point.kind in buy_points and weekly_confirm and weekly_trend == "down":
            return []

        is_buy = point.kind in buy_points
        score = self._score(df, point, weekly_trend, is_buy)
        pivot = result.latest_pivot
        pivot_txt = (
            f"中枢[{pivot.zd:.2f},{pivot.zg:.2f}]" if pivot is not None else "中枢未形成"
        )
        reason = (
            f"日线{point.kind}（{point.reason}），{pivot_txt}，"
            f"周线{weekly_trend}"
        )
        if not point.confirmed:
            reason += "（结构待确认）"

        return [Signal(
            code=code,
            name=name,
            signal="BUY" if is_buy else "SELL",
            price=last_close,
            reason=reason,
            score=score,
            extra={"chan": result.summary_dict(weekly_trend)},
        )]

    def _score(
        self,
        df: pd.DataFrame,
        point,
        weekly_trend: str,
        is_buy: bool,
    ) -> float:
        score = (60.0 if is_buy else 65.0) + point.strength * 25.0
        if weekly_trend == "up":
            score += 8.0
        elif weekly_trend == "down":
            score -= 10.0
        if point.confirmed:
            score += 3.0

        # 量能确认
        try:
            volume = pd.to_numeric(df["volume"], errors="coerce")
            if len(volume) >= 21:
                vol_ma = float(volume.iloc[-21:-1].mean())
                if vol_ma > 0 and float(volume.iloc[-1]) > vol_ma * 1.2:
                    score += 5.0
        except Exception:
            pass
        return round(min(max(score, 0.0), 95.0), 1)

    def compute_exit_levels(self, df, entry_price, fundamental_data=None):
        """缠论出场：中枢下沿 ZD 硬止损，中枢上沿 ZG + 中枢高度止盈。"""
        from config.settings import RISK_CONTROL
        from strategies.base import ExitLevels, ScaleLevel
        from strategies.position_manager import build_scale_levels, calc_atr

        result = ChanEngine().analyze(df, level="daily")
        pivot = result.latest_pivot
        if pivot is None:
            return super().compute_exit_levels(df, entry_price, fundamental_data)

        atr = calc_atr(df)
        stop_loss = round(pivot.zd - atr * 0.2, 2)
        if stop_loss >= entry_price:
            stop_loss = round(entry_price - atr * 2.0, 2)

        height = max(pivot.height, atr)
        take_profit = round(pivot.zg + height, 2)
        if take_profit <= entry_price:
            take_profit = round(entry_price + atr * 3.0, 2)

        support = round(pivot.zd, 2)
        resistance = round(pivot.zg, 2)
        scale_cfg = RISK_CONTROL.get("scale_out", {})
        levels_raw = build_scale_levels(
            entry_price,
            stop_loss,
            take_profit,
            support=support,
            resistance=resistance,
            atr=atr,
            scale_cfg=scale_cfg,
        )
        for level in levels_raw:
            if level["level_type"] == "hard_stop":
                level["reason"] = f"缠论中枢下沿 ZD={pivot.zd:.2f}/ATR"
            elif level["level_type"] == "soft_stop":
                level["reason"] = "跌破中枢下沿减仓"
            elif level["level_type"] == "take_1":
                level["reason"] = "中枢上沿/前高止盈1"

        levels = [ScaleLevel(**level) for level in levels_raw]
        return ExitLevels(
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr,
            support=support,
            resistance=resistance,
            exit_logic="trail_chan",
            scale_levels=levels,
        )
