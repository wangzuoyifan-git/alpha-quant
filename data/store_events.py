"""
data/store_events.py — events 域存储层 (D8 E1/E2/E3)
Phase 1 数据结构，预留 Phase 2 NLP 字段
"""
import json
import logging
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent / "events"
BASE_DIR.mkdir(parents=True, exist_ok=True)

E1_CALENDAR_FILE = BASE_DIR / "calendar.json"
E2_CORP_DIR = BASE_DIR / "corporate"
E3_SHOCK_DIR = BASE_DIR / "market_shock"
E2_CORP_DIR.mkdir(parents=True, exist_ok=True)
E3_SHOCK_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
# E1 定期事件日历
# ═══════════════════════════════════════════════════════════════

def save_event_calendar(events: List[dict]) -> None:
    """保存 E1 事件日历，增量合并"""
    existing = load_event_calendar()
    idx = {e["event_id"]: e for e in existing}
    for e in events:
        idx[e["event_id"]] = e
    with open(E1_CALENDAR_FILE, "w", encoding="utf-8") as f:
        json.dump(list(idx.values()), f, ensure_ascii=False, indent=2)
    logger.info(f"E1 日历已保存: {len(idx)} 条")


def load_event_calendar() -> List[dict]:
    if not E1_CALENDAR_FILE.exists():
        return []
    with open(E1_CALENDAR_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_active_event_windows(date: str = None) -> List[dict]:
    """查询当日处于窗口期内的事件（含 pre_window/post_window）"""
    from datetime import datetime, timedelta
    events = load_event_calendar()
    if not events:
        return []

    d = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()
    active = []
    for e in events:
        ev_date = datetime.strptime(e["date"], "%Y-%m-%d")
        pre = e.get("pre_window", 0)
        post = e.get("post_window", 0)
        start = ev_date - timedelta(days=pre)
        end_dt = datetime.strptime(e.get("end_date", e["date"]), "%Y-%m-%d") + timedelta(days=post)
        if start <= d <= end_dt:
            active.append(e)
    return active


# ═══════════════════════════════════════════════════════════════
# E2 企业事件
# ═══════════════════════════════════════════════════════════════

def save_corp_events(code: str, events: List[dict]) -> None:
    """保存企业事件，按 {code}.json 存储，增量合并"""
    p = E2_CORP_DIR / f"{code}.json"
    existing = load_corp_events(code)
    idx = {e["event_id"]: e for e in existing}
    for e in events:
        e.setdefault("source", "tushare")
        e.setdefault("phase", 1)
        idx[e["event_id"]] = e
    with open(p, "w", encoding="utf-8") as f:
        json.dump(list(idx.values()), f, ensure_ascii=False, indent=2)


def load_corp_events(code: str) -> List[dict]:
    p = E2_CORP_DIR / f"{code}.json"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def has_reduce_window(code: str, date: str = None) -> bool:
    """RC7：检查是否处于大股东减持窗口期"""
    from datetime import datetime
    events = load_corp_events(code)
    if not events:
        return False
    d = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()
    reduce_types = ("reduce", "decrease")
    for e in events:
        if e.get("event_type") not in reduce_types:
            continue
        ev_date = datetime.strptime(e["date"], "%Y-%m-%d")
        end_str = e.get("end_date") or e["date"]
        end_date = datetime.strptime(end_str, "%Y-%m-%d")
        if ev_date <= d <= end_date:
            return True
    return False


# ═══════════════════════════════════════════════════════════════
# E3 突发事件
# ═══════════════════════════════════════════════════════════════

def save_market_shock(event: dict) -> None:
    """保存突发事件"""
    from datetime import datetime
    eid = event.get("event_id", datetime.now().strftime("shock_%Y%m%d_%H%M%S"))
    event.setdefault("source", "manual")
    event.setdefault("phase", 1)
    p = E3_SHOCK_DIR / f"{eid}.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(event, f, ensure_ascii=False, indent=2)


def load_market_shocks(date: str = None) -> List[dict]:
    """加载突发事件列表"""
    shocks = []
    for p in sorted(E3_SHOCK_DIR.glob("*.json")):
        with open(p, encoding="utf-8") as f:
            shock = json.load(f)
        if date and shock.get("date", "") != date:
            continue
        shocks.append(shock)
    return shocks
