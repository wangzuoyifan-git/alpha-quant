"""
data/downloader_events.py — 事件数据下载器

E1 定期事件日历：按规则自动生成全年事件日历
E2 企业事件：通过 Tushare 接口下载减持/质押/分红数据
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_FMT = "%Y%m%d"
_FMT_DASH = "%Y-%m-%d"


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


# ═══════════════════════════════════════════════════════════════
# E1 — 定期事件日历
# ═══════════════════════════════════════════════════════════════

def build_calendar(year: int = None) -> list:
    """按规则生成全年事件日历"""
    year = year or datetime.now().year
    from data.store_events import save_event_calendar

    events = []
    events += _earnings_windows(year)
    events += _macro_release_dates(year)
    events += _fomc_dates(year)
    events += _cn_policy_dates(year)

    save_event_calendar(events)
    _log(f"E1 日历生成完毕: {len(events)} 条事件（{year}年）")
    return events


def _earnings_windows(year: int) -> list:
    """财报窗口期（中国上市公司披露规则）"""
    return [
        {
            "event_id": f"earnings_annual_{year}",
            "event_type": "earnings",
            "event_name": f"{year-1}年报窗口期",
            "date": f"{year}-01-01",
            "end_date": f"{year}-04-30",
            "impact_scope": "cn",
            "pre_window": 0,
            "post_window": 0,
            "confirmed": True,
        },
        {
            "event_id": f"earnings_q1_{year}",
            "event_type": "earnings",
            "event_name": f"{year}一季报窗口期",
            "date": f"{year}-04-01",
            "end_date": f"{year}-04-30",
            "impact_scope": "cn",
            "pre_window": 0,
            "post_window": 0,
            "confirmed": True,
        },
        {
            "event_id": f"earnings_semi_{year}",
            "event_type": "earnings",
            "event_name": f"{year}半年报窗口期",
            "date": f"{year}-07-01",
            "end_date": f"{year}-08-31",
            "impact_scope": "cn",
            "pre_window": 0,
            "post_window": 0,
            "confirmed": True,
        },
        {
            "event_id": f"earnings_q3_{year}",
            "event_type": "earnings",
            "event_name": f"{year}三季报窗口期",
            "date": f"{year}-10-01",
            "end_date": f"{year}-10-31",
            "impact_scope": "cn",
            "pre_window": 0,
            "post_window": 0,
            "confirmed": True,
        },
    ]


def _macro_release_dates(year: int) -> list:
    """宏观数据发布日（按经验近似日期，每月更新）"""
    events = []
    for m in range(1, 13):
        ym = f"{year}-{m:02d}"
        events += [
            {
                "event_id": f"macro_pmi_{year}{m:02d}",
                "event_type": "macro",
                "event_name": f"制造业PMI发布 ({ym})",
                "date": f"{_last_day(year, m)}",
                "end_date": f"{_last_day(year, m)}",
                "impact_scope": "cn",
                "pre_window": 1,
                "post_window": 1,
                "confirmed": False,
            },
            {
                "event_id": f"macro_cpi_ppi_{year}{m:02d}",
                "event_type": "macro",
                "event_name": f"CPI/PPI发布 ({ym})",
                "date": f"{year}-{m:02d}-09",
                "end_date": f"{year}-{m:02d}-09",
                "impact_scope": "cn",
                "pre_window": 3,
                "post_window": 1,
                "confirmed": False,
            },
            {
                "event_id": f"macro_m2_shrongji_{year}{m:02d}",
                "event_type": "macro",
                "event_name": f"M2/社融发布 ({ym})",
                "date": f"{year}-{m:02d}-12",
                "end_date": f"{year}-{m:02d}-12",
                "impact_scope": "cn",
                "pre_window": 2,
                "post_window": 1,
                "confirmed": False,
            },
        ]

    # GDP（季频，1/4/7/10月）
    for m in (1, 4, 7, 10):
        ym = f"{year}-{m:02d}"
        events.append({
            "event_id": f"macro_gdp_{year}q{(m+2)//3}",
            "event_type": "macro",
            "event_name": f"GDP发布 ({ym})",
            "date": f"{year}-{m:02d}-18",
            "end_date": f"{year}-{m:02d}-18",
            "impact_scope": "cn",
            "pre_window": 3,
            "post_window": 1,
            "confirmed": False,
        })

    return events


def _fomc_dates(year: int) -> list:
    """美联储议息日（2026年为已知日程，其余年份用近似规则）"""
    fomc_schedule = {
        2026: [
            ("2026-01-28", "2026-01-29"),
            ("2026-03-18", "2026-03-19"),
            ("2026-05-06", "2026-05-07"),
            ("2026-06-17", "2026-06-18"),
            ("2026-07-29", "2026-07-30"),
            ("2026-09-16", "2026-09-17"),
            ("2026-11-04", "2026-11-05"),
            ("2026-12-16", "2026-12-17"),
        ],
        2027: [
            ("2027-01-27", "2027-01-28"),
            ("2027-03-17", "2027-03-18"),
            ("2027-05-05", "2027-05-06"),
            ("2027-06-16", "2027-06-17"),
            ("2027-07-28", "2027-07-29"),
            ("2027-09-15", "2027-09-16"),
            ("2027-11-03", "2027-11-04"),
            ("2027-12-15", "2027-12-16"),
        ],
    }
    schedule = fomc_schedule.get(year, [])
    if not schedule:
        # 近似规则：大约每6-7周一次
        d = datetime(year, 1, 28)
        while d.year == year:
            end = d + timedelta(days=1)
            schedule.append((d.strftime(_FMT_DASH), end.strftime(_FMT_DASH)))
            d += timedelta(days=49)
    return [
        {
            "event_id": f"fomc_{start.replace('-', '')}",
            "event_type": "macro",
            "event_name": f"FOMC议息会议 ({start}~{end})",
            "date": start,
            "end_date": end,
            "impact_scope": "global",
            "pre_window": 5,
            "post_window": 3,
            "confirmed": year >= 2025,
        }
        for start, end in schedule
    ]


def _cn_policy_dates(year: int) -> list:
    """中国政策会议日历"""
    events = [
        {
            "event_id": f"two_sessions_{year}",
            "event_type": "policy",
            "event_name": f"全国两会 ({year}年)",
            "date": f"{year}-03-04",
            "end_date": f"{year}-03-11",
            "impact_scope": "cn",
            "pre_window": 5,
            "post_window": 3,
            "confirmed": True,
        },
        {
            "event_id": f"politburo_apr_{year}",
            "event_type": "policy",
            "event_name": f"政治局会议 (4月)",
            "date": f"{year}-04-25",
            "end_date": f"{year}-04-25",
            "impact_scope": "cn",
            "pre_window": 2,
            "post_window": 2,
            "confirmed": False,
        },
        {
            "event_id": f"politburo_jul_{year}",
            "event_type": "policy",
            "event_name": f"政治局会议 (7月)",
            "date": f"{year}-07-25",
            "end_date": f"{year}-07-25",
            "impact_scope": "cn",
            "pre_window": 2,
            "post_window": 2,
            "confirmed": False,
        },
        {
            "event_id": f"politburo_oct_{year}",
            "event_type": "policy",
            "event_name": f"政治局会议 (10月)",
            "date": f"{year}-10-25",
            "end_date": f"{year}-10-25",
            "impact_scope": "cn",
            "pre_window": 2,
            "post_window": 2,
            "confirmed": False,
        },
        {
            "event_id": f"cec_{year}",
            "event_type": "policy",
            "event_name": f"中央经济工作会议 ({year}年)",
            "date": f"{year}-12-15",
            "end_date": f"{year}-12-15",
            "impact_scope": "cn",
            "pre_window": 5,
            "post_window": 3,
            "confirmed": False,
        },
    ]
    return events


def _last_day(year: int, month: int) -> str:
    """返回该月的最后一天字符串"""
    if month == 12:
        return f"{year}-12-31"
    next_m = datetime(year, month + 1, 1)
    last = next_m - timedelta(days=1)
    return last.strftime(_FMT_DASH)


# ═══════════════════════════════════════════════════════════════
# E2 — 企业事件下载（Tushare）
# ═══════════════════════════════════════════════════════════════

def download_all_corp_events(codes: list = None,
                              start: str = "2024-01-01",
                              end: str = None) -> dict:
    """下载全部企业事件：减持/质押/分红"""
    end = end or datetime.now().strftime(_FMT_DASH)
    from data.downloader_moneyflow import _get_all_stock_codes
    from data.store_events import save_corp_events

    if codes is None:
        try:
            pro = _get_pro()
            codes = _get_all_stock_codes(pro)
        except Exception as e:
            _log(f"获取股票列表失败: {e}")
            # 从 market 数据获取已覆盖的股票作为 fallback
            import data.store as mstore
            codes = [f"{c}.SZ" if c.startswith("0") or c.startswith("3")
                     else f"{c}.SH" for c in mstore.list_codes("stock", "cn")]
    total = len(codes)
    _log(f"E2 企业事件下载: {total} 只, {start} ~ {end}")

    # 增量更新（短时间范围）时质押只取最近26条，全量初始化取52条
    is_incremental = start >= (datetime.now() - timedelta(days=730)).strftime(_FMT_DASH)
    pledge_limit = 26 if is_incremental else 52
    dividend_limit = 10 if is_incremental else 20

    repurchase_limit = 10 if is_incremental else 20
    result = {"reduce": 0, "pledge": 0, "dividend": 0, "buyback": 0, "fail": 0}
    for i, ts_code in enumerate(codes):
        code6 = ts_code.split(".")[0]
        events = []
        try:
            ev = _fetch_reduce(ts_code, start, end)
            if ev:
                events += ev
                result["reduce"] += len(ev)
        except Exception as e:
            logger.debug(f"{ts_code} 减持失败: {e}")
            result["fail"] += 1

        try:
            ev = _fetch_repurchase(ts_code, start, end, max_records=repurchase_limit)
            if ev:
                events += ev
                result["buyback"] += len(ev)
        except Exception as e:
            logger.debug(f"{ts_code} 回购失败: {e}")

        try:
            ev = _fetch_pledge(ts_code, max_records=pledge_limit)
            if ev:
                events += ev
                result["pledge"] += len(ev)
        except Exception as e:
            logger.debug(f"{ts_code} 质押失败: {e}")

        try:
            ev = _fetch_dividend(ts_code, max_records=dividend_limit)
            if ev:
                events += ev
                result["dividend"] += len(ev)
        except Exception as e:
            logger.debug(f"{ts_code} 分红失败: {e}")

        if events:
            save_corp_events(code6, events)
        time.sleep(0.15)

        if (i + 1) % 100 == 0 or (i + 1) == total:
            _log(f"  E2进度: {i+1}/{total}  "
                 f"减持{result['reduce']} 质押{result['pledge']} 分红{result['dividend']} "
                 f"回购{result['buyback']} 失败{result['fail']}")

    _log(f"E2完成: 减持{result['reduce']} 质押{result['pledge']} 分红{result['dividend']} "
         f"回购{result['buyback']} 失败{result['fail']}")
    return result


def _fmt_date(val) -> Optional[str]:
    """把 Tushare 的 YYYYMMDD 转为 YYYY-MM-DD，NaN/None 返回 None"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s if s and s != "nan" else None


