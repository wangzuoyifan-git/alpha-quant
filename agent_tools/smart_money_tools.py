"""
agent_tools/smart_money_tools.py — 资金主体识别与跨期画像 Tools

薄适配层：把 agents/smart_money_agent.py 的能力封装为结构化 dict，
供 Orchestrator / LLM 调用（AC1：统一返回 success 字段，异常内部捕获）。

定位：描述性行为追踪（谁在买卖、他们一贯怎么做），不产因子、不做拥挤评级、
不做单日全市场复盘、不给买卖指令。
"""
import logging

import agents.smart_money_agent as sma

logger = logging.getLogger(__name__)


def _ok(payload: dict) -> dict:
    return {"success": True, **payload}


def get_seat_identity(agency: str) -> dict:
    """识别龙虎榜席位身份标签（规则匹配/推断，非官方认定）。"""
    try:
        info = sma.classify_seat(agency)
        return _ok({"agency": agency, **info,
                    "disclaimer": "标签来自规则匹配/推断，不等于官方认定"})
    except Exception as e:
        logger.error(f"get_seat_identity 失败: {e}")
        return {"success": False, "error": str(e)}


def get_seat_profile(agency: str, window_months: int = 12,
                     horizons="5,10,20") -> dict:
    """单个席位跨股画像：上榜频次/累计净买卖/上榜后N日胜率/持有周期/偏好板块。"""
    try:
        card = sma.profile_seat(agency, window_months=window_months,
                                horizons=sma.parse_horizons(horizons))
        return _ok({"profile": card})
    except Exception as e:
        logger.error(f"get_seat_profile 失败: {e}")
        return {"success": False, "error": str(e)}


def get_stock_smart_money(code: str, mode: str = "full",
                          window_months: int = 12, horizons="5,10,20",
                          fetch: bool = False) -> dict:
    """
    单只个股的资金主体画像。

    mode: full / seat / northbound / consensus
    fetch: 北向持股缺失时是否按需联网下载（默认 False，只用本地数据）
    """
    try:
        report = sma.build_profile(code, target_type="stock", mode=mode,
                                   window_months=window_months,
                                   horizons=sma.parse_horizons(horizons),
                                   fetch=fetch, update_seat_archive=False)
        return _ok({"report": report, "markdown": sma.report_to_markdown(report)})
    except Exception as e:
        logger.error(f"get_stock_smart_money 失败: {e}")
        return {"success": False, "error": str(e)}


def get_capital_consensus(code: str, window_months: int = 6,
                          fetch: bool = False) -> dict:
    """多源资金合力/分歧：北向 × 机构席位 × 融资盘 × 大宗买方。"""
    try:
        result = sma.assess_consensus(code, window_months=window_months, fetch=fetch)
        return _ok({"consensus": result})
    except Exception as e:
        logger.error(f"get_capital_consensus 失败: {e}")
        return {"success": False, "error": str(e)}


def get_northbound_behavior(code: str, window_months: int = 12,
                            fetch: bool = False) -> dict:
    """北向在某标的上的跨期持股行为（加/减仓 streak、集中度变化、与价格背离）。"""
    try:
        result = sma.profile_northbound(code, window_months=window_months, fetch=fetch)
        return _ok({"northbound": result})
    except Exception as e:
        logger.error(f"get_northbound_behavior 失败: {e}")
        return {"success": False, "error": str(e)}


def get_recent_lhb_smart_money(days: int = 1, history_months: int = 0,
                               top_n: int = 10, horizons="5,10,20") -> dict:
    """盘前扫描最近 N 个龙虎榜披露日标的：本次席位 + 席位跨股历史画像 + 本股历史其他上榜。"""
    try:
        result = sma.scan_recent_lhb(days=days, history_months=history_months,
                                     top_n=top_n,
                                     horizons=sma.parse_horizons(horizons))
        return _ok({"report": result, "markdown": sma.recent_lhb_to_markdown(result)})
    except Exception as e:
        logger.error(f"get_recent_lhb_smart_money 失败: {e}")
        return {"success": False, "error": str(e)}


def rank_smart_money_seats(window_months: int = 12, top_n: int = 20,
                           horizons="5,10,20") -> dict:
    """近期活跃资金主体榜（活动名册，非买入清单），按 |累计净买卖| 排序。"""
    try:
        cards = sma.rank_active_seats(window_months=window_months, top_n=top_n,
                                      horizons=sma.parse_horizons(horizons))
        return _ok({"seats": cards, "count": len(cards)})
    except Exception as e:
        logger.error(f"rank_smart_money_seats 失败: {e}")
        return {"success": False, "error": str(e)}
