"""
fundamental 域存储层

目录结构:
  data/fundamental/
    profit/   {code}.parquet    季频盈利数据
    balance/  {code}.parquet    季频资产负债
    cashflow/ {code}.parquet    季频现金流
    growth/   {code}.parquet    季频成长能力
    dupont/   {code}.parquet    季频杜邦分析

接口:
  save(code, df, data_type)
  load(code, data_type, start=None, end=None)
  load_asof(code, data_type, as_of=None, history=None)  # point-in-time
  exists(code, data_type)
  last_date(code, data_type)
"""
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent / "fundamental"

VALID_TYPES = ("profit", "balance", "cashflow", "growth", "dupont")

# 初始化目录（A股保持原路径；港股落在 data/fundamental/hk/ 下）
for _t in VALID_TYPES:
    (BASE_DIR / _t).mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "hk" / _t).mkdir(parents=True, exist_ok=True)


def _base(market: str = "cn") -> Path:
    return BASE_DIR if market == "cn" else BASE_DIR / market


def _path(code: str, data_type: str, market: str = "cn") -> Path:
    if data_type not in VALID_TYPES:
        raise ValueError(f"data_type 必须是 {VALID_TYPES} 之一，got: {data_type}")
    return _base(market) / data_type / f"{code}.parquet"


def save(code: str, df: pd.DataFrame, data_type: str,
         market: str = "cn") -> None:
    """
    写入季频财务数据，以 statDate（报告期）为 index，自动去重。
    code: 纯数字代码，如 '600519' / 港股 '00700'
    data_type: profit / balance / cashflow / growth / dupont
    market: cn / hk
    """
    if df is None or df.empty:
        return
    df = df.copy()

    # 统一 index 为 statDate（报告期）
    if "statDate" in df.columns:
        df["statDate"] = pd.to_datetime(df["statDate"])
        df = df.set_index("statDate")
    elif "stat_date" in df.columns:
        df["stat_date"] = pd.to_datetime(df["stat_date"])
        df = df.set_index("stat_date")
    elif "REPORT_DATE" in df.columns:
        df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
        df = df.set_index("REPORT_DATE")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.index.name = "stat_date"

    # 去掉 code 列（冗余）
    for c in ("code", "SECUCODE", "SECURITY_CODE", "ORG_CODE"):
        if c in df.columns:
            df = df.drop(columns=[c])

    # 数值列转换
    for col in df.columns:
        if col not in ("pubDate", "pub_date", "SECURITY_NAME_ABBR",
                       "DATE_TYPE_CODE", "CURRENCY", "IS_CNY_CODE",
                       "START_DATE", "FISCAL_YEAR"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    p = _path(code, data_type, market=market)
    existing = load(code, data_type, market=market)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    df.to_parquet(p, engine="pyarrow", compression="snappy")
    logger.debug(f"fundamental/{market}/{data_type}/{code} 已保存: {len(df)} 期")


def load(code: str,
         data_type: str,
         start: Optional[str] = None,
         end: Optional[str] = None,
         market: str = "cn") -> Optional[pd.DataFrame]:
    """
    读取季频财务数据，index 为 stat_date（报告期）。
    start/end 格式 'YYYY-MM-DD'，按报告期过滤。
    """
    p = _path(code, data_type, market=market)
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


# 财报默认披露滞后（无 pubDate 时的保守回退，防止报告期即视为可用）
_DEFAULT_PUB_LAG_DAYS = 90


def _publication_dates(df: pd.DataFrame) -> pd.Series:
    """返回每行的可见日期（pubDate），缺失时按报告期 + 90 天保守估算。"""
    fallback = pd.Series(df.index + pd.Timedelta(days=_DEFAULT_PUB_LAG_DAYS),
                         index=df.index)
    for col in ("pubDate", "pub_date"):
        if col in df.columns:
            pub = pd.to_datetime(df[col], errors="coerce")
            return pub.where(pub.notna(), fallback)
    return fallback


def load_asof(code: str,
              data_type: str,
              as_of=None,
              history: Optional[int] = None,
              market: str = "cn") -> Optional[pd.DataFrame]:
    """Point-in-time 读取：只返回 ``as_of`` 当时已公告的财报。

    :param as_of: 回测日（'YYYY-MM-DD' / Timestamp）。None 表示取最新数据，
                  保持扫描器原有行为。
    :param history: 返回最近 N 期（含 as_of 当日可见的最新一期）。
                    None 或 <=0 时只返回最新一期，供“最新快照”类策略使用。
    提示：需要多期数据计算 TTM 时应显式传 history（如 history=8）。
    """
    df = load(code, data_type, market=market)
    if df is None or df.empty:
        return None
    df = df.sort_index()
    if as_of is not None:
        cutoff = pd.Timestamp(as_of)
        visible = _publication_dates(df) <= cutoff
        df = df[visible]
        if df.empty:
            return None
    if history is None or int(history) <= 0:
        return df.iloc[[-1]]
    return df.iloc[-int(history):]


def exists(code: str, data_type: str, market: str = "cn") -> bool:
    try:
        return _path(code, data_type, market=market).exists()
    except ValueError:
        return False


def last_date(code: str, data_type: str, market: str = "cn") -> Optional[str]:
    """返回最新报告期 'YYYY-MM-DD'，无数据返回 None"""
    df = load(code, data_type, market=market)
    if df is None or df.empty:
        return None
    return df.index.max().strftime("%Y-%m-%d")


def list_codes(data_type: str, market: str = "cn") -> list:
    """返回指定类型已下载的股票代码列表"""
    d = _base(market) / data_type
    if not d.exists():
        return []
    return [p.stem for p in d.glob("*.parquet")]


def stats(market: str = "cn") -> dict:
    """返回各类型数据统计"""
    result = {}
    for t in VALID_TYPES:
        d = _base(market) / t
        files = list(d.glob("*.parquet")) if d.exists() else []
        size_mb = round(sum(f.stat().st_size for f in files) / 1024 / 1024, 1)
        result[t] = {"count": len(files), "size_mb": size_mb}
    return result