def _fmt_float(val) -> Optional[float]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    return float(val)


def _get_pro():
    import os
    import tushare as ts
    token = os.getenv("TUSHARE_TOKEN", "")
    if not token:
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith("TUSHARE_TOKEN="):
                    token = line.strip().split("=", 1)[1]
                    break
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    ts.set_token(token)
    return ts.pro_api()


def _fetch_reduce(ts_code: str, start: str, end: str) -> list:
    """大股东增减持计划 (Tushare stk_holdertrade)"""
    pro = _get_pro()
    df = pro.stk_holdertrade(
        ts_code=ts_code,
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
    )
    if df is None or df.empty:
        return []
    events = []
    for _, row in df.iterrows():
        in_de = row.get("in_de", "")
        holder = str(row.get("holder_name", "unknown"))[:20]
        ann_date = _fmt_date(row.get("ann_date"))
        if not ann_date:
            continue
        events.append({
            "event_id": f"reduce_{ts_code}_{ann_date.replace('-', '')}_{holder}",
            "code": ts_code.split(".")[0],
            "event_type": "increase" if in_de == "I" else "reduce",
            "date": ann_date,
            "end_date": None,
            "detail": {
                "holder_name": row.get("holder_name", ""),
                "holder_type": row.get("holder_type", ""),
                "change_vol": _fmt_float(row.get("change_vol")),
                "change_ratio": _fmt_float(row.get("change_ratio")),
                "after_ratio": _fmt_float(row.get("after_ratio")),
                "avg_price": _fmt_float(row.get("avg_price")),
            },
            "source": "tushare",
            "phase": 1,
        })
    return events


