"""
agents/smart_money_agent.py — 资金主体识别与跨期画像（聪明钱画像）

定位（描述性行为追踪，非预测信号）：
  回答两个问题——「谁在买卖」与「他们一贯怎么做」。把龙虎榜席位、北向个股持股、
  融资盘、大宗交易串成三大支柱：
    ① 席位身份与画像：身份规则标签 + 上榜频次 / 累计净买卖 / 上榜后 N 日胜率 /
       平均持有周期 / 偏好板块
    ② 北向跨期行为：持股比例变动 streak、集中度变化、与价格背离
    ③ 多源资金合力 / 分歧：北向 × 机构席位 × 融资盘 × 大宗买方

数据来源（data/moneyflow/）：
  top_inst    龙虎榜席位明细（Tushare top_inst，按交易日分片）
  hk_hold     北向个股持股（Tushare hk_hold，按代码；普通积分账户为季度披露）
  margin      融资融券（Tushare margin_detail，按代码）
  block_trade 大宗交易（Tushare block_trade，按交易日分片）
  market/stock K 线（data/market，后复权）—— 用于上榜后 N 日胜率与持有周期

持久化档案：data/smart_money/seats.json（append/update，幂等，可按需维护）

核心约束（与开源 skill-smart-money-profiler 对齐）：
  - 席位标签来自规则匹配，不等于官方认定，统一带 label_basis
  - 胜率 / 持有周期必须标注交易日窗口与样本数
  - 空数据如实标注（方法 + 窗口），不估算
  - 不产因子 / 不做拥挤评级 / 不做单日全市场复盘 / 不给买卖指令
"""
import json
import logging
import statistics
from datetime import datetime
from pathlib import Path

import pandas as pd

import data.store_moneyflow as store_mf

logger = logging.getLogger(__name__)

HORIZONS = (5, 10, 20)
DEFAULT_WINDOW_MONTHS = 12

_BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "smart_money"
ARCHIVE_PATH = _BASE_DIR / "seats.json"
SEAT_ALIAS_PATH = _BASE_DIR / "seat_aliases.json"

DISCLAIMER = "本报告基于公开数据与规则化分析生成，仅供研究参考，不构成任何投资建议。"

LABEL_INST = "机构专用席位"
LABEL_NORTH = "陆股通/外资通道"
LABEL_QUANT = "量化/程序化席位"
LABEL_HOT = "知名游资营业部"
LABEL_BRANCH = "普通营业部"
LABEL_UNKNOWN = "未分类"

# 内置别名字典（推断标签，可被 data/smart_money/seat_aliases.json 覆盖/扩展）
_DEFAULT_ALIAS_RULES = [
    {"match": "拉萨", "label": LABEL_QUANT, "tag": "拉萨通道(推断)"},
    {"match": "东方财富证券", "label": LABEL_QUANT, "tag": "东财通道(推断)"},
]


# ═══════════════════════════════════════════════════════════════
# 基础工具
# ═══════════════════════════════════════════════════════════════

def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _norm_code6(code) -> str:
    return str(code).split(".")[0]


def _window_start(months: int, end: str) -> str:
    ts = pd.Timestamp(end) - pd.DateOffset(months=max(int(months), 1))
    return ts.strftime("%Y-%m-%d")


def parse_horizons(value) -> tuple:
    """把 '5,10,20' / [5,10] / None 解析为 int 元组。"""
    if value is None:
        return HORIZONS
    seq = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = []
    for v in seq:
        try:
            n = int(str(v).strip())
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return tuple(out) or HORIZONS


def _safe_float(v):
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════
# ① 席位身份识别（规则匹配）
# ═══════════════════════════════════════════════════════════════

_alias_cache = None


def _alias_rules() -> list:
    global _alias_cache
    if _alias_cache is not None:
        return _alias_cache
    rules = list(_DEFAULT_ALIAS_RULES)
    if SEAT_ALIAS_PATH.exists():
        try:
            raw = json.loads(SEAT_ALIAS_PATH.read_text(encoding="utf-8"))
            rules = list(raw.get("rules", rules))
        except Exception as e:
            logger.warning(f"seat_aliases.json 加载失败: {e}")
    _alias_cache = rules
    return rules


def classify_seat(agency: str) -> dict:
    """
    对龙虎榜席位名称做规则归类。

    返回 {"label", "label_basis", "tags"}；label_basis 为 "规则匹配" 或 "推断"。
    明确声明：标签来自规则匹配，不等于官方认定。
    """
    a = str(agency or "")
    if not a:
        return {"label": LABEL_UNKNOWN, "label_basis": "规则匹配", "tags": []}
    if "机构专用" in a:
        return {"label": LABEL_INST, "label_basis": "规则匹配", "tags": []}
    if "沪股通专用" in a or "深股通专用" in a:
        return {"label": LABEL_NORTH, "label_basis": "规则匹配", "tags": []}
    # Tushare top_inst 偶发的投资者分类汇总行（非具体营业部）
    if a in ("机构", "机构投资者"):
        return {"label": "机构投资者(汇总)", "label_basis": "规则匹配", "tags": []}
    if "自然人" in a or "中小投资者" in a:
        return {"label": "个人投资者(汇总)", "label_basis": "规则匹配", "tags": []}
    if a in ("沪股通", "深股通") or "股通投资者" in a:
        return {"label": LABEL_NORTH, "label_basis": "规则匹配", "tags": []}
    for rule in _alias_rules():
        m = str(rule.get("match", ""))
        if m and m in a:
            return {
                "label": rule.get("label", LABEL_HOT),
                "label_basis": "推断",
                "tags": [rule.get("tag")] if rule.get("tag") else [],
            }
    if "证券" in a or "营业部" in a:
        return {"label": LABEL_BRANCH, "label_basis": "规则匹配", "tags": []}
    return {"label": LABEL_UNKNOWN, "label_basis": "规则匹配", "tags": []}


