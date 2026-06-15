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
    "industry_classify": BASE_DIR,   # 申万三级分类树（Tushare SW2021）
    "industry_member":   BASE_DIR,   # 股票→三级行业映射（index_member_all）
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


# ── 申万三级行业辅助查询（2026-06 新增，L2-R5）──────────────

# level → industry_member 中对应的代码/名称列名
_LEVEL_COL = {
    "L1": ("l1_code", "l1_name"),
    "L2": ("l2_code", "l2_name"),
    "L3": ("l3_code", "l3_name"),
}


def get_member_stocks(industry_codes: list, level: str = "L1") -> list:
    """
    返回属于指定行业代码（任意层级）的股票代码列表（6位，不含后缀）。

    :param industry_codes: 申万行业代码列表，支持带/不带 .SI 后缀
    :param level: L1/L2/L3
    :return: 股票代码列表（如 ["000001", "600519"]），数据缺失返回 []
    """
    df = load("industry_member")
    if df is None or df.empty or level not in _LEVEL_COL:
        return []
    code_col, _ = _LEVEL_COL[level]
    if code_col not in df.columns or "ts_code" not in df.columns:
        return []
    # 规范化：统一去掉 .SI 后缀比较
    targets = {str(c).split(".")[0] for c in industry_codes}
    mask = df[code_col].astype(str).str.split(".").str[0].isin(targets)
    sel = df[mask]
    # ts_code 形如 000001.SZ → 取 000001
    return sorted({str(c).split(".")[0] for c in sel["ts_code"]})


def list_industry_codes(level: str = "L1") -> list:
    """返回指定层级的所有申万行业代码（含名称），用于校验和展示。"""
    df = load("industry_classify")
    if df is None or df.empty:
        return []
    sel = df[df["level"] == level] if "level" in df.columns else df
    return sel[["index_code", "industry_name"]].to_dict("records")
