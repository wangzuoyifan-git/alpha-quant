"""
多级别分析（当前支持 日线 + 周线）。

周线由日线聚合得到，并且剔除未走完的当周，避免用未完成周期做高级别确认。
未来接入分钟线后，可在本模块扩展 30m/60m 联立，策略层接口保持不变。
"""
from __future__ import annotations

import pandas as pd

from .adapter import resample_ohlc
from .engine import ChanEngine
from .models import ChanResult


def analyze_multi_level(
    df: pd.DataFrame,
    engine: ChanEngine | None = None,
    code: str = "",
    min_weekly_bars: int = 20,
) -> dict:
    """
    返回：
      {
        "daily": ChanResult,
        "weekly": ChanResult | None,
        "higher_level_trend": "up"/"down"/"consolidation"/"unknown",
        "weekly_summary": dict | None,
      }
    """
    eng = engine or ChanEngine()
    daily = eng.analyze(df, level="daily", code=code)

    weekly_df = resample_ohlc(df, rule="W-FRI")
    weekly: ChanResult | None = None
    if weekly_df is not None and len(weekly_df) >= min_weekly_bars:
        weekly = eng.analyze(weekly_df, level="weekly", code=code)

    higher_trend = weekly.trend_type if weekly is not None else "unknown"
    return {
        "daily": daily,
        "weekly": weekly,
        "higher_level_trend": higher_trend,
        "weekly_summary": weekly.summary_dict() if weekly is not None else None,
    }
