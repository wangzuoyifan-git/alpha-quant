"""
A 股估值补充下载器（PCF 市现率，百度股市通）

背景：Tushare daily_basic 不提供 pcf_ttm，A 股 K 线中的 pcf_ttm 长期为空。
本模块按个股抓取百度股市通「市现率」并合并进 `data/market/cn/stock/{code}.parquet`
（只补 pcf_ttm 列，不覆盖行情/其他估值列）。

用法:
  python main.py update-cn-pcf                 # 全市场（约 1 小时）
  python main.py update-cn-pcf --codes 600519 000001
"""
import logging
import time

import pandas as pd

import data.store as store

logger = logging.getLogger(__name__)

_BAIDU_URL = "https://gushitong.baidu.com/opendata"
_BAIDU_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer": "https://gushitong.baidu.com/",
}
_SLEEP = 0.3


def _log(msg: str):
    from datetime import datetime
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _fetch_pcf(code: str, period: str = "近一年") -> pd.DataFrame | None:
    """百度股市通-A股-市现率（日频）"""
    import requests

    params = {
        "openapi": "1", "dspName": "iphone", "tn": "tangram", "client": "app",
        "query": "市现率", "code": code, "word": "",
        "resource_id": "51171", "market": "ab", "tag": "市现率",
        "chart_select": period, "industry_select": "",
        "skip_industry": "1", "finClientType": "pc",
    }
    r = requests.get(_BAIDU_URL, params=params, headers=_BAIDU_HEADERS, timeout=20)
    r.raise_for_status()
    body = (r.json()["Result"][0]["DisplayData"]["resultData"]["tplData"]
            ["result"]["chartInfo"][0]["body"])
    rows = []
    for item in body or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            rows.append({"date": item[0], "pcf_ttm": float(item[1])})
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows).drop_duplicates("date").set_index("date")
    df.index = pd.to_datetime(df.index)
    return df


def _merge_pcf(kline: pd.DataFrame, val: pd.DataFrame) -> pd.DataFrame:
    """按日期把 pcf_ttm 合并进 K 线（百度值覆盖旧值，日期以 K 线为准）"""
    merged = kline.copy()
    new = val["pcf_ttm"].reindex(merged.index)
    if "pcf_ttm" in merged.columns:
        merged["pcf_ttm"] = new.where(new.notna(), merged["pcf_ttm"])
    else:
        merged["pcf_ttm"] = new
    return merged


def update_cn_pcf(codes: list[str] | None = None,
                  period: str = "近一年") -> dict:
    """更新 A 股 pcf_ttm（百度市现率，默认近一年）"""
    if codes is None:
        codes = store.list_codes("stock", market="cn")
    total = len(codes)
    _log("=" * 55)
    _log(f"CN 估值补充（PCF）  标的: {total} 只  区间: {period}")
    _log("=" * 55)

    ok = skip = fail = 0
    t0 = time.time()
    for code in codes:
        try:
            val = _fetch_pcf(code, period)
            kline = store.load(code, "stock", market="cn")
            if val is None or val.empty or kline is None or kline.empty:
                skip += 1
            else:
                store.replace(code, _merge_pcf(kline, val),
                              asset_type="stock", market="cn")
                ok += 1
        except Exception as e:
            logger.warning(f"{code} PCF 更新失败: {e}")
            fail += 1
        done = ok + skip + fail
        if done % 200 == 0 or done == total:
            _log(f"  进度: {done}/{total}  成功={ok} 跳过={skip} 失败={fail}  "
                 f"已用:{time.time()-t0:.0f}s")
        time.sleep(_SLEEP)
    _log(f"PCF 更新完成: 成功={ok} 跳过={skip} 失败={fail}")
    return {"ok": ok, "skip": skip, "fail": fail, "total": total}
