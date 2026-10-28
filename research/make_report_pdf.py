"""把 docs/strategy_research.md 生成带图表的 PDF 总结。

依赖：weasyprint、markdown、matplotlib（已安装）。
用法：venv/bin/python3 -m research.make_report_pdf
产物：docs/Alpha量化_策略回测总结_2026-10.pdf
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from markdown import Markdown
from weasyprint import HTML

from .combo import PERIODS, run_period

ROOT = Path(__file__).parent.parent
RESULTS = ROOT / "research" / "results"
DOCS = ROOT / "docs"
MD_PATH = DOCS / "strategy_research.md"
PDF_PATH = DOCS / "Alpha量化_策略回测总结_2026-10.pdf"

CJK = "Noto Sans CJK SC"
BLUE = "#1f4e79"
ORANGE = "#d97706"
GREEN = "#2e7d32"
GREY = "#8a8a8a"
RED = "#c62828"


def _setup_matplotlib():
    plt.rcParams["font.sans-serif"] = [CJK, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 160
    plt.rcParams["savefig.dpi"] = 160
    plt.rcParams["axes.edgecolor"] = "#cccccc"
    plt.rcParams["axes.linewidth"] = 0.8
    plt.rcParams["grid.color"] = "#e8e8e8"
    plt.rcParams["grid.linewidth"] = 0.6


def _benchmark_series(index: pd.DatetimeIndex) -> pd.Series:
    from data.store import load
    df = load("000300", asset_type="index", market="cn",
              start=str(index[0].date()), end=str(index[-1].date()))
    return df["close"].reindex(index).ffill()


def fig_equity(equity: pd.Series, index: pd.DatetimeIndex, out: Path):
    bm = _benchmark_series(index)
    from data.store import load
    _bf = load("000300", asset_type="index", market="cn",
               start="2024-07-01", end=str(index[-1].date()))["close"]
    bm_ret_full = float(_bf.iloc[-1] / _bf.iloc[0] - 1)
    eq = equity.reindex(index).ffill()
    eq_n = eq / eq.iloc[0]
    bm_n = bm / bm.iloc[0]
    dd = eq / eq.cummax() - 1

    fig, (ax, ax2) = plt.subplots(
        2, 1, figsize=(10, 7.2), sharex=True,
        gridspec_kw={"height_ratios": [3, 1.15], "hspace": 0.08},
    )
    ax.plot(eq_n.index, eq_n.values, color=BLUE, lw=1.8, label="最终组合")
    ax.plot(bm_n.index, bm_n.values, color=GREY, lw=1.4, ls="--", label="沪深300")
    ax.set_ylabel("净值（起点=1）", fontsize=10)
    ax.grid(True, axis="y")
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    ax.set_title("最终组合 vs 沪深300  |  2024-07-01 ~ 2026-10-26",
                 fontsize=13, color="#222222", pad=10)
    ax.text(0.99, 0.04,
            f"组合 {eq_n.iloc[-1]-1:+.1%}   Sharpe {_sharpe(eq):.2f}\n"
            f"基准 {bm_ret_full:+.1%}   回撤 {dd.min():.1%}",
            transform=ax.transAxes, ha="right", va="bottom",
            fontsize=9, color="#444444")

    ax2.fill_between(dd.index, dd.values, 0, color=RED, alpha=0.25)
    ax2.plot(dd.index, dd.values, color=RED, lw=0.9)
    ax2.set_ylabel("回撤", fontsize=9)
    ax2.grid(True, axis="y")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    for label in ax2.get_xticklabels():
        label.set_rotation(0)
        label.set_fontsize(8)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _sharpe(eq: pd.Series) -> float:
    ret = eq.pct_change().dropna()
    ex = ret - 0.02 / 252
    return float(ex.mean() / (ex.std() + 1e-12) * (252 ** 0.5))


def fig_sleeves(sleeves: dict[str, pd.Series], out: Path):
    fig, ax = plt.subplots(figsize=(10, 5.6))
    colors = {"core": BLUE, "etf": ORANGE, "b1": GREEN, "b2": "#7b1fa2"}
    labels = {"core": "Core 低波动趋势", "etf": "ETF 动量",
              "b1": "B1 质量", "b2": "B2 价值"}
    for k, s in sleeves.items():
        n = s / s.iloc[0]
        ax.plot(n.index, n.values, lw=1.4, color=colors.get(k, GREY),
                label=labels.get(k, k))
    ax.set_ylabel("净值（起点=1）", fontsize=10)
    ax.set_title("四腿净值曲线（归一化，单倍成本）", fontsize=13, pad=10)
    ax.grid(True, axis="y")
    ax.legend(loc="upper left", frameon=False, fontsize=9, ncol=2)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    for label in ax.get_xticklabels():
        label.set_fontsize(8)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_yearly(equity: pd.Series, index: pd.DatetimeIndex, out: Path):
    bm = _benchmark_series(index)
    periods = [("2024H2", "2024-07-01", "2024-12-31"),
               ("2025", "2025-01-01", "2025-12-31"),
               ("2026YTD", "2026-01-01", "2026-10-26")]
    labels, combo, bench = [], [], []
    for lab, st, en in periods:
        e = equity[(equity.index >= st) & (equity.index <= en)]
        b = bm[(bm.index >= st) & (bm.index <= en)]
        labels.append(lab)
        combo.append((e.iloc[-1] / e.iloc[0] - 1) * 100)
        bench.append((b.iloc[-1] / b.iloc[0] - 1) * 100)
    x = range(len(labels))
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    w = 0.36
    b1 = ax.bar([i - w / 2 for i in x], combo, w, color=BLUE, label="最终组合")
    b2 = ax.bar([i + w / 2 for i in x], bench, w, color=GREY, label="沪深300")
    for bars in (b1, b2):
        for rect in bars:
            h = rect.get_height()
            ax.annotate(f"{h:+.1f}%", (rect.get_x() + rect.get_width() / 2, h),
                        ha="center", va="bottom" if h >= 0 else "top",
                        fontsize=8.5, color="#333333")
    ax.axhline(0, color="#999999", lw=0.8)
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("区间收益（%）", fontsize=10)
    ax.set_title("分年度收益：最终组合 vs 沪深300", fontsize=13, pad=10)
    ax.legend(frameon=False, fontsize=9)
    ax.grid(True, axis="y")
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def build_charts(full: dict) -> dict[str, str]:
    _setup_matplotlib()
    eq = full["equity"]
    idx = eq.index
    p1 = RESULTS / "fig_equity.png"
    p2 = RESULTS / "fig_sleeves.png"
    p3 = RESULTS / "fig_yearly.png"
    fig_equity(eq, idx, p1)
    fig_sleeves(full["sleeve_equity"], p2)
    fig_yearly(eq, idx, p3)
    return {"equity": str(p1), "sleeves": str(p2), "yearly": str(p3)}


def charts_html(paths: dict[str, str]) -> str:
    def fig(path, cap):
        return (f'<figure><img src="file://{path}" alt="{cap}">'
                f"<figcaption>{cap}</figcaption></figure>")
    return (
        '<section class="charts"><h2>图表</h2>'
        + fig(paths["equity"], "图 1  最终组合净值与回撤（对比沪深300）")
        + fig(paths["yearly"], "图 2  分年度收益：最终组合 vs 沪深300")
        + fig(paths["sleeves"], "图 3  四腿净值曲线（Core / ETF / B1 / B2）")
        + "</section>"
    )


COVER = """
<section class="cover">
  <div class="cover-top">ALPHA QUANT · STRATEGY RESEARCH</div>
  <h1 class="cover-title">策略回测总结</h1>
  <div class="cover-sub">从 9 个内置策略到最终四腿组合</div>
  <div class="cover-line"></div>
  <div class="cover-period">回测区间 2024-07-01 ~ 2026-10-26 &nbsp;|&nbsp; 样本外 2026-01 ~ 2026-10</div>

  <table class="kpi">
    <tr>
      <td><div class="kpi-v">+60.3%</div><div class="kpi-l">全区间收益</div></td>
      <td><div class="kpi-v">-13.5%</div><div class="kpi-l">最大回撤</div></td>
      <td><div class="kpi-v">1.45</div><div class="kpi-l">Sharpe</div></td>
    </tr>
    <tr>
      <td><div class="kpi-v">+10.9%</div><div class="kpi-l">OOS 收益</div></td>
      <td><div class="kpi-v">-7.0%</div><div class="kpi-l">OOS 回撤</div></td>
      <td><div class="kpi-v">+17.98%</div><div class="kpi-l">沪深300 全区间</div></td>
    </tr>
  </table>

  <div class="cover-weights">
    <b>最终配置</b>　Core 40%　·　ETF 40%　·　B1 10%　·　B2 10%
  </div>
  <div class="cover-note">
    数据：本地 A 股 5595 只 / ETF 2210 只。回测含次日开盘成交、涨跌停不可买/不可卖、
    T+1、佣金/滑点/印花税。双倍成本压力测试：+51.6%，回撤 -14.8%，Sharpe 1.25。
  </div>
  <div class="cover-foot">生成日期：__DATE__　|　详细过程见 docs/strategy_research.md</div>
