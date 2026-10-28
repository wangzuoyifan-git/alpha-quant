# rounds/ — 分轮实验记录

完整的逐轮过程（假设、改动、全区间/训练/OOS 指标、结论）写在
[`docs/strategy_research.md`](../../docs/strategy_research.md) 第 2~8 节。

原始产物：

- `../results/baseline.csv`：第 1 轮全部内置策略基线
- `../results/final_metrics_cost1.0.json`：最终组合训练/OOS/全区间
- `../results/final_metrics_cost2.0.json`：双倍成本压力测试
- `../results/final_equity_cost*.csv`：最终组合日净值曲线

复现：`venv/bin/python3 -m research.combo --period all --cost 1`
