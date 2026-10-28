"""最终组合：40% 低波动质量趋势 + 40% ETF动量 + 10% B1质量 + 10% B2价值。

四个 sleeve 日收益相关性 0.2~0.5，组合显著降低回撤。
用法：
    python -m research.combo                 # 输出全区间/训练/测试
    python -m research.combo --cost 2        # 双倍交易成本压力测试
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .fundamental import build_fund_matrices, run_monthly, scores
from .panel import eligibility, load_panel
from .signals import SignalSet, TechContext
from .simulator import BTConfig, run_rotation, run_signals

WEIGHTS = {"core": 0.40, "etf": 0.40, "b1": 0.10, "b2": 0.10}
PERIODS = {
    "train": ("2024-07-01", "2025-12-31"),
    "test": ("2026-01-01", "2026-10-26"),
    "full": ("2024-07-01", "2026-10-26"),
}
OUT = Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)


def core_signal(panel, params: dict | None = None):
    p = params or {}
    vol_max = float(p.get("vol_max", 0.05))
    ext_max = float(p.get("ext_max", 0.08))
    mom_max = float(p.get("mom_max", 0.25))
    min_amount = float(p.get("min_amount", 1e8))
    ctx = TechContext(panel)
    close = panel["close"]
    ma20, ma60 = ctx.ma(20), ctx.ma(60)
    atr = ctx.atr(14)
    vol = atr / close
    mom20 = close / close.shift(20) - 1
    ext = close / ma20 - 1
    trend = (close > ma60) & (ma20 > ma60)
    liq = panel["amount"].rolling(5).mean() > min_amount
    cond = (trend & (vol < vol_max) & (ext >= 0) & (ext < ext_max)
            & (mom20 > 0) & (mom20 < mom_max) & liq)
    qual = ((1 - vol / vol_max).clip(0, 1) * 40
            + (1 - (ext / ext_max).clip(0, 1)) * 20
            + (1 - (mom20 / mom_max).clip(0, 1)) * 20 + 20)
    # 回踩 MA20 后重新站上（不是追涨突破）
    buy = (cond & (panel["low"] <= ma20) & (close > ma20)
           & (close.shift(1) <= ma20.shift(1)))
    return SignalSet(
        "低波动质量趋势", buy, close < ma20, qual,
        exit_atr=3.0, trail_atr=3.0,
    )


def etf_score(panel):
    ctx = TechContext(panel)
    close = panel["close"]
    trend = (close > ctx.ma(60)) & (ctx.ma(20) > ctx.ma(60))
    return (close / close.shift(30) - 1).where(trend)


def market_gate(index: pd.DatetimeIndex, ma: int = 60) -> pd.Series:
    idx = pd.read_parquet(OUT.parent.parent / "data/market/cn/index/000300.parquet")["close"]
    idx = idx.reindex(index).ffill()
    return (idx > idx.rolling(ma).mean()).shift(1).fillna(False).astype(float)


def _metrics(eq: pd.Series, name: str = "") -> dict:
    eq = eq.dropna().ffill()
    total = eq.iloc[-1] / 1e6 - 1
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    annual = (1 + total) ** (365 / days) - 1
    mdd = float((eq / eq.cummax() - 1).min())
    ret = eq.pct_change().dropna()
    ex = ret - 0.02 / 252
    return {
        "name": name,
        "total_return": round(total * 100, 2),
        "annual_return": round(annual * 100, 2),
        "max_drawdown": round(mdd * 100, 2),
        "sharpe": round(float(ex.mean() / (ex.std() + 1e-12) * np.sqrt(252)), 2),
        "calmar": round(float(annual / abs(mdd)) if mdd < 0 else 0, 2),
        "n_days": len(eq),
    }


def run_period(start: str, end: str, cost_mult: float = 1.0,
               p_stock=None, p_etf=None, fund=None, weights=None) -> dict:
    weights = weights or WEIGHTS
    p_stock = p_stock or load_panel()
    p_etf = p_etf or load_panel(asset_type="etf")
    e_stock = eligibility(p_stock)
    e_etf = eligibility(p_etf, min_amount=5_000_000)
    fund = fund or build_fund_matrices(p_stock)

    cfg_stock = BTConfig(
        regime="trend", max_positions=10, position_size=0.1,
        commission=0.0003 * cost_mult, slippage=0.001 * cost_mult,
        stamp_duty=0.0005 * cost_mult,
    )
    core = run_signals(p_stock, core_signal(p_stock), e_stock, cfg_stock,
                       start=start, end=end)

    cfg_etf = BTConfig(
        max_positions=5, position_size=0.2,
        commission=0.0003 * cost_mult, slippage=0.001 * cost_mult,
        stamp_duty=0.0005 * cost_mult,
    )
    etf = run_rotation(p_etf, etf_score(p_etf), e_etf, top_n=5,
                       rebalance_days=20, cfg=cfg_etf, start=start, end=end)
    gate = market_gate(etf.equity.index)
    er = etf.equity.pct_change().fillna(0) * gate
    eeq = (1 + er).cumprod() * 1e6

    fund_scores = scores(p_stock, fund)
    b1 = run_monthly(p_stock, fund_scores["B1_quality"], e_stock,
                     top_n=20, name="B1", start=start, end=end,
                     commission=0.0003 * cost_mult, slippage=0.001 * cost_mult,
                     stamp_duty=0.0005 * cost_mult)
    b2 = run_monthly(p_stock, fund_scores["B2_value"], e_stock,
                     top_n=20, name="B2", start=start, end=end,
                     commission=0.0003 * cost_mult, slippage=0.001 * cost_mult,
                     stamp_duty=0.0005 * cost_mult)

    rets = pd.DataFrame({
        "core": core.equity.pct_change(),
        "etf": er,
        "b1": b1.equity.pct_change(),
        "b2": b2.equity.pct_change(),
    }).dropna()
    blend_ret = sum(rets[k] * w for k, w in weights.items())
    blend_eq = (1 + blend_ret).cumprod() * 1e6

    return {
        "sleeves": {
            "core": _metrics(core.equity, "低波动质量趋势"),
            "etf": _metrics(eeq, "ETF动量(MA60闸门)"),
            "b1": _metrics(b1.equity, "B1质量价值"),
            "b2": _metrics(b2.equity, "B2深度价值"),
        },
        "blend": _metrics(blend_eq, "最终组合"),
        "corr": rets.corr().round(3).to_dict(),
        "equity": blend_eq,
        "sleeve_equity": {"core": core.equity, "etf": eeq, "b1": b1.equity,
                          "b2": b2.equity},
        "weights": weights,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=1.0, help="交易成本倍数")
    ap.add_argument("--period", choices=[*PERIODS, "all"], default="all")
    args = ap.parse_args()

    p_stock = load_panel()
    p_etf = load_panel(asset_type="etf")
    fund = build_fund_matrices(p_stock)
    periods = PERIODS if args.period == "all" else {args.period: PERIODS[args.period]}
    results = {}
    for name, (st, en) in periods.items():
        r = run_period(st, en, args.cost, p_stock, p_etf, fund)
        results[name] = {k: v for k, v in r.items()
                         if k not in ("equity", "sleeve_equity")}
        print(f"\n===== {name} ({st} ~ {en})  cost x{args.cost} =====")
        for k, v in r["sleeves"].items():
            print(f"  {k:5s}", v)
        print("  BLEND", r["blend"])
        if name == "full":
            r["equity"].to_csv(OUT / f"final_equity_cost{args.cost}.csv", header=True)
    (OUT / f"final_metrics_cost{args.cost}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
