"""P2-1：本地数据质量审计（离线、抽样）。

检查：
- K 线必需列/重复日期/OHLC 关系/非正价格；
- 异常价格跳变（按板块涨跌停放宽）；
- 长期零成交（停牌/退市残留）；
- 财务 pubDate 缺失比例；
- 资金流最新日期与行情最新日期的对齐情况。

用法：
    venv/bin/python3 -m tools.data_quality_audit --limit 200
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent


def _limit_pct(code: str) -> float:
    c = str(code).split(".")[0]
    if c[:3] in ("300", "301", "688", "689"):
        return 0.20
    if c[:1] in ("4", "8") or c[:2] == "92":
        return 0.30
    return 0.10


def audit_kline(path: Path, code: str) -> dict:
    issues = []
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        return {"code": code, "issues": [f"read_error:{exc}"]}
    if df.empty:
        return {"code": code, "issues": ["empty"]}
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        issues.append(f"missing_columns:{sorted(missing)}")
    if not isinstance(df.index, pd.DatetimeIndex):
        issues.append("index_not_datetime")
    if df.index.duplicated().any():
        issues.append(f"duplicate_dates:{int(df.index.duplicated().sum())}")
    if missing:
        return {"code": code, "issues": issues}

    o, h, lo, c, v = (pd.to_numeric(df[k], errors="coerce")
                     for k in ("open", "high", "low", "close", "volume"))
    if (c <= 0).any():
        issues.append(f"nonpositive_close:{int((c <= 0).sum())}")
    bad_hl = (h < lo).sum()
    if bad_hl:
        issues.append(f"high_below_low:{int(bad_hl)}")
    outside = ((o > h * 1.001) | (o < lo * 0.999)).sum()
    if outside:
        issues.append(f"open_outside_hl:{int(outside)}")
    if (v < 0).any():
        issues.append(f"negative_volume:{int((v < 0).sum())}")

    ret = c.pct_change()
    lim = _limit_pct(code) * 1.5
    jumps = ret.abs() > lim
    if jumps.any():
        issues.append(f"price_jump_gt_{lim*100:.0f}pct:{int(jumps.sum())}")
    zero_vol_ratio = float((v == 0).mean()) if len(v) else 0.0
    if zero_vol_ratio > 0.2:
        issues.append(f"zero_volume_ratio:{zero_vol_ratio:.2f}")
    return {"code": code, "rows": len(df), "last": str(df.index[-1].date()),
            "issues": issues}


def audit_fundamental(limit: int = 200) -> dict:
    base = ROOT / "data" / "fundamental" / "profit"
    files = sorted(base.glob("*.parquet"))[:limit]
    missing_pub = total = 0
    for p in files:
        try:
            df = pd.read_parquet(p, columns=["pubDate"])
        except Exception:
            continue
        total += len(df)
        missing_pub += int(pd.to_datetime(df["pubDate"], errors="coerce").isna().sum())
    return {"files": len(files), "rows": total,
            "missing_pubdate_ratio": round(missing_pub / max(total, 1), 4),
            "note": "pubDate 缺失时 load_asof 按报告期+90天回退"}


def audit_moneyflow(limit: int = 200) -> dict:
    base = ROOT / "data" / "moneyflow" / "stock"
    files = sorted(base.glob("*.parquet"))[:limit]
    latest = []
    for p in files:
        try:
            df = pd.read_parquet(p, columns=None)
        except Exception:
            continue
        if not df.empty:
            latest.append(df.index.max())
    if not latest:
        return {"files": 0}
    idx = ROOT / "data" / "market" / "cn" / "index" / "000300.parquet"
    market_last = None
    if idx.exists():
        market_last = pd.read_parquet(idx).index.max()
    series = pd.Series(latest)
    return {
        "files": len(latest),
        "latest_min": str(series.min().date()),
        "latest_median": str(series.sort_values().iloc[len(series)//2].date()),
        "latest_max": str(series.max().date()),
        "market_latest": str(market_last.date()) if market_last is not None else None,
        "stale_vs_market_days": int((market_last - series.median()).days) if market_last is not None else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset-type", default="stock", choices=["stock", "etf"])
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    base = ROOT / "data" / "market" / "cn" / args.asset_type
    files = sorted(base.glob("*.parquet"))[:args.limit]
    results = [audit_kline(p, p.stem) for p in files]
    flagged = [r for r in results if r.get("issues")]
    audit = {
        "asset_type": args.asset_type,
        "sampled": len(results),
        "flagged": len(flagged),
        "flag_ratio": round(len(flagged) / max(len(results), 1), 4),
        "top_issues": sorted(
            {i.split(":")[0] for r in flagged for i in r["issues"]}
        ),
        "examples": flagged[:20],
        "fundamental": audit_fundamental(args.limit),
        "moneyflow": audit_moneyflow(args.limit),
    }
    out = ROOT / "data" / "processed" / "data_quality_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in audit.items() if k != "examples"},
                     ensure_ascii=False, indent=2))
    print(f"明细已保存: {out}")


if __name__ == "__main__":
    main()
