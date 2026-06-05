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


VALID_DOMAINS = ("market", "meta", "fundamental", "factor",
                 "macro", "tushare", "events")


def update_domain(domain: str) -> dict:
    """
    按需更新单个域，返回结构化结果（AC7：各域可独立更新）。
    domain 取值：market / meta / fundamental / factor / macro / tushare / events

    Returns:
        {"success": bool, "domain": str, "end_date": str, "error": str}
    """
    import time
    if domain not in VALID_DOMAINS:
        return {"success": False, "domain": domain,
                "error": f"未知域: {domain}，可选 {VALID_DOMAINS}"}

    t0 = time.time()
    end = datetime.now().strftime("%Y-%m-%d")
    try:
        if domain == "market":
            from data.fetcher import DataFetcher
            from data.downloader import KlineDownloader
            from config.settings import DATA_SOURCE
            KlineDownloader(DataFetcher(DATA_SOURCE)).update_db(end)
        elif domain == "meta":
            from data.downloader_meta import MetaDownloader
            MetaDownloader().update_db()
        elif domain == "fundamental":
            from data.downloader_fundamental import FundamentalDownloader
            FundamentalDownloader().update_db()
        elif domain == "factor":
            from data.downloader_factor import FactorDownloader
            FactorDownloader().update_db()
        elif domain == "macro":
            from data.downloader_macro import MacroDownloader
            MacroDownloader().update_db()
        elif domain == "tushare":
            from data.downloader_tushare import TushareDownloader
            TushareDownloader().update_db()
        elif domain == "events":
            from data.downloader_events import update_events
            update_events()

        return {"success": True, "domain": domain, "end_date": end,
                "elapsed": round(time.time() - t0, 1)}
    except Exception as e:
        logger.error(f"update_domain {domain} 失败: {e}")
        return {"success": False, "domain": domain, "error": str(e),
                "elapsed": round(time.time() - t0, 1)}


def get_active_event_windows(date: str = None) -> dict:
    """
    查询指定日期处于窗口期内的所有 E1 事件，供策略降权和辅助分析使用。

    Returns:
        {"success": bool, "date": str,
         "windows": [{"event_name", "event_type", "days_to_event",
                      "impact_scope"}]}
    """
    try:
        from data.store_events import get_active_event_windows as _windows
        d = date or datetime.now().strftime("%Y-%m-%d")
        ref = datetime.strptime(d, "%Y-%m-%d")
        events = _windows(d)
        windows = []
        for e in events:
            ev_date = datetime.strptime(e["date"], "%Y-%m-%d")
            windows.append({
                "event_name": e.get("event_name", ""),
                "event_type": e.get("event_type", ""),
                "days_to_event": (ev_date - ref).days,
                "impact_scope": e.get("impact_scope", ""),
            })
        return {"success": True, "date": d, "windows": windows}
    except Exception as e:
        logger.error(f"get_active_event_windows 失败: {e}")
        return {"success": False, "error": str(e)}


def check_reduce_window(code: str, date: str = None) -> dict:
    """
    检查标的是否处于大股东减持窗口期（对应 RC7）。

    Returns:
        {"success": bool, "code": str, "in_window": bool,
         "detail": {"holder", "end_date"}}
    """
    try:
        from data.store_events import has_reduce_window, load_corp_events
        d = date or datetime.now().strftime("%Y-%m-%d")
        in_window = has_reduce_window(code, d)
        detail = {}
        if in_window:
            ref = datetime.strptime(d, "%Y-%m-%d")
            for e in load_corp_events(code):
                if e.get("event_type") not in ("reduce", "decrease"):
                    continue
                ev_date = datetime.strptime(e["date"], "%Y-%m-%d")
                end_str = e.get("end_date") or e["date"]
                end_date = datetime.strptime(end_str, "%Y-%m-%d")
                if ev_date <= ref <= end_date:
                    detail = {
                        "holder": e.get("detail", {}).get("holder_name", ""),
                        "end_date": end_str,
                    }
                    break
        return {"success": True, "code": code, "in_window": in_window,
                "detail": detail}
    except Exception as e:
        logger.error(f"check_reduce_window 失败: {e}")
        return {"success": False, "code": code, "error": str(e)}


def get_domain_stats() -> dict:
    """返回各域数据统计"""
    return {
        "market": store.stats(),
    }


def validate_data(domain: str = "market", sample_codes: list = None) -> dict:
    """
    校验数据完整性，抽样检查关键列非空、行数合理（复用 data.quality）。

    Returns:
        {"success": bool, "domain": str, "checked": int,
         "issues": [{"code", "issue"}]}
    """
    try:
        from data.quality import run_quality_check
        if domain != "market":
            return {"success": False, "domain": domain,
                    "error": f"validate_data 暂仅支持 market 域，收到 {domain}"}

        codes = sample_codes or store.list_codes("stock")[:20]
        issues = []
        checked = 0
        for code in codes:
            df = store.load(code, "stock")
            if df is None or df.empty:
                issues.append({"code": code, "issue": "无数据或为空"})
                continue
            checked += 1
            report = run_quality_check(code, df)
            if not report["passed"]:
                n = len(report["anomalies"])
                issues.append({"code": code,
                               "issue": f"{n} 项异常",
                               "anomalies": report["anomalies"][:5]})
        return {"success": True, "domain": domain,
                "checked": checked, "issues": issues}
    except Exception as e:
        logger.error(f"validate_data 失败: {e}")
        return {"success": False, "domain": domain, "error": str(e)}


def query_kline(code: str, start: str = None, end: str = None,
                asset_type: str = "stock") -> dict:
    """
    查询 K 线数据，返回结构化摘要（不返回完整 DataFrame，避免 token 爆炸）。
    后复权价已在下载时写入（AC8：股票/指数后复权，ETF 不复权）。

    Returns:
        {"success": bool, "code": str, "rows": int, "start": str, "end": str,
         "columns": list, "sample": list[dict]}  # 最后3行样本
    """
    try:
        df = store.load(code, asset_type=asset_type, start=start, end=end)
        if df is None or df.empty:
            return {"success": False, "code": code,
                    "error": "无数据", "rows": 0}
        tail = df.tail(3).reset_index()
        sample = []
        for _, row in tail.iterrows():
            rec = {}
            for k, v in row.items():
                if hasattr(v, "strftime"):
                    rec[k] = v.strftime("%Y-%m-%d")
                else:
                    rec[k] = None if v != v else v  # NaN -> None
            sample.append(rec)
        return {
            "success": True,
            "code": code,
            "rows": len(df),
            "start": df.index.min().strftime("%Y-%m-%d"),
            "end": df.index.max().strftime("%Y-%m-%d"),
            "columns": list(df.columns),
            "sample": sample,
        }
    except Exception as e:
        logger.error(f"query_kline {code} 失败: {e}")
        return {"success": False, "code": code, "error": str(e)}
