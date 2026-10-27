"""
K 线适配层：把项目内 DataFrame / 外部数据统一成 chan.models.Bar。

输入约定：
- 支持 index=date 或普通列 date，统一转为升序、去重、数值化。
- 必填列：open/high/low/close；volume 可选。
- 不做复权处理，调用方需保证同一口径。
"""
from __future__ import annotations

import pandas as pd

from .models import Bar


def to_bars(df: pd.DataFrame) -> list[Bar]:
    """DataFrame -> list[Bar]，数据不合法时返回空列表。"""
    if df is None or len(df) == 0:
        return []

    d = df.copy()
    if "date" in d.columns:
        d = d.set_index("date")
    if not isinstance(d.index, pd.DatetimeIndex):
        try:
            d.index = pd.to_datetime(d.index)
        except Exception:
            return []

    required = ["open", "high", "low", "close"]
    if any(c not in d.columns for c in required):
        return []

    d = d[~d.index.duplicated(keep="last")].sort_index()
    d = d.dropna(subset=required)
    if d.empty:
        return []

    if "volume" not in d.columns:
        d = d.assign(volume=0.0)

    bars: list[Bar] = []
    for idx, (dt, row) in enumerate(d.iterrows()):
        try:
            o = float(row["open"])
            h = float(row["high"])
            lo = float(row["low"])
            c = float(row["close"])
            v = float(row.get("volume", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if h < lo:
            h, lo = lo, h
        bars.append(Bar(
            idx=idx,
            dt=pd.Timestamp(dt),
            open=o,
            high=h,
            low=lo,
            close=c,
            volume=v,
        ))
    return bars


def resample_ohlc(df: pd.DataFrame, rule: str = "W-FRI") -> pd.DataFrame:
    """日线聚合到周线/月线。保留 OHLCV，index 为周期结束日。"""
    if df is None or len(df) == 0:
        return pd.DataFrame()

    d = df.copy()
    if "date" in d.columns:
        d = d.set_index("date")
    d.index = pd.to_datetime(d.index)
    d = d[~d.index.duplicated(keep="last")].sort_index()

    agg: dict[str, str] = {}
    for col, how in (
        ("open", "first"),
        ("high", "max"),
        ("low", "min"),
        ("close", "last"),
        ("volume", "sum"),
        ("amount", "sum"),
    ):
        if col in d.columns:
            agg[col] = how
    if not agg:
        return pd.DataFrame()

    out = d.resample(rule).agg(agg)
    out = out.dropna(subset=[c for c in ("open", "high", "low", "close") if c in out.columns])

    # 去掉尚未走完的最后一根周/月线，避免用未完成周期做高级别确认
    if len(out) and len(d) and pd.Timestamp(out.index[-1]) > pd.Timestamp(d.index[-1]):
        out = out.iloc[:-1]
    return out
