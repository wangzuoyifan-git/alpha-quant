"""
缠论分析引擎。

调用方式：
    engine = ChanEngine()
    result = engine.analyze(df, level="daily", code="600519")
    print(result.summary_dict())

输入 df 必须是"截止当前可见"的行情，引擎只使用传入数据，不读取未来数据。
"""
from __future__ import annotations

import pandas as pd

from .adapter import to_bars
from .bi import find_bis
from .buy_sell import detect_points
from .divergence import find_divergences
from .fractal import find_fractals
from .kline import merge_bars
from .models import Bar, Bi, ChanResult, Pivot
from .pivot import find_pivots

_DEFAULTS = {
    "bi_min_bars": 4,
    "divergence_ratio": 0.8,
    "divergence_min_bars": 3,
}


def _load_settings() -> dict:
    try:
        from config.settings import CHAN

        return dict(CHAN)
    except Exception:
        return {}


class ChanEngine:
    def __init__(self, config: dict | None = None):
        self.cfg = {**_DEFAULTS, **_load_settings(), **(config or {})}

    def analyze(
        self,
        df: pd.DataFrame,
        level: str = "daily",
        code: str = "",
        asof: str | None = None,
    ) -> ChanResult:
        bars = to_bars(df)
        if len(bars) < 3:
            return ChanResult(code=code, level=level, asof=asof)

        merged = merge_bars(bars)
        fractals = find_fractals(merged)
        bis = find_bis(fractals, min_bars=int(self.cfg.get("bi_min_bars", 4)))
        pivots = find_pivots(bis)
        divergences = find_divergences(
            bars,
            bis,
            ratio=float(self.cfg.get("divergence_ratio", 0.8)),
            min_bars=int(self.cfg.get("divergence_min_bars", 3)),
        )
        points = detect_points(bis, pivots, divergences)
        trend_type = self._classify_trend(bis, pivots, bars)

        last_dt = bars[-1].dt
        asof_str = asof or (last_dt.strftime("%Y-%m-%d") if last_dt is not None else None)
        return ChanResult(
            code=code,
            level=level,
            asof=asof_str,
            bars=bars,
            merged=merged,
            fractals=fractals,
            bis=bis,
            pivots=pivots,
            points=points,
            divergences=divergences,
            trend_type=trend_type,
        )

    @staticmethod
    def _classify_trend(bis: list[Bi], pivots: list[Pivot], bars: list[Bar]) -> str:
        if len(pivots) >= 2:
            prev, cur = pivots[-2], pivots[-1]
            if cur.zg > prev.zg and cur.zd > prev.zd:
                return "up"
            if cur.zg < prev.zg and cur.zd < prev.zd:
                return "down"
        if bis:
            return "up" if bis[-1].direction == "up" else "down"
        if len(bars) >= 2:
            return "up" if bars[-1].close >= bars[0].close else "down"
        return "consolidation"
