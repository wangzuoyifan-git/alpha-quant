"""
中枢识别（笔级别）。

由至少三根连续笔的重叠区间构成：
  ZG = min(第一笔高点, 第三笔高点)
  ZD = max(第一笔低点, 第三笔低点)
后续笔只要仍与 [ZD, ZG] 有重叠，就并入中枢延伸；否则中枢结束。
"""
from __future__ import annotations

from .models import Bi, Pivot


def find_pivots(bis: list[Bi]) -> list[Pivot]:
    if len(bis) < 3:
        return []

    pivots: list[Pivot] = []
    i = 0
    n = len(bis)

    while i <= n - 3:
        b1, b2, b3 = bis[i], bis[i + 1], bis[i + 2]
        zg = min(b1.high, b3.high)
        zd = max(b1.low, b3.low)

        # 三笔必须有共同重叠区间，且第二笔穿过该区间
        if zd >= zg or b2.high < zd or b2.low > zg:
            i += 1
            continue

        members = [b1, b2, b3]
        j = i + 3
        while j < n:
            b = bis[j]
            if b.high >= zd and b.low <= zg:
                members.append(b)
                j += 1
            else:
                break

        pivots.append(Pivot(
            start_bi=i,
            end_bi=j - 1,
            zg=zg,
            zd=zd,
            gg=max(b.high for b in members),
            dd=min(b.low for b in members),
            start_idx=b1.start_idx,
            end_idx=members[-1].end_idx,
            start_dt=b1.start_dt,
            end_dt=members[-1].end_dt,
            confirmed=j < n,
        ))

        # 离开中枢的那根笔可能成为下一个中枢的第一笔
        i = max(j - 1, i + 1)

    return pivots


def pivot_overlap(pivot: Pivot, bi: Bi) -> bool:
    """判断笔是否仍与中枢区间重叠。"""
    return bi.high >= pivot.zd and bi.low <= pivot.zg
