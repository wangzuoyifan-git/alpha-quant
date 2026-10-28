"""P1-3：3 折滚动 walk-forward 评估。

固定 train / valid / test 切分，不自动选参；每一步只报告各段指标，
并拼接所有 test 段得到 OOS 净值。结果写入 ``research/experiments.jsonl``。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .experiments import record_experiment


def _d(ts) -> str:
    return str(pd.Timestamp(ts).date())


def make_folds(start: str, end: str, n_folds: int = 3,
               train_months: int = 12, valid_months: int = 3,
               test_months: int = 4) -> list[dict]:
    end_ts = pd.Timestamp(end)
    folds = []
    for i in range(n_folds):
        back = test_months * (n_folds - 1 - i)
        test_end = end_ts - pd.DateOffset(months=back)
        test_start = test_end - pd.DateOffset(months=test_months)
        valid_end = test_start
        valid_start = valid_end - pd.DateOffset(months=valid_months)
        train_end = valid_start
        train_start = train_end - pd.DateOffset(months=train_months)
        folds.append({
            "fold": i + 1,
            "train": (_d(max(pd.Timestamp(start), train_start)), _d(train_end)),
            "valid": (_d(valid_start), _d(valid_end)),
            "test": (_d(test_start), _d(test_end)),
        })
    return folds


def _metric_row(res) -> dict:
    return {
        "total_return": round(float(res.total_return), 6),
        "annual_return": round(float(res.annual_return), 6),
        "max_drawdown": round(float(res.max_drawdown), 6),
        "sharpe": round(float(res.sharpe_ratio), 4),
        "trades": int(res.total_trades),
    }


def _metrics_from_returns(rets: pd.Series) -> dict:
    if rets.empty:
        return {}
    eq = (1 + rets).cumprod() * 1_000_000
    total = float(eq.iloc[-1] / 1_000_000 - 1)
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    annual = (1 + total) ** (365 / days) - 1 if total > -1 else -1.0
    mdd = float((eq / eq.cummax() - 1).min())
    ex = rets - 0.02 / 252
    sharpe = float(ex.mean() / (ex.std() + 1e-12) * np.sqrt(252))
    return {
        "total_return": round(total, 6),
        "annual_return": round(float(annual), 6),
        "max_drawdown": round(mdd, 6),
        "sharpe": round(sharpe, 4),
    }


def run_walkforward(strategy_key: str, start: str, end: str,
                    n_folds: int = 3, train_months: int = 12,
                    valid_months: int = 3, test_months: int = 4,
                    cost_mult: float = 1.0,
                    params: dict | None = None,
                    record: bool = True) -> dict:
    from backtest.fast import run_fast
    from config.settings import BACKTEST, EXECUTION

    config = dict(BACKTEST)
    config.update({
        "commission_rate": EXECUTION["commission_rate"] * cost_mult,
        "slippage": EXECUTION["slippage"] * cost_mult,
        "stamp_duty": EXECUTION["stamp_duty"] * cost_mult,
    })
    folds = make_folds(start, end, n_folds, train_months,
                       valid_months, test_months)
    rows = []
    oos_rets = []
    for f in folds:
        tr = run_fast(strategy_key, *f["train"], config=config, params=params)
        va = run_fast(strategy_key, *f["valid"], config=config, params=params)
        te = run_fast(strategy_key, *f["test"], config=config, params=params)
        rows.append({
            "fold": f["fold"],
            "train": {"period": f["train"], **_metric_row(tr)},
            "valid": {"period": f["valid"], **_metric_row(va)},
            "test": {"period": f["test"], **_metric_row(te)},
        })
        oos_rets.append(te.equity_curve.pct_change().dropna())
    combined = pd.concat(oos_rets).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    oos = _metrics_from_returns(combined)
    out = {"strategy": strategy_key, "folds": rows, "oos": oos,
           "params": params or {}}
    if record:
        record_experiment(
            kind="walkforward",
            name=strategy_key,
            params={"strategy_params": params or {},
                    "n_folds": n_folds, "train_months": train_months,
                    "valid_months": valid_months, "test_months": test_months,
                    "cost_mult": cost_mult},
            periods={"start": start, "end": end,
                     "folds": [[f["train"], f["valid"], f["test"]] for f in folds]},
            metrics={"folds": rows, "oos": oos},
            conclusion="固定参数 walk-forward，未做样本内选参",
        )
    return out


def format_walkforward(result: dict) -> str:
    lines = [
        f"Walk-forward: {result['strategy']}",
        f"{'fold':>4}  {'train':>8}  {'valid':>8}  {'test':>8}  "
        f"{'test_sharpe':>11}  {'test_mdd':>9}",
    ]
    for row in result["folds"]:
        lines.append(
            f"{row['fold']:>4}  {row['train']['total_return']*100:>7.1f}%  "
            f"{row['valid']['total_return']*100:>7.1f}%  "
            f"{row['test']['total_return']*100:>7.1f}%  "
            f"{row['test']['sharpe']:>11.2f}  "
            f"{row['test']['max_drawdown']*100:>8.1f}%"
        )
    oos = result["oos"]
    lines.append(
        f"OOS 拼接: 收益 {oos['total_return']*100:+.2f}%  "
        f"年化 {oos['annual_return']*100:+.2f}%  "
        f"回撤 {oos['max_drawdown']*100:.2f}%  Sharpe {oos['sharpe']:.2f}"
    )
    return "\n".join(lines)
