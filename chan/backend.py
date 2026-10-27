"""
缠论后端协议。

默认使用内部实现 InternalChanBackend。
未来若需要接入 chan.py / czsc，可新增适配器实现同一协议，策略层零改动。
"""
from __future__ import annotations

from typing import Protocol

import pandas as pd

from .engine import ChanEngine
from .models import ChanResult


class ChanBackend(Protocol):
    def analyze(
        self,
        df: pd.DataFrame,
        level: str = "daily",
        code: str = "",
        asof: str | None = None,
    ) -> ChanResult: ...


class InternalChanBackend:
    """默认后端：chan 包内部实现。"""

    def __init__(self, config: dict | None = None):
        self.engine = ChanEngine(config)

    def analyze(
        self,
        df: pd.DataFrame,
        level: str = "daily",
        code: str = "",
        asof: str | None = None,
    ) -> ChanResult:
        return self.engine.analyze(df, level=level, code=code, asof=asof)
