"""把内置策略规则向量化，产出 买入/卖出/评分 矩阵。

规则尽量对齐 strategies/builtin/*.py 的 generate_signals，差异：
- 不做逐日 df_slice 重算，改为整段序列向量化（数学等价，速度提升几个数量级）
- 基本面/资金流确认在技术面信号之后作为可选的过滤层单独回测
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class SignalSet:
    name: str
    buy: pd.DataFrame
    sell: pd.DataFrame
    score: pd.DataFrame
    exit_atr: float = 2.0       # 硬止损 ATR 倍数
    trail_atr: float = 0.0      # 0=不启用移动止损
    take_atr: float = 0.0       # 0=不启用固定止盈


class TechContext:
    """按需计算并缓存指标矩阵。"""

    def __init__(self, panel):
        self.panel = panel
        self._c: dict = {}

    def ma(self, n: int):
        k = ("ma", n)
        if k not in self._c:
            self._c[k] = self.panel["close"].rolling(n, min_periods=n).mean()
        return self._c[k]

    def atr(self, n: int = 14, method: str = "rolling"):
        k = ("atr", n, method)
        if k not in self._c:
            self._c[k] = ind.atr(self.panel, n, method)
        return self._c[k]

    def adx(self, n: int = 14):
        k = ("adx", n)
        if k not in self._c:
            self._c[k] = ind.adx(self.panel, n)
        return self._c[k]

    def rsi(self, n: int = 14):
        k = ("rsi", n)
        if k not in self._c:
            self._c[k] = ind.rsi(self.panel, n)
        return self._c[k]

    def macd(self, fast=12, slow=26, signal=9):
        k = ("macd", fast, slow, signal)
        if k not in self._c:
            self._c[k] = ind.macd(self.panel, fast, slow, signal)
        return self._c[k]

    def bb(self, n=20, sd=2.0):
        k = ("bb", n, sd)
        if k not in self._c:
            self._c[k] = ind.bollinger(self.panel, n, sd)
        return self._c[k]

    def vol_ma(self, n=20, exclude_today=True):
        k = ("vol_ma", n, exclude_today)
        if k not in self._c:
            v = self.panel["volume"]
            s = v.shift(1) if exclude_today else v
            self._c[k] = s.rolling(n, min_periods=n).mean()
        return self._c[k]


def ma_cross(ctx: TechContext, p: dict | None = None) -> SignalSet:
    p = p or {}
    fast = p.get("fast", 5)
    slow = p.get("slow", 20)
    adx_min = p.get("adx_min", 20)
    vol_ratio = p.get("vol_ratio", 1.5)

    ma_f, ma_s = ctx.ma(fast), ctx.ma(slow)
    cu, cd = ind.cross_up(ma_f, ma_s), ind.cross_down(ma_f, ma_s)
    adx = ctx.adx(14)
    vol_ma = ctx.vol_ma(20)
    vol_ok = ctx.panel["volume"] >= vol_ma * vol_ratio

    buy = cu & (adx >= adx_min)
    sell = cd
    score = pd.DataFrame(55.0, index=buy.index, columns=buy.columns)
    score = score + vol_ok.astype(float) * 15 + (adx / 10).clip(0, 20)
    return SignalSet(f"ma_cross[{fast}/{slow},adx{adx_min}]", buy.fillna(False),
                     sell.fillna(False), score, exit_atr=2.5, trail_atr=0.0)


def macd_rsi(ctx: TechContext, p: dict | None = None) -> SignalSet:
    p = p or {}
    fast, slow, sig = p.get("fast", 12), p.get("slow", 26), p.get("signal", 9)
    period = p.get("rsi_period", 14)
    oversold = p.get("rsi_oversold", 40)
    lookback = p.get("divergence_lookback", 10)

    dif, dea = ctx.macd(fast, slow, sig)
    rsi = ctx.rsi(period)
    close = ctx.panel["close"]

    cu, cd = ind.cross_up(dif, dea), ind.cross_down(dif, dea)
    oversold_ok = (rsi.shift(1) < oversold) & (rsi >= oversold - 5)
    div_ok = (close <= close.rolling(lookback, min_periods=lookback).min()) & \
             (rsi > rsi.rolling(lookback, min_periods=lookback).min() + 2.0)
    buy = cu & (oversold_ok | div_ok)
    sell = cd
    score = pd.DataFrame(50.0, index=buy.index, columns=buy.columns)
    score = score + div_ok.astype(float) * 25 + oversold_ok.astype(float) * 15
    score = score + (close > ctx.ma(20)).astype(float) * 10
    return SignalSet(f"macd_rsi[rsi{oversold}]", buy.fillna(False), sell.fillna(False),
                     score, exit_atr=2.0, trail_atr=0.0)


def vol_price_breakout(ctx: TechContext, p: dict | None = None) -> SignalSet:
    p = p or {}
    lookback = p.get("lookback", 20)
    vol_ratio = p.get("vol_ratio", 2.0)
    breakout_atr = p.get("breakout_atr", 0.2)
    buildup_days = p.get("buildup_days", 5)
    buildup_ratio = p.get("buildup_ratio", 1.0)

    close, volume = ctx.panel["close"], ctx.panel["volume"]
    ref_high = ind.rolling_high(close, lookback)
    ref_low = ind.rolling_low(close, lookback)
    atr = ctx.atr(14)
    vol_ma = ctx.vol_ma(20)

    price_break = close > ref_high + atr * breakout_atr
    vol_surge = volume >= vol_ma * vol_ratio
    if buildup_days > 0:
        buildup_vol = volume.shift(lookback + 1).rolling(
            buildup_days, min_periods=buildup_days).mean()
        buildup = buildup_vol <= vol_ma * buildup_ratio
    else:
        buildup = pd.DataFrame(True, index=close.index, columns=close.columns)
    buy = price_break & vol_surge & buildup
    sell = close < ref_low
    breakout_pct = close / ref_high - 1
    score = 50 + (breakout_pct * 800).clip(0, 25) + ((volume / vol_ma) - vol_ratio).clip(0, 4) * 5
    return SignalSet(f"breakout[lb{lookback},vr{vol_ratio}]", buy.fillna(False),
                     sell.fillna(False), score, exit_atr=2.0, trail_atr=0.0)


def etf_momentum(ctx: TechContext, p: dict | None = None) -> SignalSet:
    """ETF 相对+绝对动量：评分用于每日/每周排序，不产生精确卖点。"""
    p = p or {}
    lookback = p.get("lookback", 20)
    ma_trend = p.get("ma_trend", 60)
    abs_mom = p.get("abs_momentum", True)
    close = ctx.panel["close"]
    mom = close / close.shift(lookback) - 1
    rf = 0.02 * lookback / 252
    score = mom * 1000 + 50
    buy = mom > rf if abs_mom else mom > 0
    if ma_trend:
        buy = buy & (close > ctx.ma(ma_trend))
    sell = mom < 0
    return SignalSet(f"etf_momentum[lb{lookback}]", buy.fillna(False), sell.fillna(False),
                     score, exit_atr=2.5, trail_atr=0.0)


def regime_series(panel, index_code: str = "000300") -> pd.Series:
    """沪深300 趋势 regime：1=risk_on, 0=neutral, -1=risk_off。"""
    from data.store import load
    df = load(index_code, asset_type="index", market="cn",
              start=str(panel.dates[0].date()), end=str(panel.dates[-1].date()))
    if df is None or df.empty:
        return pd.Series(0, index=panel.dates)
    close = df["close"].reindex(panel.dates).ffill()
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    reg = pd.Series(0, index=panel.dates)
    reg[(close > ma60) & (ma20 > ma60)] = 1
    reg[close < ma60] = -1
    return reg


def breadth_series(panel, eligible: pd.DataFrame, ma_n: int = 20) -> pd.Series:
    close = panel["close"].where(eligible)
    ma = close.rolling(ma_n, min_periods=ma_n).mean()
    above = (close > ma) & eligible
    return above.sum(axis=1) / eligible.sum(axis=1).replace(0, np.nan)
