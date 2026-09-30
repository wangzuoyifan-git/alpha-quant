"""
agents/sector_aggregator.py — 板块/ETF 资金聚合（Phase 2）

把个股级资金明细聚合到「申万行业（L1/L2/L3）」或「ETF（指数成分权重）」维度。

板块标识：
  - 申万行业：行业代码 6 位（如 801080）或带后缀（801080.SI）
  - ETF：6 位 ETF 代码（如 510300 / 159915）

聚合口径（对齐 reference 7.1）：
  - 龙虎榜净额 / 两融余额 / 北向 / 大宗 / 产业资本：求和
  - ETF 申赎：Δ份额 × 净值（求和）
"""
import logging
from functools import lru_cache

import pandas as pd

import data.store_fund as store_fund
import data.store_moneyflow as store_mf
from agents.smart_money_agent import (
    LABEL_HOT,
    LABEL_INST,
    LABEL_NORTH,
    LABEL_QUANT,
    classify_seat,
    load_block_trades,
    load_seat_episodes,
)
from data.store_meta import load as load_meta

logger = logging.getLogger(__name__)

_LEVEL_COL = {"L1": "l1_code", "L2": "l2_code", "L3": "l3_code"}


def _code6(x) -> str:
    return str(x).split(".")[0]


# ═══════════════════════════════════════════════════════════════
# 成员映射
# ═══════════════════════════════════════════════════════════════

@lru_cache(maxsize=4)
def _stock_sector_map(level: str) -> dict:
    """{股票6位: 行业6位}（申万 level）。"""
    df = load_meta("industry_member")
    if df is None or df.empty:
        return {}
    col = _LEVEL_COL.get(level, "l1_code")
    if "ts_code" not in df.columns or col not in df.columns:
        return {}
    return {_code6(ts): _code6(sc) for ts, sc in zip(df["ts_code"], df[col], strict=False)}


def sector_members(sector_code: str, level: str = "L1") -> list:
    sc = _code6(sector_code)
    m = _stock_sector_map(level)
    return sorted(c for c, s in m.items() if s == sc)


def stock_sector(code6: str, level: str = "L1") -> str | None:
    return _stock_sector_map(level).get(_code6(code6))


@lru_cache(maxsize=1)
def _sector_name_map() -> dict:
    """{行业6位: 名称}（申万 L1 名称）。"""
    out = {}
    try:
        from data.store_meta import list_industry_codes
        for r in list_industry_codes("L1") + list_industry_codes("L2"):
            out[_code6(r.get("index_code", ""))] = r.get("industry_name", "")
    except Exception:
        pass
    return out


def sector_name(sector_code: str) -> str:
    sc = _code6(sector_code)
    name = _sector_name_map().get(sc)
    if name:
        return name
    basic = store_fund.load("basic")
    if basic is not None and not basic.empty and "ts_code" in basic.columns:
        row = basic[basic["ts_code"].astype(str).str.split(".").str[0] == sc]
        if not row.empty:
            return str(row.iloc[0].get("name", sc))
    return sc


def etf_members(etf_code: str) -> dict:
    """ETF 成分股 {股票6位: 权重}。

    优先：ETF→跟踪指数（etf_index_map）→ index_weight 最新月权重；
    降级：etf_config.sw_l2_codes → 申万 L2 成分股等权。
    """
    code6 = _code6(etf_code)
    emap = store_fund.load_etf_index_map()
    idx = (emap.get(code6) or {}).get("index_code")
    if idx:
        w = store_fund.load("index_weight", code=idx.split(".")[0])
        if w is not None and not w.empty and "con_code" in w.columns:
            latest = w.index.max()
            snap = w[w.index == latest]
            return {_code6(c): float(wt) for c, wt in zip(snap["con_code"], snap["weight"], strict=False)}
    # 降级：etf_config sw_l2_codes → L2 成分股等权
    try:
        from agents.etf_agent import get_etf_meta
        from data.store_meta import get_member_stocks
        l2s = get_etf_meta(code6).get("sw_l2_codes") or []
        codes = []
        for l2 in l2s:
            codes += get_member_stocks([l2], level="L2")
        if codes:
            eq = 1.0 / len(codes)
            return {c: eq for c in codes}
    except Exception as e:
        logger.debug(f"etf_members 降级失败 {code6}: {e}")
    return {}


# ═══════════════════════════════════════════════════════════════
# 个股级资金聚合
# ═══════════════════════════════════════════════════════════════

