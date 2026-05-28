"""
macro 域存储层

目录结构:
  data/macro/
    bond_yield.parquet    国债收益率（日频）
    m2.parquet            M2货币供应（月频）
    cpi_ppi.parquet       CPI/PPI（月频）
    pmi.parquet           PMI（月频）

接口:
  save(data_type, df)
  load(data_type, start=None, end=None)
  exists(data_type)
  last_date(data_type)
"""
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent / "macro"
BASE_DIR.mkdir(parents=True, exist_ok=True)

VALID_TYPES = ("bond_yield", "m2", "cpi_ppi", "pmi")


def _path(data_type: str) -> Path:
    if data_type not in VALID_TYPES:
        raise ValueError(f"data_type 必须是 {VALID_TYPES} 之一，got: {data_type}")
    return BASE_DIR / f"{data_type}.parquet"


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一 date/月份 为 DatetimeIndex"""
    df = df.copy()
    date_cols = ["date", "日期", "月份", "时间"]
    for col in date_cols:
        if col in df.columns:
            df = df.rename(columns={col: "date"})
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            df = df.set_index("date")
            break
    else:
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, errors="coerce")
            df = df[df.index.notna()]
    df.index.name = "date"
    return df


def save(data_type: str, df: pd.DataFrame) -> None:
    """
    写入宏观数据，以 date 为 index，自动去重追加。
    data_type: bond_yield / m2 / cpi_ppi / pmi
    """
    if df is None or df.empty:
        return
    df = _normalize(df)
    p = _path(data_type)

    existing = load(data_type)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    df.to_parquet(p, engine="pyarrow", compression="snappy")
    logger.debug(f"macro/{data_type} 已保存: {len(df)} 行")


def load(data_type: str,
         start: Optional[str] = None,
         end: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    读取宏观数据，index 为 date。
    """
    p = _path(data_type)
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


def exists(data_type: str) -> bool:
    try:
        return _path(data_type).exists()
    except ValueError:
        return False


def last_date(data_type: str) -> Optional[str]:
    """返回最新日期 'YYYY-MM-DD'"""
    df = load(data_type)
    if df is None or df.empty:
        return None
    return df.index.max().strftime("%Y-%m-%d")