# ═══════════════════════════════════════════════════════════════
# 数据加载与规范化
# ═══════════════════════════════════════════════════════════════

def load_seat_rows(start: str | None = None, end: str | None = None,
                   code: str | None = None) -> pd.DataFrame:
    """
    加载龙虎榜席位明细（top_inst），返回规范化后的逐行记录。

    规范化：agency=exalter、code=6位、buy/sell 数值化、net=buy-sell；
    并去除「同一席位在买/卖榜重复且数值相同」的镜像重复行。
    空数据返回空 DataFrame。
    """
    dates = store_mf.list_dates("top_inst")
    frames = []
    for d in dates:
        if start and d < start:
            continue
        if end and d > end:
            continue
        df = store_mf.load("top_inst", date=d)
        if df is None or df.empty or "exalter" not in df.columns:
            continue
        frames.append(df.reset_index())
    if not frames:
        return pd.DataFrame(columns=["date", "code", "agency", "buy", "sell", "net", "reason"])

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.copy()
    raw["date"] = pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d")
    raw["agency"] = raw["exalter"].astype(str)
    raw["code"] = raw["ts_code"].astype(str).str.split(".").str[0]
    raw["buy"] = pd.to_numeric(raw.get("buy"), errors="coerce").fillna(0.0)
    raw["sell"] = pd.to_numeric(raw.get("sell"), errors="coerce").fillna(0.0)
    if "reason" not in raw.columns:
        raw["reason"] = ""

    if code:
        raw = raw[raw["code"] == _norm_code6(code)]
        if raw.empty:
            return pd.DataFrame(columns=["date", "code", "agency", "buy", "sell", "net", "reason"])

    # 去镜像重复：同一 (date, ts_code, agency, reason, buy, sell) 只保留一行
    raw = raw.drop_duplicates(subset=["date", "ts_code", "agency", "reason", "buy", "sell"])
    raw["net"] = raw["buy"] - raw["sell"]
    return raw[["date", "code", "agency", "buy", "sell", "net", "reason"]].reset_index(drop=True)


def aggregate_seat_episodes(rows: pd.DataFrame) -> pd.DataFrame:
    """
    把逐行席位明细聚合为「席位 × 标的 × 日期」的上榜 episode。

    同一席位/标的/日期可能命中多个涨跌口径（reason），此处合并求和，
    net = Σbuy - Σsell。
    """
    cols = ["date", "code", "agency", "buy", "sell", "net", "reasons"]
    if rows is None or rows.empty:
        return pd.DataFrame(columns=cols)
    g = rows.groupby(["date", "code", "agency"], as_index=False).agg(
        buy=("buy", "sum"),
        sell=("sell", "sum"),
        reasons=("reason", lambda s: sorted({str(x) for x in s if str(x)})),
    )
    g["net"] = g["buy"] - g["sell"]
    return g[cols]


def load_seat_episodes(start: str | None = None, end: str | None = None,
                       code: str | None = None) -> pd.DataFrame:
    return aggregate_seat_episodes(load_seat_rows(start=start, end=end, code=code))


def load_block_trades(start: str | None = None, end: str | None = None,
                      code: str | None = None) -> pd.DataFrame:
    """加载大宗交易（block_trade）逐笔记录，按窗口/代码过滤。"""
    dates = store_mf.list_dates("block_trade")
    frames = []
    for d in dates:
        if start and d < start:
            continue
        if end and d > end:
            continue
        df = store_mf.load("block_trade", date=d)
        if df is None or df.empty or "buyer" not in df.columns:
            continue
        frames.append(df.reset_index())
    cols = ["date", "code", "price", "vol", "amount", "buyer", "seller"]
    if not frames:
        return pd.DataFrame(columns=cols)
    raw = pd.concat(frames, ignore_index=True).copy()
    raw["date"] = pd.to_datetime(raw["date"]).dt.strftime("%Y-%m-%d")
    raw["code"] = raw["ts_code"].astype(str).str.split(".").str[0]
    for c in ("price", "vol", "amount"):
        raw[c] = pd.to_numeric(raw.get(c), errors="coerce")
    if code:
        raw = raw[raw["code"] == _norm_code6(code)]
    return raw[cols].reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════
# 价格/收益工具
# ═══════════════════════════════════════════════════════════════

