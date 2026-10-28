# Alpha量化 — 回测与策略体系优化交接文档

> 交接时间：2026-10-28
> 前置阅读：`docs/strategy_research.md`（本轮研究过程与结论）、`docs/strategy.md` 2.2.2、`research/README.md`
> 本文档目标：让新 Session 在不重复研究的前提下，按优先级完成回测真实性、数据 as-of、组合回测和工程化改造。
>
> **实施状态（2026-10-28）**：P0-1 / P0-2 / P0-3 / P0-4 / P1-1 / P1-2 / P1-3 已完成；
> P2-2 现有策略全市场复评已完成，P2-1 数据质量审计已覆盖停牌残留/财报 pubDate/资金流对齐，
> 退市标的、ETF 复权/分红、ETF 容量/折溢价仍待做。进度、指标差异与剩余风险见
> `docs/optimization_progress.md`。实际 CLI：`backtest --fast`、`portfolio-backtest`、
> `walkforward`、`platform-check`、`tools.data_quality_audit`。

---

## 0. 交接摘要

本轮已完成：

- 新建 `research/` 研究回测框架（面板化、向量化信号、次日开盘成交、涨跌停、T+1、成本、止损/移动止损）。
- 新增并注册 `strategies/builtin/quality_trend.py`（低波动质量趋势，L3-A）。
- 通过 7 轮回测综合出最终四腿组合：`quality_trend 40% + etf_momentum 40% + B1 10% + B2 10%`。
- 全区间（2024-07~2026-10）：**+60.31% / 最大回撤 -13.52% / Sharpe 1.45**；
  OOS（2026）：**+10.85% / -7.02% / Sharpe 0.97**；双倍成本：**+51.60% / -14.75% / Sharpe 1.25**（上述为修复前研究值；P0 修复后最终为 +57.78% / OOS +10.00% / 双倍成本 +49.06%，见 `docs/optimization_progress.md`）。
- 生成 PDF：`docs/Alpha量化_策略回测总结_2026-10.pdf`。
- 已通过相关单测：`tests/test_strategies.py`、`tests/test_l3b_strategies.py`、`tests/test_backtest.py`（82 passed）。

**下一步不是继续找策略，而是把“研究结论”变成“生产可信的回测与组合能力”。**
当前最大风险：生产 `backtest/engine.py` 的成交假设和未来函数会得出与原研究相反的结论。

---

## 1. 已交付产物（不要重做）

| 产物 | 路径 | 说明 |
|---|---|---|
| 新策略 | `strategies/builtin/quality_trend.py` | 已注册进 `BUILTIN_STRATEGIES` |
| 研究框架 | `research/panel.py` / `signals.py` / `simulator.py` / `fundamental.py` | 向量化、可复现 |
| 最终组合 | `research/combo.py` | 四腿 40/40/10/10 |
| 结果 | `research/results/*.json` / `*.csv` / `baseline.csv` | 训练/OOS/全区间/成本压力 |
| 报告 | `docs/strategy_research.md`、`docs/Alpha量化_策略回测总结_2026-10.pdf` | 过程记录 |
| 图表 | `research/results/fig_*.png` | PDF 用 |
| PDF 生成 | `research/make_report_pdf.py` | `venv/bin/python3 -m research.make_report_pdf` |

**关键研究结论（新 Session 必须接受的前提）：**

1. 原 `ma_cross` / `macd_rsi` / `vol_price_breakout` 在加入“次日开盘 + 涨停不可买”后基本失效，历史表现高度依赖不可成交的追涨。
2. 资金流作为入场过滤器在本区间有害（+29.5% → -13.5%），不要默认它有效。
3. 低波动 + 温和动量 + 回踩 MA20 入场是有效的；高波动/追高是主要亏损源。
4. 训练/OOS 参数排序相关性很低（0.09~0.37），必须坚持参数平台中部，不能选全区间最优点。

---

## 2. 待优化问题清单（按优先级）

### P0-1 回测成交真实性（最高优先级）

**问题**：

