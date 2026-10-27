"""
tests/test_chan_tools.py — 缠论 Tool Layer 单元测试
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_tools import (
    get_chan_buy_points,
    get_chan_multi_level,
    get_chan_snapshot,
)


def _df(n: int = 120) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    close = np.linspace(10, 16, n) + rng.normal(0, 0.2, n).cumsum() * 0.3
    return pd.DataFrame({
        "open": close,
        "high": close + 0.3,
        "low": close - 0.3,
        "close": close,
        "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
    }, index=dates)


class TestChanTools:

    def test_snapshot_success(self, monkeypatch):
        import data.store as store

        monkeypatch.setattr(store, "load", lambda *a, **k: _df())
        result = get_chan_snapshot("600519")
        assert result["success"] is True
        assert result["snapshot"]["level"] == "daily"
        assert "last_state" in result["snapshot"]

    def test_snapshot_weekly_resamples(self, monkeypatch):
        import data.store as store

        monkeypatch.setattr(store, "load", lambda *a, **k: _df(n=200))
        result = get_chan_snapshot("600519", level="weekly")
        assert result["success"] is True
        assert result["snapshot"]["level"] == "weekly"

    def test_snapshot_no_data(self, monkeypatch):
        import data.store as store

        monkeypatch.setattr(store, "load", lambda *a, **k: None)
        result = get_chan_snapshot("600519")
        assert result["success"] is False
        assert "无本地行情数据" in result["error"]

    def test_buy_points_success(self, monkeypatch):
        import data.store as store

        monkeypatch.setattr(store, "load", lambda *a, **k: _df())
        result = get_chan_buy_points("600519", limit=5)
        assert result["success"] is True
        assert isinstance(result["points"], list)
        assert result["count"] == len(result["points"])

    def test_multi_level_success(self, monkeypatch):
        import data.store as store

        monkeypatch.setattr(store, "load", lambda *a, **k: _df(n=400))
        result = get_chan_multi_level("600519")
        assert result["success"] is True
        assert result["daily"]["level"] == "daily"
        assert result["higher_level_trend"] in {"up", "down", "consolidation", "unknown"}
