"""
分型识别（在包含处理后的 K 线上进行）。

顶分型：中间 K 线高点最高，且低点也最高。
底分型：中间 K 线低点最低，且高点也最低。
"""
from __future__ import annotations

from .models import Fractal, MergedBar


def find_fractals(merged: list[MergedBar]) -> list[Fractal]:
    if len(merged) < 3:
        return []

    out: list[Fractal] = []
    for i in range(1, len(merged) - 1):
        prev, cur, nxt = merged[i - 1], merged[i], merged[i + 1]

        is_top = (
            cur.high > prev.high
            and cur.high > nxt.high
            and cur.low > prev.low
            and cur.low > nxt.low
        )
        is_bottom = (
            cur.low < prev.low
            and cur.low < nxt.low
            and cur.high < prev.high
            and cur.high < nxt.high
        )

        if is_top:
            out.append(Fractal(
                kind="top",
                price=cur.high,
                merged_idx=i,
                orig_idx=cur.idx,
                dt=cur.dt,
            ))
        elif is_bottom:
            out.append(Fractal(
                kind="bottom",
                price=cur.low,
                merged_idx=i,
                orig_idx=cur.idx,
                dt=cur.dt,
            ))

    return out
