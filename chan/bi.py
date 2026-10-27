"""
笔的识别。

规则（可参数化）：
- 相邻分型必须一顶一底交替。
- 顶底之间至少间隔 min_bars 根合并 K 线（默认 4，即分型中心之间至少隔 3 根）。
- 顶必须高于底，底必须低于顶。
- 连续同类分型只保留更极端的一个。
"""
from __future__ import annotations

from itertools import pairwise

from .models import Bi, Fractal


def find_bis(fractals: list[Fractal], min_bars: int = 4) -> list[Bi]:
    if len(fractals) < 2:
        return []

    selected: list[Fractal] = []
    for f in fractals:
        if not selected:
            selected.append(f)
            continue

        last = selected[-1]
        if f.kind == last.kind:
            more_extreme = (
                (f.kind == "top" and f.price > last.price)
                or (f.kind == "bottom" and f.price < last.price)
            )
            if not more_extreme:
                continue
            # 替换端点时，必须仍与前一异类分型保持足够间隔
            if len(selected) >= 2 and f.merged_idx - selected[-2].merged_idx < min_bars:
                continue
            selected[-1] = f
            continue

        # 异类分型：检查间隔与价格有效性
        if f.merged_idx - last.merged_idx < min_bars:
            continue
        if f.kind == "top" and f.price <= last.price:
            continue
        if f.kind == "bottom" and f.price >= last.price:
            continue
        selected.append(f)

    bis: list[Bi] = []
    for a, b in pairwise(selected):
        direction = "up" if b.kind == "top" else "down"
        bis.append(Bi(start=a, end=b, direction=direction))

    if bis:
        bis[-1].is_open = True
    return bis