class PriceCache:
    """按代码缓存 K 线，提供上榜后 N 个交易日收益与持有周期计算。"""

    def __init__(self, loader=None):
        self._loader = loader or _default_price_loader
        self._cache: dict = {}
        self._fwd_cache: dict = {}

    def get(self, code: str):
        code = _norm_code6(code)
        if code not in self._cache:
            try:
                self._cache[code] = self._loader(code)
            except Exception as e:
                logger.debug(f"价格加载 {code} 失败: {e}")
                self._cache[code] = None
        return self._cache[code]

    def _close(self, code: str):
        df = self.get(code)
        if df is None or df.empty or "close" not in df.columns:
            return None
        close = pd.to_numeric(df["close"], errors="coerce").dropna()
        return close if len(close) >= 2 else None

    def forward_return(self, code: str, date: str, horizon: int):
        """上榜日 T 起，T+horizon 个交易日收益 = close[T+h]/close[T]-1。"""
        cache_key = (_norm_code6(code), str(date), int(horizon))
        if cache_key in self._fwd_cache:
            return self._fwd_cache[cache_key]
        result = None
        close = self._close(code)
        if close is not None:
            try:
                pos = close.index.searchsorted(pd.Timestamp(date))
            except Exception:
                pos = None
            if pos is not None and pos < len(close):
                tgt = pos + int(horizon)
                base = float(close.iloc[pos])
                if tgt < len(close) and base > 0:
                    result = float(close.iloc[tgt]) / base - 1
        self._fwd_cache[cache_key] = result
        return result

    def trading_days_between(self, code: str, d1: str, d2: str):
        close = self._close(code)
        if close is None:
            return None
        try:
            p1 = min(close.index.searchsorted(pd.Timestamp(d1)), len(close) - 1)
            p2 = min(close.index.searchsorted(pd.Timestamp(d2)), len(close) - 1)
        except Exception:
            return None
        return int(p2 - p1)

    def window_return(self, code: str, start: str, end: str):
        close = self._close(code)
        if close is None:
            return None
        try:
            p1 = min(close.index.searchsorted(pd.Timestamp(start)), len(close) - 1)
            p2 = min(close.index.searchsorted(pd.Timestamp(end)), len(close) - 1)
        except Exception:
            return None
        base = float(close.iloc[p1])
        if base <= 0:
            return None
        return float(close.iloc[p2]) / base - 1


def _default_price_loader(code: str):
    import data.store as store
    return store.load(code, asset_type="stock", market="cn")


# ═══════════════════════════════════════════════════════════════
# 席位画像（支柱一）
# ═══════════════════════════════════════════════════════════════

_l1_name_cache = None


def _l1_name_map() -> dict:
    global _l1_name_cache
    if _l1_name_cache is None:
        m = {}
        try:
            from data.store_meta import list_industry_codes
            for r in list_industry_codes("L1"):
                m[str(r.get("index_code", "")).split(".")[0]] = r.get("industry_name", "")
        except Exception:
            pass
        _l1_name_cache = m
    return _l1_name_cache


def _sector_of(code: str) -> str:
    try:
        from data.store_meta import get_stock_l1_industry
        l1 = get_stock_l1_industry(code)
        if l1:
            return _l1_name_map().get(str(l1).split(".")[0], str(l1)) or "未知"
    except Exception:
        pass
    return "未知"


def _win_rate(episodes: pd.DataFrame, prices: PriceCache, horizons: tuple) -> dict:
    """对净买入 episode 计算上榜后 N 个交易日胜率（含样本数）。"""
    buy_eps = episodes[episodes["net"] > 0]
    out = {"samples": len(buy_eps)}
    for h in horizons:
        rets = []
        for _, r in buy_eps.iterrows():
            fr = prices.forward_return(r["code"], r["date"], h)
            if fr is not None:
                rets.append(fr)
        out[f"d{h}"] = (round(statistics.mean(x > 0 for x in rets), 3) if rets else None)
        out[f"d{h}_n"] = len(rets)
    return out


def _avg_hold_days(episodes: pd.DataFrame, prices: PriceCache):
    """席位在同一标的「先买后卖」的相邻配对，取交易日间隔均值（近似）。"""
    gaps = []
    for code, g in episodes.sort_values("date").groupby("code"):
        last_buy = None
        for _, r in g.iterrows():
            if r["net"] > 0:
                last_buy = r["date"]
            elif r["net"] < 0 and last_buy is not None:
                days = prices.trading_days_between(code, last_buy, r["date"])
                if days is not None and days > 0:
                    gaps.append(days)
                last_buy = None
    if not gaps:
        return None
    return round(statistics.mean(gaps))


