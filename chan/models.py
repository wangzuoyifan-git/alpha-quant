"""
缠论核心数据结构。

设计约束：
- 所有结构都记录原始 K 线索引（orig_idx）和合并后索引（merged_idx），
  便于策略层按 "当前可见 K 线" 做因果过滤，避免未来函数。
- 不依赖 strategies / agents / data，保持 chan 包为纯计算层。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Bar:
    """标准化后的单根 K 线。"""

    idx: int
    dt: pd.Timestamp | None
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class MergedBar:
    """包含处理后的 K 线。idx 为合并区间内最后一根原始 K 线索引。"""

    idx: int
    start_idx: int
    dt: pd.Timestamp | None
    high: float
    low: float
    direction: str = "up"  # up / down


@dataclass
class Fractal:
    """分型。top=顶分型，bottom=底分型。"""

    kind: str
    price: float
    merged_idx: int
    orig_idx: int
    dt: pd.Timestamp | None


@dataclass
class Bi:
    """笔。"""

    start: Fractal
    end: Fractal
    direction: str  # up / down
    is_open: bool = False

    @property
    def start_idx(self) -> int:
        return self.start.orig_idx

    @property
    def end_idx(self) -> int:
        return self.end.orig_idx

    @property
    def high(self) -> float:
        return max(self.start.price, self.end.price)

    @property
    def low(self) -> float:
        return min(self.start.price, self.end.price)

    @property
    def start_dt(self) -> pd.Timestamp | None:
        return self.start.dt

    @property
    def end_dt(self) -> pd.Timestamp | None:
        return self.end.dt


@dataclass
class Pivot:
    """中枢。ZG/ZD 为中枢上下沿，GG/DD 为中枢内最高/最低点。"""

    start_bi: int
    end_bi: int
    zg: float
    zd: float
    gg: float
    dd: float
    start_idx: int
    end_idx: int
    start_dt: pd.Timestamp | None = None
    end_dt: pd.Timestamp | None = None
    confirmed: bool = True

    @property
    def height(self) -> float:
        return max(self.zg - self.zd, 0.0)


@dataclass
class Divergence:
    """背驰。kind=top 顶背驰（看跌），bottom 底背驰（看涨）。"""

    kind: str
    strength: float
    bi_index: int
    prev_bi_index: int
    price: float
    orig_idx: int
    reason: str = ""


@dataclass
class BuySellPoint:
    """买卖点。kind: 1buy/2buy/3buy/1sell/2sell/3sell。"""

    kind: str
    price: float
    orig_idx: int
    merged_idx: int
    dt: pd.Timestamp | None
    bi_index: int = -1
    pivot_index: int = -1
    strength: float = 0.0
    confirmed: bool = True
    reason: str = ""


def _ts_str(ts: pd.Timestamp | None) -> str | None:
    if ts is None:
        return None
    try:
        return pd.Timestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


@dataclass
class ChanResult:
    """单级别缠论分析结果。"""

    code: str
    level: str
    asof: str | None
    bars: list[Bar] = field(default_factory=list)
    merged: list[MergedBar] = field(default_factory=list)
    fractals: list[Fractal] = field(default_factory=list)
    bis: list[Bi] = field(default_factory=list)
    pivots: list[Pivot] = field(default_factory=list)
    points: list[BuySellPoint] = field(default_factory=list)
    divergences: list[Divergence] = field(default_factory=list)
    trend_type: str = "consolidation"  # up / down / consolidation

    @property
    def latest_bi(self) -> Bi | None:
        return self.bis[-1] if self.bis else None

    @property
    def latest_pivot(self) -> Pivot | None:
        return self.pivots[-1] if self.pivots else None

    @property
    def latest_divergence(self) -> Divergence | None:
        return self.divergences[-1] if self.divergences else None

    def last_state(self) -> dict[str, Any]:
        last = self.bars[-1] if self.bars else None
        close = float(last.close) if last else 0.0
        pivot = self.latest_pivot
        state: dict[str, Any] = {
            "close": close,
            "trend_type": self.trend_type,
            "last_bi_direction": self.latest_bi.direction if self.latest_bi else None,
            "pivot": None,
        }
        if pivot:
            if close > pivot.zg:
                pos = "above"
            elif close < pivot.zd:
                pos = "below"
            else:
                pos = "inside"
            state["pivot"] = {
                "zd": round(pivot.zd, 3),
                "zg": round(pivot.zg, 3),
                "gg": round(pivot.gg, 3),
                "dd": round(pivot.dd, 3),
                "position": pos,
            }
        return state

    def summary_dict(self, higher_level_trend: str | None = None) -> dict[str, Any]:
        """紧凑摘要，写入 Signal.extra["chan"] 或供 Agent/LLM 消费。"""
        bi = self.latest_bi
        pivot = self.latest_pivot
        div = self.latest_divergence
        out: dict[str, Any] = {
            "level": self.level,
            "asof": self.asof,
            "trend_type": self.trend_type,
            "bi": None,
            "pivot": None,
            "points": [],
            "divergence": None,
            "last_state": self.last_state(),
        }
        if bi:
            out["bi"] = {
                "direction": bi.direction,
                "start": _ts_str(bi.start_dt),
                "end": _ts_str(bi.end_dt),
                "high": round(bi.high, 3),
                "low": round(bi.low, 3),
                "is_open": bi.is_open,
            }
        if pivot:
            out["pivot"] = {
                "zd": round(pivot.zd, 3),
                "zg": round(pivot.zg, 3),
                "gg": round(pivot.gg, 3),
                "dd": round(pivot.dd, 3),
                "start": _ts_str(pivot.start_dt),
                "end": _ts_str(pivot.end_dt),
                "confirmed": pivot.confirmed,
            }
        if div:
            div_dt = self.bars[div.orig_idx].dt if 0 <= div.orig_idx < len(self.bars) else None
            out["divergence"] = {
                "type": div.kind,
                "strength": round(div.strength, 3),
                "date": _ts_str(div_dt),
            }
        for p in self.points[-5:]:
            out["points"].append({
                "kind": p.kind,
                "price": round(p.price, 3),
                "date": _ts_str(p.dt),
                "orig_idx": p.orig_idx,
                "strength": round(p.strength, 3),
                "reason": p.reason,
            })
        if higher_level_trend is not None:
            out["higher_level_trend"] = higher_level_trend
        return out

    def to_dict(self) -> dict[str, Any]:
        """完整结构，供 CLI / Web / 调试使用。"""
        return {
            "code": self.code,
            "level": self.level,
            "asof": self.asof,
            "trend_type": self.trend_type,
            "counts": {
                "bars": len(self.bars),
                "merged": len(self.merged),
                "fractals": len(self.fractals),
                "bis": len(self.bis),
                "pivots": len(self.pivots),
                "points": len(self.points),
                "divergences": len(self.divergences),
            },
            "bis": [
                {
                    "direction": b.direction,
                    "start": _ts_str(b.start_dt),
                    "end": _ts_str(b.end_dt),
                    "high": round(b.high, 3),
                    "low": round(b.low, 3),
                    "is_open": b.is_open,
                }
                for b in self.bis
            ],
            "pivots": [
                {
                    "start": _ts_str(p.start_dt),
                    "end": _ts_str(p.end_dt),
                    "zd": round(p.zd, 3),
                    "zg": round(p.zg, 3),
                    "gg": round(p.gg, 3),
                    "dd": round(p.dd, 3),
                    "confirmed": p.confirmed,
                }
                for p in self.pivots
            ],
            "points": [
                {
                    "kind": p.kind,
                    "price": round(p.price, 3),
                    "date": _ts_str(p.dt),
                    "orig_idx": p.orig_idx,
                    "strength": round(p.strength, 3),
                    "reason": p.reason,
                }
                for p in self.points
            ],
            "divergences": [
                {
                    "type": d.kind,
                    "strength": round(d.strength, 3),
                    "price": round(d.price, 3),
                    "date": _ts_str(self.bars[d.orig_idx].dt) if 0 <= d.orig_idx < len(self.bars) else None,
                    "reason": d.reason,
                }
                for d in self.divergences
            ],
            "last_state": self.last_state(),
        }
