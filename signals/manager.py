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


def save_signals(signals: List[Signal], date: str = None) -> str:
    """将信号列表持久化为 JSON"""
    date = date or datetime.now().strftime("%Y-%m-%d")
    path = SIGNAL_DIR / f"signals_{date}.json"
    data = [
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"信号已保存: {path}")
    return str(path)


def load_signals(date: str) -> List[dict]:
    """加载指定日期的信号"""
    path = SIGNAL_DIR / f"signals_{date}.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)