def _fetch_pledge(ts_code: str, max_records: int = 52) -> list:
    """股权质押统计 (Tushare pledge_stat)，最多保留最新 max_records 条"""
    pro = _get_pro()
    df = pro.pledge_stat(ts_code=ts_code)
    if df is None or df.empty:
        return []
    df = df.sort_values("end_date", ascending=False).head(max_records)
    events = []
    for _, row in df.iterrows():
        end_d = _fmt_date(row.get("end_date"))
        if not end_d:
            continue
        events.append({
            "event_id": f"pledge_{ts_code}_{end_d.replace('-', '')}",
            "code": ts_code.split(".")[0],
            "event_type": "pledge",
            "date": end_d,
            "end_date": None,
            "detail": {
                "pledge_count": int(row["pledge_count"]) if pd.notna(row.get("pledge_count")) else None,
                "unrest_pledge": _fmt_float(row.get("unrest_pledge")),
                "rest_pledge": _fmt_float(row.get("rest_pledge")),
                "total_share": _fmt_float(row.get("total_share")),
                "pledge_ratio": _fmt_float(row.get("pledge_ratio")),
            },
            "source": "tushare",
            "phase": 1,
        })
    return events


def _fetch_repurchase(ts_code: str, start: str, end: str, max_records: int = 20) -> list:
    """股票回购 (Tushare repurchase)，只保留公告日在 [start,end] 的最新 max_records 条。"""
    pro = _get_pro()
    df = pro.repurchase(
        ts_code=ts_code,
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
    )
    if df is None or df.empty:
        return []
    events = []
    for _, row in df.iterrows():
        ann = _fmt_date(row.get("ann_date"))
        if not ann or ann < start or ann > end:
            continue
        proc = str(row.get("proc", "") or "")
        events.append({
            "event_id": f"buyback_{ts_code}_{ann.replace('-', '')}_{proc}",
            "code": ts_code.split(".")[0],
            "event_type": "buyback",
            "date": ann,
            "end_date": _fmt_date(row.get("end_date")),
            "detail": {
                "proc": proc,
                "vol": _fmt_float(row.get("vol")),
                "amount": _fmt_float(row.get("amount")),
                "high_limit": _fmt_float(row.get("high_limit")),
                "low_limit": _fmt_float(row.get("low_limit")),
            },
            "source": "tushare",
            "phase": 1,
        })
    events.sort(key=lambda e: e["date"], reverse=True)
    return events[:max_records]