def _profile_from_group(agency: str,
                        sub: pd.DataFrame,
                        horizons: tuple,
                        prices: PriceCache,
                        max_recent: int = 20) -> dict:
    """在已按席位过滤（并按日期排序）的 episode 子集上计算画像。"""
    info = classify_seat(agency)
    if sub is None or sub.empty:
        return {
            "agency": agency, **info,
            "appearance_count": {"buy": 0, "sell": 0, "total": 0},
            "cum_net_value": 0.0,
            "win_rate": _win_rate(pd.DataFrame(columns=["net", "code", "date"]),
                                  prices, horizons),
            "avg_hold_days": None,
            "fav_sectors": [],
            "recent_episodes": [],
            "data_window": None,
        }

    buy_n = int((sub["net"] > 0).sum())
    sell_n = int((sub["net"] < 0).sum())
    sectors: dict = {}
    for c in sub["code"]:
        s = _sector_of(c)
        sectors[s] = sectors.get(s, 0) + 1
    fav = sorted(sectors.items(), key=lambda kv: kv[1], reverse=True)[:5]

    recent = []
    for _, r in sub.tail(max_recent).iloc[::-1].iterrows():
        recent.append({
            "symbol": f"{r['code']}", "code": r["code"], "date": r["date"],
            "net": _safe_float(r["net"]), "buy": _safe_float(r["buy"]),
            "sell": _safe_float(r["sell"]), "reasons": list(r["reasons"]),
        })

    return {
        "agency": agency,
        **info,
        "appearance_count": {"buy": buy_n, "sell": sell_n, "total": len(sub)},
        "cum_net_value": round(float(sub["net"].sum()), 2),
        "win_rate": _win_rate(sub, prices, horizons),
        "avg_hold_days": _avg_hold_days(sub, prices),
        "fav_sectors": [{"sector": s, "count": n} for s, n in fav],
        "recent_episodes": recent,
        "data_window": {
            "start": str(sub["date"].min()),
            "end": str(sub["date"].max()),
        },
    }


def compute_seat_profile(agency: str,
                         episodes: pd.DataFrame,
                         horizons: tuple = HORIZONS,
                         prices: PriceCache = None,
                         max_recent: int = 20) -> dict:
    """
    给定席位名称与全量 episode，产出可持久化的席位画像档案。
    统计口径：累计净买卖 = Σnet（已排除镜像重复）；胜率基于净买入 episode。
    """
    prices = prices or PriceCache()
    if episodes is None or episodes.empty:
        sub = pd.DataFrame(columns=["date", "code", "agency", "buy", "sell", "net", "reasons"])
    else:
        sub = episodes[episodes["agency"] == agency].sort_values("date")
    return _profile_from_group(agency, sub, horizons, prices, max_recent)


def profile_stock_seats(code: str,
                        window_months: int = DEFAULT_WINDOW_MONTHS,
                        horizons: tuple = HORIZONS,
                        prices: PriceCache = None,
                        episodes: pd.DataFrame = None) -> dict:
    """
    支柱一：某标的在窗口内的上榜席位身份与画像。

    返回该股近期上榜席位清单（身份标签 + 本股净买卖）以及对每个席位的跨股画像卡片。
    """
    code6 = _norm_code6(code)
    prices = prices or PriceCache()
    end = _today()
    start = _window_start(window_months, end)
    eps = episodes if episodes is not None else load_seat_episodes(start=start, end=end)
    stock_eps = eps[eps["code"] == code6] if not eps.empty else eps

    notes = []
    if stock_eps is None or stock_eps.empty:
        notes.append(f"无龙虎榜席位明细（top_inst，窗口 {start}~{end}）")
        return {
            "code": code6, "window": {"start": start, "end": end},
            "available": False, "seats": [], "recent_episodes": [], "notes": notes,
        }

    eps_groups = {a: g.sort_values("date") for a, g in eps.groupby("agency")}
    seats = []
    for agency, g in stock_eps.groupby("agency"):
        card = _profile_from_group(agency, eps_groups.get(agency, g), horizons, prices)
        card["this_stock"] = {
            "code": code6,
            "appearances": len(g),
            "buy": round(float(g["buy"].sum()), 2),
            "sell": round(float(g["sell"].sum()), 2),
            "net": round(float(g["net"].sum()), 2),
            "last_date": str(g["date"].max()),
        }
        seats.append(card)
    seats.sort(key=lambda c: abs(c["this_stock"]["net"]), reverse=True)

    recent = []
    for _, r in stock_eps.sort_values("date").tail(30).iloc[::-1].iterrows():
        recent.append({
            "date": r["date"], "agency": r["agency"],
            "label": classify_seat(r["agency"])["label"],
            "net": _safe_float(r["net"]), "reasons": list(r["reasons"]),
        })

    return {
        "code": code6, "window": {"start": start, "end": end},
        "available": True, "seats": seats, "recent_episodes": recent, "notes": notes,
    }


def profile_seat(agency: str,
                 window_months: int = DEFAULT_WINDOW_MONTHS,
                 horizons: tuple = HORIZONS,
                 prices: PriceCache = None,
                 episodes: pd.DataFrame = None) -> dict:
    """支柱一（按资金主体）：单个席位的跨股画像卡片。"""
    prices = prices or PriceCache()
    end = _today()
    start = _window_start(window_months, end)
    eps = episodes if episodes is not None else load_seat_episodes(start=start, end=end)
    card = compute_seat_profile(agency, eps, horizons=horizons, prices=prices)
    card["window"] = {"start": start, "end": end}
    card["available"] = bool(card["appearance_count"]["total"])
    card["notes"] = [] if card["available"] else [
        f"窗口内无该席位上榜记录（top_inst，窗口 {start}~{end}）"
    ]
    return card


