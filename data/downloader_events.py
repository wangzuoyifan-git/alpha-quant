"""
data/downloader_events.py — 事件数据下载器（Phase 1 框架）
E1 日历人工维护，E2 通过 Tushare 接口下载减持/质押数据
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def init_events() -> None:
    """
    初始化事件域。
    Phase 1：导入预设的 E1 定期事件日历（人工维护），下载 E2 企业事件。
    """
    from data.store_events import save_event_calendar
    _init_calendar_template()
    logger.info("E1 事件日历初始化完成（预设模板）")


def _init_calendar_template() -> None:
    """E1 预设事件日历模板（人工维护，按季度补充）"""
    from data.store_events import save_event_calendar
    events = [
        {
            "event_id": "macro_cpi_2026q1",
            "event_type": "macro",
            "event_name": "CPI/PPI 发布 (2026Q1)",
            "date": "2026-01-10",
            "end_date": "2026-01-10",
            "impact_scope": "cn",
            "pre_window": 3,
            "post_window": 0,
            "confirmed": False,
        },
        {
            "event_id": "earnings_2025q4",
            "event_type": "earnings",
            "event_name": "2025年报/2026一季报窗口期",
            "date": "2026-03-15",
            "end_date": "2026-04-30",
            "impact_scope": "cn",
            "pre_window": 0,
            "post_window": 0,
            "confirmed": False,
        },
    ]
    save_event_calendar(events)


def update_events() -> None:
    """增量更新事件数据（Phase 1: 仅 E2 企业事件）"""
    logger.info("E2 企业事件通过 Tushare 接口下载（待接入 stk_holdertrade / pledge_stat）")


def download_reduce_events(codes: list = None) -> list:
    """
    下载大股东减持计划数据（Tushare stk_holdertrade）。

    Phase 1 暂时返回空列表，接入 Tushare 后实现。
    """
    logger.info(f"减持数据下载: {len(codes or [])} 只标的（Phase 1: 接口待接入 Tushare）")
    return []
