"""
signals 模块 - 信号聚合与持久化
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from strategies.base import Signal

logger = logging.getLogger(__name__)

SIGNAL_DIR = Path(__file__).parent.parent / "data" / "processed"
SIGNAL_DIR.mkdir(parents=True, exist_ok=True)


def save_signals(signals: List[Signal], date: str = None,
                 overwrite: bool = False) -> str:
    """
    将信号列表持久化为 JSON。

    行为说明：
      overwrite=True   → 替换现有文件（`main.py scan` 场景）
      overwrite=False  → 追加合并，同日期同一 code+signal 去重保留最新（默认）
    """
    date = date or datetime.now().strftime("%Y-%m-%d")
    path = SIGNAL_DIR / f"signals_{date}.json"

    new_data = [
        {
            "code": s.code,
            "name": s.name,
            "signal": s.signal,
            "price": s.price,
            "reason": s.reason,
            "score": s.score,
            "extra": s.extra,
            "generated_at": datetime.now().isoformat(),
        }
        for s in signals
    ]

    if overwrite or not path.exists():
        merged = new_data
    else:
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

        # 合并 + 去重：同 code+signal 保留最新的
        all_items = existing + new_data
        seen = {}
        for item in all_items:
            key = (item["code"], item["signal"])
            seen[key] = item  # 后出现的覆盖先出现的
        merged = list(seen.values())

    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    logger.info(f"信号已保存: {path} ({len(merged)} 条)")
    return str(path)


def load_signals(date: str) -> List[dict]:
    """加载指定日期的信号"""
    path = SIGNAL_DIR / f"signals_{date}.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)
