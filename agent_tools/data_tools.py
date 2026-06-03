"""
agent_tools/data_tools.py — 数据相关 Tools
供 Orchestrator Agent 直接调用，返回结构化 dict（AC1）
"""
import logging
from datetime import datetime, timedelta

import data.store as store
import data.store_fundamental as store_fund
import data.store_factor as store_factor
import data.store_macro as store_macro
import data.store_tushare as store_ts

logger = logging.getLogger(__name__)


def check_data_freshness() -> dict:
    """DR3：检查所有数据域新鲜度，返回 {success, domains, stale}"""
    from data.store_meta import load as load_calendar
    result = {"success": True, "domains": {}, "stale": []}
    today = datetime.now()

    calendar = load_calendar("calendar")
    if calendar is not None and not calendar.empty:
        filtered = calendar.index[calendar.index <= today]
        if len(filtered) > 0:
            last_trading = filtered.max().strftime("%Y-%m-%d")
        else:
            dt = today
            while dt.weekday() >= 5:
                dt -= timedelta(days=1)
            last_trading = dt.strftime("%Y-%m-%d")
    else:
        dt = today
        while dt.weekday() >= 5:
            dt -= timedelta(days=1)
        last_trading = dt.strftime("%Y-%m-%d")

    codes = store.list_codes("stock")
    if codes:
        ld = store.last_date(codes[0])
        result["domains"]["market/stock"] = ld or "N/A"
        if ld and ld < last_trading:
            result["stale"].append("market/stock")

    for dtype in store_fund.VALID_TYPES:
        codes = store_fund.list_codes(dtype)
        if codes:
            result["domains"][f"fundamental/{dtype}"] = store_fund.last_date(codes[0], dtype) or "N/A"

    for dtype in ("moneyflow_sector", "northbound"):
        ld = store_factor.last_date(dtype)
        if ld:
            result["domains"][f"factor/{dtype}"] = ld

    for dtype in store_macro.VALID_TYPES:
        ld = store_macro.last_date(dtype)
        if ld:
            result["domains"][f"macro/{dtype}"] = ld

    margin_codes = store_ts.list_codes("margin")
    if margin_codes:
        ld = store_ts.last_date("margin", code=margin_codes[0])
        result["domains"]["tushare/margin"] = ld or "N/A"

    return result


def update_domain(domain: str) -> dict:
    """更新指定域的数据"""
    try:
        from data.downloader import KlineDownloader
        dl = KlineDownloader(None, {})
        end = datetime.now().strftime("%Y-%m-%d")
        if domain == "market":
            dl.update_db(end)
        return {"success": True, "domain": domain, "end_date": end}
    except Exception as e:
        logger.error(f"update_domain {domain} 失败: {e}")
        return {"success": False, "domain": domain, "error": str(e)}


def get_domain_stats() -> dict:
    """返回各域数据统计"""
    return {
        "market": store.stats(),
    }
