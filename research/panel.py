"""研究用行情面板：把全市场日线一次性读成 日期 x 代码 的 numpy 矩阵。

只用于离线回测研究，不参与生产扫描。内存约 200MB，首次构建后缓存到
data/cache/research_panel_*.npz，后续秒级加载。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from data.store import BASE_DIR, load_ipo_dates

CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

FIELDS = ("open", "high", "low", "close", "volume", "amount",
          "is_st", "pe_ttm", "pb_mrq", "pct_chg")


@dataclass
class Panel:
    dates: pd.DatetimeIndex
    codes: list[str]
    data: dict[str, pd.DataFrame]   # field -> DataFrame(dates x codes)

    def __getitem__(self, field: str) -> pd.DataFrame:
        return self.data[field]

    def slice(self, start: str | None = None, end: str | None = None) -> Panel:
        mask = np.ones(len(self.dates), dtype=bool)
        if start:
            mask &= self.dates >= pd.Timestamp(start)
        if end:
            mask &= self.dates <= pd.Timestamp(end)
        return Panel(
            dates=self.dates[mask],
            codes=self.codes,
            data={k: v.loc[mask] for k, v in self.data.items()},
        )


def _cache_path(asset_type: str, start: str, end: str) -> Path:
    return CACHE_DIR / f"research_panel_{asset_type}_{start}_{end}.npz"


def load_panel(start: str = "2024-01-02", end: str = "2026-10-26",
               asset_type: str = "stock", use_cache: bool = True) -> Panel:
    cache = _cache_path(asset_type, start, end)
    if use_cache and cache.exists():
        z = np.load(cache, allow_pickle=True)
        dates = pd.DatetimeIndex(z["dates"])
        codes = list(z["codes"])
        data = {f: pd.DataFrame(z[f], index=dates, columns=codes) for f in FIELDS}
        return Panel(dates=dates, codes=codes, data=data)

    src = BASE_DIR / "cn" / asset_type
    files = sorted(src.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"本地无 {asset_type} 行情: {src}")

    frames: dict[str, pd.DataFrame] = {f: {} for f in FIELDS}
    for i, p in enumerate(files):
        code = p.stem
        if asset_type == "stock" and code[0] in ("4", "8"):
            continue  # 北交所
        try:
            df = pd.read_parquet(p)
        except Exception:
            continue
        df = df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
        if df.empty:
            continue
        for f in FIELDS:
            if f in df.columns:
                frames[f][code] = df[f]
        if (i + 1) % 1000 == 0:
            print(f"  读取 {asset_type} {i+1}/{len(files)}", flush=True)

    if not frames["close"]:
        raise ValueError("面板为空")
    codes0 = list(pd.DataFrame(frames["close"]).columns)
    dates0 = pd.DataFrame(frames["close"]).sort_index().index
    data = {}
    for f in FIELDS:
        if frames[f]:
            data[f] = pd.DataFrame(frames[f]).sort_index().astype("float32")
        else:
            data[f] = pd.DataFrame(index=dates0, columns=codes0, dtype="float32")
    # 对齐到共同日期
    dates = data["close"].index
    for f in FIELDS:
        data[f] = data[f].reindex(index=dates)
    codes = list(data["close"].columns)
    # 停牌/缺失日：close 向前填充用于计算指标，但成交量置 0
    data["close"] = data["close"].ffill()
    data["high"] = data["high"].ffill()
    data["low"] = data["low"].ffill()
    data["amount"] = data["amount"].fillna(0)
    data["volume"] = data["volume"].fillna(0)
    data["is_st"] = data["is_st"].fillna(0 if asset_type == "etf" else 1)

    panel = Panel(dates=pd.DatetimeIndex(dates), codes=codes, data=data)
    if use_cache:
        np.savez_compressed(
            cache,
            dates=np.array(dates, dtype="datetime64[ns]"),
            codes=np.array(codes, dtype=object),
            **{f: data[f].to_numpy(dtype="float32") for f in FIELDS},
        )
    return panel


def listing_age(panel: Panel) -> pd.DataFrame:
    """每个交易日各股票已上市天数（自然日），缺失按数据首日估算。"""
    ipo = load_ipo_dates()
    first_valid = panel["close"].apply(
        lambda s: s.first_valid_index() if s.first_valid_index() is not None else pd.NaT
    )
    ages = pd.DataFrame(index=panel.dates, columns=panel.codes, dtype="float32")
    for c in panel.codes:
        entry = ipo.get(f"cn:{c}", {})
        d0 = entry.get("ipo_date")
        start = pd.Timestamp(d0) if d0 else first_valid[c]
        if pd.isna(start):
            ages[c] = 0
        else:
            ages[c] = (panel.dates - start).days
    return ages


def eligibility(panel: Panel, min_amount: float = 30_000_000,
                min_age_days: int = 60, exclude_st: bool = True) -> pd.DataFrame:
    """信号/买入日的可交易过滤。只使用当日及之前信息。"""
    close = panel["close"]
    amount = panel["amount"]
    valid = close.notna() & panel["open"].notna()
    age = listing_age(panel)
    ok = valid & (amount >= min_amount) & (age >= min_age_days)
    if exclude_st:
        ok &= panel["is_st"].fillna(1) == 0
    return ok


def save_meta(extra: dict) -> None:
    p = CACHE_DIR / "research_meta.json"
    existing = {}
    if p.exists():
        existing = json.loads(p.read_text(encoding="utf-8"))
    existing.update(extra)
    p.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