def rank_active_seats(window_months: int = DEFAULT_WINDOW_MONTHS,
                      horizons: tuple = HORIZONS,
                      prices: PriceCache = None,
                      episodes: pd.DataFrame = None,
                      top_n: int = 20) -> list:
    """近期活跃资金主体榜：按 |累计净买卖| 排序的席位画像列表（活动名册，非买入清单）。"""
    prices = prices or PriceCache()
    end = _today()
    start = _window_start(window_months, end)
    eps = episodes if episodes is not None else load_seat_episodes(start=start, end=end)
    if eps is None or eps.empty:
        return []
    cards = [_profile_from_group(a, g.sort_values("date"), horizons, prices)
             for a, g in eps.groupby("agency")]
    cards.sort(key=lambda c: abs(c["cum_net_value"]), reverse=True)
    return cards[:top_n]


# ═══════════════════════════════════════════════════════════════
# 北向跨期行为（支柱二）
# ═══════════════════════════════════════════════════════════════

def _load_hk_hold(code6: str, fetch: bool = False):
    df = store_mf.load("hk_hold", code=code6)
    if (df is None or df.empty) and fetch:
        try:
            from data.downloader_moneyflow import MoneyflowDownloader
            MoneyflowDownloader().fetch_hk_hold(code6)
            df = store_mf.load("hk_hold", code=code6)
        except Exception as e:
            logger.debug(f"hk_hold 按需下载 {code6} 失败: {e}")
    return df


def _streak(values: list) -> dict:
    """给定同向变动序列，返回最长同向 streak 方向与长度及累计变动。"""
    best_dir = 0
    best_len = 0
    best_sum = 0.0
    cur_dir = 0
    cur_len = 0
    cur_sum = 0.0
    for v in values:
        sign = 1 if v > 0 else (-1 if v < 0 else 0)
        if sign != 0 and sign == cur_dir:
            cur_len += 1
            cur_sum += v
        else:
            cur_dir, cur_len, cur_sum = sign, (1 if sign else 0), (v if sign else 0.0)
        if cur_len > best_len:
            best_dir, best_len, best_sum = cur_dir, cur_len, cur_sum
    return {"direction": ("加仓" if best_dir > 0 else "减仓" if best_dir < 0 else "无"),
            "length": best_len, "cum_change": round(best_sum, 4)}


def profile_northbound(code: str,
                       window_months: int = DEFAULT_WINDOW_MONTHS,
                       fetch: bool = False,
                       prices: PriceCache = None) -> dict:
    """
    支柱二：北向在某标的上的跨期持股行为。

    注意：Tushare hk_hold 对普通积分账户为**季度披露**（2025Q1 起），
    因此 streak 以披露点（季度）计数，非日频。
    """
    code6 = _norm_code6(code)
    prices = prices or PriceCache()
    end = _today()
    start = _window_start(window_months, end)
    df = _load_hk_hold(code6, fetch=fetch)

    notes = []
    if df is None or df.empty:
        notes.append(f"无北向持股数据（hk_hold，代码 {code6}，窗口 {start}~{end}）")
        return {"code": code6, "available": False, "window": {"start": start, "end": end},
                "streak": None, "holding_ratio": {}, "divergence": None, "notes": notes}

    df = df.sort_index()
    df = df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]
    if df.empty:
        notes.append(f"窗口内无北向持股披露（hk_hold，窗口 {start}~{end}）")
        return {"code": code6, "available": False, "window": {"start": start, "end": end},
                "streak": None, "holding_ratio": {}, "divergence": None, "notes": notes}

    ratio_col = "ratio" if "ratio" in df.columns else ("holding_ratio" if "holding_ratio" in df.columns else None)
    ratios = pd.to_numeric(df[ratio_col], errors="coerce").dropna() if ratio_col else pd.Series(dtype=float)
    deltas = ratios.diff().dropna().tolist()
    ratio_start = _safe_float(ratios.iloc[0]) if len(ratios) else None
    ratio_end = _safe_float(ratios.iloc[-1]) if len(ratios) else None

    divergence = None
    win_ret = prices.window_return(code6, start, end)
    if ratio_start is not None and ratio_end is not None and win_ret is not None:
        if ratio_end > ratio_start and win_ret < 0:
            divergence = "北向加仓而价格下跌，与价格背离，需要关注"
        elif ratio_end < ratio_start and win_ret > 0:
            divergence = "北向减仓而价格上涨，与价格背离，需要关注"

    return {
        "code": code6,
        "available": True,
        "window": {"start": start, "end": end},
        "disclosure": "quarterly" if len(df) <= 8 else "periodic",
        "points": len(df),
        "streak": _streak(deltas) if deltas else {"direction": "无", "length": 0, "cum_change": 0.0},
        "holding_ratio": {
            "start": ratio_start, "end": ratio_end,
            "change": round((ratio_end - ratio_start), 4) if (ratio_start is not None and ratio_end is not None) else None,
        },
        "price_return": round(win_ret, 4) if win_ret is not None else None,
        "divergence": divergence,
        "notes": [*notes, "北向持股为披露口径，可能滞后于成交日；普通积分账户为季度披露"],
    }


# ═══════════════════════════════════════════════════════════════
# 多源资金合力 / 分歧（支柱三）
# ═══════════════════════════════════════════════════════════════

def _route(direction: str, metric, note: str, raw=None) -> dict:
    return {"direction": direction, "metric": metric, "note": note, "raw": raw}


def _no_data(note: str) -> dict:
    return {"direction": "no_data", "metric": None, "note": note, "raw": None}