def aggregate_lhb(members: set, start: str, end: str) -> dict:
    """板块龙虎榜：按席位类别求和 + 机构专用净额。"""
    out = {"hot_money": 0.0, "quant": 0.0, "institutional": 0.0, "northbound": 0.0,
           "lhb_total": 0.0, "n": 0}
    try:
        eps = load_seat_episodes(start=start, end=end)
    except Exception:
        return out
    if eps is None or eps.empty:
        return out
    sub = eps[eps["code"].isin(members)]
    if sub.empty:
        return out
    for agency, net in zip(sub["agency"], sub["net"], strict=False):
        lab = classify_seat(agency)["label"]
        net = float(net)
        out["lhb_total"] += net
        if lab == LABEL_HOT:
            out["hot_money"] += net
        elif lab == LABEL_QUANT:
            out["quant"] += net
        elif lab == LABEL_INST:
            out["institutional"] += net
        elif lab == LABEL_NORTH:
            out["northbound"] += net
    out["n"] = len(sub)
    return out


def aggregate_margin(members: list, start: str, end: str) -> dict:
    """板块两融：成员股融资余额首末求和，返回变动率。"""
    first = last = 0.0
    n = 0
    for code in members:
        m = store_mf.load("margin", code=code, start=start, end=end)
        if m is None or m.empty or "rzye" not in m.columns:
            continue
        s = pd.to_numeric(m["rzye"], errors="coerce").dropna()
        if len(s) < 2:
            continue
        first += float(s.iloc[0])
        last += float(s.iloc[-1])
        n += 1
    if n == 0 or first == 0:
        return {"change_rate": None, "members": n}
    return {"change_rate": last / first - 1, "members": n}


def aggregate_block(members: set, start: str, end: str) -> dict:
    bt = load_block_trades(start=start, end=end)
    if bt is None or bt.empty:
        return {"inst_net": None, "amount": 0.0, "n": 0}
    sub = bt[bt["code"].isin(members)]
    if sub.empty:
        return {"inst_net": None, "amount": 0.0, "n": 0}
    b = sub[sub["buyer"].astype(str).str.contains("机构专用")]["amount"].sum()
    s = sub[sub["seller"].astype(str).str.contains("机构专用")]["amount"].sum()
    inst_net = float(b) - float(s) if (b or s) else None
    return {"inst_net": inst_net, "amount": float(sub["amount"].sum()), "n": len(sub)}


def aggregate_industrial(members: list, start: str, end: str) -> dict:
    """板块产业资本：回购/增持金额求和。"""
    from data.store_events import load_corp_events

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    amount = 0.0
    n = 0
    for code in members:
        try:
            events = load_corp_events(code) or []
        except Exception:
            continue
        for e in events:
            d = e.get("date", "")
            if not (start <= d <= end):
                continue
            et = e.get("event_type", "")
            det = e.get("detail", {}) or {}
            if et == "buyback":
                amount += _num(det.get("amount"))
                n += 1
            elif et == "increase":
                amount += _num(det.get("change_vol")) * _num(det.get("avg_price"))
                n += 1
    return {"amount": amount, "n": n}


def aggregate_northbound(members: list, end: str) -> dict:
    """板块北向：成员股 hk_hold 持股比例变动求和（季度）。"""
    total = 0.0
    n = 0
    for code in members:
        df = store_mf.load("hk_hold", code=code)
        if df is None or df.empty or "ratio" not in df.columns:
            continue
        df = df[df.index <= pd.Timestamp(end)].sort_index()
        if len(df) < 2:
            continue
        total += float(df["ratio"].iloc[-1] - df["ratio"].iloc[0])
        n += 1
    return {"ratio_change": total if n else None, "members": n}


def etf_flow(etf_code: str, start: str, end: str) -> dict:
    """ETF 申赎（近似）：Δ份额 × 净值，窗口求和。"""
    code6 = _code6(etf_code)
    share = store_fund.load("share", code=code6, start=start, end=end)
    if share is None or share.empty or "fd_share" not in share.columns:
        return {"net_amount": None, "days": 0}
    s = pd.to_numeric(share["fd_share"], errors="coerce").dropna()
    if len(s) < 2:
        return {"net_amount": None, "days": len(s)}
    nav = store_fund.load("nav", code=code6, start=start, end=end)
    px = None
    if nav is not None and not nav.empty:
        for col in ("adj_nav", "unit_nav"):
            if col in nav.columns:
                px = pd.to_numeric(nav[col], errors="coerce").dropna()
                break
    if px is None:
        px = pd.Series(1.0, index=s.index)
    aligned = pd.concat([s.rename("share"), px.rename("nav")], axis=1).dropna()
    if len(aligned) < 2:
        return {"net_amount": None, "days": len(aligned)}
    d_share = aligned["share"].diff().dropna()
    approx = (d_share * aligned["nav"].reindex(d_share.index)).sum()
    return {"net_amount": float(approx), "days": len(aligned)}
