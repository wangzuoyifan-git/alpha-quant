"""
缠论买卖点识别。

P1/P2 重点覆盖：
- 三买：向上离开中枢后，回踩不重新进入中枢（回踩低点 > ZG）。
- 三卖：向下离开中枢后，反抽不重新进入中枢（反抽高点 < ZD）。
- 一买/一卖：趋势末端出现背驰。
- 二买/二卖：一买/一卖后的次级别回踩不创新低/新高。

说明：三买/三卖是"结构确认"信号，回踩笔结束时即可作为候选；
若回踩笔之后已有反向笔，则 confirmed=True，否则为候选（可能延伸）。
"""
from __future__ import annotations

from .models import Bi, BuySellPoint, Divergence, Pivot


def _point(
    kind: str,
    bi: Bi,
    bi_index: int,
    strength: float,
    reason: str,
    pivot_index: int = -1,
    confirmed: bool = True,
) -> BuySellPoint:
    return BuySellPoint(
        kind=kind,
        price=bi.end.price,
        orig_idx=bi.end_idx,
        merged_idx=bi.end.merged_idx,
        dt=bi.end.dt,
        bi_index=bi_index,
        pivot_index=pivot_index,
        strength=round(strength, 4),
        confirmed=confirmed,
        reason=reason,
    )


def detect_points(
    bis: list[Bi],
    pivots: list[Pivot],
    divergences: list[Divergence],
) -> list[BuySellPoint]:
    points: list[BuySellPoint] = []
    n = len(bis)

    # ── 三买 / 三卖 ────────────────────────────────────────────
    # 中枢结束后的第一根笔若不与中枢重叠，即为离开/回踩笔。
    # 三买：回踩笔（向下）低点 > ZG；若第一根是跳空向上的笔，则看下一根回踩。
    # 三卖：反抽笔（向上）高点 < ZD；若第一根是跳空向下的笔，则看下一根反抽。
    for pi, p in enumerate(pivots):
        first_i = p.end_bi + 1
        if first_i >= n:
            continue
        first = bis[first_i]
        confirmed = not first.is_open

        if first.direction == "down" and first.low > p.zg:
            strength = 0.75 + min(0.15, (first.low - p.zg) / max(p.height, 1e-9) * 0.15)
            points.append(_point(
                "3buy", first, first_i, strength,
                f"三买：离开中枢后回踩低点 {first.low:.2f} > ZG {p.zg:.2f}",
                pivot_index=pi, confirmed=confirmed,
            ))
        elif first.direction == "up" and first.low > p.zg:
            # 跳空向上离开中枢，下一根向下笔才是回踩
            pull_i = first_i + 1
            if pull_i < n:
                pull = bis[pull_i]
                if pull.direction == "down" and pull.low > p.zg:
                    strength = 0.75 + min(
                        0.15, (pull.low - p.zg) / max(p.height, 1e-9) * 0.15
                    )
                    points.append(_point(
                        "3buy", pull, pull_i, strength,
                        f"三买：跳空离开中枢后回踩低点 {pull.low:.2f} > ZG {p.zg:.2f}",
                        pivot_index=pi, confirmed=not pull.is_open,
                    ))

        if first.direction == "up" and first.high < p.zd:
            strength = 0.75 + min(0.15, (p.zd - first.high) / max(p.height, 1e-9) * 0.15)
            points.append(_point(
                "3sell", first, first_i, strength,
                f"三卖：离开中枢后反抽高点 {first.high:.2f} < ZD {p.zd:.2f}",
                pivot_index=pi, confirmed=confirmed,
            ))
        elif first.direction == "down" and first.high < p.zd:
            # 跳空向下离开中枢，下一根向上笔才是反抽
            pull_i = first_i + 1
            if pull_i < n:
                pull = bis[pull_i]
                if pull.direction == "up" and pull.high < p.zd:
                    strength = 0.75 + min(
                        0.15, (p.zd - pull.high) / max(p.height, 1e-9) * 0.15
                    )
                    points.append(_point(
                        "3sell", pull, pull_i, strength,
                        f"三卖：跳空离开中枢后反抽高点 {pull.high:.2f} < ZD {p.zd:.2f}",
                        pivot_index=pi, confirmed=not pull.is_open,
                    ))

    # ── 一买 / 一卖（背驰）──────────────────────────────────────
    for d in divergences:
        idx = d.bi_index
        if idx >= n:
            continue
        bi = bis[idx]
        if d.kind == "bottom" and bi.direction == "down":
            points.append(_point(
                "1buy", bi, idx, 0.55 + d.strength * 0.3,
                f"一买：底背驰，{d.reason}",
                confirmed=not bi.is_open,
            ))
        elif d.kind == "top" and bi.direction == "up":
            points.append(_point(
                "1sell", bi, idx, 0.55 + d.strength * 0.3,
                f"一卖：顶背驰，{d.reason}",
                confirmed=not bi.is_open,
            ))

    # ── 二买 / 二卖（一买/一卖后的次级别回踩）──────────────────
    one_buy_bi: set[int] = set()
    one_sell_bi: set[int] = set()
    for pt in points:
        if pt.kind == "1buy":
            one_buy_bi.add(pt.bi_index)
        elif pt.kind == "1sell":
            one_sell_bi.add(pt.bi_index)

    for k in sorted(one_buy_bi):
        pull_i = k + 2
        if pull_i >= n:
            continue
        pull = bis[pull_i]
        first_low = bis[k].end.price
        if pull.direction == "down" and pull.low > first_low:
            points.append(_point(
                "2buy", pull, pull_i, 0.65,
                f"二买：回踩低点 {pull.low:.2f} 高于一买低点 {first_low:.2f}",
                confirmed=not pull.is_open,
            ))

    for k in sorted(one_sell_bi):
        pull_i = k + 2
        if pull_i >= n:
            continue
        pull = bis[pull_i]
        first_high = bis[k].end.price
        if pull.direction == "up" and pull.high < first_high:
            points.append(_point(
                "2sell", pull, pull_i, 0.65,
                f"二卖：反抽高点 {pull.high:.2f} 低于一卖高点 {first_high:.2f}",
                confirmed=not pull.is_open,
            ))

    # 去重 + 按发生时间排序
    dedup: dict[tuple[str, int], BuySellPoint] = {}
    for pt in points:
        key = (pt.kind, pt.orig_idx)
        if key not in dedup or pt.strength > dedup[key].strength:
            dedup[key] = pt
    return sorted(dedup.values(), key=lambda x: (x.orig_idx, x.kind))