def _sign_direction(x) -> str:
    if x is None:
        return "no_data"
    if x > 0:
        return "buy"
    if x < 0:
        return "sell"
    return "flat"


def classify_consensus(routes: dict) -> dict:
    """按四路资金方向给出合力/分歧结论（列出全部四路，含无数据）。"""
    buys = [k for k, v in routes.items() if v["direction"] == "buy"]
    sells = [k for k, v in routes.items() if v["direction"] == "sell"]
    nodata = [k for k, v in routes.items() if v["direction"] == "no_data"]
    flat = [k for k, v in routes.items() if v["direction"] == "flat"]

    if len(nodata) >= 2:
        verdict = "证据不足"
    elif len(buys) >= 3 and not sells:
        verdict = "资金合力（同向买入）"
    elif len(sells) >= 3 and not buys:
        verdict = "资金合力（同向卖出）"
    elif buys and sells:
        verdict = "资金分歧（对打）"
    elif (len(buys) + len(sells) + len(flat)) <= 1:
        verdict = "证据不足"
    elif len(buys) > len(sells):
        verdict = "偏多（未达合力阈值）"
    else:
        verdict = "偏空（未达合力阈值）"

    return {
        "verdict": verdict,
        "buy_routes": buys, "sell_routes": sells,
        "no_data_routes": nodata, "flat_routes": flat,
    }


def assess_consensus(code: str,
                     window_months: int = 6,
                     fetch: bool = False,
                     prices: PriceCache = None,
                     episodes: pd.DataFrame = None) -> dict:
    """
    支柱三：同一标的、同一窗口下四路资金方向与合力/分歧结论。

    四路：北向（hk_hold 持股比例变动）、机构席位（龙虎榜机构专用净额）、
    融资盘（margin 融资余额变动）、大宗买方（block_trade 机构专用买卖净额）。
    """
    code6 = _norm_code6(code)
    prices = prices or PriceCache()
    end = _today()
    start = _window_start(window_months, end)
    routes = {}

    nb = profile_northbound(code6, window_months=window_months, fetch=fetch, prices=prices)
    if nb.get("available"):
        chg = nb["holding_ratio"].get("change")
        routes["北向"] = _route(_sign_direction(chg),
                                chg, f"持股比例变动 {chg:+.2f}pct" if chg is not None else "持股比例变动无数据")
    else:
        routes["北向"] = _no_data("无北向持股数据（hk_hold）")

    eps = episodes if episodes is not None else load_seat_episodes(start=start, end=end, code=code6)
    if eps is not None and not eps.empty:
        inst = eps[eps["agency"].astype(str).str.contains("机构专用")]
        if not inst.empty:
            net = float(inst["net"].sum())
            routes["机构席位"] = _route(_sign_direction(net), net,
                                        f"机构专用净额 {net/1e8:+.2f}亿（{len(inst)}条）")
        else:
            routes["机构席位"] = _no_data("窗口内无机构专用席位上榜")
    else:
        routes["机构席位"] = _no_data(f"无龙虎榜席位明细（top_inst，窗口 {start}~{end}）")

    try:
        m = store_mf.load("margin", code=code6, start=start, end=end)
    except Exception:
        m = None
    if m is None or m.empty or "rzye" not in m.columns:
        routes["融资盘"] = _no_data("无融资融券数据（margin）")
    else:
        rzye = pd.to_numeric(m["rzye"], errors="coerce").dropna()
        delta = float(rzye.iloc[-1] - rzye.iloc[0]) if len(rzye) > 1 else 0.0
        routes["融资盘"] = _route(_sign_direction(delta), delta, f"融资余额变动 {delta/1e8:+.2f}亿")

    bt = load_block_trades(start=start, end=end, code=code6)
    if bt is None or bt.empty:
        routes["大宗买方"] = _no_data("无大宗交易数据（block_trade）")
    else:
        b_inst = bt[bt["buyer"].astype(str).str.contains("机构专用")]["amount"].sum()
        s_inst = bt[bt["seller"].astype(str).str.contains("机构专用")]["amount"].sum()
        if b_inst == 0 and s_inst == 0:
            routes["大宗买方"] = _no_data("大宗交易无机构专用买方/卖方")
        else:
            net = float(b_inst) - float(s_inst)
            routes["大宗买方"] = _route(_sign_direction(net), net,
                                        f"机构大宗净买入 {net/1e8:+.2f}亿（总成交 {bt['amount'].sum()/1e8:.2f}亿）")

    verdict = classify_consensus(routes)
    win_ret = prices.window_return(code6, start, end)
    return {
        "code": code6,
        "window": {"start": start, "end": end},
        "routes": routes,
        **verdict,
        "price_return_in_window": round(win_ret, 4) if win_ret is not None else None,
        "available": any(v["direction"] != "no_data" for v in routes.values()),
    }


# ═══════════════════════════════════════════════════════════════
# 席位档案持久化
# ═══════════════════════════════════════════════════════════════

def load_archive() -> dict:
    if not ARCHIVE_PATH.exists():
        return {"schema_version": "1.0", "generated_by": "smart-money-profiler",
                "last_updated": None, "seats": []}
    try:
        return json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"seats.json 读取失败: {e}")
        return {"schema_version": "1.0", "generated_by": "smart-money-profiler",
                "last_updated": None, "seats": []}


