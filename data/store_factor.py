"""
factor 域存储层

目录结构:
  data/factor/
    moneyflow/ {code}.parquet   个股资金流向（日频）
    moneyflow_sector.parquet    行业资金流向（日频）
    moneyflow_market.parquet    大盘资金流向（日频）
    northbound.parquet          北向资金（日频）

接口:
  save(data_type, df, code=None)
  load(data_type, code=None, start=None, end=None)
  exists(data_type, code=None)
  last_date(data_type, code=None)
"""
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent / "factor"

# 单文件类型（无 code 参数）
_SINGLE_FILE_TYPES = ("moneyflow_sector", "moneyflow_market", "northbound")
# 多文件类型（需要 code 参数）
_MULTI_FILE_TYPES = ("moneyflow",)

# 初始化目录
(BASE_DIR / "moneyflow").mkdir(parents=True, exist_ok=True)
BASE_DIR.mkdir(parents=True, exist_ok=True)


def _path(data_type: str, code: Optional[str] = None) -> Path:
    if data_type == "moneyflow":
        if not code:
            raise ValueError("moneyflow 需要提供 code 参数")
        return BASE_DIR / "moneyflow" / f"{code}.parquet"
    return BASE_DIR / f"{data_type}.parquet"


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一 date 为 DatetimeIndex"""
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    elif "日期" in df.columns:
        df = df.rename(columns={"日期": "date"})
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    return df


def save(data_type: str, df: pd.DataFrame, code: Optional[str] = None) -> None:
    """
    写入 factor 数据，以 date 为 index，自动去重追加。
    data_type: moneyflow / moneyflow_sector / moneyflow_market / northbound
    code: moneyflow 时必须提供股票代码
    """
    if df is None or df.empty:
        return
    df = _normalize(df)
    p = _path(data_type, code)

    existing = load(data_type, code=code)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    df.to_parquet(p, engine="pyarrow", compression="snappy")
    logger.debug(f"factor/{data_type} 已保存: {len(df)} 行")


def load(data_type: str,
         code: Optional[str] = None,
         start: Optional[str] = None,
         end: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    读取 factor 数据，index 为 date。
    """
    try:
        p = _path(data_type, code)
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


def exists(data_type: str, code: Optional[str] = None) -> bool:
    try:
        return _path(data_type, code).exists()
    except ValueError:
        return False


def last_date(data_type: str, code: Optional[str] = None) -> Optional[str]:
    """返回最新日期 'YYYY-MM-DD'"""
    df = load(data_type, code=code)
    if df is None or df.empty:
        return None
    return df.index.max().strftime("%Y-%m-%d")


def list_moneyflow_codes() -> list:
    """返回已下载个股资金流向的代码列表"""
    d = BASE_DIR / "moneyflow"
    if not d.exists():
        return []
    return [p.stem for p in d.glob("*.parquet")]
