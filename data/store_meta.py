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


_STOCK_L1_CACHE: Optional[dict] = None


def get_stock_l1_industry(code: str) -> Optional[str]:
    """
    反查股票所属申万一级行业代码（6位，无后缀）。

    :param code: 股票代码（带/不带后缀均可，如 "600519" 或 "600519.SH"）
    :return: L1 行业代码（6位，如 "801780"）；数据缺失或未匹配返回 None
    """
    global _STOCK_L1_CACHE
    if _STOCK_L1_CACHE is None:
        df = load("industry_member")
        if df is None or df.empty or "ts_code" not in df.columns or "l1_code" not in df.columns:
            _STOCK_L1_CACHE = {}
        else:
            _STOCK_L1_CACHE = {
                str(ts).split(".")[0]: str(l1).split(".")[0]
                for ts, l1 in zip(df["ts_code"], df["l1_code"])
            }
    code6 = str(code).split(".")[0]
    return _STOCK_L1_CACHE.get(code6)


def list_industry_codes(level: str = "L1") -> list:
    """返回指定层级的所有申万行业代码（含名称），用于校验和展示。"""
    df = load("industry_classify")
    if df is None or df.empty:
        return []
    sel = df[df["level"] == level] if "level" in df.columns else df
    return sel[["index_code", "industry_name"]].to_dict("records")


def map_to_l1(industry_codes: list[str], level: str = "L2") -> list[str]:
    """
    将 L2/L3 申万行业代码（index_code，如 "801081.SI"）映射到其 L1 父级代码。

    用途：L2/L3 粒度的行业池选出后，仍需映射到 L1 行业ETF白名单
    （SW_L1_ETF_MAP 按 L1 键）。基于 industry_classify 树的 parent_code 链。

    L1 直接原样返回（去重）。数据缺失或无法映射的代码被跳过。

    :param industry_codes: 子级行业代码列表（带/不带 .SI 后缀均可）
    :param level: 输入代码的层级 L2/L3
    :return: 去重的 L1 行业代码列表（带 .SI 后缀，如 ["801080.SI"]）
    """
    if level == "L1":
        # 规范化为带 .SI 后缀
        seen, out = set(), []
        for c in industry_codes:
            k = c if c.endswith(".SI") else c.split(".")[0] + ".SI"
            if k not in seen:
                seen.add(k); out.append(k)
        return out

    df = load("industry_classify")
    if df is None or df.empty or "parent_code" not in df.columns:
        return []

    # index_code(801xxx.SI) → industry_code(6位) 与反向映射
    code6_by_index = dict(zip(df["index_code"].astype(str),
                              df["industry_code"].astype(str)))
    index_by_code6 = dict(zip(df["industry_code"].astype(str),
                              df["index_code"].astype(str)))
    parent_by_code6 = dict(zip(df["industry_code"].astype(str),
                               df["parent_code"].astype(str)))
    level_by_code6 = dict(zip(df["industry_code"].astype(str),
                              df["level"].astype(str)))

    seen, out = set(), []
    for raw in industry_codes:
        idx = raw if raw.endswith(".SI") else raw.split(".")[0] + ".SI"
        code6 = code6_by_index.get(idx)
        if code6 is None:
            continue
        # 沿 parent_code 链上溯到 L1
        guard = 0
        while code6 and level_by_code6.get(code6) != "L1" and guard < 5:
            code6 = parent_by_code6.get(code6)
            guard += 1
        if not code6 or level_by_code6.get(code6) != "L1":
            continue
        l1_idx = index_by_code6.get(code6)
        if l1_idx and l1_idx not in seen:
            seen.add(l1_idx); out.append(l1_idx)
    return out


# ─── 申万L1行业 → 代表性行业ETF 映射表 ───────────────────────────────────────
# 每个行业选 1~3 只流动性较好的代表性行业ETF（优先老牌主流产品）。
# 用途：L2 行业池选出后，映射出对应ETF白名单，供 L3 etf_momentum 在白名单内择时。
# 数据来源：本地 ETF 数据验证（2026-06），如本地无该ETF则自动跳过（见 get_industry_etfs）。
SW_L1_ETF_MAP: dict[str, list[str]] = {
    "801010.SI": ["159570", "159007"],          # 农林牧渔 → 农业ETF
    "801030.SI": ["159870", "159129"],           # 基础化工
    "801040.SI": ["515210"],                     # 钢铁
    "801050.SI": ["512400", "159029"],           # 有色金属
    "801080.SI": ["512480", "159310", "159153"], # 电子/半导体/芯片
    "801110.SI": ["159192", "159176"],           # 家用电器
    "801120.SI": ["512690", "159843"],           # 食品饮料（白酒龙头+食品ETF）
    "801130.SI": ["512730"],                     # 纺织服饰（用纺织ETF）
    "801140.SI": ["159802"],                     # 轻工制造
    "801150.SI": ["512010", "159929"],           # 医药生物（医药ETF）
    "801160.SI": ["159146", "159160"],           # 公用事业
    "801170.SI": ["159662"],                     # 交通运输
    "801180.SI": ["159707", "159768"],           # 房地产
    "801200.SI": ["159898"],                     # 商贸零售
    "801210.SI": ["159766"],                     # 社会服务（旅游ETF）
    "801710.SI": ["159745", "159787"],           # 建筑材料
    "801720.SI": ["159745"],                     # 建筑装饰（复用建材ETF）
    "801730.SI": ["515030", "159959"],           # 电力设备/新能源
    "801740.SI": ["512660", "512680"],           # 国防军工
    "801750.SI": ["512720", "159586"],           # 计算机
    "801760.SI": ["512980", "159805"],           # 传媒
    "801770.SI": ["515050", "159938"],           # 通信（5G ETF）
    "801780.SI": ["512700", "159253"],           # 银行
    "801790.SI": ["512880", "159934"],           # 非银金融（券商ETF）
    "801880.SI": ["159179", "159183"],           # 汽车/新能源汽车
    "801890.SI": ["159977", "159138"],           # 机械设备
    "801950.SI": ["515220"],                     # 煤炭
    "801960.SI": ["159018", "159019"],           # 石油石化
    "801970.SI": ["512580", "159861"],           # 环保
    "801980.SI": ["159887"],                     # 美容护理（用800银行替代）
}


def get_industry_etfs(industry_codes: list[str],
                      available_codes: set[str] = None) -> list[str]:
    """
    给定申万L1行业代码列表，返回对应行业ETF代码列表（去重，只含本地有数据的）。

    Args:
        industry_codes: 申万L1行业代码（如 ["801080.SI", "801780.SI"]）
        available_codes: 本地可用ETF代码集合（None=不过滤，全部返回）

    Returns:
        去重的ETF代码列表（6位纯数字）
    """
    result = []
    seen = set()
    for ind in industry_codes:
        # 兼容带/不带 .SI 后缀
        key = ind if ind in SW_L1_ETF_MAP else ind.split(".")[0] + ".SI"
        for etf in SW_L1_ETF_MAP.get(key, []):
            if etf not in seen:
                if available_codes is None or etf in available_codes:
                    result.append(etf)
                    seen.add(etf)
    return result