- `backtest/engine.py:253` 在信号当日按 `close` 买入，隐含“收盘前已知收盘价”。
- `backtest/engine.py:170` 起逐日、逐股重算 `generate_signals(df_slice)`，无涨跌停/T+1/印花税。
- `config/settings.py::BACKTEST` 只有佣金、滑点、仓位、持仓数，缺少成交规则。

**目标**：

- 增加统一执行模型：t 日收盘信号 → t+1 开盘成交；涨停/一字板不可买；一字跌停不可卖；T+1；100 股整手；印花税；停牌跳过。
- 涨跌幅按代码自动：主板 10%、创业板/科创板 20%、北交所 30%、ETF 10%。
- 保留兼容开关 `execution_mode = "next_open" | "same_close"`，默认 `next_open`，旧行为仅用于历史复现。

**参考实现**：`research/simulator.py` 的 `can_buy` / `can_sell` / `limit_up` / `one_word_up` 逻辑，但不要直接复制；生产版需接入 `config`、日志和测试。

**验收**：

- 新增测试：次日开盘成交、涨停跳过、一字跌停不可卖、T+1、印花税、整手。
- `main.py backtest` 仍可运行；旧策略在真实约束下结果会变化，需在文档记录差异。
- `quality_trend` 生产回测与 `research.combo` 的 core 腿收益差异可解释（允许因止损/成交细节有差异，但不能出现数量级差异）。

### P0-2 Point-in-time 数据（消除未来函数）

**问题**：

- `ma_cross.py:136` 直接 `load_mf("stock", code=code)` 取最新资金流。
- `fundamental_value.py:55/62`、`deep_value.py:79/89`、`growth_pick.py:53/64` 直接 `load_fund(code, ...)` 取最新财报。
- 回测时这些数据没有按回测日过滤，基本面/资金流策略存在严重未来函数。
- `strategies/base.py:69 ttm_sum` 假设 `roeAvg` 为单季值，但当前数据部分为 YTD 累计，B1 的 ROE 阈值可能被高估。

**目标**：

- 在 `data/store_fundamental.py` 增加 `load_asof(code, data_type, as_of)`：返回 `pubDate <= as_of` 的最新报告。
- 在 `data/store_moneyflow.py` 增加 `load_asof(data_type, code, as_of, lookback)`：只返回 as_of 之前的数据。
- 策略通过显式上下文取数，例如 `generate_signals(df, code, name, as_of=None)` 或 `StrategyContext`；`as_of=None` 时保持扫描行为。
- 修正或新增 TTM ROE 口径：优先 `TTM 净利润 / 平均净资产`，保留旧 `ttm_sum` 兼容。

**验收**：

- 合成测试：pubDate 在回测日之后的财报不可见；资金流未来日期不可见。
- B1/B2/B3 回测结果在修复前后各跑一次，记录差异与原因。
- 新增测试覆盖 `ttm_sum`/新 ROE 口径。

### P0-3 策略信号与组合构建分离

**问题**：

- 策略同时负责入场、出场、止损、仓位，导致扫描/回测/组合行为不一致。
- `etf_momentum` 输出 `WATCH`，而 `backtest/engine.py` 只买 `BUY`；ETF 动量本质是横截面轮动，不是单标的信号。
- `compute_exit_levels` 在每个策略内重复实现，难以统一风控。

**目标**：

- 抽出 `ExecutionModel` / `PortfolioManager`：统一处理成交、费用、T+1、止损、仓位、行业上限、regime 开关。
- 策略只负责“产生 alpha 信号 + 可选出场意图”；组合层负责最终买卖。
- 为 `etf_momentum` 增加 rotation 类型或在组合层把 WATCH 作为候选池排序。
- 保留现有 `BaseStrategy` 接口兼容扫描器；回测走新接口。

**验收**：

- 新组合层能复现四腿组合权重与再平衡。
- 扫描器行为不变（或明确记录变更）。
- 新增“生产策略 vs 研究向量化信号”一致性测试（同一标的、同一日期信号应一致）。

### P0-4 执行/未来函数测试

**目标**：把本轮踩过的坑固化为测试：