</section>
"""

CSS = """
@page {
  size: A4;
  margin: 20mm 15mm 18mm 15mm;
  @top-center {
    content: "Alpha量化 · 策略回测总结";
    font-family: "Noto Sans CJK SC", sans-serif;
    font-size: 7.5pt; color: #999999;
  }
  @bottom-right {
    content: "第 " counter(page) " 页";
    font-family: "Noto Sans CJK SC", sans-serif;
    font-size: 7.5pt; color: #999999;
  }
}
@page cover {
  margin: 0;
  @top-center { content: none; }
  @bottom-right { content: none; }
}
html { font-family: "Noto Sans CJK SC", sans-serif; }
body {
  margin: 0;
  font-family: "Noto Sans CJK SC", sans-serif;
  font-size: 9.6pt; line-height: 1.62; color: #222222;
}
.cover {
  page: cover;
  height: 297mm; box-sizing: border-box;
  padding: 32mm 24mm 22mm 24mm;
  background: linear-gradient(160deg, #0f2f4d 0%, #1f4e79 45%, #2b6ca3 100%);
  color: #ffffff;
}
.cover-top { font-size: 9pt; letter-spacing: 2.5pt; color: #bcd6ee; }
.cover-title {
  font-size: 38pt; line-height: 1.15; margin: 22mm 0 4mm 0;
  color: #ffffff; border: none; page-break-before: avoid;
}
.cover-sub { font-size: 15pt; color: #dce9f5; }
.cover-line { width: 42mm; height: 1.4mm; background: #f0b35a; margin: 9mm 0 6mm 0; }
.cover-period { font-size: 9.5pt; color: #c9dbed; }
table.kpi {
  margin-top: 18mm; width: 100%; border-collapse: separate; border-spacing: 4mm 3mm;
}
table.kpi tbody tr, table.kpi tbody tr:nth-child(even) { background: transparent !important; }
table.kpi td {
  width: 33%; background: rgba(255,255,255,0.10) !important;
  border: 0.4mm solid rgba(255,255,255,0.25);
  border-radius: 2mm; padding: 5mm 4mm; text-align: left;
}
.kpi-v { font-size: 22pt; font-weight: bold; color: #ffffff; }
.kpi-l { font-size: 8.5pt; color: #cfe1f2; margin-top: 1.5mm; }
.cover-weights {
  margin-top: 16mm; padding: 4mm 5mm; font-size: 10.5pt;
  background: rgba(240,179,90,0.16); border-left: 1.2mm solid #f0b35a;
}
.cover-note { margin-top: 8mm; font-size: 9pt; line-height: 1.7; color: #d8e6f3; }
.cover-foot { position: absolute; bottom: 18mm; font-size: 8pt; color: #a9c4dd; }

.toc-page { page-break-before: always; }
.toc-page h2 { border: none; color: #1f4e79; }
.toc-page ul { list-style: none; padding-left: 0; }
.toc-page li { margin: 1.1mm 0; font-size: 10pt; }
.toc-page li ul { padding-left: 5mm; }
.toc-page a { color: #333333; text-decoration: none; }

h1 {
  font-size: 20pt; color: #1f4e79; line-height: 1.3;
  border-bottom: 1.2mm solid #1f4e79; padding-bottom: 3mm;
  margin: 0 0 5mm 0; page-break-before: always;
}
h1 + blockquote { margin-top: 0; }
h2 {
  font-size: 14.5pt; color: #1f4e79; margin: 8mm 0 3mm 0;
  border-bottom: 0.3mm solid #cfdbe6; padding-bottom: 1.6mm;
  page-break-after: avoid;
}
h3 {
  font-size: 11.5pt; color: #2b5f8a; margin: 5.5mm 0 2mm 0;
  page-break-after: avoid;
}
p { margin: 0 0 2.6mm 0; text-align: justify; }
strong { color: #163b5c; }
hr { border: none; border-top: 0.3mm solid #e0e0e0; margin: 5mm 0; }
blockquote {
  margin: 3mm 0 4mm 0; padding: 2.5mm 4mm;
  background: #f4f8fc; border-left: 1mm solid #7fa8cc;
  color: #3d4b58; font-size: 9pt;
}
ul, ol { margin: 1.5mm 0 3mm 0; padding-left: 6mm; }
li { margin: 0.8mm 0; }
code {
  font-family: "DejaVu Sans Mono", monospace; font-size: 8.2pt;
  background: #f2f4f6; color: #8a2f2f; padding: 0.2mm 1mm; border-radius: 0.8mm;
}
pre {
  background: #f6f8fa; border: 0.3mm solid #e3e7eb; border-radius: 1mm;
  padding: 3mm 4mm; margin: 3mm 0 4mm 0; page-break-inside: avoid;
}
pre code { background: none; color: #333333; font-size: 8pt; }
table {
  width: 100%; border-collapse: collapse; margin: 3mm 0 5mm 0;
  font-size: 7.8pt; line-height: 1.35; page-break-inside: auto;
}
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th {
  background: #eaf1f7; color: #1f4e79; font-weight: bold;
  border: 0.25mm solid #c8d6e2; padding: 1.8mm 1.5mm; text-align: center;
}
td {
  border: 0.25mm solid #d8e1e8; padding: 1.6mm 1.5mm;
  text-align: left; vertical-align: top; word-break: break-word;
}
tbody tr:nth-child(even) { background: #fafcfe; }

.charts { page-break-before: always; }
figure {
  margin: 2mm 0 3mm 0; text-align: center; page-break-inside: avoid;
}
figure img { width: 100%; max-width: 154mm; border: 0.25mm solid #e0e6eb; }
figcaption { font-size: 8.5pt; color: #666666; margin-top: 2mm; }

.toc-page { page-break-after: always; }
a { color: #1f4e79; text-decoration: none; }
"""


def main():
    print("运行最终组合回测（生成图表所需净值）...", flush=True)
    full = run_period(*PERIODS["full"])
    paths = build_charts(full)
    print("图表已生成", flush=True)

    md_text = MD_PATH.read_text(encoding="utf-8")
    md = Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"])
    body = md.convert(md_text)
    toc_html = md.toc

    # 在第一张图前插入图表区（位于“0. 结论摘要”之后、“1. 回测方法”之前）
    first = body.find("<h2")
    second = body.find("<h2", first + 1)
    if second > 0:
        body = body[:second] + charts_html(paths) + body[second:]

    cover = COVER.replace("__DATE__", date.today().strftime("%Y-%m-%d"))
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Alpha量化 · 策略回测总结</title>
<meta name="author" content="Alpha量化">
<meta name="description" content="基于本地数据与全部内置策略的多轮回测总结">
<style>{CSS}</style>
</head>
<body>
{cover}
<section class="toc-page"><h2>目录</h2>{toc_html}</section>
{body}
</body>
</html>
"""
    html_path = RESULTS / "strategy_research_report.html"
    html_path.write_text(html, encoding="utf-8")

    print("渲染 PDF...", flush=True)
    HTML(string=html, base_url=str(ROOT)).write_pdf(
        str(PDF_PATH),
        options={"metadata": {"title": "Alpha量化 · 策略回测总结",
                              "author": "Alpha量化"}},
    )
    size_mb = PDF_PATH.stat().st_size / 1024 / 1024
    print(f"完成: {PDF_PATH}  ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
