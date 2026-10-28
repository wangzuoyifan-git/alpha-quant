"""资金流面板（主力净流入），用于验证 moneyflow 过滤层。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).parent.parent / "data" / "moneyflow" / "stock"
CACHE = Path(__file__).parent.parent / "data" / "cache" / "research_mf.npz"


def load_net_mf(dates: pd.DatetimeIndex, codes: list[str],
                use_cache: bool = True) -> pd.DataFrame:
    if use_cache and CACHE.exists():
        z = np.load(CACHE, allow_pickle=True)
        df = pd.DataFrame(z["x"], index=pd.DatetimeIndex(z["dates"]),
                          columns=list(z["codes"]))
        return df.reindex(index=dates, columns=codes)
    cols = {}
    files = list(BASE.glob("*.parquet"))
    for i, p in enumerate(files):
        try:
            df = pd.read_parquet(p, columns=["net_mf_amount"])
        except Exception:
            continue
        cols[p.stem] = df["net_mf_amount"]
        if (i + 1) % 1000 == 0:
            print(f"  moneyflow {i+1}/{len(files)}", flush=True)
    x = pd.DataFrame(cols).sort_index().astype("float32")
    if use_cache:
        np.savez_compressed(CACHE, x=x.to_numpy(dtype="float32"),
                            dates=np.array(x.index, dtype="datetime64[ns]"),
                            codes=np.array(x.columns, dtype=object))
    return x.reindex(index=dates, columns=codes)