def _write_archive(data: dict) -> None:
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ARCHIVE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ARCHIVE_PATH)


def update_archive(episodes: pd.DataFrame,
                   horizons: tuple = HORIZONS,
                   prices: PriceCache = None,
                   max_recent: int = 100) -> dict:
    """
    用最新 episode 增量更新席位档案（append/update，幂等）。

    以 (code,date) 去重合并；从累计 episode 重新推导统计量后写回。
    """
    prices = prices or PriceCache()
    archive = load_archive()
    by_agency = {s["agency"]: s for s in archive.get("seats", [])}
    if episodes is None or episodes.empty:
        archive["last_updated"] = datetime.now().strftime("%Y%m%d")
        _write_archive(archive)
        return archive

    for agency, g in episodes.groupby("agency"):
        rec = by_agency.get(agency)
        if rec is None:
            rec = {"agency": agency, **classify_seat(agency),
                   "episodes": []}
            by_agency[agency] = rec
        existing = {(e.get("code"), e.get("date")): e for e in rec.get("episodes", [])}
        for _, r in g.iterrows():
            key = (r["code"], r["date"])
            fwd = {f"d{h}": prices.forward_return(r["code"], r["date"], h) for h in horizons}
            existing[key] = {
                "code": r["code"], "date": r["date"],
                "buy": _safe_float(r["buy"]), "sell": _safe_float(r["sell"]),
                "net": _safe_float(r["net"]), "reasons": list(r["reasons"]),
                "fwd_ret": fwd,
            }
        episodes_list = sorted(existing.values(), key=lambda e: e["date"], reverse=True)[:max_recent]
        rec["episodes"] = episodes_list
        rec["appearance_count"] = {
            "buy": sum(1 for e in episodes_list if (e.get("net") or 0) > 0),
            "sell": sum(1 for e in episodes_list if (e.get("net") or 0) < 0),
            "total": len(episodes_list),
        }
        rec["cum_net_value"] = round(sum((e.get("net") or 0) for e in episodes_list), 2)
        win = {"samples": rec["appearance_count"]["buy"]}
        for h in horizons:
            vals = [e["fwd_ret"].get(f"d{h}") for e in episodes_list
                    if (e.get("net") or 0) > 0 and e.get("fwd_ret", {}).get(f"d{h}") is not None]
            win[f"d{h}"] = round(statistics.mean(x > 0 for x in vals), 3) if vals else None
            win[f"d{h}_n"] = len(vals)
        rec["win_rate"] = win
        ep_df = pd.DataFrame(episodes_list)
        if "net" in ep_df.columns:
            ep_df["net"] = pd.to_numeric(ep_df["net"], errors="coerce").fillna(0.0)
        rec["avg_hold_days"] = _avg_hold_days(ep_df, prices)
        rec["data_window"] = {
            "start": min(e["date"] for e in episodes_list),
            "end": max(e["date"] for e in episodes_list),
        }

    archive["seats"] = sorted(by_agency.values(), key=lambda s: s["agency"])
    archive["last_updated"] = datetime.now().strftime("%Y%m%d")
    _write_archive(archive)
    return archive


# ═══════════════════════════════════════════════════════════════
# 主入口：完整画像
# ═══════════════════════════════════════════════════════════════

def _looks_like_seat(target: str) -> bool:
    t = str(target or "")
    return any(k in t for k in ("机构专用", "股通专用", "证券", "营业部", "席位"))


def build_profile(target: str,
                  target_type: str = "auto",
                  mode: str = "full",
                  window_months: int = DEFAULT_WINDOW_MONTHS,
                  horizons=HORIZONS,
                  fetch: bool = False,
                  update_seat_archive: bool = True) -> dict:
    """
    构建资金主体行为画像报告（结构化 dict）。

    :param target: 股票代码 / 名称 / 席位名称（如 "机构专用"）
    :param target_type: auto / stock / seat
    :param mode: full / seat / northbound / consensus
    :param window_months: 回溯月数
    :param horizons: 事后验证窗口（交易日）
    :param fetch: 北向持股缺失时是否按需下载（联网）
    """
    horizons = parse_horizons(horizons)
    prices = PriceCache()
    if target_type == "auto":
        target_type = "seat" if _looks_like_seat(target) else "stock"

    report = {
        "subject": {"target": str(target), "type": target_type},
        "mode": mode,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_months": window_months,
        "horizons": list(horizons),
        "disclaimer": DISCLAIMER,
        "notes": ["席位标签来自规则匹配/推断，不等于官方认定"],
    }

    if target_type == "seat":
        report["seat_profile"] = profile_seat(target, window_months=window_months,
                                              horizons=horizons, prices=prices)
        report["available"] = report["seat_profile"]["available"]
        report["data_appendix"] = [
            {"module": "席位明细", "source": "top_inst",
             "window": f"{report['seat_profile']['window']['start']}~{report['seat_profile']['window']['end']}",
             "rows": report["seat_profile"]["appearance_count"]["total"], "notes": "规则匹配"}]
        return report

    code6 = _norm_code6(target)
    report["subject"] = {"target": code6, "type": "stock"}
    if mode in ("full", "seat"):
        report["seats"] = profile_stock_seats(code6, window_months=window_months,
                                              horizons=horizons, prices=prices)
    if mode in ("full", "northbound"):
        report["northbound"] = profile_northbound(code6, window_months=window_months,
                                                  fetch=fetch, prices=prices)
    if mode in ("full", "consensus"):
        report["consensus"] = assess_consensus(code6, window_months=min(window_months, 6),
                                               fetch=fetch, prices=prices)
    if update_seat_archive:
        # 只追加本次画像主体相关 episode，避免每次写全市场档案（体积/耗时）
        try:
            start = _window_start(window_months, _today())
            if target_type == "seat":
                eps = load_seat_episodes(start=start)
                eps = eps[eps["agency"] == target]
            else:
                eps = load_seat_episodes(start=start, code=code6)
            update_archive(eps, horizons=horizons, prices=prices)
            report["archive_updated"] = True
        except Exception as e:
            logger.debug(f"席位档案更新失败: {e}")
            report["archive_updated"] = False

    report["available"] = any(
        report.get(k, {}).get("available") if isinstance(report.get(k), dict) else False
        for k in ("seats", "northbound", "consensus")
    )
    report["data_appendix"] = _build_appendix(code6, report)
    return report


