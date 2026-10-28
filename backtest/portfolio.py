"""P0-3/P1-2：多 sleeve 组合回测与组合管理层。

职责：
- 组合层只消费各 sleeve 的净值/收益序列，统一负责资金分配、再平衡、
  现金管理、组合级回撤熔断与归因；
- sleeve 内部由研究口径的向量化执行器负责成交/费用/止损
  （``research.simulator``），策略本身不再决定组合权重；
- 默认权重 40/40/10/10 可复现 ``research.combo``，并支持月度/季度再平衡。

限制：sleeve 级再平衡，不做个股级跨 sleeve 调仓（个股持仓仍由 sleeve 内部管理）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from research.combo import core_signal, etf_score, market_gate
from research.fundamental import build_fund_matrices, run_monthly, scores
from research.panel import Panel, eligibility, load_panel
from research.simulator import BTConfig, run_rotation, run_signals

DEFAULT_WEIGHTS = {"core": 0.40, "etf": 0.40, "b1": 0.10, "b2": 0.10}
SLEEVE_NAMES = ("core", "etf", "b1", "b2")


@dataclass
class SleeveSpec:
    name: str
    kind: str                 # signal / rotation / fundamental
    weight: float
    top_n: int = 20


@dataclass
class PortfolioConfig:
    initial_capital: float = 1_000_000.0
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    rebalance: str = "daily"       # daily / monthly / quarterly
    tolerance: float = 0.05        # 权重偏离超过此值才再平衡
    commission: float = 0.0003
    slippage: float = 0.001
    stamp_duty: float = 0.0005
    max_drawdown: float = 0.0      # 0=关闭组合级回撤熔断
    cooldown_days: int = 5         # 熔断后空仓天数
    rebalance_cost_bps: float = 5.0


@dataclass
class PortfolioResult:
    name: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float
    calmar: float
    weights: dict
    rebalance: str
    equity: pd.Series
    sleeve_metrics: dict
    contributions: dict
    drawdown_attribution: dict
    corr: dict

    def summary(self) -> str:
        lines = [
            "=" * 56,
            f"组合回测: {self.name}",
            f"区间: {self.start_date} ~ {self.end_date}",
            f"再平衡: {self.rebalance}   权重: {self.weights}",
            f"初始资金: ¥{self.initial_capital:,.0f}",
            f"最终资金: ¥{self.final_capital:,.0f}",
            f"总收益率: {self.total_return*100:.2f}%",
            f"年化收益: {self.annual_return*100:.2f}%",
            f"最大回撤: {self.max_drawdown*100:.2f}%",
            f"夏普比率: {self.sharpe:.3f}    Calmar: {self.calmar:.2f}",
            "-" * 56,
        ]
        for key in SLEEVE_NAMES:
            m = self.sleeve_metrics.get(key)
            if not m:
                continue
            lines.append(
                f"{key:4s} w={self.weights.get(key, 0):.0%}  "
                f"收益 {m['total_return']*100:+.2f}%  "
                f"回撤 {m['max_drawdown']*100:.2f}%  "
                f"Sharpe {m['sharpe']:.2f}  "
                f"贡献 {self.contributions.get(key, 0)*100:+.2f}pp"
            )
        att = self.drawdown_attribution
        if att:
            lines.append(
                f"回撤区间 {att.get('peak')} ~ {att.get('trough')}  "
                f"（{att.get('depth', 0)*100:.2f}%）"
            )
            for key, val in att.get("sleeves", {}).items():
                lines.append(f"  {key:4s} 区间贡献 {val*100:+.2f}pp")
        lines.append("=" * 56)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start": self.start_date,
            "end": self.end_date,
            "initial_capital": self.initial_capital,
            "final_capital": round(self.final_capital, 2),
            "total_return": round(self.total_return, 6),
            "annual_return": round(self.annual_return, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "sharpe": round(self.sharpe, 4),
            "calmar": round(self.calmar, 4),
            "weights": self.weights,
            "rebalance": self.rebalance,
            "sleeve_metrics": self.sleeve_metrics,
            "contributions": self.contributions,
            "drawdown_attribution": self.drawdown_attribution,
            "corr": self.corr,
        }


def _metrics(eq: pd.Series, name: str = "",
             initial: float | None = None) -> dict:
    eq = eq.dropna().ffill()
    if eq.empty:
        return {"name": name, "total_return": 0.0, "annual_return": 0.0,
                "max_drawdown": 0.0, "sharpe": 0.0, "calmar": 0.0}
    base = float(eq.iloc[0]) if initial is None else float(initial)
    total = float(eq.iloc[-1] / base - 1)
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    annual = (1 + total) ** (365 / days) - 1 if total > -1 else -1.0
    mdd = float((eq / eq.cummax() - 1).min())
    ret = eq.pct_change().dropna()
    ex = ret - 0.02 / 252
    sharpe = float(ex.mean() / (ex.std() + 1e-12) * np.sqrt(252))
    calmar = float(annual / abs(mdd)) if mdd < 0 else 0.0
    return {
        "name": name,
        "total_return": total,
        "annual_return": float(annual),
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "n_days": len(eq),
    }


def _bt_config(cfg: PortfolioConfig, **extra) -> BTConfig:
    return BTConfig(
        initial_capital=cfg.initial_capital,
        commission=cfg.commission,
        slippage=cfg.slippage,
        stamp_duty=cfg.stamp_duty,
        **extra,
    )


def _run_sleeves(start: str, end: str, cfg: PortfolioConfig,
                 p_stock: Panel | None = None,
                 p_etf: Panel | None = None,
                 fund: dict | None = None) -> dict[str, dict]:
    """运行四个 sleeve，返回 {name: {equity, metrics, spec}}。"""
    weights = cfg.weights
    out: dict[str, dict] = {}
    if weights.get("core", 0) > 0:
        p_stock = p_stock or load_panel()
        e_stock = eligibility(p_stock)
        bc = _bt_config(cfg, regime="trend", max_positions=10, position_size=0.10)
        res = run_signals(p_stock, core_signal(p_stock), e_stock, bc,
                          start=start, end=end)
        out["core"] = {"equity": res.equity, "metrics": _metrics(res.equity, "core", cfg.initial_capital)}
    else:
        p_stock = p_stock or load_panel()

    if weights.get("etf", 0) > 0:
        p_etf = p_etf or load_panel(asset_type="etf")
        e_etf = eligibility(p_etf, min_amount=5_000_000)
        bc = _bt_config(cfg, max_positions=5, position_size=0.2)
        res = run_rotation(p_etf, etf_score(p_etf), e_etf, top_n=5,
                           rebalance_days=20, cfg=bc, start=start, end=end)
        gate = market_gate(res.equity.index)
        er = res.equity.pct_change().fillna(0) * gate
        eeq = (1 + er).cumprod() * cfg.initial_capital
        out["etf"] = {"equity": eeq, "metrics": _metrics(eeq, "etf", cfg.initial_capital)}

    if weights.get("b1", 0) > 0 or weights.get("b2", 0) > 0:
        p_stock = p_stock or load_panel()
        e_stock = eligibility(p_stock)
        fund = fund or build_fund_matrices(p_stock)
        sc = scores(p_stock, fund)
        month_res = {
            "b1": (sc["B1_quality"], "B1_quality"),
            "b2": (sc["B2_value"], "B2_value"),
        }
        for key, (score, name) in month_res.items():
            if weights.get(key, 0) <= 0:
                continue
            res = run_monthly(
                p_stock, score, e_stock, top_n=20, name=name,
                start=start, end=end,
                commission=cfg.commission, slippage=cfg.slippage,
                stamp_duty=cfg.stamp_duty,
            )
            out[key] = {"equity": res.equity, "metrics": _metrics(res.equity, key, cfg.initial_capital)}
    return out


def _rebalance_label(date: pd.Timestamp, mode: str) -> str:
    if mode == "monthly":
        return str(date.to_period("M"))
    if mode == "quarterly":
        return str(date.to_period("Q"))
    return str(date)


def _simulate_portfolio(rets: pd.DataFrame, cfg: PortfolioConfig) -> pd.Series:
    """按 sleeve 收益序列模拟组合净值（支持 daily/monthly/quarterly 再平衡）。"""
    total_w = sum(cfg.weights.get(k, 0.0) for k in SLEEVE_NAMES)
    if total_w > 1.0 + 1e-9:
        raise ValueError(f"权重之和 {total_w:.4f} > 1")
    cash_weight = max(0.0, 1.0 - total_w)
    active = [k for k in SLEEVE_NAMES if cfg.weights.get(k, 0) > 0]
    rets = rets[active].fillna(0.0)

    if cfg.rebalance == "daily":
        blend = sum(rets[k] * cfg.weights[k] for k in active)
        return (1 + blend).cumprod() * cfg.initial_capital

    values = {k: cfg.initial_capital * cfg.weights[k] for k in active}
    cash = cfg.initial_capital * cash_weight
    equity = [cfg.initial_capital]
    prev_label = _rebalance_label(rets.index[0], cfg.rebalance)
    dates = list(rets.index)
    for i, date in enumerate(dates):
        # 先按当日收益更新 sleeve
        for k in active:
            values[k] *= (1 + float(rets.iloc[i][k]))
        label = _rebalance_label(date, cfg.rebalance)
        if i > 0 and label != prev_label:
            total = cash + sum(values.values())
            turnover = 0.0
            for k in active:
                target = total * cfg.weights[k]
                turnover += abs(target - values[k])
                values[k] = target
            # 现金补足/吸收；turnover 按双边名义额计成本
            cash = total - sum(values.values())
            cost = total * (turnover / max(total, 1e-9)) * cfg.rebalance_cost_bps / 1e4
            cash -= cost
        prev_label = label
        equity.append(cash + sum(values.values()))
    return pd.Series(equity[1:], index=rets.index)


def _apply_drawdown_guard(eq: pd.Series, max_drawdown: float,
                          cooldown_days: int) -> pd.Series:
    if max_drawdown <= 0 or eq.empty:
        return eq
    rets = eq.pct_change().fillna(0.0)
    values = [eq.iloc[0]]
    peak = eq.iloc[0]
    halted = False
    left = 0
    for r in rets.iloc[1:]:
        if halted:
            used = 0.0
            left -= 1
            if left <= 0:
                halted = False
        else:
            used = float(r)
        value = values[-1] * (1 + used)
        values.append(value)
        peak = max(peak, value)
        if not halted and (value / peak - 1) <= -max_drawdown:
            halted = True
            left = max(int(cooldown_days), 1)
    return pd.Series(values, index=eq.index)


def _drawdown_attribution(rets: pd.DataFrame, eq: pd.Series,
                         cfg: PortfolioConfig) -> dict:
    if eq.empty:
        return {}
    peak = eq.cummax()
    dd = eq / peak - 1
    trough = dd.idxmin()
    depth = float(dd.min())
    if depth >= 0:
        return {}
    peak_date = eq.loc[:trough].idxmax()
    sleeves = {}
    for k in SLEEVE_NAMES:
        if k not in rets.columns:
            continue
        window = rets.loc[peak_date:trough, k].fillna(0)
        sleeves[k] = float(cfg.weights.get(k, 0) * ((1 + window).prod() - 1))
    return {
        "peak": str(pd.Timestamp(peak_date).date()),
        "trough": str(pd.Timestamp(trough).date()),
        "depth": depth,
        "sleeves": sleeves,
    }


def run_portfolio_backtest(start: str, end: str,
                           config: PortfolioConfig | None = None,
                           name: str = "多 sleeve 组合") -> PortfolioResult:
    cfg = config or PortfolioConfig()
    sleeves = _run_sleeves(start, end, cfg)
    if not sleeves:
        raise ValueError("没有启用任何 sleeve")

    eq_df = pd.DataFrame({k: v["equity"] for k, v in sleeves.items()})
    eq_df = eq_df.sort_index().dropna()
    if eq_df.empty:
        raise ValueError("sleeve 净值无重叠区间")
    rets = eq_df.pct_change().fillna(0.0)

    portfolio_eq = _simulate_portfolio(rets, cfg)
    portfolio_eq = _apply_drawdown_guard(portfolio_eq, cfg.max_drawdown,
                                         cfg.cooldown_days)
    m = _metrics(portfolio_eq, name, cfg.initial_capital)

    sleeve_metrics = {k: v["metrics"] for k, v in sleeves.items()}
    contributions = {}
    for k in sleeves:
        if k not in rets.columns:
            continue
        contributions[k] = float(cfg.weights.get(k, 0) * ((1 + rets[k]).prod() - 1))
    corr = rets.corr().round(3).to_dict()

    return PortfolioResult(
        name=name,
        start_date=str(portfolio_eq.index[0].date()),
        end_date=str(portfolio_eq.index[-1].date()),
        initial_capital=cfg.initial_capital,
        final_capital=float(portfolio_eq.iloc[-1]),
        total_return=float(m["total_return"]),
        annual_return=float(m["annual_return"]),
        max_drawdown=float(m["max_drawdown"]),
        sharpe=float(m["sharpe"]),
        calmar=float(m["calmar"]),
        weights=dict(cfg.weights),
        rebalance=cfg.rebalance,
        equity=portfolio_eq,
        sleeve_metrics=sleeve_metrics,
        contributions=contributions,
        drawdown_attribution=_drawdown_attribution(rets, portfolio_eq, cfg),
        corr=corr,
    )
