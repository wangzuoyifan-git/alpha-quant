"""
背驰识别（简化版）。

用 MACD 柱面积近似力度：
- 向下笔：比较当前下跌笔与上一根下跌笔的 MACD 负柱面积，同时要求创新低。
- 向上笔：比较当前上涨笔与上一根上涨笔的 MACD 正柱面积，同时要求创新高。
面积缩小到前一次的 ratio 以下，视为背驰。
"""
from __future__ import annotations

import math

import pandas as pd

from .models import Bar, Bi, Divergence


def _macd_hist(bars: list[Bar], fast: int = 12, slow: int = 26, signal: int = 9) -> list[float]:
    if not bars:
        return []
    closes = pd.Series([b.close for b in bars], dtype="float64")
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - dea
    return [float(x) for x in hist.tolist()]


def _area(hist: list[float], start: int, end: int, positive: bool) -> float:
    lo = max(min(start, end), 0)
    hi = min(max(start, end), len(hist) - 1)
    if hi < lo:
        return 0.0
    if positive:
        return float(sum(v for v in hist[lo:hi + 1] if v > 0))
    return float(sum(-v for v in hist[lo:hi + 1] if v < 0))


def find_divergences(
    bars: list[Bar],
    bis: list[Bi],
    ratio: float = 0.8,
    min_bars: int = 3,
) -> list[Divergence]:
    if len(bars) < 30 or len(bis) < 3:
        return []

    hist = _macd_hist(bars)
    out: list[Divergence] = []

    for i in range(2, len(bis)):
        cur = bis[i]
        # 笔是交替的，上一根同向笔在 i-2
        prev = bis[i - 2]
        if cur.direction != prev.direction:
            continue
        if cur.end_idx - cur.start_idx < min_bars:
            continue

        cur_area = _area(hist, cur.start_idx, cur.end_idx, positive=(cur.direction == "up"))
        prev_area = _area(hist, prev.start_idx, prev.end_idx, positive=(prev.direction == "up"))
        if prev_area <= 1e-9:
            continue

        shrink = cur_area / prev_area
        if shrink >= ratio:
            continue

        if cur.direction == "down" and cur.end.price < prev.end.price:
            strength = max(0.0, min(1.0, 1.0 - shrink))
            out.append(Divergence(
                kind="bottom",
                strength=round(strength, 4),
                bi_index=i,
                prev_bi_index=i - 2,
                price=cur.end.price,
                orig_idx=cur.end_idx,
                reason=f"底背驰: 面积比 {shrink:.2f}，创新低",
            ))
        elif cur.direction == "up" and cur.end.price > prev.end.price:
            strength = max(0.0, min(1.0, 1.0 - shrink))
            out.append(Divergence(
                kind="top",
                strength=round(strength, 4),
                bi_index=i,
                prev_bi_index=i - 2,
                price=cur.end.price,
                orig_idx=cur.end_idx,
                reason=f"顶背驰: 面积比 {shrink:.2f}，创新高",
            ))

    # 过滤无效数值
    return [d for d in out if not math.isnan(d.strength)]
