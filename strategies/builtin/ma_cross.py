"""
内置策略：趋势跟踪（个股主力）

正交维度：趋势跟踪（Trend）— 交易仓正交框架的核心策略
升级自原 ma_cross（v1.0），新增：
  - ADX ≥ 20 趋势强度过滤（剔除无趋势震荡市的假金叉）
  - 资金流内置确认（主力近 N 日净流入为正，吸收原 money_flow 策略功能）
  - 快线斜率趋势加分保留

金叉判定：MA5 上穿 MA20 + 量能放大 + ADX≥20（趋势存在）+ 资金流净流入（可选）
死叉：MA5 下穿 MA20 → SELL
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal
from data.store_moneyflow import load as load_mf


def _calc_adx(df: pd.DataFrame, period: int = 14) -> float:
    """计算当日 ADX（平均趋向指数），衡量趋势强度（与方向无关）。"""
    if len(df) < period * 2:
        return 0.0
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    dm_plus = (high - high.shift()).clip(lower=0)
    dm_minus = (low.shift() - low).clip(lower=0)
    # 若同一根K线两个方向都有，取较大方向，另一方向置0
    mask = dm_plus < dm_minus
    dm_plus[mask] = 0
    dm_minus[~mask] = 0

    atr = tr.ewm(span=period, adjust=False).mean()
    di_plus = 100 * dm_plus.ewm(span=period, adjust=False).mean() / (atr + 1e-9)
    di_minus = 100 * dm_minus.ewm(span=period, adjust=False).mean() / (atr + 1e-9)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-9)
    adx = dx.ewm(span=period, adjust=False).mean()
    return float(adx.iloc[-1])


class MACrossStrategy(BaseStrategy):
    """
    趋势跟踪策略（个股主力）

    参数：
      fast_period  : 快线周期（默认 5）
      slow_period  : 慢线周期（默认 20）
      volume_ratio : 放量倍数（默认 1.5）
      adx_period   : ADX 计算周期（默认 14）
      adx_min      : 最低趋势强度阈值（默认 20，低于此视为震荡不开仓）
      mf_lookback  : 资金流确认回看天数（默认 5，0=禁用）
    """

    name = "趋势跟踪"
    description = "MA金叉+ADX趋势确认+资金流共振，剔除无趋势震荡市假信号"
    version = "2.0.0"
    applicable_regimes = ["balanced", "risk_on"]
    style = "momentum"
    applicable_scenario = "趋势启动期量价配合且主力资金流入时表现最佳；震荡市被ADX过滤"
    risk_profile = "balanced"
    time_horizon = "short"
    data_dependency = ["technical", "moneyflow"]
    market_condition = "range"  # @deprecated

    def requires_bars(self) -> int:
        slow = self.params.get("slow_period", 20)
        adx = self.params.get("adx_period", 14)
        return max(slow * 2, adx * 3, 60)

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        fast = self.params.get("fast_period", 5)
        slow = self.params.get("slow_period", 20)
        vol_ratio = self.params.get("volume_ratio", 1.5)
        adx_period = self.params.get("adx_period", 14)
        adx_min = self.params.get("adx_min", 20)
        mf_lookback = self.params.get("mf_lookback", 5)

        if len(df) < slow + 2:
            return []

        df = df.copy()
        close = pd.to_numeric(df["close"], errors="coerce")
        df["ma_fast"] = close.rolling(fast).mean()
        df["ma_slow"] = close.rolling(slow).mean()
        df["vol_ma"] = pd.to_numeric(df["volume"], errors="coerce").rolling(20).mean()

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # ── 金叉检测 ────────────────────────────────────────────────
        if not (prev["ma_fast"] <= prev["ma_slow"] and last["ma_fast"] > last["ma_slow"]):
            # 死叉：SELL
            if prev["ma_fast"] >= prev["ma_slow"] and last["ma_fast"] < last["ma_slow"]:
                return [Signal(
                    code=code, name=name, signal="SELL",
                    price=float(last["close"]),
                    reason=f"MA{fast}下穿MA{slow}",
                    score=65.0,
                )]
            return []

        # ── ADX 趋势强度过滤 ─────────────────────────────────────────
        adx = _calc_adx(df, adx_period)
        if adx < adx_min:
            return []  # 震荡市，过滤

        # ── 量能放大 ─────────────────────────────────────────────────
        vol_ok = bool(pd.notna(last["vol_ma"]) and
                      last["volume"] >= last["vol_ma"] * vol_ratio)

        score = 55.0 + (15.0 if vol_ok else 0.0)
        reasons = [f"MA{fast}上穿MA{slow}", f"ADX{adx:.0f}"]
        if vol_ok:
            reasons.append(f"量比{float(last['volume'])/float(last['vol_ma']):.1f}x")

        # ── 快线斜率加分 ─────────────────────────────────────────────
        if len(df) >= 3:
            base = float(df.iloc[-3]["ma_fast"])
            if base > 0:
                slope = (float(last["ma_fast"]) - base) / base * 100
                score = min(score + slope * 5, 95.0)

        # ── 资金流确认（内置，可选）──────────────────────────────────
        if mf_lookback > 0:
            try:
                mf = load_mf("stock", code=code)
                if mf is not None and len(mf) >= mf_lookback and "net_mf_amount" in mf.columns:
                    net = float(mf.iloc[-mf_lookback:]["net_mf_amount"].sum())
                    # 时效标注：资金流最新日期是否明显落后于当前 df
                    mf_latest = mf.index.max()
                    df_latest = pd.to_datetime(df["date"].iloc[-1]) if "date" in df.columns else None
                    stale = (df_latest is not None and
                             (df_latest - mf_latest).days > 5)
                    stale_tag = "(数据较旧)" if stale else ""
                    # 金额显示：≥100万用亿，否则用万，避免 0.0亿 的误导
                    abs_net = abs(net)
                    if abs_net >= 1e6:
                        amt_str = f"{net/1e8:.2f}亿"
                    else:
                        amt_str = f"{net/1e4:.1f}万"
                    if net > 0:
                        score = min(score + 5.0, 100.0)
                        reasons.append(f"主力净流入{amt_str}{stale_tag}")
                    else:
                        reasons.append(f"主力净流出{amt_str}⚠{stale_tag}")
                else:
                    reasons.append("资金流数据不足")
            except Exception:
                reasons.append("资金流不可用")

        return [Signal(
            code=code, name=name, signal="BUY",
            price=float(last["close"]),
            reason="，".join(reasons),
            score=min(score, 100.0),
        )]
