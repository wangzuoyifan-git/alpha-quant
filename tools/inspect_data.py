"""
数据直查工具 — 终端表格 + 可选 matplotlib 图表

用法（从项目根目录）：
  python main.py inspect kline  600519 [--n 20] [--plot]
  python main.py inspect kline  000300 --type index [--plot]
  python main.py inspect mf     600519 [--n 10] [--plot]          # 个股资金流
  python main.py inspect ind    食品饮料 [--n 20] [--level L1] [--plot]  # 行业资金流
  python main.py inspect macro  bond_yield [--n 20] [--plot]
  python main.py inspect macro  list                               # 列出可用宏观指标
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd


# ─────────────────────────────────────────────────────────
# 终端表格辅助
# ─────────────────────────────────────────────────────────

def _print_df(df: pd.DataFrame, title: str = "", float_fmt: str = "{:.2f}"):
    """终端表格输出。列数 ≤8 用 rich，更多列用 pandas to_string（避免截断）"""
    if title:
        print(f"\n{'─'*60}")
        print(f"  {title}")
        print(f"{'─'*60}")
    if len(df.columns) > 8:
        # 列多时直接 pandas，保证列名完整
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 160)
        print(df.to_string(float_format=lambda x: float_fmt.format(x)))
        pd.reset_option("display.max_columns")
        pd.reset_option("display.width")
        return
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        tbl = Table(show_header=True, header_style="bold cyan", box=None,
                    show_edge=False, padding=(0, 1))
        tbl.add_column("date", style="dim")
        for col in df.columns:
            tbl.add_column(col, justify="right")
        for idx, row in df.iterrows():
            vals = []
            for v in row:
                if isinstance(v, float):
                    vals.append(float_fmt.format(v) if not pd.isna(v) else "—")
                else:
                    vals.append(str(v) if not pd.isna(v) else "—")
            tbl.add_row(str(idx)[:10], *vals)
        console.print(tbl)
    except ImportError:
        print(df.to_string(float_format=lambda x: float_fmt.format(x)))


# ─────────────────────────────────────────────────────────
# 名称/代码解析工具
# ─────────────────────────────────────────────────────────

def _resolve_codes(queries: list[str], asset_type: str = "stock") -> list[str] | None:
    """
    将查询列表（代码 or 名称）解析为代码列表。
    - 6位纯数字 → 直接当代码
    - 其他 → 在 ipo_dates 中按名称精确/模糊匹配
    - 多个候选 → 打印候选，返回 None 让用户明确
    """
    import data.store as store
    ipo = store.load_ipo_dates()
    # 只保留指定 asset_type
    filtered = {k: v for k, v in ipo.items() if v.get("type") == asset_type}

    result = []
    for q in queries:
        if q.isdigit() and len(q) in (5, 6):
            result.append(q)
            continue
        # 精确名称匹配
        exact = [k.split(":")[1] for k, v in filtered.items() if v.get("name") == q]
        if exact:
            result.extend(exact)
            continue
        # 模糊匹配
        fuzzy = [(k.split(":")[1], v.get("name", ""))
                 for k, v in filtered.items() if q in v.get("name", "")]
        if not fuzzy:
            print(f"[错误] 找不到 '{q}'，请检查名称或代码")
            return None
        if len(fuzzy) == 1:
            result.append(fuzzy[0][0])
            continue
        # 多个候选
        print(f"[提示] '{q}' 匹配到 {len(fuzzy)} 个，请用更精确的名称或代码：")
        for code, name in fuzzy[:20]:
            print(f"  {code}  {name}")
        if len(fuzzy) > 20:
            print(f"  ...（共 {len(fuzzy)} 个）")
        return None
    return result


def _resolve_industries(queries: list[str], ref_df) -> list[str] | None:
    """
    将行业名称查询解析为东财 ts_code 列表。
    ref_df: 最近一天的行业资金流 DataFrame（含 ts_code / name 列）
    精确匹配优先，模糊多候选时提示用户。
    返回 matched name list，None 表示需要用户明确。
    """
    matched = []
    for q in queries:
        exact = ref_df[ref_df["name"] == q]
        if not exact.empty:
            matched.append(exact.iloc[0]["name"])
            continue
        fuzzy = ref_df[ref_df["name"].str.contains(q, na=False)]
        if fuzzy.empty:
            print(f"[错误] 找不到行业 '{q}'")
            print(f"  示例可用行业：{ref_df['name'].sample(min(8, len(ref_df))).tolist()}")
            return None
        if len(fuzzy) == 1:
            matched.append(fuzzy.iloc[0]["name"])
            continue
        print(f"[提示] '{q}' 匹配到 {len(fuzzy)} 个行业，请用更精确的名称：")
        for _, row in fuzzy.iterrows():
            print(f"  {row['ts_code']}  {row['name']}")
        return None
    return matched


# ─────────────────────────────────────────────────────────
# 基本面查询
# ─────────────────────────────────────────────────────────

# 各表的关键字段（减少列宽，只显示最有用的）
_FUNDAMENTAL_COLS = {
    "profit":   ["roeAvg", "npMargin", "gpMargin", "netProfit", "epsTTM"],
    "balance":  ["totalAssets", "totalDebt", "totalEquity", "monetaryFunds"],
    "cashflow": ["operateCashFlow", "investCashFlow", "financeCashFlow"],
    "growth":   ["YOYEquity", "YOYAsset", "YOYNI", "YOYEPSBasic", "YOYPNI"],
    "dupont":   ["dupontROE", "dupontAssetStoEquity", "dupontAssetTurn",
                 "dupontNitogr", "dupontPnitoni"],
}

_FUNDAMENTAL_DESC = {
    "profit":   "利润表（ROE/净利润率/毛利率/净利润/EPS）",
    "balance":  "资产负债表（总资产/总负债/净资产/货币资金）",
    "cashflow": "现金流量表（经营/投资/融资）",
    "growth":   "成长指标（净资产/总资产/净利润/EPS 同比）",
    "dupont":   "杜邦分析（ROE拆解）",
}


def cmd_fundamental(code: str, data_type: str = "profit",
                    n: int = 8, start: str = None, end: str = None):
    import data.store_fundamental as fs
    import data.store as store

    if data_type == "list":
        print("\n可用基本面表：")
        for k, v in _FUNDAMENTAL_DESC.items():
            print(f"  {k:<10} {v}")
        return

    if data_type not in _FUNDAMENTAL_DESC:
        print(f"[错误] 未知表 '{data_type}'，可选: {list(_FUNDAMENTAL_DESC)}")
        return

    # 支持名称输入
    resolved = _resolve_codes([code], asset_type="stock")
    if not resolved:
        return
    code = resolved[0]

    name = code
    try:
        ipo = store.load_ipo_dates()
        name = ipo.get(f"cn:{code}", {}).get("name", code)
    except Exception:
        pass

    df = fs.load(code, data_type)
    if df is None or df.empty:
        print(f"[错误] 未找到 {code} {name} 的 {data_type} 数据")
        return

    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]

    tail = df.tail(n) if not (start or end) else df

    # 只显示关键列（取实际存在的）
    show_cols = [c for c in _FUNDAMENTAL_COLS.get(data_type, []) if c in tail.columns]
    if not show_cols:
        show_cols = [c for c in tail.columns if c != "pubDate"]

    _print_df(tail[show_cols],
              title=f"基本面  {code} {name}  {data_type}  "
                    f"{_FUNDAMENTAL_DESC.get(data_type, '')}  "
                    f"{len(tail)} 期  （最新 {df.index.max().date()}）",
              float_fmt="{:.4f}")


# ─────────────────────────────────────────────────────────
# K 线查询
# ─────────────────────────────────────────────────────────

def cmd_kline(code: str, asset_type: str = "stock", n: int = 20,
              start: str = None, end: str = None, plot: bool = False,
              market: str = "cn"):
    import data.store as store

    df = store.load(code, asset_type=asset_type, market=market)
    if df is None or df.empty:
        print(f"[错误] 未找到 {market}/{asset_type}/{code} 的本地数据")
        return

    name = code
    try:
        ipo = store.load_ipo_dates()
        name = ipo.get(f"{market}:{code}", ).get("name", code)
    except Exception:
        pass

    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    if df.empty:
        print(f"[错误] 该时间范围内无数据")
        return

    tail = df.tail(n) if not (start or end) else df
    cols = ["close", "pct_chg", "volume", "amount", "turn", "pe_ttm", "pb_mrq"]
    show_cols = [c for c in cols if c in tail.columns]

    _print_df(tail[show_cols],
              title=f"K线行情  {code} {name}  {len(tail)} 条  "
                    f"（共 {len(df)} 行，最新 {df.index.max().date()}）")

    print(f"\n  最新收盘: {df['close'].iloc[-1]:.2f}  "
          f"区间涨跌: {df['pct_chg'].sum():.2f}%  "
          f"区间最高: {df['high'].max():.2f}  "
          f"区间最低: {df['low'].min():.2f}")

    if plot:
        _plot_kline(df, f"{code} {name}")


# ─────────────────────────────────────────────────────────
# 多标的横向对比（每行一日期，每列一标的）
# ─────────────────────────────────────────────────────────

def _cross_table(series_dict: dict, title: str, float_fmt: str = "{:.2f}"):
    """把 {name: pd.Series(index=date)} 合并成横向对比表输出"""
    df = pd.DataFrame(series_dict)
    df.index.name = "date"
    _print_df(df, title=title, float_fmt=float_fmt)


def cmd_kline_multi(codes: list[str], metric: str = "close",
                    asset_type: str = "stock",
                    n: int = 20, start: str = None, end: str = None,
                    market: str = "cn"):
    """多只K线横向对比，每行一日期，每列一标的的 metric 值"""
    import data.store as store
    ipo = {}
    try:
        ipo = store.load_ipo_dates()
    except Exception:
        pass

    series = {}
    for code in codes:
        df = store.load(code, asset_type=asset_type, market=market)
        if df is None or df.empty or metric not in df.columns:
            print(f"[跳过] {code}: 无数据或无 {metric} 列")
            continue
        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        name = ipo.get(f"{market}:{code}", {}).get("name", code)
        label = f"{code}\n{name}" if len(codes) <= 6 else code
        series[label] = df[metric] if (start or end) else df[metric].tail(n)

    if not series:
        print("[错误] 无有效数据")
        return
    _cross_table(series,
                 title=f"K线对比  {metric}  {len(series)} 只  "
                       f"{'最近'+str(n)+'条' if not (start or end) else f'{start}~{end}'}")


def cmd_stock_mf_multi(codes: list[str], metric: str = "net_mf_amount",
                       n: int = 20, start: str = None, end: str = None):
    """多只个股资金流横向对比"""
    import data.store_moneyflow as mf

    series = {}
    for code in codes:
        df = mf.load("stock", code=code)
        if df is None or df.empty or metric not in df.columns:
            print(f"[跳过] {code}: 无数据或无 {metric} 列")
            continue
        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        s = df[metric] if (start or end) else df[metric].tail(n)
        series[code] = s / 1e4  # 万元 → 亿元

    if not series:
        print("[错误] 无有效数据")
        return
    _cross_table(series,
                 title=f"个股资金流对比  {metric}(亿)  {len(series)} 只  "
                       f"{'最近'+str(n)+'条' if not (start or end) else f'{start}~{end}'}",
                 float_fmt="{:.3f}")


def cmd_ind_mf_multi(industries: list[str], metric: str = "net_amount",
                     all_industries: bool = False,
                     n: int = 20, start: str = None, end: str = None):
    """多行业资金流横向对比。all_industries=True 时显示全部行业排名汇总"""
    import data.store_moneyflow as mf

    dates = sorted(mf.list_dates("industry"))
    if not dates:
        print("[错误] 无行业资金流数据")
        return

    if start or end:
        dates = [d for d in dates
                 if (not start or d >= start) and (not end or d <= end)]
    else:
        dates = dates[-n:]

    # 确认行业 ts_code 映射
    ref = mf.load("industry", date=dates[-1])
    if ref is None:
        print("[错误] 无法读取行业资金流")
        return

    if all_industries:
        # 全行业：输出最新一天的排名汇总表
        show = ref[["name", "pct_change", "net_amount", "rank"]].copy()
        show["net_amount"] = show["net_amount"] / 1e8
        show = show.rename(columns={"pct_change": "涨跌%",
                                    "net_amount": "净流入(亿)", "rank": "排名"})
        show = show.sort_values("净流入(亿)", ascending=False)
        print(f"\n{'─'*60}\n  行业资金流汇总  {dates[-1]}\n{'─'*60}")
        pd.set_option("display.max_rows", None)
        print(show.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
        pd.reset_option("display.max_rows")
        return

    # 多行业按名称模糊匹配
    matched = {}
    for ind in industries:
        # 精确匹配优先，再模糊匹配
        exact = ref[ref["name"] == ind]
        cands = exact if not exact.empty else ref[ref["name"].str.contains(ind, na=False)]
        if cands.empty:
            print(f"[跳过] 找不到行业: {ind}")
            continue
        row = cands.iloc[0]
        if len(cands) > 1 and exact.empty:
            print(f"[提示] '{ind}' 匹配到多个，取: {row['name']}")
        matched[row["name"]] = row["ts_code"]

    if not matched:
        return

    # 跨日期收集
    series = {name: {} for name in matched}
    for d in dates:
        day = mf.load("industry", date=d)
        if day is None:
            continue
        for name, tscode in matched.items():
            row = day[day["ts_code"] == tscode]
            if not row.empty and metric in row.columns:
                v = row.iloc[0][metric]
                series[name][d] = v / 1e8 if "amount" in metric else v

    result = {name: pd.Series(vals) for name, vals in series.items() if vals}
    if not result:
        print("[错误] 无有效数据")
        return
    _cross_table(result,
                 title=f"行业资金流对比  {metric}  "
                       f"{'最近'+str(n)+'条' if not (start or end) else f'{start}~{end}'}")


def _plot_kline(df: pd.DataFrame, title: str):
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7),
                                        gridspec_kw={"height_ratios": [3, 1]},
                                        sharex=True)
        ax1.plot(df.index, df["close"], color="#1f77b4", linewidth=1.2, label="收盘价")
        ax1.fill_between(df.index, df["close"], alpha=0.08, color="#1f77b4")
        ax1.set_ylabel("收盘价（后复权）")
        ax1.set_title(title)
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax1.grid(alpha=0.3)

        vol_color = ["#d62728" if r >= 0 else "#2ca02c"
                     for r in df["pct_chg"].fillna(0)]
        ax2.bar(df.index, df["volume"] / 1e6, color=vol_color, width=1)
        ax2.set_ylabel("成交量（百万股）")
        ax2.grid(alpha=0.3)

        plt.tight_layout()
        plt.show()
    except ImportError:
        print("[提示] 安装 matplotlib 后可显示图表: pip install matplotlib")


# ─────────────────────────────────────────────────────────
# 个股资金流
# ─────────────────────────────────────────────────────────

def cmd_stock_mf(code: str, n: int = 10,
                 start: str = None, end: str = None, plot: bool = False):
    import data.store_moneyflow as mf

    df = mf.load("stock", code=code)
    if df is None or df.empty:
        print(f"[错误] 未找到 {code} 的个股资金流数据")
        return

    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]
    if df.empty:
        print("[错误] 该时间范围内无数据")
        return

    tail = df.tail(n) if not (start or end) else df
    tail = tail.copy()
    # 金额转亿元
    for col in ["buy_elg_amount", "sell_elg_amount",
                "buy_lg_amount", "sell_lg_amount",
                "buy_sm_amount", "net_mf_amount"]:
        if col in tail.columns:
            tail[col] = tail[col] / 1e4  # 万元 → 亿元

    show = [c for c in ["buy_elg_amount", "sell_elg_amount",
                         "buy_lg_amount", "sell_lg_amount",
                         "buy_sm_amount", "net_mf_amount"]
            if c in tail.columns]
    rename = {
        "buy_elg_amount": "超大买(亿)", "sell_elg_amount": "超大卖(亿)",
        "buy_lg_amount": "大单买(亿)", "sell_lg_amount": "大单卖(亿)",
        "buy_sm_amount": "小单买(亿)", "net_mf_amount": "净流入(亿)",
    }
    _print_df(tail[show].rename(columns=rename),
              title=f"个股资金流  {code}  最近 {n} 日  "
                    f"（最新 {df.index.max().date()}）",
              float_fmt="{:.3f}")

    if plot:
        _plot_stock_mf(df.tail(60), code)


def _plot_stock_mf(df: pd.DataFrame, code: str):
    try:
        import matplotlib.pyplot as plt
        if "net_mf_amount" not in df.columns:
            print("[提示] 无 net_mf_amount 字段，无法绘图")
            return
        net = df["net_mf_amount"] / 1e4  # 万元 → 亿元
        colors = ["#d62728" if v >= 0 else "#2ca02c" for v in net]
        fig, ax = plt.subplots(figsize=(14, 4))
        ax.bar(df.index, net, color=colors, width=1)
        ax.axhline(0, color="gray", linewidth=0.8)
        ax.set_ylabel("净流入（亿元）")
        ax.set_title(f"{code} 个股资金流（近60日）")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.show()
    except ImportError:
        print("[提示] 安装 matplotlib 后可显示图表")


# ─────────────────────────────────────────────────────────
# 行业资金流
# ─────────────────────────────────────────────────────────

def cmd_ind_mf(industry: str, n: int = 20, level: str = "L1",
               start: str = None, end: str = None, plot: bool = False):
    """
    按行业名称查询历史资金流。
    industry: 申万行业名或东财板块名，模糊匹配。
    """
    import data.store_moneyflow as mf
    import glob

    dates = sorted(mf.list_dates("industry"))
    if not dates:
        print("[错误] 无行业资金流数据")
        return

    # 先从最近一天确认行业名
    latest = mf.load("industry", date=dates[-1])
    if latest is None:
        print("[错误] 无法读取行业资金流")
        return

    # 模糊匹配
    candidates = latest[latest["name"].str.contains(industry, na=False)]
    if candidates.empty:
        print(f"[错误] 找不到包含 '{industry}' 的行业，可用行业示例：")
        print(latest["name"].head(20).tolist())
        return
    if len(candidates) > 1:
        print(f"[提示] 匹配到多个行业，取第一个：")
        print(candidates[["ts_code", "name"]].to_string(index=False))
    matched_name = candidates.iloc[0]["name"]
    matched_code = candidates.iloc[0]["ts_code"]

    # 跨日期收集（支持 start/end 过滤）
    if start or end:
        filtered = [d for d in dates
                    if (not start or d >= start) and (not end or d <= end)]
        scan_dates = filtered
    else:
        scan_dates = dates[-n:]
    rows = []
    for d in scan_dates:
        try:
            day_df = mf.load("industry", date=d)
            if day_df is None:
                continue
            row = day_df[day_df["ts_code"] == matched_code]
            if row.empty:
                continue
            rows.append(row.iloc[0])
        except Exception:
            continue

    if not rows:
        print(f"[错误] 没有找到 {matched_name} 的历史资金流数据")
        return

    result = pd.DataFrame(rows)
    result.index = pd.to_datetime(result.index)
    result.index.name = "date"

    show_cols = [c for c in ["pct_change", "net_amount",
                              "buy_elg_amount", "buy_lg_amount",
                              "buy_sm_amount", "rank"]
                 if c in result.columns]
    display = result[show_cols].copy()
    for col in ["net_amount", "buy_elg_amount", "buy_lg_amount", "buy_sm_amount"]:
        if col in display.columns:
            display[col] = display[col] / 1e8
    rename = {
        "pct_change": "涨跌%", "net_amount": "净流入(亿)",
        "buy_elg_amount": "超大单(亿)", "buy_lg_amount": "大单(亿)",
        "buy_sm_amount": "小单(亿)", "rank": "排名",
    }
    _print_df(display.rename(columns=rename),
              title=f"行业资金流  {matched_name}（{matched_code}）  最近 {n} 日",
              float_fmt="{:.2f}")

    if plot:
        _plot_ind_mf(result, matched_name)


def _plot_ind_mf(df: pd.DataFrame, name: str):
    try:
        import matplotlib.pyplot as plt
        if "net_amount" not in df.columns:
            return
        net = df["net_amount"] / 1e8
        colors = ["#d62728" if v >= 0 else "#2ca02c" for v in net]
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 6), sharex=True)
        ax1.plot(df.index, df["pct_change"].cumsum(), color="#1f77b4")
        ax1.set_ylabel("累计涨跌%")
        ax1.set_title(f"{name} 行业资金流")
        ax1.grid(alpha=0.3)
        ax2.bar(df.index, net, color=colors, width=1)
        ax2.axhline(0, color="gray", linewidth=0.8)
        ax2.set_ylabel("净流入（亿元）")
        ax2.grid(alpha=0.3)
        plt.tight_layout()
        plt.show()
    except ImportError:
        print("[提示] 安装 matplotlib 后可显示图表")


# ─────────────────────────────────────────────────────────
# 宏观数据
# ─────────────────────────────────────────────────────────

_MACRO_TYPES = (
    "bond_yield", "m2", "cpi_ppi", "pmi", "shrongji",
    "cny_usd", "vix", "dxy", "oil_wti", "sp500", "hsi",
    "us_bond_yield", "copper_lme", "fed_rate",
)

_MACRO_DESC = {
    "bond_yield":   "中国国债收益率（2Y/5Y/10Y/30Y）",
    "us_bond_yield":"美国国债收益率（2Y/10Y）",
    "m2":           "M2货币供应（月频）",
    "cpi_ppi":      "CPI / PPI（月频）",
    "pmi":          "PMI制造业/非制造业（月频）",
    "shrongji":     "社会融资规模/新增信贷（月频）",
    "cny_usd":      "人民币兑美元汇率",
    "vix":          "VIX 恐慌指数",
    "dxy":          "美元指数 DXY",
    "oil_wti":      "原油 WTI（美元/桶）",
    "copper_lme":   "LME 铜价",
    "sp500":        "标普500指数",
    "hsi":          "恒生指数",
    "fed_rate":     "美联储基准利率",
}


def cmd_macro(data_type: str, n: int = 20,
              start: str = None, end: str = None, plot: bool = False):
    import data.store_macro as mac

    if data_type == "list":
        print("\n可用宏观指标：")
        for k, v in _MACRO_DESC.items():
            exists = "✓" if mac.exists(k) else "✗"
            last = mac.last_date(k) or "无数据"
            print(f"  {exists} {k:<16} {v}  (最新: {last})")
        return

    if data_type not in _MACRO_TYPES:
        print(f"[错误] 未知指标 '{data_type}'，运行 'inspect macro list' 查看可用列表")
        return

    df = mac.load(data_type, start=start, end=end)
    if df is None or df.empty:
        print(f"[错误] {data_type} 该时间范围内无数据")
        return

    tail = df.tail(n) if not (start or end) else df
    _print_df(tail,
              title=f"宏观数据  {data_type}  {_MACRO_DESC.get(data_type, '')}  "
                    f"{len(tail)} 条  （最新 {df.index.max().date()}）")

    if plot:
        _plot_macro(tail if (start or end) else df.tail(max(n * 3, 60)), data_type)


def _plot_macro(df: pd.DataFrame, data_type: str):
    try:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(14, 4))
        for col in df.columns:
            if df[col].notna().sum() > 2:
                ax.plot(df.index, df[col], linewidth=1.2, label=col)
        ax.set_title(f"{data_type}  {_MACRO_DESC.get(data_type, '')}")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.show()
    except ImportError:
        print("[提示] 安装 matplotlib 后可显示图表")
