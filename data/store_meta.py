"""
meta 域存储层

目录结构:
  data/meta/
    industry_csrc.parquet       证监会行业分类
    industry_sw.parquet         申万行业分类（股票→行业映射）
    sw_index/ {code}.parquet    申万行业指数日线
    calendar.parquet            交易日历

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

BASE_DIR = Path(__file__).parent / "meta"

# 各子目录
_DIRS = {
    "industry_csrc": BASE_DIR,
    "industry_sw":   BASE_DIR,
    "sw_index":      BASE_DIR / "sw_index",
    "calendar":      BASE_DIR,
}

# 初始化目录
for _d in _DIRS.values():
    _d.mkdir(parents=True, exist_ok=True)


def _path(data_type: str, code: Optional[str] = None) -> Path:
    """返回 parquet 文件路径"""
    if data_type == "sw_index":
        if not code:
            raise ValueError("sw_index 需要提供 code 参数")
        return BASE_DIR / "sw_index" / f"{code}.parquet"
    # 单文件类型
    return BASE_DIR / f"{data_type}.parquet"


def save(data_type: str, df: pd.DataFrame, code: Optional[str] = None) -> None:
    """
    写入 meta 数据，自动去重排序。
    data_type: industry_csrc / industry_sw / sw_index / calendar
    code: sw_index 时必须提供行业代码
    """
    if df is None or df.empty:
        return
    p = _path(data_type, code)

    if data_type == "sw_index":
        # 日线数据：date 为 index，追加去重
        df = df.copy()
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        elif not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        df.index.name = "date"

        existing = load(data_type, code=code)
        if existing is not None and not existing.empty:
            df = pd.concat([existing, df])
            df = df[~df.index.duplicated(keep="last")]
        df.sort_index(inplace=True)
    elif data_type == "calendar":
        df = df.copy()
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        df.index.name = "date"
        df.sort_index(inplace=True)

    df.to_parquet(p, engine="pyarrow", compression="snappy")
    logger.debug(f"meta/{data_type} 已保存: {len(df)} 行 -> {p}")


def load(data_type: str,
         code: Optional[str] = None,
         start: Optional[str] = None,
         end: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    读取 meta 数据，不存在返回 None。
    sw_index 支持 start/end 过滤。
    """
    p = _path(data_type, code)
    if not p.exists():
        return None
    df = pd.read_parquet(p, engine="pyarrow")
    if df.empty:
        return None
    if data_type in ("sw_index", "calendar") and start:
        df = df[df.index >= pd.Timestamp(start)]
    if data_type in ("sw_index", "calendar") and end:
        df = df[df.index <= pd.Timestamp(end)]
    return df if not df.empty else None


def exists(data_type: str, code: Optional[str] = None) -> bool:
    try:
        return _path(data_type, code).exists()
    except ValueError:
        return False


def last_date(data_type: str, code: Optional[str] = None) -> Optional[str]:
    """返回最新日期 'YYYY-MM-DD'，仅对有日期索引的类型有效"""
    df = load(data_type, code=code)
    if df is None or df.empty:
        return None
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index.max().strftime("%Y-%m-%d")
    return None


def list_sw_index_codes() -> list:
    """返回已下载的申万行业指数代码列表"""
    d = BASE_DIR / "sw_index"
    if not d.exists():
        return []
    return [p.stem for p in d.glob("*.parquet")]
