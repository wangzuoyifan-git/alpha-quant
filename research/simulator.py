"""事件驱动组合回测器（研究版）。

相比 backtest/engine.py 的改进：
- 信号在 t 日收盘生成，t+1 日开盘成交（消除未来函数）
- 全市场矩阵化，一次回测 5595 只股票只需数秒
- 硬止损/移动止损/止盈按日内 high/low 触发，含跳空处理
- 支持 regime 过滤、组合仓位、A股 T+1/整手/印花税
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .signals import SignalSet, regime_series


@dataclass
class BTConfig:
    initial_capital: float = 1_000_000
    max_positions: int = 10
    position_size: float = 0.10
    commission: float = 0.0003
    slippage: float = 0.001
    stamp_duty: float = 0.0005
    stop_atr: float | None = 2.5        # None 表示用 SignalSet.exit_atr
    trail_atr: float | None = None      # None 表示用 SignalSet.trail_atr
    take_atr: float | None = None       # None 表示用 SignalSet.take_atr
    regime: str = "none"                # none / trend / anti_trend
    regime_index: str = "000300"
    entry_score_min: float = -1e9
    max_hold_days: int = 0              # 0=不限制
    rank_by: str = "score"              # score / momentum
    max_per_industry: int = 0           # 0=不限制
    industry: np.ndarray | None = None  # 长度=代码数，行业代码数组
    limit_pct: float | None = None      # None=按代码自动（主板10%/创业科创20%/北交30%）


@dataclass
class BTTrade:
    code: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp | None
    exit_price: float | None
    shares: int
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0
    reason_out: str = ""


@dataclass
class BTResult:
    name: str
    start: str
    end: str
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    calmar: float
    win_rate: float
    profit_factor: float
    n_trades: int
    avg_hold_days: float
    exposure: float
    benchmark_return: float
    alpha: float
    beta: float
    equity: pd.Series = field(default_factory=pd.Series)
    trades: list[BTTrade] = field(default_factory=list)

    def row(self) -> dict:
        return {
            "name": self.name,
            "total_return": round(self.total_return * 100, 2),
            "annual_return": round(self.annual_return * 100, 2),
            "max_drawdown": round(self.max_drawdown * 100, 2),
            "sharpe": round(self.sharpe, 3),
            "sortino": round(self.sortino, 3),
            "calmar": round(self.calmar, 3),
            "win_rate": round(self.win_rate * 100, 1),
            "profit_factor": round(self.profit_factor, 2),
            "n_trades": self.n_trades,
            "avg_hold": round(self.avg_hold_days, 1),
            "exposure": round(self.exposure * 100, 1),
            "benchmark": round(self.benchmark_return * 100, 2),
            "alpha": round(self.alpha * 100, 2),
            "beta": round(self.beta, 2),
        }


def _benchmark(panel, code: str = "000300") -> pd.Series:
    from data.store import load
    df = load(code, asset_type="index", market="cn",
              start=str(panel.dates[0].date()), end=str(panel.dates[-1].date()))
    if df is None or df.empty:
        return pd.Series(dtype=float)
    return df["close"].reindex(panel.dates).ffill()


def _regime_mask(panel, mode: str, index: str) -> np.ndarray:
    if mode == "none":
        return np.ones(len(panel.dates), dtype=bool)
    reg = regime_series(panel, index)
    if mode == "trend":
        return (reg == 1).to_numpy()
    if mode == "anti_trend":
        return (reg <= 0).to_numpy()
    return np.ones(len(panel.dates), dtype=bool)


def run_signals(panel, sig: SignalSet, eligible: pd.DataFrame,
                cfg: BTConfig | None = None,
                start: str | None = None,
                end: str | None = None) -> BTResult:
    cfg = cfg or BTConfig()
    dates = panel.dates
    start_i = int(np.searchsorted(dates, pd.Timestamp(start))) if start else 0
    end_i = int(np.searchsorted(dates, pd.Timestamp(end), side="right") - 1) if end else len(dates) - 1

    op, hi, lo, cl = (panel["open"].to_numpy(), panel["high"].to_numpy(),
                      panel["low"].to_numpy(), panel["close"].to_numpy())
    vol = panel["volume"].to_numpy()
    tradable = np.isfinite(op) & np.isfinite(vol) & (vol > 0)
    buy = sig.buy.to_numpy()
    sell = sig.sell.to_numpy()
    score = sig.score.to_numpy()
    elig = eligible.to_numpy()
    atr = np.maximum(
        (hi - lo), np.maximum(np.abs(hi - np.roll(cl, 1)),
                              np.abs(lo - np.roll(cl, 1)))
    )
    atr = pd.DataFrame(atr, index=panel.dates, columns=panel.codes).rolling(
        14, min_periods=14).mean().to_numpy()

    reg_ok = _regime_mask(panel, cfg.regime, cfg.regime_index)

    # 涨跌停撮合约束：次日开盘接近涨停则买不进；一字跌停则卖不出
    prev_close = np.roll(cl, 1)
    prev_close[0] = cl[0]
    if cfg.limit_pct is not None:
        lim = np.full(len(panel.codes), cfg.limit_pct, dtype="float32")
    else:
        lim = np.array([
            0.30 if c[0] in ("4", "8") else
            0.20 if c[:3] in ("300", "301", "688", "689") else 0.10
            for c in panel.codes
        ], dtype="float32")
    limit_up = prev_close * (1 + lim[None, :] * 0.98)
    limit_dn = prev_close * (1 - lim[None, :] * 0.98)
    one_word_up = np.isfinite(hi) & (hi == lo)
    one_word_dn = one_word_up & (cl < prev_close)
    can_buy = tradable & (op < limit_up) & ~one_word_up
    can_sell = tradable & ~(one_word_dn & (op <= limit_dn))

    stop_mult = cfg.stop_atr if cfg.stop_atr is not None else sig.exit_atr
    trail_mult = cfg.trail_atr if cfg.trail_atr is not None else sig.trail_atr
    take_mult = cfg.take_atr if cfg.take_atr is not None else sig.take_atr

    cash = float(cfg.initial_capital)
    positions: dict[int, dict] = {}
    trades: list[BTTrade] = []
    equity_curve = np.full(len(dates), np.nan)
    invested_days = 0

    for i in range(max(start_i, 1), end_i + 1):
        # ---------- 1. 持仓退出（当日开盘/日内） ----------
        for ci in list(positions.keys()):
            pos = positions[ci]
            if i <= pos["entry_i"]:
                continue
            o, h, lo_i, c = op[i, ci], hi[i, ci], lo[i, ci], cl[i, ci]
            if not np.isfinite(o):
                continue
            if not can_sell[i, ci]:
                continue
            exit_price = None
            reason = ""
            # 信号卖出（前一交易日收盘确认）
            if sell[i - 1, ci]:
                exit_price, reason = o, "signal"
            else:
                stop = pos["stop"]
                # 移动止损：用入场后最高价更新
                if trail_mult > 0:
                    pos["peak"] = max(pos["peak"], h)
                    trail = pos["peak"] - trail_mult * pos["atr"]
                    if pos["peak"] >= pos["entry_price"] + pos["atr"]:
                        stop = max(stop, trail)
                        pos["stop"] = stop
                # 跳空低开
                if exit_price is None and np.isfinite(o) and o <= stop:
                    exit_price, reason = o, "gap_stop"
                elif exit_price is None and np.isfinite(lo_i) and lo_i <= stop:
                    exit_price, reason = stop, "stop"
                # 止盈
                if exit_price is None and take_mult > 0:
                    tp = pos["entry_price"] + take_mult * pos["atr"]
                    if np.isfinite(o) and o >= tp:
                        exit_price, reason = o, "gap_tp"
                    elif np.isfinite(h) and h >= tp:
                        exit_price, reason = tp, "take_profit"
                # 时间止损
                if (exit_price is None and cfg.max_hold_days > 0
                        and i - pos["entry_i"] >= cfg.max_hold_days):
                    exit_price, reason = o, "time"

            if exit_price is not None:
                px = float(exit_price) * (1 - cfg.slippage)
                proceeds = pos["shares"] * px * (1 - cfg.commission - cfg.stamp_duty)
                cash += proceeds
                pnl = proceeds - pos["cost"]
                trades.append(BTTrade(
                    code=panel.codes[ci], entry_date=dates[pos["entry_i"]],
                    entry_price=pos["entry_price"], exit_date=dates[i],
                    exit_price=px, shares=pos["shares"], pnl=pnl,
                    pnl_pct=(px / pos["entry_price"] - 1) * 100,
                    hold_days=i - pos["entry_i"], reason_out=reason,
                ))
                del positions[ci]

        # ---------- 2. 新开仓（前一日信号，当日开盘成交） ----------
        slots = cfg.max_positions - len(positions)
        if slots > 0 and reg_ok[i - 1]:
            cands = np.where(buy[i - 1] & elig[i] & can_buy[i])[0]
            cands = [ci for ci in cands if ci not in positions]
            if cands:
                inds = cfg.industry
                ind_count: dict = {}
                if inds is not None and cfg.max_per_industry > 0:
                    for ci0 in positions:
                        ind_count[inds[ci0]] = ind_count.get(inds[ci0], 0) + 1
                if cfg.rank_by == "momentum" and i >= 21:
                    mom = cl[i - 1, cands] / cl[i - 21, cands] - 1
                    order = np.argsort(-np.nan_to_num(mom))
                else:
                    order = np.argsort(-np.nan_to_num(score[i - 1, cands]))
                for k in order:
                    if slots <= 0:
                        break
                    ci = cands[k]
                    if score[i - 1, ci] < cfg.entry_score_min:
                        continue
                    if inds is not None and cfg.max_per_industry > 0:
                        ind = inds[ci]
                        if ind_count.get(ind, 0) >= cfg.max_per_industry:
                            continue
                    px = float(op[i, ci]) * (1 + cfg.slippage)
                    a = atr[i - 1, ci]
                    if not np.isfinite(a) or a <= 0:
                        a = px * 0.02
                    alloc = min(cash, cfg.initial_capital * cfg.position_size)
                    if alloc < px * 100:
                        continue
                    shares = int(alloc / px / 100) * 100
                    if shares <= 0:
                        continue
                    cost = shares * px * (1 + cfg.commission)
                    while cost > cash and shares >= 100:
                        shares -= 100
                        cost = shares * px * (1 + cfg.commission)
                    if shares <= 0:
                        continue
                    cash -= cost
                    stop = px - stop_mult * a
                    positions[ci] = {
                        "shares": shares, "entry_price": px, "entry_i": i,
                        "cost": cost, "atr": a, "stop": stop, "peak": hi[i, ci],
                    }
                    if inds is not None and cfg.max_per_industry > 0:
                        ind_count[inds[ci]] = ind_count.get(inds[ci], 0) + 1
                    slots -= 1

        # ---------- 3. 当日净值 ----------
        mv = 0.0
        for ci, pos in positions.items():
            c = cl[i, ci]
            mv += pos["shares"] * (c if np.isfinite(c) else pos["entry_price"])
        equity_curve[i] = cash + mv
        if mv > 0:
            invested_days += 1

    # 区间内有效净值
    valid = np.arange(len(dates)) >= max(start_i, 1)
    valid &= np.arange(len(dates)) <= end_i
    eq = pd.Series(equity_curve[valid], index=dates[valid]).dropna()
    if eq.empty:
        raise ValueError("回测区间无净值")
    eq = eq.ffill().fillna(cfg.initial_capital)

    # 期末平仓，计入交易统计
    last_i = end_i
    for ci, pos in list(positions.items()):
        c = cl[last_i, ci]
        if not np.isfinite(c):
            c = pos["entry_price"]
        px = float(c) * (1 - cfg.slippage)
        proceeds = pos["shares"] * px * (1 - cfg.commission - cfg.stamp_duty)
        trades.append(BTTrade(
            code=panel.codes[ci], entry_date=dates[pos["entry_i"]],
            entry_price=pos["entry_price"], exit_date=dates[last_i],
            exit_price=px, shares=pos["shares"], pnl=proceeds - pos["cost"],
            pnl_pct=(px / pos["entry_price"] - 1) * 100,
            hold_days=last_i - pos["entry_i"], reason_out="end",
        ))

    return _metrics(sig.name, panel, eq, trades, cfg, start_i, end_i,
                    invested_days)


def _metrics(name, panel, eq, trades, cfg, start_i, end_i, invested_days) -> BTResult:
    initial = cfg.initial_capital
    final = float(eq.iloc[-1])
    total = final / initial - 1
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    annual = (1 + total) ** (365 / days) - 1
    rolling_max = eq.cummax()
    dd = (eq - rolling_max) / rolling_max
    mdd = float(dd.min())
    ret = eq.pct_change().dropna()
    rf = 0.02 / 252
    excess = ret - rf
    sharpe = float(excess.mean() / (excess.std() + 1e-12) * np.sqrt(252))
    downside = ret[ret < 0]
    sortino = float((ret.mean() - rf) / (downside.std() + 1e-12) * np.sqrt(252)) if len(downside) else 0.0
    calmar = float(annual / abs(mdd)) if mdd < 0 else 0.0

    closed = [t for t in trades if t.exit_price is not None]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    win_rate = len(wins) / max(len(closed), 1)
    avg_win = np.mean([t.pnl for t in wins]) if wins else 0.0
    avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 1.0
    pf = avg_win / max(avg_loss, 1e-9)
    avg_hold = float(np.mean([t.hold_days for t in closed])) if closed else 0.0

    bm = _benchmark(panel)
    bm = bm.reindex(eq.index).ffill().dropna()
    bm_ret = 0.0
    alpha = 0.0
    beta = 0.0
    if len(bm) > 5:
        bm_ret = float(bm.iloc[-1] / bm.iloc[0] - 1)
        bm_days = max((bm.index[-1] - bm.index[0]).days, 1)
        bm_ann = (1 + bm_ret) ** (365 / bm_days) - 1
        alpha = annual - bm_ann
        a, b = ret.align(bm.pct_change().dropna(), join="inner")
        if len(a) > 5 and b.var() > 1e-12:
            beta = float(np.cov(a, b)[0, 1] / b.var())

    n = max(end_i - max(start_i, 1) + 1, 1)
    return BTResult(
        name=name, start=str(eq.index[0].date()), end=str(eq.index[-1].date()),
        total_return=total, annual_return=annual, max_drawdown=mdd,
        sharpe=sharpe, sortino=sortino, calmar=calmar, win_rate=win_rate,
        profit_factor=float(pf), n_trades=len(closed), avg_hold_days=avg_hold,
        exposure=invested_days / n, benchmark_return=bm_ret, alpha=alpha,
        beta=beta, equity=eq, trades=trades,
    )


def run_rotation(panel, score: pd.DataFrame, eligible: pd.DataFrame,
                 top_n: int = 5, rebalance_days: int = 5,
                 cfg: BTConfig | None = None,
                 start: str | None = None, end: str | None = None) -> BTResult:
    """ETF 等按评分排序的轮动组合：每 rebalance_days 调仓，持有 Top N。"""
    cfg = cfg or BTConfig(max_positions=top_n, position_size=1.0 / top_n)
    dates = panel.dates
    start_i = int(np.searchsorted(dates, pd.Timestamp(start))) if start else 0
    end_i = int(np.searchsorted(dates, pd.Timestamp(end), side="right") - 1) if end else len(dates) - 1
    op, cl, hi, lo = (panel["open"].to_numpy(), panel["close"].to_numpy(),
                      panel["high"].to_numpy(), panel["low"].to_numpy())
    vol = panel["volume"].to_numpy()
    tradable = np.isfinite(op) & np.isfinite(vol) & (vol > 0)
    sc = score.to_numpy()
    elig = eligible.to_numpy()
    prev_close = np.roll(cl, 1)
    prev_close[0] = cl[0]
    if cfg.limit_pct is not None:
        lim = np.full(len(panel.codes), cfg.limit_pct, dtype="float32")
    else:
        lim = np.array([
            0.30 if c[0] in ("4", "8") else
            0.20 if c[:3] in ("300", "301", "688", "689") else 0.10
            for c in panel.codes
        ], dtype="float32")
    limit_up = prev_close * (1 + lim[None, :] * 0.98)
    limit_dn = prev_close * (1 - lim[None, :] * 0.98)
    one_word_up = np.isfinite(hi) & (hi == lo)
    one_word_dn = one_word_up & (cl < prev_close)
    can_buy = tradable & (op < limit_up) & ~one_word_up
    can_sell = tradable & ~(one_word_dn & (op <= limit_dn))
    cash = float(cfg.initial_capital)
    positions: dict[int, dict] = {}
    trades: list[BTTrade] = []
    eq_arr = np.full(len(dates), np.nan)
    invested = 0

    for i in range(max(start_i, 1), end_i + 1):
        is_reb = ((i - start_i) % rebalance_days == 0) or i == max(start_i, 1)
        if is_reb:
            sc_row = np.nan_to_num(sc[i - 1], nan=-1e12)
            ranked = np.argsort(-sc_row)
            top = [ci for ci in ranked if elig[i, ci] and can_buy[i, ci]
                   and sc_row[ci] > -1e11][:top_n]
            # 卖出不在 Top 的持仓
            for ci in list(positions.keys()):
                if ci not in top:
                    if not can_sell[i, ci]:
                        continue
                    pos = positions.pop(ci)
                    px_raw = op[i, ci]
                    if not np.isfinite(px_raw):
                        px_raw = cl[i, ci]
                    if not np.isfinite(px_raw):
                        px_raw = pos["entry_price"]
                    px = float(px_raw) * (1 - cfg.slippage)
                    proceeds = pos["shares"] * px * (1 - cfg.commission - cfg.stamp_duty)
                    cash += proceeds
                    trades.append(BTTrade(
                        code=panel.codes[ci], entry_date=dates[pos["entry_i"]],
                        entry_price=pos["entry_price"], exit_date=dates[i],
                        exit_price=px, shares=pos["shares"], pnl=proceeds - pos["cost"],
                        pnl_pct=(px / pos["entry_price"] - 1) * 100,
                        hold_days=i - pos["entry_i"], reason_out="rotate"))
            # 买入缺位
            for ci in top:
                if ci in positions:
                    continue
                px = float(op[i, ci]) * (1 + cfg.slippage)
                target = cfg.initial_capital / top_n
                shares = int(min(cash, target) / px / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * px * (1 + cfg.commission)
                if cost > cash:
                    continue
                cash -= cost
                positions[ci] = {"shares": shares, "entry_price": px,
                                 "entry_i": i, "cost": cost}
        mv = sum(p["shares"] * cl[i, ci] for ci, p in positions.items()
                 if np.isfinite(cl[i, ci]))
        eq_arr[i] = cash + mv
        if mv > 0:
            invested += 1

    valid = (np.arange(len(dates)) >= max(start_i, 1)) & (np.arange(len(dates)) <= end_i)
    eq = pd.Series(eq_arr[valid], index=dates[valid]).dropna().ffill()
    last_i = end_i
    for ci, pos in list(positions.items()):
        c = cl[last_i, ci]
        if not np.isfinite(c):
            c = pos["entry_price"]
        px = float(c) * (1 - cfg.slippage)
        proceeds = pos["shares"] * px * (1 - cfg.commission - cfg.stamp_duty)
        trades.append(BTTrade(
            code=panel.codes[ci], entry_date=dates[pos["entry_i"]],
            entry_price=pos["entry_price"], exit_date=dates[last_i],
            exit_price=px, shares=pos["shares"], pnl=proceeds - pos["cost"],
            pnl_pct=(px / pos["entry_price"] - 1) * 100,
            hold_days=last_i - pos["entry_i"], reason_out="end"))
    return _metrics("rotation", panel, eq, trades, cfg, start_i, end_i, invested)
