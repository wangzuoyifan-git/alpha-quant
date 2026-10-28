# research/ — 策略研究回测实验室

离线研究代码，不参与生产扫描。完整研究过程与结论见
[`docs/strategy_research.md`](../docs/strategy_research.md)。

## 快速复现

```bash
# 最终四腿组合（训练 / 样本外 / 全区间）
venv/bin/python3 -m research.combo --period all --cost 1

# 双倍成本压力测试
venv/bin/python3 -m research.combo --period all --cost 2

# 生成带图表的 PDF 总结（依赖 weasyprint + markdown）
venv/bin/python3 -m research.make_report_pdf
# -> docs/Alpha量化_策略回测总结_2026-10.pdf
```

首次运行会构建并缓存：

- `data/cache/research_panel_stock_*.npz`：全市场股票 OHLCV/估值矩阵
- `data/cache/research_panel_etf_*.npz`：ETF 矩阵
- `data/cache/research_mf.npz`：个股主力资金流
- `data/cache/research_fund_v2.npz`：point-in-time 财务矩阵

## 模块

| 文件 | 作用 |
|---|---|
| `panel.py` | 把本地 Parquet 读成 `日期 × 代码` 矩阵；ST/上市天数/成交额过滤 |
| `indicators.py` | 向量化 MA/ATR/ADX/RSI/MACD/布林 |
| `signals.py` | 内置策略规则的向量化版本（ma_cross / macd_rsi / breakout / ETF 动量） |
| `simulator.py` | 次日开盘成交、涨跌停、T+1、止损/移动止损/止盈、组合净值与指标 |
| `../backtest/fast.py` | 生产侧快速回测入口（`main.py backtest --fast`），复用本目录面板与信号 |
| `fundamental.py` | B1/B2/B3 的 point-in-time 月度组合回测 |
| `combo.py` | 最终四腿组合（Core/ETF/B1/B2 = 40/40/10/10） |
| `experiments.py` | 实验注册表：代码/数据/参数/指标写入 `experiments.jsonl` |
| `walkforward.py` | 3 折滚动 walk-forward（固定参数） |
| `platform.py` | 邻域参数稳定性检查（不选单点最优） |
| `flow.py` | 主力净流入矩阵（用于验证资金流过滤，最终未采用） |

> 注意：`simulator.py` 是研究口径，不等同于生产 `backtest/engine.py`。两者结论差异
> 主要来自“信号日收盘买入 vs 次日开盘买入”和“涨停是否可买”，详见研究文档第 1、4 节。
> 生产侧快速入口为 `python main.py backtest --fast`；多 sleeve 为
> `python main.py portfolio-backtest`；walk-forward / 参数平台为
> `python main.py walkforward`、`python main.py platform-check`。
