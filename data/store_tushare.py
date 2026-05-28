"""
tushare 域存储层

目录结构:
  data/tushare/
    moneyflow/ {code}.parquet      个股资金流向（日频，2010起）
    daily/     {code}.parquet      日线行情（stk_daily，可替代 BaoStock）
    daily_basic/{code}.parquet     每日指标（daily_basic：PE/PB/换手率等）
    top_list/  {date}.parquet      龙虎榜（按日期分片）
    margin/    {code}.parquet      融资融券（按股票代码）
    moneyflow_ind/ {date}.parquet  行业资金流（东财，按日期分片）
    moneyflow_mkt.parquet          大盘资金流（东财，单文件）

接口:
  save(data_type, df, code=None, date=None)
  load(data_type, code=None, date=None, start=None, end=None)
  exists(data_type, code=None, date=None)
  last_date(data_type, code=None)
  list_codes(data_type)
  list_dates(data_type)
"""
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent / "tushare"

# 按股票代码分片的类型
_CODE_TYPES = ("moneyflow", "daily", "daily_basic", "margin")
# 按日期分片的类型
_DATE_TYPES = ("top_list", "moneyflow_ind")
# 单文件类型（无 code / date 参数）
_SINGLE_TYPES = ("moneyflow_mkt",)

# 初始化目录
for _d in _CODE_TYPES + _DATE_TYPES:
    (BASE_DIR / _d).mkdir(parents=True, exist_ok=True)
BASE_DIR.mkdir(parents=True, exist_ok=True)


def _path(data_type: str,
          code: Optional[str] = None,
          date: Optional[str] = None) -> Path:
    if data_type in _CODE_TYPES:
        if not code:
            raise ValueError(f"{data_type} 需要提供 code 参数")
        return BASE_DIR / data_type / f"{code}.parquet"
    if data_type in _DATE_TYPES:
        if not date:
            raise ValueError(f"{data_type} 需要提供 date 参数")
        return BASE_DIR / data_type / f"{date}.parquet"
    if data_type in _SINGLE_TYPES:
        return BASE_DIR / f"{data_type}.parquet"
    raise ValueError(f"未知 data_type: {data_type}")


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一 trade_date / date 为 DatetimeIndex"""
    df = df.copy()
    # Tushare 日期列通常叫 trade_date（格式 YYYYMMDD）
    if "trade_date" in df.columns:
        df = df.rename(columns={"trade_date": "date"})
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], format="mixed", dayfirst=False)
        df = df.set_index("date")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    df.sort_index(inplace=True)
    return df


def save(data_type: str,
         df: pd.DataFrame,
         code: Optional[str] = None,
         date: Optional[str] = None) -> None:
    """
    写入 tushare 数据，以 date 为 index，自动去重追加。

    data_type: moneyflow / daily / daily_basic / top_list / margin /
               moneyflow_ind / moneyflow_mkt
    code: 股票代码（code 分片类型必须提供）
    date: 日期字符串 YYYY-MM-DD（date 分片类型必须提供）
    """
    if df is None or df.empty:
        return
    df = _normalize(df)
    p = _path(data_type, code=code, date=date)

    existing = load(data_type, code=code, date=date)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    df.to_parquet(p, engine="pyarrow", compression="snappy")
    logger.debug(f"tushare/{data_type} 已保存: {len(df)} 行  code={code} date={date}")


def load(data_type: str,
         code: Optional[str] = None,
         date: Optional[str] = None,
         start: Optional[str] = None,
         end: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    读取 tushare 数据，index 为 date（DatetimeIndex）。

    start / end: 'YYYY-MM-DD' 过滤范围（仅对 code 分片类型有意义）
    """
    try:
        p = _path(data_type, code=code, date=date)
    except ValueError:
        return None
    if not p.exists():
        return None
    df = pd.read_parquet(p, engine="pyarrow")
    if df.empty:
        return None
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df if not df.empty else None


def exists(data_type: str,
           code: Optional[str] = None,
           date: Optional[str] = None) -> bool:
    try:
        return _path(data_type, code=code, date=date).exists()
    except ValueError:
        return False


def last_date(data_type: str, code: Optional[str] = None) -> Optional[str]:
    """返回最新日期 'YYYY-MM-DD'"""
    if data_type in _SINGLE_TYPES:
        df = load(data_type)
    else:
        df = load(data_type, code=code)
    if df is None or df.empty:
        return None
    return df.index.max().strftime("%Y-%m-%d")


def list_codes(data_type: str) -> list:
    """返回已下载的股票代码列表（code 分片类型）"""
    d = BASE_DIR / data_type
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.parquet"))


def list_dates(data_type: str) -> list:
    """返回已下载的日期列表（date 分片类型）"""
    d = BASE_DIR / data_type
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.parquet"))


def stats() -> dict:
    """返回各类型数据统计信息"""
    result = {}
    all_types = _CODE_TYPES + _DATE_TYPES + _SINGLE_TYPES
    for dt in all_types:
        if dt in _SINGLE_TYPES:
            p = BASE_DIR / f"{dt}.parquet"
            if not p.exists():
                result[dt] = {"count": 0, "rows": 0, "size_mb": 0.0}
                continue
            try:
                df = pd.read_parquet(p, engine="pyarrow")
                result[dt] = {"count": 1, "rows": len(df), "size_mb": p.stat().st_size / 1024 / 1024}
            except Exception:
                result[dt] = {"count": 0, "rows": 0, "size_mb": 0.0}
            continue

        d = BASE_DIR / dt
        if not d.exists():
            result[dt] = {"count": 0, "rows": 0, "size_mb": 0.0}
            continue
        files = list(d.glob("*.parquet"))
        total_rows = 0
        total_size = 0
        for f in files:
            try:
                df = pd.read_parquet(f, engine="pyarrow")
                total_rows += len(df)
                total_size += f.stat().st_size
            except Exception:
                pass
        result[dt] = {
            "count": len(files),
            "rows": total_rows,
            "size_mb": total_size / 1024 / 1024,
        }
    return result
