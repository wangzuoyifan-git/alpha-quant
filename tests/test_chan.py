"""
tests/test_chan.py — 缠论核心结构 + chan_theory 策略单元测试

覆盖：
- K线包含处理
- 分型 / 笔 / 中枢
- 三买 / 三卖识别
- 日线+周线多级别
- 因果性（不引用未来K线）
- 策略注册 / 出场框架
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from chan.adapter import resample_ohlc
from chan.bi import find_bis
from chan.buy_sell import detect_points
from chan.divergence import find_divergences
from chan.engine import ChanEngine
from chan.fractal import find_fractals
from chan.kline import merge_bars
from chan.models import Bar, Bi, Fractal
from chan.multi_level import analyze_multi_level
from chan.pivot import find_pivots
from strategies.base import ExitLevels
from strategies.builtin import BUILTIN_STRATEGIES
from strategies.builtin.chan_theory import ChanTheoryStrategy


def make_df(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    close = np.linspace(10, 18, n) + rng.normal(0, 0.25, n).cumsum() * 0.4
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "date": dates,
        "open": close + rng.uniform(-0.2, 0.2, n),
        "high": close + rng.uniform(0.05, 0.6, n),
        "low": close - rng.uniform(0.05, 0.6, n),
        "close": close,
        "volume": rng.integers(1_000_000, 3_000_000, n).astype(float),
    })


def _bar(idx: int, high: float, low: float) -> Bar:
    return Bar(
        idx=idx,
        dt=pd.Timestamp("2024-01-02") + pd.Timedelta(days=idx),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=1_000_000,
    )


def _fx(kind: str, price: float, merged_idx: int) -> Fractal:
    return Fractal(
        kind=kind,
        price=price,
        merged_idx=merged_idx,
        orig_idx=merged_idx,
        dt=pd.Timestamp("2024-01-02") + pd.Timedelta(days=merged_idx),
    )


def _bi(kind: str, start_price: float, end_price: float, merged_idx: int) -> Bi:
    if kind == "up":
        start = _fx("bottom", start_price, merged_idx)
        end = _fx("top", end_price, merged_idx + 2)
    else:
        start = _fx("top", start_price, merged_idx)
        end = _fx("bottom", end_price, merged_idx + 2)
    return Bi(start=start, end=end, direction=kind)


def _mark_open(bis: list[Bi]) -> list[Bi]:
    for i, b in enumerate(bis):
        b.is_open = i == len(bis) - 1
    return bis


class TestKline:
    def test_inclusion_merge_down(self):
        bars = [_bar(0, 10, 5), _bar(1, 9, 6)]
        merged = merge_bars(bars)
        assert len(merged) == 1
        assert merged[0].high == 9
        assert merged[0].low == 5
        assert merged[0].idx == 1

    def test_no_inclusion_keeps_bars(self):
        bars = [_bar(0, 10, 5), _bar(1, 11, 7), _bar(2, 9, 6)]
        merged = merge_bars(bars)
        assert len(merged) == 3
        assert merged[1].direction == "up"
        assert merged[2].direction == "down"


class TestFractalBi:
    def test_fractal_top_bottom(self):
        merged = merge_bars([
            _bar(0, 10, 8),
            _bar(1, 12, 9),
            _bar(2, 11, 8),
            _bar(3, 9, 6),
            _bar(4, 10, 7),
        ])
        fractals = find_fractals(merged)
        kinds = [f.kind for f in fractals]
        assert "top" in kinds
        assert "bottom" in kinds

    def test_bi_alternates_and_min_gap(self):
        fractals = [
            _fx("bottom", 10, 0),
            _fx("top", 12, 2),   # gap too small -> skip
            _fx("top", 13, 5),
            _fx("bottom", 11, 9),
            _fx("top", 14, 13),
        ]
        bis = find_bis(fractals, min_bars=4)
        assert bis
        assert all(b.direction in {"up", "down"} for b in bis)
        assert len(bis) >= 2
        # 相邻笔方向交替
        for a, b in itertools.pairwise(bis):
            assert a.direction != b.direction

    def test_bi_replaces_more_extreme_same_kind(self):
        fractals = [
            _fx("bottom", 10, 0),
            _fx("top", 12, 5),
            _fx("top", 13, 8),   # 更极端的顶，替换
            _fx("bottom", 11, 12),
        ]
        bis = find_bis(fractals, min_bars=4)
        assert bis[0].end.price == 13


class TestPivotAndPoints:
    def test_three_buy(self):
        bis = _mark_open([
            _bi("up", 10, 12, 0),
            _bi("down", 12, 10.5, 2),
            _bi("up", 10.5, 12.5, 4),
            _bi("down", 12.5, 12.2, 6),
            _bi("up", 12.2, 14, 8),
        ])
        pivots = find_pivots(bis)
        assert pivots
        assert pivots[0].zg == 12
        assert pivots[0].zd == 10.5
        points = detect_points(bis, pivots, [])
        assert any(p.kind == "3buy" for p in points)
        p = next(p for p in points if p.kind == "3buy")
        assert p.price == 12.2

    def test_three_sell(self):
        bis = _mark_open([
            _bi("down", 20, 18, 0),
            _bi("up", 18, 19, 2),
            _bi("down", 19, 17.5, 4),
            _bi("up", 17.5, 17.8, 6),
            _bi("down", 17.8, 15, 8),
        ])
        pivots = find_pivots(bis)
        points = detect_points(bis, pivots, [])
        assert any(p.kind == "3sell" for p in points)


class TestDivergence:
    def test_bottom_divergence(self):
        closes = (
            list(np.linspace(100, 80, 40))
            + list(np.linspace(80, 90, 20))
            + list(np.linspace(90, 79, 30))
            + list(np.linspace(79, 85, 50))
        )
        bars = [
            Bar(
                idx=i,
                dt=pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                open=c,
                high=c + 0.3,
                low=c - 0.3,
                close=c,
                volume=1_000_000,
            )
            for i, c in enumerate(closes)
        ]

        def fx(kind: str, price: float, idx: int) -> Fractal:
            return Fractal(kind=kind, price=price, merged_idx=idx, orig_idx=idx, dt=bars[idx].dt)

        bis = _mark_open([
            Bi(start=fx("top", 100, 0), end=fx("bottom", 80, 39), direction="down"),
            Bi(start=fx("bottom", 80, 39), end=fx("top", 90, 59), direction="up"),
            Bi(start=fx("top", 90, 59), end=fx("bottom", 79, 89), direction="down"),
            Bi(start=fx("bottom", 79, 89), end=fx("top", 85, 139), direction="up"),
        ])
        divs = find_divergences(bars, bis)
        assert any(d.kind == "bottom" for d in divs)


class TestEngineAndMultiLevel:
    def test_engine_outputs_structures(self):
        df = make_df()
        result = ChanEngine().analyze(df, code="600519")
        assert result.code == "600519"
        assert len(result.bars) == len(df)
        assert len(result.merged) <= len(result.bars)
        assert result.asof == df["date"].iloc[-1].strftime("%Y-%m-%d")
        assert result.trend_type in {"up", "down", "consolidation"}
        assert "last_state" in result.summary_dict()

    def test_no_future_index_in_points(self):
        df = make_df(n=260)
        result = ChanEngine().analyze(df)
        for p in result.points:
            assert 0 <= p.orig_idx < len(result.bars)

    def test_weekly_resample_drops_incomplete_week(self):
        dates = pd.to_datetime([
            "2024-01-01", "2024-01-02", "2024-01-03",
            "2024-01-08", "2024-01-09",
        ])
        df = pd.DataFrame({
            "date": dates,
            "open": [10, 11, 12, 13, 14],
            "high": [11, 12, 13, 14, 15],
            "low": [9, 10, 11, 12, 13],
            "close": [10.5, 11.5, 12.5, 13.5, 14.5],
            "volume": [100, 200, 300, 400, 500],
        })
        weekly = resample_ohlc(df, "W-FRI")
        assert len(weekly) == 1
        assert weekly.index[0] == pd.Timestamp("2024-01-05")

    def test_multi_level(self):
        df = make_df(n=400)
        ml = analyze_multi_level(df, code="600519")
        assert ml["daily"] is not None
        assert ml["higher_level_trend"] in {"up", "down", "consolidation", "unknown"}
        if ml["weekly"] is not None:
            assert ml["weekly"].level == "weekly"


class TestChanStrategy:
    def test_registered(self):
        assert "chan_theory" in BUILTIN_STRATEGIES

    def test_insufficient_data_returns_empty(self):
        df = make_df(n=100)
        assert ChanTheoryStrategy().generate_signals(df, "600519", "测试") == []

    def test_exit_levels_are_chan_based(self):
        df = make_df(n=400)
        strategy = ChanTheoryStrategy()
        result = ChanEngine().analyze(df)
        if result.latest_pivot is None:
            return
        levels = strategy.compute_exit_levels(df, entry_price=float(df["close"].iloc[-1]))
        assert isinstance(levels, ExitLevels)
        assert levels.exit_logic == "trail_chan"
        assert levels.stop_loss > 0

    def test_profile(self):
        profile = ChanTheoryStrategy().profile()
        assert profile["style"] == "breakout"
        assert profile["time_horizon"] == "short"
        assert profile["position_type"] == "trading"
