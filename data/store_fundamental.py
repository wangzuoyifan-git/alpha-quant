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

# 各类型的 statDate 字段（报告期，作为 index）
# 初始化目录
for _t in VALID_TYPES:
    (BASE_DIR / _t).mkdir(parents=True, exist_ok=True)


def _path(code: str, data_type: str) -> Path:
    if data_type not in VALID_TYPES:
        raise ValueError(f"data_type 必须是 {VALID_TYPES} 之一，got: {data_type}")
    return BASE_DIR / data_type / f"{code}.parquet"


def save(code: str, df: pd.DataFrame, data_type: str) -> None:
    """
    写入季频财务数据，以 statDate（报告期）为 index，自动去重。
    code: 纯数字代码，如 '600519'
    data_type: profit / balance / cashflow / growth / dupont
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
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.index.name = "stat_date"

    # 去掉 code 列（冗余）
    if "code" in df.columns:
        df = df.drop(columns=["code"])

    # 数值列转换
    for col in df.columns:
        if col not in ("pubDate", "pub_date"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

    p = _path(code, data_type)
    existing = load(code, data_type)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    df.to_parquet(p, engine="pyarrow", compression="snappy")
    logger.debug(f"fundamental/{data_type}/{code} 已保存: {len(df)} 季")


def load(code: str,
         data_type: str,
         start: Optional[str] = None,
         end: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    读取季频财务数据，index 为 stat_date（报告期）。
    start/end 格式 'YYYY-MM-DD'，按报告期过滤。
    """
    p = _path(code, data_type)
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


def exists(code: str, data_type: str) -> bool:
    try:
        return _path(code, data_type).exists()
    except ValueError:
        return False


def last_date(code: str, data_type: str) -> Optional[str]:
    """返回最新报告期 'YYYY-MM-DD'，无数据返回 None"""
    df = load(code, data_type)
    if df is None or df.empty:
        return None
    return df.index.max().strftime("%Y-%m-%d")


def list_codes(data_type: str) -> list:
    """返回指定类型已下载的股票代码列表"""
    d = BASE_DIR / data_type
    if not d.exists():
        return []
    return [p.stem for p in d.glob("*.parquet")]


def stats() -> dict:
    """返回各类型数据统计"""
    result = {}
    for t in VALID_TYPES:
        d = BASE_DIR / t
        files = list(d.glob("*.parquet")) if d.exists() else []
        size_mb = round(sum(f.stat().st_size for f in files) / 1024 / 1024, 1)
        result[t] = {"count": len(files), "size_mb": size_mb}
    return result
