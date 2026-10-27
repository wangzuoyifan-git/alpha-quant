"""
K 线包含处理。

缠论预处理的核心：把存在包含关系的相邻 K 线合并，消除噪音后再找分型。
合并方向由前两根合并 K 线的高点决定；向上取高高，向下取低低。
"""
from __future__ import annotations

from .models import Bar, MergedBar


def _included(a_high: float, a_low: float, b_high: float, b_low: float) -> bool:
    """b 被 a 包含，或 b 包含 a。"""
    return (b_high <= a_high and b_low >= a_low) or (b_high >= a_high and b_low <= a_low)


def merge_bars(bars: list[Bar]) -> list[MergedBar]:
    """原始 K 线 -> 包含处理后的 K 线序列。"""
    if not bars:
        return []

    merged: list[MergedBar] = [
        MergedBar(
            idx=bars[0].idx,
            start_idx=bars[0].idx,
            dt=bars[0].dt,
            high=bars[0].high,
            low=bars[0].low,
            direction="up",
        )
    ]

    for bar in bars[1:]:
        prev = merged[-1]
        if not _included(prev.high, prev.low, bar.high, bar.low):
            direction = "up" if bar.high >= prev.high else "down"
            merged.append(MergedBar(
                idx=bar.idx,
                start_idx=bar.idx,
                dt=bar.dt,
                high=bar.high,
                low=bar.low,
                direction=direction,
            ))
            continue

        # 包含关系：按当前走势方向合并
        if len(merged) >= 2:
            direction = "up" if prev.high > merged[-2].high else "down"
        else:
            direction = "up" if bar.high >= prev.high else "down"

        if direction == "up":
            new_high = max(prev.high, bar.high)
            new_low = max(prev.low, bar.low)
        else:
            new_high = min(prev.high, bar.high)
            new_low = min(prev.low, bar.low)

        prev.high = new_high
        prev.low = new_low
        prev.idx = bar.idx
        prev.dt = bar.dt
        prev.direction = direction

    return merged
