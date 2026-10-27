"""
K线图验证工具
用法:
  python tools/plot_kline.py 600519 000001 300750
  python tools/plot_kline.py 600519 --start 2024-01-01 --end 2025-12-31
  python tools/plot_kline.py 600519 --chan          # 日线叠加缠论笔/中枢/买卖点

每只股票生成三张图：日线 / 月线 / 年线（周K），保存到 data/charts/
"""
import sys
import argparse
from pathlib import Path

# 项目根目录加入 path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import mplfinance as mpf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
# 配置中文字体
plt.rcParams["font.family"] = ["Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import data.store as store

CHART_DIR = ROOT / "data" / "charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)


def load(code: str, start: str, end: str) -> pd.DataFrame:
    df = store.load(code, start=start, end=end)
    if df is None or df.empty:
        raise ValueError(f"{code} 本地无数据，请先运行 init")
    # mplfinance 需要 DatetimeIndex + OHLCV 列名大写
    df = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })
    df.index.name = "Date"
    df.index = pd.to_datetime(df.index)
    return df[["Open", "High", "Low", "Close", "Volume"]]


def resample_monthly(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("ME").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna()


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """用周K近似年线视图（数据量少时更清晰）"""
    return df.resample("W").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna()


def build_chan_overlays(code: str, start: str, end: str, plot_index: pd.DatetimeIndex):
    """构造缠论叠加元素：笔/中枢用 alines，买卖点用 scatter addplot。"""
    from chan.engine import ChanEngine

    raw = store.load(code, start=start, end=end)
    if raw is None or len(raw) < 30:
        print("  缠论叠加跳过: 行情数据不足")
        return None, []

    result = ChanEngine().analyze(raw, code=code)
    alines = []
    colors = []
    for bi in result.bis:
        if bi.start_dt is None or bi.end_dt is None:
            continue
        alines.append([(bi.start_dt, bi.start.price), (bi.end_dt, bi.end.price)])
        colors.append("red" if bi.direction == "up" else "green")

    for pivot in result.pivots:
        if pivot.start_dt is None or pivot.end_dt is None:
            continue
        alines.append([(pivot.start_dt, pivot.zg), (pivot.end_dt, pivot.zg)])
        alines.append([(pivot.start_dt, pivot.zd), (pivot.end_dt, pivot.zd)])
        colors.extend(["orange", "orange"])

    buy = pd.Series(float("nan"), index=plot_index)
    sell = pd.Series(float("nan"), index=plot_index)
    for point in result.points:
        if point.dt is None or point.dt not in plot_index:
            continue
        if point.kind.endswith("buy"):
            buy.loc[point.dt] = point.price
        else:
            sell.loc[point.dt] = point.price

    addplots = []
    if buy.notna().any():
        addplots.append(mpf.make_addplot(
            buy, type="scatter", marker="^", markersize=90, color="red",
        ))
    if sell.notna().any():
        addplots.append(mpf.make_addplot(
            sell, type="scatter", marker="v", markersize=90, color="green",
        ))

    aline_cfg = None
    if alines:
        aline_cfg = dict(alines=alines, colors=colors, linewidths=1.1, alpha=0.8)
    return aline_cfg, addplots


def plot_one(code: str, start: str, end: str, chan: bool = False):
    print(f"\n绘制 {code} ...", flush=True)
    try:
        df = load(code, start, end)
    except ValueError as e:
        print(f"  跳过: {e}")
        return

    print(f"  数据范围: {df.index[0].date()} ~ {df.index[-1].date()}  共 {len(df)} 根K线")

    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        marketcolors=mpf.make_marketcolors(
            up="red", down="green",
            edge="inherit", wick="inherit", volume="inherit",
        ),
        gridstyle="--", gridcolor="#e0e0e0",
        facecolor="white", figcolor="white",
    )

    kwargs = dict(
        type="candle",
        style=style,
        volume=True,
        tight_layout=True,
        warn_too_much_data=99999,
    )

    # ---- 日线 ----
    out = CHART_DIR / f"{code}_daily.png"
    aline_cfg, addplots = (None, [])
    if chan:
        aline_cfg, addplots = build_chan_overlays(code, start, end, df.index)
    mpf.plot(df, **kwargs,
             addplot=addplots or None,
             alines=aline_cfg,
             title=f"{code}  日线  {df.index[0].date()}~{df.index[-1].date()}"
                   + ("（缠论）" if chan else ""),
             savefig=dict(fname=str(out), dpi=150))
    print(f"  日线 -> {out}")

    # ---- 月线 ----
    df_m = resample_monthly(df)
    out = CHART_DIR / f"{code}_monthly.png"
    mpf.plot(df_m, **kwargs,
             title=f"{code}  月线  {df_m.index[0].date()}~{df_m.index[-1].date()}",
             savefig=dict(fname=str(out), dpi=150))
    print(f"  月线 -> {out}")

    # ---- 周线（年线视图）----
    df_w = resample_weekly(df)
    out = CHART_DIR / f"{code}_weekly.png"
    mpf.plot(df_w, **kwargs,
             title=f"{code}  周线  {df_w.index[0].date()}~{df_w.index[-1].date()}",
             savefig=dict(fname=str(out), dpi=150))
    print(f"  周线 -> {out}")


def main():
    parser = argparse.ArgumentParser(description="K线图验证工具")
    parser.add_argument("codes", nargs="+", help="股票代码，如 600519 000001")
    parser.add_argument("--start", default="2024-01-01", help="开始日期（默认2024-01-01）")
    parser.add_argument("--end",   default=None,         help="结束日期（默认最新）")
    parser.add_argument("--chan", action="store_true",
                        help="日线叠加缠论笔/中枢/买卖点")
    args = parser.parse_args()

    end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    print(f"时间范围: {args.start} ~ {end}")
    print(f"图片保存至: {CHART_DIR}")

    for code in args.codes:
        plot_one(code.strip(), args.start, end, chan=args.chan)

    print(f"\n完成，共生成 {len(args.codes)*3} 张图片")


if __name__ == "__main__":
    main()
