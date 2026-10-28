"""向量化技术指标（日期 x 代码矩阵）。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ema(df: pd.DataFrame, span: int) -> pd.DataFrame:
    return df.ewm(span=span, adjust=False, min_periods=span).mean()


def true_range(panel) -> pd.DataFrame:
    high, low, close = panel["high"], panel["low"], panel["close"]
    prev_close = close.shift(1)
    a = high - low
    b = (high - prev_close).abs()
    c = (low - prev_close).abs()
    return np.maximum(np.maximum(a, b), c)


def atr(panel, period: int = 14, method: str = "rolling") -> pd.DataFrame:
    tr = true_range(panel)
    if method == "ewm":
        return tr.ewm(span=period, adjust=False, min_periods=period).mean()
    return tr.rolling(period, min_periods=period).mean()


def adx(panel, period: int = 14) -> pd.DataFrame:
    high, low = panel["high"], panel["low"]
    tr = true_range(panel)
    up = high.diff()
    down = -low.diff()
    dm_plus = up.clip(lower=0).where(up > down, 0.0)
    dm_minus = down.clip(lower=0).where(down >= up, 0.0)
    atr_e = tr.ewm(span=period, adjust=False, min_periods=period).mean()
    di_plus = 100 * dm_plus.ewm(span=period, adjust=False, min_periods=period).mean() / (atr_e + 1e-9)
    di_minus = 100 * dm_minus.ewm(span=period, adjust=False, min_periods=period).mean() / (atr_e + 1e-9)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-9)
    return dx.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(panel, period: int = 14) -> pd.DataFrame:
    delta = panel["close"].diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - 100 / (1 + rs)


def macd(panel, fast: int = 12, slow: int = 26, signal: int = 9):
    close = panel["close"]
    dif = _ema(close, fast) - _ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return dif, dea


def bollinger(panel, period: int = 20, std_dev: float = 2.0):
    close = panel["close"]
    mid = close.rolling(period, min_periods=period).mean()
    std = close.rolling(period, min_periods=period).std()
    return mid + std_dev * std, mid, mid - std_dev * std


def cross_up(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return (a.shift(1) <= b.shift(1)) & (a > b)


def cross_down(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return (a.shift(1) >= b.shift(1)) & (a < b)


def rolling_high(df: pd.DataFrame, n: int, exclude_today: bool = True) -> pd.DataFrame:
    s = df.shift(1) if exclude_today else df
    return s.rolling(n, min_periods=n).max()


def rolling_low(df: pd.DataFrame, n: int, exclude_today: bool = True) -> pd.DataFrame:
    s = df.shift(1) if exclude_today else df
    return s.rolling(n, min_periods=n).min()