def _fetch_dividend(ts_code: str, max_records: int = 20) -> list:
    """分红送转 (Tushare dividend)，最多保留最新 max_records 条"""
    pro = _get_pro()
    df = pro.dividend(ts_code=ts_code)
    if df is None or df.empty:
        return []
    df = df.sort_values("ann_date", ascending=False).head(max_records)
    events = []
    for _, row in df.iterrows():
        ann_date = _fmt_date(row.get("ann_date"))
        ex_date = _fmt_date(row.get("ex_date"))
        if not ann_date:
            continue
        record_date = _fmt_date(row.get("record_date"))
        pay_date = _fmt_date(row.get("pay_date"))
        events.append({
            "event_id": f"dividend_{ts_code}_{ann_date.replace('-', '')}",
            "code": ts_code.split(".")[0],
            "event_type": "dividend",
            "date": ann_date,
            "end_date": ex_date,
            "detail": {
                "cash_div": _fmt_float(row.get("cash_div")),
                "cash_div_tax": _fmt_float(row.get("cash_div_tax")),
                "stk_div": _fmt_float(row.get("stk_div")),
                "stk_bo_rate": _fmt_float(row.get("stk_bo_rate")),
                "record_date": record_date,
                "ex_date": ex_date,
                "pay_date": pay_date,
                "div_proc": row.get("div_proc", ""),
            },
            "source": "tushare",
            "phase": 1,
        })
    return events


def init_events(year: int = None) -> None:
    """初始化事件域全量数据"""
    year = year or datetime.now().year

    _log("=" * 55)
    _log("Events 域初始化")
    _log("=" * 55)

    _log(f"[1/2] 生成 E1 定期事件日历 ({year}年)...")
    build_calendar(year)

    _log("[2/2] 下载 E2 企业事件（减持/质押/分红）...")
    download_all_corp_events()

    _log("Events 域初始化完成！")


def update_events(codes: list = None) -> None:
    """增量更新事件数据。

    :param codes: 指定股票代码（带/不带后缀均可）；None 表示全市场。
    """
    _log("Events 域增量更新...")

    # E1：每季度补充下次季度的宏观事件（预发布确认日期）
    _log("[1/2] 补充 E1 事件日历...")
    today = datetime.now()
    # 如果快季度末了，预生成下季度 macro 事件
    year = today.year
    build_calendar(year)
    build_calendar(year + 1)

    # E2：下载最近3个月的增减持/分红/回购
    start_90 = (today - timedelta(days=90)).strftime(_FMT_DASH)
    end_today = today.strftime(_FMT_DASH)
    if codes:
        codes = [c if "." in str(c) else
                 (f"{c}.SH" if str(c).startswith(("6", "9")) else f"{c}.SZ")
                 for c in codes]
        _log(f"[2/2] 更新 E2 企业事件（近3月，指定 {len(codes)} 只）...")
    else:
        _log("[2/2] 更新 E2 企业事件（近3月，全市场）...")
    download_all_corp_events(codes=codes, start=start_90, end=end_today)

    _log("Events 域更新完成！")