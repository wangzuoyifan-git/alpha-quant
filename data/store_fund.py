"""
fund 域存储层（Phase 2：ETF/基金画像数据）

目录结构 data/fund/:
  basic.parquet                  ETF 列表（Tushare fund_basic market=E）
  share/{code}.parquet           基金份额（Tushare fund_share，按代码，日频）
  nav/{code}.parquet             基金净值（Tushare fund_nav，按代码，日频）
  index_weight/{index_code}.parquet  指数成分权重（Tushare index_weight，按月）
  etf_index_map.json             ETF→跟踪指数映射（派生 + 人工可覆盖）

接口（与 store_moneyflow 风格一致）:
  save(data_type, df, code=None)
  load(data_type, code=None, start=None, end=None)
  exists(data_type, code=None)
  last_date(data_type, code=None)
  list_codes(data_type)
  load_etf_index_map() / save_etf_index_map(mapping)
"""
import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent / "fund"

_SINGLE_TYPES = ("basic",)
_CODE_TYPES = ("share", "nav", "index_weight")
_DIRS = {
    "basic": BASE_DIR,
    "share": BASE_DIR / "share",
    "nav": BASE_DIR / "nav",
    "index_weight": BASE_DIR / "index_weight",
}
for _d in _DIRS.values():
    _d.mkdir(parents=True, exist_ok=True)

ETF_INDEX_MAP_PATH = BASE_DIR / "etf_index_map.json"


def _path(data_type: str, code: Optional[str] = None) -> Path:
    if data_type in _SINGLE_TYPES:
        return BASE_DIR / f"{data_type}.parquet"
    if data_type in _CODE_TYPES:
        if not code:
            raise ValueError(f"{data_type} 需要提供 code 参数")
        return _DIRS[data_type] / f"{code}.parquet"
    raise ValueError(f"未知 data_type: {data_type}")


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "trade_date" in df.columns:
        df = df.rename(columns={"trade_date": "date"})
    if "日期" in df.columns:
        df = df.rename(columns={"日期": "date"})
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], format="mixed", dayfirst=False)
        df = df.set_index("date")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    df.sort_index(inplace=True)
    return df


def save(data_type: str, df: pd.DataFrame, code: Optional[str] = None) -> None:
    if df is None or df.empty:
        return
    df = _normalize(df)
    p = _path(data_type, code)
    existing = load(data_type, code=code)
    if existing is not None and not existing.empty and data_type in _CODE_TYPES:
        df = pd.concat([existing, df])
        df = df[~df.index.duplicated(keep="last")]
        df.sort_index(inplace=True)
    df.to_parquet(p, engine="pyarrow", compression="snappy")
    logger.debug(f"fund/{data_type} 已保存: {len(df)} 行 code={code}")


def load(data_type: str, code: Optional[str] = None,
         start: Optional[str] = None, end: Optional[str] = None) -> Optional[pd.DataFrame]:
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
    df = load(data_type, code=code)
    return df.index.max().strftime("%Y-%m-%d") if df is not None and not df.empty else None


def list_codes(data_type: str) -> list:
    d = _DIRS.get(data_type)
    if not d or not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.parquet"))


def load_etf_index_map() -> dict:
    if not ETF_INDEX_MAP_PATH.exists():
        return {}
    try:
        return json.loads(ETF_INDEX_MAP_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"etf_index_map.json 读取失败: {e}")
        return {}


def save_etf_index_map(mapping: dict) -> None:
    ETF_INDEX_MAP_PATH.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