- 信号 t 日产生，t+1 才可买；
- 开盘接近涨停（主板/创业板/科创板/北交所）不可买；
- 一字跌停不可卖；
- T+1 当日买入不可卖；
- 财报/资金流 as-of 过滤；
- 停牌日不成交。

**验收**：`pytest -q` 通过；新增测试独立、不依赖网络。

### P1-1 向量化快速回测引擎 / CLI

**问题**：生产引擎 O(N·D²)，全市场回测不可用；研究框架与生产框架两套逻辑，容易漂移。

**目标**：

- 把 `research/simulator.py` 的面板化/向量化能力产品化；
- 提供 `main.py backtest --fast` 或 `main.py research-backtest`；
- 统一信号生成、执行模型、绩效指标，研究/生产共用一套核心。

**验收**：

- 全市场 `quality_trend` 回测 <60 秒（当前研究版约 1 秒，生产版目标可放宽）。
- CLI 输出与 `research.combo` 的核心指标一致（允许执行细节差异，需在报告中说明）。
- 缓存面板可复用，避免每次重读 5595 个 Parquet。

### P1-2 多 sleeve 组合回测

**目标**：

- 支持 `allocations`、目标权重、月度/季度再平衡、现金管理、组合级回撤熔断。
- 支持股票/ETF 混合资产；ETF 轮动按 TopN 排序和定期调仓。
- 把本轮 40/40/10/10 组合产品化，而不是只存在于 `research/combo.py`。

**验收**：

- 组合回测能复现研究结果（允许 ±1~2pp 差异，需解释）。
- 支持配置化权重，输出每腿贡献、相关性、回撤归因。

### P1-3 Walk-forward 与实验注册表

**目标**：

- 固定训练/验证/测试流程；支持 3 折滚动 walk-forward。
- 增加 `research/experiments.jsonl`（或 SQLite）：记录参数、数据快照、git commit、指标、净值文件、结论。
- 增加参数平台检查：同一策略在邻域参数下的稳定性，而非单点最优。

**验收**：

- 任意一次回测都能从注册表追溯到代码版本、数据版本、参数、结果。
- 报告默认展示训练/OOS/全区间，不再只报全区间。

### P2-1 数据治理

- 幸存者偏差：补退市/暂停上市标的；至少提供 point-in-time universe。
- ETF 复权/分红：确认 close 是否含分红再投资；统一 adjusted close 或总收益序列。
- ETF 容量：单 ETF 日均成交额占比上限、规模下限、折溢价过滤。
- 数据质量检查：价格跳变、成交额单位、pubDate 缺失、资金流日期对齐、ST 状态变更。

### P2-2 现有策略重新评估

- 用 P0 的真实成交模型重新评估 `ma_cross` / `macd_rsi` / `vol_price_breakout`；
- 对 `money_flow` 不要恢复为独立策略；测试其作为 regime 条件化辅助因子或风险预警；
- `chan_theory` 增加缓存/增量计算，或只对流动性前 N 只每周重算；
- 修正 B1 ROE 口径后，重新评估 B1/B3 是否仍值得保留。

---

## 3. 建议实施顺序

### Phase 0：冻结基线（0.5 天）

- 记录当前 `research.combo` 结果、`git commit`、数据快照。
- 跑一遍现有测试，确认基线。
- 不要在这个阶段改策略逻辑。

### Phase 1：执行模型（P0-1，2~3 天）

1. 在 `config/settings.py` 增加 `EXECUTION` 配置。
2. 在 `backtest/engine.py` 实现 `ExecutionModel`（或独立 `backtest/execution.py`）。
3. 接入涨跌停、T+1、印花税、整手、停牌。
4. 写测试；跑旧策略对比，记录差异。

### Phase 2：as-of 数据（P0-2，2~3 天）

1. `store_fundamental.load_asof`、`store_moneyflow.load_asof`。
2. 策略取数改为显式 `as_of`；保持扫描兼容。
3. 修正 TTM ROE；写 PIT 测试。
4. 重跑 B1/B2/B3，记录修复前后差异。

### Phase 3：信号/组合分层 + 快速引擎（P0-3、P1-1，3~5 天）

