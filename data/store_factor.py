"""
factor 域存储层（重新定位：计算型因子）

台阶二重构后，factor 域专用于"计算/衍生"的因子值（动量/价值/质量等）。
原始资金流数据已迁往 moneyflow 域（store_moneyflow.py）。

目录结构:
  data/factor/
    {factor_name}.parquet            全市场单文件因子
    {factor_name}/{code}.parquet     个股因子（按代码分片）

接口:
  save(factor_name, df, code=None)
  load(factor_name, code=None, start=None, end=None)
  exists(factor_name, code=None)
  last_date(factor_name, code=None)
"""
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent / "factor"
BASE_DIR.mkdir(parents=True, exist_ok=True)

# 暂无已知因子类型（台阶二后续填充）
VALID_TYPES: tuple = ()


def _path(factor_name: str, code: Optional[str] = None) -> Path:
    if code:
        return BASE_DIR / factor_name / f"{code}.parquet"
    return BASE_DIR / f"{factor_name}.parquet"


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    return df


def save(factor_name: str, df: pd.DataFrame, code: Optional[str] = None) -> None:
    if df is None or df.empty:
        return
    df = _normalize(df)
    p = _path(factor_name, code)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = load(factor_name, code=code)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    df.to_parquet(p, engine="pyarrow", compression="snappy")
    logger.debug(f"factor/{factor_name} 已保存: {len(df)} 行")


def load(factor_name: str,
         code: Optional[str] = None,
         start: Optional[str] = None,
         end: Optional[str] = None) -> Optional[pd.DataFrame]:
    p = _path(factor_name, code)
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


def exists(factor_name: str, code: Optional[str] = None) -> bool:
    return _path(factor_name, code).exists()


def last_date(factor_name: str, code: Optional[str] = None) -> Optional[str]:
    df = load(factor_name, code=code)
    if df is None or df.empty:
        return None
    return df.index.max().strftime("%Y-%m-%d")
