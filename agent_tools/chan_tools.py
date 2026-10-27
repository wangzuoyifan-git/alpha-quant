"""
agent_tools/chan_tools.py — 缠论结构分析 Tools

薄适配层：把 chan/ 的计算能力封装为结构化 dict，供 Orchestrator / LLM 调用。
遵循 AC1：统一返回 success 字段，异常内部捕获。
"""
from __future__ import annotations

import logging

import data.store as store
from chan.engine import ChanEngine
from chan.multi_level import analyze_multi_level

logger = logging.getLogger(__name__)


def _ok(payload: dict) -> dict:
    return {"success": True, **payload}


def _load(code: str, date: str | None, asset_type: str, market: str):
    return store.load(code, asset_type=asset_type, market=market, end=date)


def get_chan_snapshot(
    code: str,
    level: str = "daily",
    date: str | None = None,
    asset_type: str = "stock",
    market: str = "cn",
    with_weekly: bool = True,
) -> dict:
    """
    单级别缠论快照：最新笔/中枢/买卖点/背驰/趋势。

    :param level: daily / weekly（weekly 由日线聚合）
    :param with_weekly: 日线分析时是否附带周线方向确认
    """
    try:
        df = _load(code, date, asset_type, market)
        if df is None or len(df) < 3:
            return {"success": False, "error": f"{code} 无本地行情数据"}

        engine = ChanEngine()
        higher_trend = None
        if level == "weekly":
            from chan.adapter import resample_ohlc

            weekly_df = resample_ohlc(df, rule="W-FRI")
            if weekly_df is None or len(weekly_df) < 3:
                return {"success": False, "error": f"{code} 周线数据不足"}
            result = engine.analyze(weekly_df, level="weekly", code=code)
        elif with_weekly:
            ml = analyze_multi_level(df, engine, code)
            result = ml["daily"]
            higher_trend = ml["higher_level_trend"]
        else:
            result = engine.analyze(df, level=level, code=code)

        return _ok({
            "code": code,
            "level": level,
            "snapshot": result.summary_dict(higher_trend),
        })
    except Exception as e:
        logger.error(f"get_chan_snapshot 失败: {e}")
        return {"success": False, "error": str(e)}


def get_chan_buy_points(
    code: str,
    date: str | None = None,
    kinds: list[str] | None = None,
    limit: int = 10,
    asset_type: str = "stock",
    market: str = "cn",
) -> dict:
    """
    查询最近买卖点。

    :param kinds: 过滤类型，如 ["3buy","3sell"]；None 返回全部
    :param limit: 最多返回最近 N 个
    """
    try:
        df = _load(code, date, asset_type, market)
        if df is None or len(df) < 3:
            return {"success": False, "error": f"{code} 无本地行情数据"}

        result = ChanEngine().analyze(df, level="daily", code=code)
        points = result.points
        if kinds:
            allowed = set(kinds)
            points = [p for p in points if p.kind in allowed]
        points = points[-max(int(limit), 1):]
        return _ok({
            "code": code,
            "asof": result.asof,
            "points": [
                {
                    "kind": p.kind,
                    "price": round(p.price, 3),
                    "date": p.dt.strftime("%Y-%m-%d") if p.dt is not None else None,
                    "orig_idx": p.orig_idx,
                    "strength": round(p.strength, 3),
                    "confirmed": p.confirmed,
                    "reason": p.reason,
                }
                for p in points
            ],
            "count": len(points),
        })
    except Exception as e:
        logger.error(f"get_chan_buy_points 失败: {e}")
        return {"success": False, "error": str(e)}


def get_chan_multi_level(
    code: str,
    date: str | None = None,
    asset_type: str = "stock",
    market: str = "cn",
) -> dict:
    """日线 + 周线结构摘要与高级别方向。"""
    try:
        df = _load(code, date, asset_type, market)
        if df is None or len(df) < 3:
            return {"success": False, "error": f"{code} 无本地行情数据"}

        ml = analyze_multi_level(df, code=code)
        daily = ml["daily"]
        weekly = ml["weekly"]
        return _ok({
            "code": code,
            "daily": daily.summary_dict(ml["higher_level_trend"]),
            "weekly": weekly.summary_dict() if weekly is not None else None,
            "higher_level_trend": ml["higher_level_trend"],
        })
    except Exception as e:
        logger.error(f"get_chan_multi_level 失败: {e}")
        return {"success": False, "error": str(e)}