1. 抽 `PortfolioManager` / `ExecutionModel`。
2. 产品化向量化面板回测。
3. 增加 `--fast` CLI。
4. 加生产/研究信号一致性测试。

### Phase 4：多 sleeve + walk-forward（P1-2、P1-3，3~5 天）

1. 组合权重、再平衡、现金管理。
2. 组合级回撤熔断。
3. 实验注册表 + 3 折 walk-forward。

### Phase 5：数据治理与策略复评（P2，按需）

1. 幸存者偏差、ETF 复权、容量。
2. 现有策略重新评估。
3. B1 ROE 修复后的配置腿复评。

---

## 4. 实施约束

1. **不联网**：只使用本地数据；不要重新下载全市场数据。
2. **不破坏 CLI**：`main.py scan` / `backtest` / `workflow` 必须继续可用。
3. **不默认改策略逻辑**：先改执行/数据口径；策略参数变更必须走训练/OOS，并记录。
4. **不提交**：除非用户明确要求 commit。
5. **不选全区间最优参数**：参数选择只看训练集，OOS 只用于验证。
6. **保持研究/生产一致**：同一规则在两套实现中必须有测试保证，或只保留一套核心。
7. **报告差异**：任何回测结果变化都要在文档记录“为什么变、变了多少”。

---

## 5. 验证命令

```bash
# 相关单测
venv/bin/python3 -m pytest tests/test_strategies.py tests/test_l3b_strategies.py tests/test_backtest.py -q

# 全量测试（时间允许时）
venv/bin/python3 -m pytest -q

# lint（只检查新增/修改文件；项目历史文件有中文标点告警，可忽略 RUF001/2/3）
venv/bin/ruff check backtest research strategies/builtin config data

# 复现研究组合
venv/bin/python3 -m research.combo --period all --cost 1

# 生成 PDF
venv/bin/python3 -m research.make_report_pdf
```

**P0 完成的判定标准：**

- 新增执行模型测试全部通过；
- 基本面/资金流策略不再读取未来数据；
- `quality_trend` 在新生产回测中的结果可解释，且与研究报告方向一致；
- 文档更新：`docs/data.md`（as-of 接口）、`docs/strategy.md`（执行假设）、`docs/strategy_research.md`（差异记录）。

---

## 6. 交接口令（复制到新 Session）

```text
你是 Alpha量化项目的新 Session。先阅读：
1) docs/optimization_handoff.md
2) docs/strategy_research.md
3) research/README.md
4) backtest/engine.py、strategies/base.py、config/settings.py

任务：按 docs/optimization_handoff.md 的 P0 → P1 顺序，优化回测与策略体系。
本轮不要继续寻找新策略，重点是让现有研究结论在生产回测中可信。

硬性要求：
- 先做 P0-1（成交真实性）、P0-2（point-in-time 数据）、P0-4（测试）；
- 保持 main.py scan/backtest/workflow 兼容；
- 不联网、不使用未来数据、不默认改策略参数；
- 任何参数/逻辑变更都要跑训练/OOS 并记录差异；
- 不 commit，除非我明确要求；
- 每完成一个 Phase，输出：改了哪些文件、关键设计、测试命令与结果、剩余风险。

建议工作方式：
- 先用 todowrite 列出 Phase 1~3 的任务；
- 每个 Phase 先写测试/验收标准，再改实现；
- 对不确定的接口设计，先给出 2~3 个方案和取舍，再落代码；
- 最终交付一份简短的 docs/optimization_progress.md，记录每阶段完成情况与指标变化。

从 Phase 1 开始：在 backtest/engine.py 实现 next-open 成交、涨跌停不可买/不可卖、T+1、印花税、整手、停牌跳过，并补测试。
```

---

## 7. 附：本轮研究的关键文件索引

- 研究过程：`docs/strategy_research.md`
- PDF：`docs/Alpha量化_策略回测总结_2026-10.pdf`
- 新策略：`strategies/builtin/quality_trend.py`
- 研究回测器：`research/simulator.py`
- 组合：`research/combo.py`
- 基线结果：`research/results/baseline.csv`
- 最终结果：`research/results/final_metrics_cost1.0.json`、`final_metrics_cost2.0.json`
