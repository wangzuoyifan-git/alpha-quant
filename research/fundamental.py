"""基本面策略的 point-in-time 月度组合回测（B1/B2/B3）。

关键点：只用 pubDate <= 回测日的财报，避免未来函数；PE 分位只用当日之前数据。
输出与 research.simulator 相同的指标，便于横向对比。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data.store_fundamental import load as load_fund
from strategies.base import ttm_roe

from .simulator import BTResult, BTTrade


def _pit_daily(df: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """把报告期数据按公告日 pubDate 对齐到日频（只用已公告数据）。"""
    if df is None or df.empty:
        return pd.DataFrame(index=dates)
    df = df.copy()
    if "pubDate" in df.columns:
        pub = pd.to_datetime(df["pubDate"], errors="coerce")
    else:
        pub = pd.Series(df.index, index=df.index)
    df["_pub"] = pub
    df = df[df["_pub"].notna()].sort_values("_pub")
    df = df.drop_duplicates("_pub", keep="last").set_index("_pub")
    df = df.drop(columns=["pubDate"], errors="ignore")
    return df.reindex(dates, method="ffill")


def build_fund_matrices(panel, use_cache: bool = True) -> dict[str, pd.DataFrame]:
    from pathlib import Path
    cache = Path(__file__).parent.parent / "data" / "cache" / "research_fund_v2.npz"
    keys = ["roe_ttm", "yoy_ni", "yoy_eq", "np1", "np2", "debt"]
    if use_cache and cache.exists():
        z = np.load(cache, allow_pickle=True)
        if set(z.files) >= set([*keys, "dates", "codes"]):
            di = pd.DatetimeIndex(z["dates"])
            codes = list(z["codes"])
            return {k: pd.DataFrame(z[k], index=di, columns=codes) for k in keys}

    out = {k: [] for k in keys}
    for i, code in enumerate(panel.codes):
        profit = load_fund(code, "profit")
        growth = load_fund(code, "growth")
        balance = load_fund(code, "balance")
        d = pd.DataFrame(index=panel.dates)
        if profit is not None and not profit.empty:
            p = profit.copy()
            if "roeAvg" in p.columns:
                # 逐期 point-in-time 计算 TTM ROE，避免用到未来季度数据
                p["roe_ttm"] = [ttm_roe(p.iloc[:k + 1]) for k in range(len(p))]
            else:
                p["roe_ttm"] = np.nan
            p["np1"] = p.get("netProfit", pd.Series(index=p.index, dtype=float))
            p["np2"] = p.get("netProfit", pd.Series(index=p.index, dtype=float)).shift(1)
            pp = _pit_daily(p[["pubDate", "roe_ttm", "np1", "np2"]], panel.dates)
            d = d.join(pp[["roe_ttm", "np1", "np2"]])
        if growth is not None and not growth.empty:
            g = _pit_daily(growth[["pubDate", "YOYNI", "YOYEquity"]], panel.dates)
            d = d.join(g.rename(columns={"YOYNI": "yoy_ni", "YOYEquity": "yoy_eq"}))
        if balance is not None and not balance.empty:
            b = _pit_daily(balance[["pubDate", "liabilityToAsset"]], panel.dates)
            d = d.join(b.rename(columns={"liabilityToAsset": "debt"}))
        for k in keys:
            out[k].append(d[k] if k in d.columns else pd.Series(np.nan, index=panel.dates))
        if (i + 1) % 1000 == 0:
            print(f"  fundamental {i+1}/{len(panel.codes)}", flush=True)
    mat = {k: pd.DataFrame(pd.concat(out[k], axis=1).to_numpy(),
                           index=panel.dates, columns=panel.codes).astype("float32")
           for k in keys}
    np.savez_compressed(cache, dates=np.array(panel.dates, dtype="datetime64[ns]"),
                        codes=np.array(panel.codes, dtype=object),
                        **{k: mat[k].to_numpy(dtype="float32") for k in keys})
    return mat


def scores(panel, fund: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    pe, pb = panel["pe_ttm"], panel["pb_mrq"]
    roe, yoy_ni, yoy_eq = fund["roe_ttm"], fund["yoy_ni"], fund["yoy_eq"]
    np1, np2, debt = fund["np1"], fund["np2"], fund["debt"]

    pe_pct = pe.rolling(60, min_periods=20).apply(
        lambda x: (x < x[-1]).sum() / max(len(x), 1), raw=True)
    b1 = (roe >= 0.15) & (pe > 0) & (pe <= 40) & (pe_pct <= 0.6) & (pb <= 5) & (yoy_ni >= 0)
    s1 = (40 + roe * 100 * 1.5 + (40 - pe).clip(lower=0) * 0.5).where(b1)

    b2 = (pe > 0) & (pe <= 15) & (pb <= 1.5) & (np1 > 0) & (np2 > 0) & (debt <= 0.6)
    # 排除金融地产（申万一级）
    from data.store_meta import get_stock_l1_industry
    fin = {"801780", "801790", "801180"}
    exclude = np.array([get_stock_l1_industry(c) in fin for c in panel.codes])
    b2 = b2.copy()
    b2.loc[:, exclude] = False
    s2 = (50 + (1.5 - pb) / 1.5 * 30 + (15 - pe).clip(lower=0)).where(b2)

    peg = pe / (yoy_ni * 100)
    b3 = (yoy_ni >= 0.25) & (yoy_eq >= 0.15) & (roe >= 0.10) & (peg > 0) & (peg <= 1.5)
    s3 = (40 + yoy_ni * 100 * 0.5 + roe * 100).where(b3)
    return {"B1_quality": s1, "B2_value": s2, "B3_growth": s3}


def run_monthly(panel, score: pd.DataFrame, eligible: pd.DataFrame,
                top_n: int = 20, name: str = "fundamental",
                start: str = "2024-07-01", end: str = "2026-10-26",
                commission: float = 0.0003, slippage: float = 0.001,
                stamp_duty: float = 0.0005) -> BTResult:
    """每月末调仓，持有评分最高的 top_n，等权。"""
    dates = panel.dates
    start_i = int(np.searchsorted(dates, pd.Timestamp(start)))
    end_i = int(np.searchsorted(dates, pd.Timestamp(end), side="right") - 1)
    op, cl = panel["open"].to_numpy(), panel["close"].to_numpy()
    hi, lo = panel["high"].to_numpy(), panel["low"].to_numpy()
    vol = panel["volume"].to_numpy()
    tradable = np.isfinite(op) & np.isfinite(vol) & (vol > 0)
    sc, elig = score.to_numpy(), eligible.to_numpy()
    prev_close = np.roll(cl, 1)
    prev_close[0] = cl[0]
    lim = np.array([
        0.30 if c[0] in ("4", "8") else
        0.20 if c[:3] in ("300", "301", "688", "689") else 0.10
        for c in panel.codes
    ], dtype="float32")
    limit_up = prev_close * (1 + lim[None, :] * 0.98)
    limit_dn = prev_close * (1 - lim[None, :] * 0.98)
    one_word = np.isfinite(hi) & (hi == lo)
    can_buy = tradable & (op < limit_up) & ~one_word
    can_sell = tradable & ~(one_word & (cl < prev_close) & (op <= limit_dn))
    month = pd.Series(dates, index=dates).dt.to_period("M")
    rebal = month.ne(month.shift(1)).to_numpy().copy()
    rebal[: max(start_i, 1)] = False
    rebal[start_i] = True

    cash = 1_000_000.0
    positions: dict[int, dict] = {}
    trades: list[BTTrade] = []
    eq_arr = np.full(len(dates), np.nan)
    invested = 0
    for i in range(max(start_i, 1), end_i + 1):
        if rebal[i]:
            row = np.nan_to_num(sc[i - 1], nan=-1e12)
            ranked = np.argsort(-row)
            top = [ci for ci in ranked if elig[i - 1, ci] and row[ci] > -1e11 and can_buy[i, ci]][:top_n]
            for ci in list(positions.keys()):
                if ci not in top:
                    if not can_sell[i, ci]:
                        continue
                    pos = positions.pop(ci)
                    px = float(op[i, ci]) * (1 - slippage) if np.isfinite(op[i, ci]) else pos["entry_price"]
                    proceeds = pos["shares"] * px * (1 - commission - stamp_duty)
                    cash += proceeds
                    trades.append(BTTrade(panel.codes[ci], dates[pos["entry_i"]], pos["entry_price"],
                                          dates[i], px, pos["shares"], proceeds - pos["cost"],
                                          (px / pos["entry_price"] - 1) * 100, i - pos["entry_i"], "rebalance"))
            for ci in top:
                if ci in positions:
                    continue
                px = float(op[i, ci]) * (1 + slippage)
                target = 1_000_000 / top_n
                shares = int(min(cash, target) / px / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * px * (1 + commission)
                if cost > cash:
                    continue
                cash -= cost
                positions[ci] = {"shares": shares, "entry_price": px, "entry_i": i, "cost": cost}
        mv = sum(p["shares"] * cl[i, ci] for ci, p in positions.items() if np.isfinite(cl[i, ci]))
        eq_arr[i] = cash + mv
        if mv > 0:
            invested += 1

    valid = (np.arange(len(dates)) >= max(start_i, 1)) & (np.arange(len(dates)) <= end_i)
    eq = pd.Series(eq_arr[valid], index=dates[valid]).dropna().ffill()
    last = end_i
    for ci, pos in list(positions.items()):
        px = float(cl[last, ci]) * (1 - slippage) if np.isfinite(cl[last, ci]) else pos["entry_price"]
        proceeds = pos["shares"] * px * (1 - commission - stamp_duty)
        trades.append(BTTrade(panel.codes[ci], dates[pos["entry_i"]], pos["entry_price"],
                              dates[last], px, pos["shares"], proceeds - pos["cost"],
                              (px / pos["entry_price"] - 1) * 100, last - pos["entry_i"], "end"))
    return _metrics(name, panel, eq, trades, start_i, end_i, invested)


def _metrics(name, panel, eq, trades, start_i, end_i, invested):
    from .simulator import BTConfig, _metrics as m
    return m(name, panel, eq, trades, BTConfig(), start_i, end_i, invested)
