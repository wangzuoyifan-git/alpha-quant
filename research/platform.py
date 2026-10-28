"""P1-3：参数平台检查（邻域稳定性，不选单点最优）。

对给定参数网格逐一回测，输出收益/Sharpe 的均值、标准差与离散度。
稳定性越好，说明结论越不依赖某一个最优点。
"""
from __future__ import annotations

from itertools import product

import pandas as pd

from .experiments import record_experiment

DEFAULT_GRIDS = {
    "quality_trend": {
        "vol_max": [0.045, 0.050, 0.055],
        "ext_max": [0.07, 0.08, 0.09],
        "mom_max": [0.25],
    },
}


def run_platform(strategy_key: str, start: str, end: str,
                 grid: dict | None = None, cost_mult: float = 1.0,
                 record: bool = True) -> dict:
    from backtest.fast import run_fast
    from config.settings import BACKTEST, EXECUTION

    grid = grid or DEFAULT_GRIDS.get(strategy_key)
    if not grid:
        raise ValueError(f"策略 {strategy_key} 没有默认参数网格，请显式传 grid")
    config = dict(BACKTEST)
    config.update({
        "commission_rate": EXECUTION["commission_rate"] * cost_mult,
        "slippage": EXECUTION["slippage"] * cost_mult,
        "stamp_duty": EXECUTION["stamp_duty"] * cost_mult,
    })
    keys = list(grid)
    rows = []
    for values in product(*(grid[k] for k in keys)):
        params = dict(zip(keys, values, strict=False))
        res = run_fast(strategy_key, start, end, config=config, params=params)
        rows.append({
            **params,
            "total_return": round(float(res.total_return), 6),
            "max_drawdown": round(float(res.max_drawdown), 6),
            "sharpe": round(float(res.sharpe_ratio), 4),
            "trades": int(res.total_trades),
        })
    df = pd.DataFrame(rows)
    sharpe = df["sharpe"].astype(float)
    stability = {
        "n": len(df),
        "sharpe_mean": round(float(sharpe.mean()), 4),
        "sharpe_std": round(float(sharpe.std(ddof=0)), 4),
        "sharpe_min": round(float(sharpe.min()), 4),
        "sharpe_max": round(float(sharpe.max()), 4),
        "return_mean": round(float(df["total_return"].mean()), 6),
        "return_std": round(float(df["total_return"].std(ddof=0)), 6),
        "positive_ratio": round(float((df["total_return"] > 0).mean()), 4),
        "dispersion": round(float(sharpe.std(ddof=0) /
                                  (abs(sharpe.mean()) + 1e-9)), 4),
    }
    out = {"strategy": strategy_key, "grid": grid, "rows": rows,
           "stability": stability}
    if record:
        record_experiment(
            kind="parameter_platform",
            name=strategy_key,
            params={"grid": grid, "cost_mult": cost_mult},
            periods={"start": start, "end": end},
            metrics={"rows": rows, "stability": stability},
            conclusion="邻域参数稳定性检查；不据此挑选全区间最优点",
        )
    return out


def format_platform(result: dict) -> str:
    s = result["stability"]
    lines = [
        f"参数平台: {result['strategy']}  n={s['n']}",
        f"Sharpe  mean={s['sharpe_mean']:.3f}  std={s['sharpe_std']:.3f}  "
        f"min={s['sharpe_min']:.3f}  max={s['sharpe_max']:.3f}",
        f"收益    mean={s['return_mean']*100:+.2f}%  std={s['return_std']*100:.2f}%  "
        f"正收益占比={s['positive_ratio']*100:.0f}%",
        f"离散度（std/|mean|）={s['dispersion']:.3f}",
        "参数组合明细:",
    ]
    for row in result["rows"]:
        extras = "  ".join(f"{k}={row[k]}" for k in result["grid"])
        lines.append(
            f"  {extras}  ret={row['total_return']*100:+.2f}%  "
            f"mdd={row['max_drawdown']*100:.2f}%  sharpe={row['sharpe']:.2f}"
        )
    return "\n".join(lines)
