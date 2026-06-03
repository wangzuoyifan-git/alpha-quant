"""
本地行情存储层（多市场多资产版）

目录结构:
  data/market/
    cn/                中国市场
      stock/           A股日线（含估值/换手率等）
      index/           A股指数
      etf/             A股ETF
    hk/                港股
      stock/
      index/
    us/                美股
      stock/
      index/
    ipo_dates.json     全市场上市日期索引

接口示例:
  store.load("600519")                          # A股（默认 cn/stock）
  store.load("00700",  market="hk")             # 港股
  store.load("AAPL",   market="us")             # 美股
  store.load("000300", market="cn", asset_type="index")  # A股指数
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

# ============================================================
# 目录配置
# ============================================================

BASE_DIR = Path(__file__).parent / "market"

# 支持的市场和资产类型（可随时扩展，不需要改接口）
MARKET_ASSET_TYPES: Dict[str, List[str]] = {
    "cn": ["stock", "index", "etf"],
    "hk": ["stock", "index"],
    "us": ["stock", "index"],
}

# 各市场各类型的标准列定义
COLUMNS: Dict[str, Dict[str, List[str]]] = {
    "cn": {
        "stock": [
            "open", "high", "low", "close", "preclose",
            "volume", "amount", "turn", "trade_status",
            "pct_chg", "pe_ttm", "pb_mrq", "ps_ttm", "pcf_ttm", "is_st",
        ],
        "index": [
            "open", "high", "low", "close", "preclose",
            "volume", "amount", "pct_chg",
        ],
        "etf": [
            "open", "high", "low", "close", "preclose",
            "volume", "amount", "pct_chg",
        ],
    },
    "hk": {
        "stock": [
            "open", "high", "low", "close", "preclose",
            "volume", "amount", "pct_chg",
        ],
        "index": [
            "open", "high", "low", "close", "preclose",
            "volume", "amount", "pct_chg",
        ],
    },
    "us": {
        "stock": [
            "open", "high", "low", "close", "preclose",
            "volume", "amount", "pct_chg",
        ],
        "index": [
            "open", "high", "low", "close", "preclose",
            "volume", "amount", "pct_chg",
        ],
    },
}

# 字符串类型列（不做数值转换）
STR_COLS = {"trade_status", "is_st"}

# 上市日期文件（全市场统一）
IPO_FILE = BASE_DIR / "ipo_dates.json"

# 初始化所有目录
for _market, _types in MARKET_ASSET_TYPES.items():
    for _atype in _types:
        (BASE_DIR / _market / _atype).mkdir(parents=True, exist_ok=True)


# ============================================================
# 内部工具
# ============================================================

def _dir(market: str, asset_type: str) -> Path:
    return BASE_DIR / market / asset_type


def _path(code: str, market: str, asset_type: str) -> Path:
    return _dir(market, asset_type) / f"{code}.parquet"


def _cols(market: str, asset_type: str) -> List[str]:
    return COLUMNS.get(market, {}).get(asset_type, [
        "open", "high", "low", "close", "preclose",
        "volume", "amount", "pct_chg",
    ])


def _normalize(df: pd.DataFrame, market: str, asset_type: str) -> pd.DataFrame:
    """统一格式：date 设为 index，补齐/规范列"""
    df = df.copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.index.name = "date"

    cols = _cols(market, asset_type)
    for col in cols:
        if col not in df.columns:
            df[col] = float("nan")
    for col in cols:
        if col not in STR_COLS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[cols]


# ============================================================
# K 线存取（核心接口）
# ============================================================

def save(code: str, df: pd.DataFrame,
         asset_type: str = "stock",
         market: str = "cn",
         force: bool = False) -> None:
    """
    写入/追加 K 线，自动去重排序。
    market: cn / hk / us（默认 cn）
    asset_type: stock / index / etf（默认 stock）
    force: True 时允许覆盖已有日期的数据，False 时保留已有数据优先（DS2）
    """
    if df is None or df.empty:
        return
    df = _normalize(df, market, asset_type)
    existing = load(code, asset_type=asset_type, market=market)
    if existing is not None and not existing.empty:
        df = pd.concat([existing, df])
        keep = "last" if force else "first"
        df = df[~df.index.duplicated(keep=keep)]
    df.sort_index(inplace=True)
    df.to_parquet(_path(code, market, asset_type),
                  engine="pyarrow", compression="snappy")


def load(code: str,
         asset_type: str = "stock",
         market: str = "cn",
         start: Optional[str] = None,
         end: Optional[str] = None) -> Optional[pd.DataFrame]:
    """
    读取本地 K 线，返回 DataFrame（index=date），不存在返回 None。
    market: cn / hk / us（默认 cn）
    """
    p = _path(code, market, asset_type)
    if not p.exists():
        return None
    df = pd.read_parquet(p, engine="pyarrow")
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    return df if not df.empty else None


def exists(code: str,
           asset_type: str = "stock",
           market: str = "cn") -> bool:
    return _path(code, market, asset_type).exists()


def last_date(code: str,
              asset_type: str = "stock",
              market: str = "cn") -> Optional[str]:
    """返回本地最新日期 'YYYY-MM-DD'，无数据返回 None"""
    df = load(code, asset_type=asset_type, market=market)
    if df is None or df.empty:
        return None
    return df.index.max().strftime("%Y-%m-%d")


def list_codes(asset_type: str = "stock",
               market: str = "cn") -> List[str]:
    """返回指定市场+类型的所有本地代码"""
    d = _dir(market, asset_type)
    if not d.exists():
        return []
    return [p.stem for p in d.glob("*.parquet")]


def stats() -> dict:
    """返回全市场本地库统计"""
    result = {}
    grand_total = {"count": 0, "rows": 0, "size_mb": 0.0}

    for market, atypes in MARKET_ASSET_TYPES.items():
        result[market] = {}
        for atype in atypes:
            d = _dir(market, atype)
            files = list(d.glob("*.parquet")) if d.exists() else []
            rows = 0
            for f in files:
                try:
                    rows += len(pd.read_parquet(
                        f, engine="pyarrow", columns=["close"]))
                except Exception:
                    pass
            size_mb = round(
                sum(f.stat().st_size for f in files) / 1024 / 1024, 1)
            result[market][atype] = {
                "count": len(files),
                "rows": rows,
                "size_mb": size_mb,
            }
            grand_total["count"]   += len(files)
            grand_total["rows"]    += rows
            grand_total["size_mb"] += size_mb

    grand_total["size_mb"] = round(grand_total["size_mb"], 1)
    result["total"] = grand_total
    return result


# ============================================================
# 上市日期索引（全市场统一）
# ============================================================

def load_ipo_dates() -> Dict[str, dict]:
    """
    读取上市日期索引。
    返回 {code: {"ipo_date": "YYYY-MM-DD", "market": "cn/hk/us",
                 "type": "stock/index/etf", "name": "..."}}
    注意：不同市场可能存在相同代码（如 cn/000001 和 hk/000001），
    key 格式为 "{market}:{code}"，如 "cn:600519"、"hk:00700"
    """
    if not IPO_FILE.exists():
        return {}
    with open(IPO_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_ipo_dates(ipo: Dict[str, dict]) -> None:
    """增量合并保存上市日期，key 格式为 '{market}:{code}'"""
    existing = load_ipo_dates()
    existing.update(ipo)
    with open(IPO_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def get_ipo_date(code: str, market: str = "cn") -> Optional[str]:
    """获取上市日期，返回 'YYYY-MM-DD' 或 None"""
    key = f"{market}:{code}"
    # 兼容旧格式（只有 code 没有 market 前缀）
    data = load_ipo_dates()
    return (data.get(key) or data.get(code) or {}).get("ipo_date")


def trading_days_since_ipo(code: str,
                            market: str = "cn",
                            as_of: str = None) -> Optional[int]:
    ipo = get_ipo_date(code, market)
    if not ipo:
        return None
    end = datetime.strptime(as_of, "%Y-%m-%d") if as_of else datetime.now()
    return (end - datetime.strptime(ipo, "%Y-%m-%d")).days


# ============================================================
# 向后兼容：旧代码用 KLINE_DIR / DIRS 的地方不报错
# ============================================================
KLINE_DIR = BASE_DIR
DIRS = {
    atype: BASE_DIR / "cn" / atype
    for atype in MARKET_ASSET_TYPES["cn"]
}
