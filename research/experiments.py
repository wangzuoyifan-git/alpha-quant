"""P1-3：实验注册表。

每次 CLI 回测/组合回测/walk-forward 追加一行 JSONL，记录：
代码版本、数据快照、参数、区间、指标、净值文件与结论。
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

REGISTRY = Path(__file__).parent / "experiments.jsonl"

_ROOT = Path(__file__).parent.parent


def git_commit() -> str:
    head = _ROOT / ".git" / "HEAD"
    try:
        text = head.read_text(encoding="utf-8").strip()
        if text.startswith("ref:"):
            ref = _ROOT / ".git" / text.split(" ", 1)[1]
            return ref.read_text(encoding="utf-8").strip()[:12]
        return text[:12]
    except OSError:
        return "unknown"


def git_dirty() -> bool:
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_ROOT, capture_output=True, text=True, timeout=3,
        )
        return bool(res.stdout.strip())
    except Exception:
        return False


def _mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


def data_snapshot() -> dict:
    cache = _ROOT / "data" / "cache"
    panels = sorted(cache.glob("research_panel_stock_*.npz"))
    etfs = sorted(cache.glob("research_panel_etf_*.npz"))
    fund = cache / "research_fund_v2.npz"
    index = _ROOT / "data" / "market" / "cn" / "index" / "000300.parquet"
    return {
        "panel_stock": panels[-1].name if panels else None,
        "panel_stock_mtime": _mtime(panels[-1]) if panels else None,
        "panel_etf": etfs[-1].name if etfs else None,
        "fund_cache": fund.name if fund.exists() else None,
        "fund_cache_mtime": _mtime(fund),
        "index_000300_mtime": _mtime(index),
    }


def record_experiment(kind: str, name: str, params: dict | None = None,
                      periods: dict | None = None, metrics: dict | None = None,
                      equity_file: str | None = None,
                      conclusion: str = "", extra: dict | None = None) -> dict:
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "name": name,
        "git_commit": git_commit(),
        "git_dirty": git_dirty(),
        "data": data_snapshot(),
        "params": params or {},
        "periods": periods or {},
        "metrics": metrics or {},
        "equity_file": equity_file,
        "conclusion": conclusion,
        "extra": extra or {},
    }
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_experiments(limit: int | None = None) -> list[dict]:
    if not REGISTRY.exists():
        return []
    rows = []
    with REGISTRY.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows[-limit:] if limit else rows