def _build_appendix(code6: str, report: dict) -> list:
    appendix = []
    seats = report.get("seats")
    if seats:
        appendix.append({"module": "席位明细", "source": "top_inst",
                         "window": f"{seats['window']['start']}~{seats['window']['end']}",
                         "rows": len(seats.get("recent_episodes", [])), "notes": "规则匹配"})
    nb = report.get("northbound")
    if nb:
        appendix.append({"module": "北向持股", "source": "hk_hold",
                         "window": f"{nb['window']['start']}~{nb['window']['end']}",
                         "rows": nb.get("points", 0), "notes": nb.get("disclosure", "")})
    cons = report.get("consensus")
    if cons:
        appendix.append({"module": "多源合力", "source": "hk_hold/top_inst/margin/block_trade",
                         "window": f"{cons['window']['start']}~{cons['window']['end']}",
                         "rows": sum(1 for v in cons["routes"].values() if v["direction"] != "no_data"),
                         "notes": cons["verdict"]})
    return appendix


def report_to_markdown(report: dict) -> str:
    """把结构化画像报告渲染为 Markdown（供 Agent/LLM 消费）。"""
    lines = []
    subj = report.get("subject", {})
    lines.append("# 资金主体行为画像")
    lines.append(f"- 标的/主体: {subj.get('target')}（{subj.get('type')}）")
    lines.append(f"- 生成时间: {report.get('generated_at')}")
    lines.append(f"- 回溯窗口: {report.get('window_months')} 个月；事后验证: {report.get('horizons')} 交易日")
    lines.append("")

    seat = report.get("seat_profile")
    if seat:
        lines.append("## 席位画像")
        lines.append(f"- 身份: {seat['label']}（{seat['label_basis']}）")
        lines.append(f"- 上榜频次: {seat['appearance_count']}")
        lines.append(f"- 累计净买卖: {seat['cum_net_value']}")
        lines.append(f"- 上榜后胜率: {seat['win_rate']}")
        lines.append(f"- 平均持有周期(交易日): {seat['avg_hold_days']}")
        lines.append(f"- 偏好板块: {seat['fav_sectors']}")
        lines.append("")

    seats = report.get("seats")
    if seats:
        lines.append("## 席位身份与画像")
        if not seats.get("available"):
            lines.append(f"- {', '.join(seats.get('notes', [])) or '无数据'}")
        else:
            lines.append("| 席位 | 身份 | 本股净买卖 | 累计净买卖 | 5/10/20日胜率 | 平均持有(交易日) |")
            lines.append("|---|---|---|---|---|---|")
            for s in seats["seats"]:
                wr = s["win_rate"]
                wr_s = "/".join(str(wr.get(f"d{h}")) for h in report.get("horizons", HORIZONS))
                hold = s.get("avg_hold_days")
                lines.append(f"| {s['agency']} | {s['label']}({s['label_basis']}) | "
                             f"{s['this_stock']['net']} | {s['cum_net_value']} | {wr_s} | "
                             f"{hold if hold is not None else '样本不足'} |")
        lines.append("")

    nb = report.get("northbound")
    if nb:
        lines.append("## 北向跨期行为")
        if not nb.get("available"):
            lines.append(f"- {', '.join(nb.get('notes', [])) or '无数据'}")
        else:
            lines.append(f"- 最长{nb['streak']['direction']} streak: {nb['streak']['length']} 个披露点")
            lines.append(f"- 持股比例: {nb['holding_ratio']}")
            lines.append(f"- 与价格背离: {nb.get('divergence') or '无'}")
        lines.append("")

    cons = report.get("consensus")
    if cons:
        lines.append("## 资金合力 / 分歧")
        lines.append(f"- 结论: {cons['verdict']}")
        for k, v in cons["routes"].items():
            lines.append(f"  - {k}: {v['direction']}（{v['note']}）")
        lines.append("")

    lines.append(f"> {report.get('disclaimer', DISCLAIMER)}")
    return "\n".join(lines)
