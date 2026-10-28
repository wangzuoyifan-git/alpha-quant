# 回测与策略体系优化进度（2026-10-28）

> 依据：`docs/optimization_handoff.md`。原则：不改策略参数、不联网、不引入未来数据；
> 所有结果变化均给出前后对比。本轮未 commit。

---

## Phase 1（P0-1 / P0-4）：成交真实性 ✅

### 改动文件

- `config/settings.py`：新增 `EXECUTION` 配置块（`next_open` 默认 / 成本 / T+1 / 整手 / 涨跌停）。
- `backtest/execution.py`（新）：涨跌停识别、可买/可卖判定、整手、买卖费用；覆盖主板/创业板/科创板/北交所/ETF/ST。
- `backtest/engine.py`：
  - 默认 `next_open`：t 日收盘信号 → t+1 开盘成交；保留 `same_close` 复现旧结果；
  - 涨停/一字板不可买、一字跌停不可卖、停牌跳过；
  - T+1、100 股整手、卖出印花税；
  - 次日开盘建仓时，出场框架只使用 t 日及以前数据（避免用 t+1 收盘）；
  - 停牌期间净值沿用最后有效价（旧实现会漏算持仓市值）。
- `tests/test_execution.py`（新）：22 个用例，覆盖上述规则、停牌残留行（volume=0）与引擎级行为。

### 验收结果

```bash
venv/bin/python3 -m pytest tests/test_execution.py -q          # 22 passed
venv/bin/python3 -m pytest tests/test_backtest.py tests/test_execution.py \
    tests/test_strategies.py tests/test_l3b_strategies.py -q    # 102 passed
```

抽样前后对比（非全市场，仅验证成交规则已生效）：

| 策略 | 样本 | same_close | next_open |
|---|---:|---:|---:|
| quality_trend | 80 只 | +4.67% | +6.72% |
| ma_cross | 40 只 | +2.52% | +4.40% |
| macd_rsi | 40 只 | +2.30% | +5.56% |
| vol_price_breakout | 40 只 | +6.65% | +11.72% |

> 小样本方向不能推翻全市场研究结论；本轮只确认成交约束已接入。

---

## Phase 2（P0-2 / P0-4）：point-in-time 数据 ✅

### 改动文件

- `data/store_fundamental.py`：新增 `load_asof(code, data_type, as_of, history)`；
  只返回 `pubDate <= as_of` 的财报；缺 `pubDate` 时按报告期 +90 天保守回退。
- `data/store_moneyflow.py`：新增 `load_asof(data_type, code, as_of, lookback)`。
- `strategies/base.py`：
  - `generate_signals(..., as_of=None)` 接口；
  - 新增 `ttm_roe`：优先 TTM 净利润 / 平均净资产，缺失时 YTD 滚动；
  - 保留 `ttm_sum` 兼容旧逻辑。
- `strategies/builtin/`：全部策略接受 `as_of`；`ma_cross` / `money_flow` / B1 / B2 / B3
  取数改为 as-of；扫描时 `as_of=None` 行为不变。
- `research/fundamental.py`：B1/B2/B3 财务矩阵改用逐期 point-in-time `ttm_roe`；
  缓存升级为 `research_fund_v2.npz`。
- `tests/test_pit_data.py`（新）：11 个用例，覆盖未来 pubDate 不可见、资金流未来日期不可见、
  `ttm_sum` / 新 ROE / 单季与 YTD 自动识别、策略级 as-of。

### 验收结果

```bash
venv/bin/python3 -m pytest tests/test_pit_data.py -q          # 11 passed
```

B1/B2/B3 修复前后（研究口径，单倍成本）：

| 腿 | 区间 | 修复前收益 / Sharpe | 修复后收益 / Sharpe |
|---|---|---:|---:|
| B1 | 训练 | +65.60% / 1.86 | +51.80% / 1.29 |
| B1 | OOS | -9.62% / -0.72 | -12.69% / -0.93 |
| B1 | 全区间 | +56.33% / 1.17 | +39.75% / 0.74 |
| B3 | 训练 | +73.63% / 1.66 | +65.81% / 1.35 |
| B3 | OOS | -8.32% / -0.44 | -9.54% / -0.58 |
| B3 | 全区间 | +65.88% / 1.09 | +58.67% / 0.91 |
| B2 | OOS | +4.60% / 0.30 | +0.34% / -0.02 |
| B2 | 全区间 | +29.74% / 0.66 | +25.48% / 0.57 |

四腿组合（40/40/10/10）：

| 区间 | 修复前 | 修复后（最终） |
|---|---:|---:|
| 训练 | +44.82% / -13.52% / 1.63 | +43.37% / -13.81% / 1.56 |
| OOS | +10.85% / -7.02% / 0.97 | +10.00% / -7.17% / 0.88 |
| 全区间 | +60.31% / -13.52% / 1.45 | +57.78% / -13.81% / 1.38 |

> “修复后（最终）”= 新 ROE 口径 + 停牌残留行（volume=0）不可成交。
> 仅修 ROE 时 B1 全区间为 +44.57%、组合 +58.86%；停牌修复进一步下调 B1/B2。
> 详见 `docs/strategy_research.md` 12.2 / 12.3。

结论：旧口径严重高估 B1/B3；组合全区间下降约 2.5pp，方向不变，但 B1/B2 绝对表现应下调。

---

## Phase 3（P0-3 / P1-1）：信号/组合分层 + 快速引擎 ✅

### P0-3 组合层

- `backtest/portfolio.py`（新）：`PortfolioConfig` / `PortfolioResult` / `run_portfolio_backtest`。
  - 组合层统一负责资金分配、权重再平衡、现金管理、组合级回撤熔断与归因；
  - 快速路径中策略只产生信号矩阵，成交/费用/止损由 `research.simulator` 统一处理；
  - 支持 daily / monthly / quarterly 再平衡与 5pp 偏离阈值；
  - 输出每腿收益/回撤/Sharpe/贡献、相关性、最大回撤区间归因。
- 事件驱动 `backtest/engine.py` 保留现有 `BaseStrategy.generate_signals` 接口，
  扫描器行为未改；旧引擎仍负责自身出场逻辑（兼容保留）。
- `tests/test_portfolio.py`（新）：5 个用例，覆盖权重、再平衡、熔断、归因。
- `tests/test_fast_backtest.py`：生产 `quality_trend` 与 `research.combo.core_signal`
  买入信号逐日一致。

### P1-1 快速回测引擎

- `backtest/fast.py`（新）：复用 `research/panel/signals/simulator`，输出生产
  `BacktestResult`；支持 `quality_trend` / `ma_cross` / `macd_rsi` / `vol_price_breakout` /
  `etf_momentum`。
- `main.py backtest --fast`：`scan` / `backtest` / `workflow` 原参数保持兼容。
- 验收：quality_trend 全区间 **7.2s**，`+29.51% / -14.62% / Sharpe 0.666`，
  与 `research.combo` core 腿完全一致。

```bash
venv/bin/python3 main.py backtest --strategy quality_trend --fast \
  --start 2024-07-01 --end 2026-10-26
venv/bin/python3 -m pytest tests/test_portfolio.py tests/test_fast_backtest.py -q
```

遗留：事件驱动引擎尚未改用组合层统一出场；B1/B2 的 `WATCH` 仍不会在旧 CLI 自动买入。
生产方向是使用 `--fast` / `portfolio-backtest` / 新组合层。

---

## Phase 4（P1-2 / P1-3）：多 sleeve + walk-forward ✅

### P1-2 多 sleeve 组合回测

`main.py portfolio-backtest`：

```bash
venv/bin/python3 main.py portfolio-backtest \
  --start 2024-07-01 --end 2026-10-26 --rebalance monthly
```

- 可配置 `--weights core=0.4,etf=0.4,b1=0.1,b2=0.1`；
- 支持 `daily/monthly/quarterly`、`--tolerance`、`--max-drawdown`、`--cooldown`、`--cost-mult`；
- 输出每腿指标、贡献、相关性、最大回撤区间归因，并保存指标 JSON + 净值 CSV。

| 再平衡 | 总收益 | 最大回撤 | Sharpe |
|---|---:|---:|---:|
| daily | +57.78% | -13.81% | 1.38 |
| monthly | +58.36% | -13.92% | 1.38 |
| quarterly | +59.20% | -13.89% | 1.39 |

月度再平衡与 `research.combo`（+57.78%）相差 **0.58pp**，在 ±1~2pp 验收范围内。
双倍成本（`--cost-mult 2`）最终组合：**+49.06% / -15.02% / Sharpe 1.19**。
组合级回撤熔断默认关闭（`--max-drawdown 0`），可通过参数启用。

### P1-3 walk-forward + 实验注册表 + 参数平台

- `research/experiments.py`：`research/experiments.jsonl` 记录 timestamp、git commit、
  dirty 状态、数据快照（面板/财务缓存 mtime）、参数、区间、指标、净值文件、结论。
  `backtest --fast` / `backtest` / `portfolio-backtest` / `walkforward` / `platform-check`
  自动写入。
- `research/walkforward.py`：固定 train/valid/test 切分，3 折滚动，拼接 test 段得到 OOS。
- `research/platform.py`：邻域参数稳定性，输出 Sharpe 均值/标准差/离散度，不选最优点。

```bash
venv/bin/python3 main.py walkforward --strategy quality_trend \
  --start 2024-01-02 --end 2026-10-26
# OOS 拼接: +18.86% / -9.98% / Sharpe 1.11

venv/bin/python3 main.py platform-check --strategy quality_trend \
  --start 2024-07-01 --end 2026-10-26
# n=9；Sharpe mean=0.479 std=0.209；正收益占比 100%；离散度 0.438
```

> walk-forward 使用固定参数、不做样本内选参；OOS 结果依赖区间切分，不能当作新策略结论。
> 参数平台显示 `quality_trend` 在 2024-07 起的邻域内 9/9 正收益，但若把起点提前到
> 2024-01，离散度升到 1.36，说明平台稳定性对区间敏感。

---

## Phase 5（P2，部分完成）：策略复评与数据质量

### P2-2 现有策略全市场复评

使用 P0 执行模型 + 快速引擎（全市场，2024-07-01~2026-10-26）：

| 策略 | 训练 | OOS | 全区间 | 全区间回撤 / Sharpe |
|---|---:|---:|---:|---:|
| quality_trend | +21.39% | +16.72% | +29.51% | -14.62% / 0.67 |
| ma_cross（无资金流过滤） | +18.06% | -20.34% | -5.68% | -40.18% / -0.11 |
| macd_rsi | +9.74% | -17.41% | +0.91% | -31.18% / 0.01 |
| vol_price_breakout | -47.58% | -0.38% | -63.34% | -66.83% / -1.21 |
| etf_momentum（绝对动量，无 MA60 闸门） | +81.43% | +3.42% | +115.75% | -23.39% / 1.28 |

结论与原研究一致：旧技术策略在真实成交约束下基本失效；ETF 动量绝对收益高但 OOS 回撤大。
注意：`ma_cross --fast` 未包含生产版资金流确认，复评结果是“纯技术版”。

### P2-1 数据质量审计（部分）

- 新增 `tools/data_quality_audit.py`：抽样检查 OHLC 关系、重复日期、异常跳变、
  零成交、财报 pubDate 缺失、资金流最新日期对齐。
- 抽样 200 只股票（`--limit 200`）：
  - 19% 标的出现 >15% 单日跳变，主要来自停牌/复牌残留行与复权跳变；
  - 财报 pubDate 缺失率 0%（抽样 200 文件）；
  - 资金流最新日期中位数与行情一致（2026-10-26）。
- 已据此修复“停牌日不可成交”（见 Phase 2 / strategy_research 12.3）。

P2-1 未完成：退市/暂停上市标的（幸存者偏差）、ETF 复权/分红确认、ETF 容量/折溢价过滤。

---

## 测试与检查

```bash
# 本轮相关测试
venv/bin/python3 -m pytest \
  tests/test_execution.py tests/test_pit_data.py tests/test_fast_backtest.py \
  tests/test_portfolio.py tests/test_walkforward.py tests/test_backtest.py \
  tests/test_strategies.py tests/test_l3b_strategies.py tests/test_scanner.py -q
# 146 passed

# 全量测试：738 passed, 3 skipped, 2 failed
# 失败项均与本轮改动无关：
#   - integration/test_strategies_domains：注册策略数 8 的旧断言（quality_trend 早已注册）
#   - test_smart_money：融资盘路由无数据（数据环境）
#   - integration/test_end_to_end 的 dbstats CLI 超时本次已通过（此前为环境抖动）

# lint（新增/修改文件，忽略中文标点规则）
venv/bin/ruff check backtest/execution.py backtest/fast.py backtest/portfolio.py \
  research/experiments.py research/walkforward.py research/platform.py \
  tests/test_execution.py tests/test_pit_data.py tests/test_fast_backtest.py \
  tests/test_portfolio.py tests/test_walkforward.py tools/data_quality_audit.py \
  --ignore RUF001,RUF002,RUF003
# All checks passed
```

---

## 剩余风险

1. 事件引擎的止损/出场为“收盘触发、次日开盘执行”，研究版为“日内 high/low 触发”，
   两者仍会有差异；本轮到出场路径未做日内撮合。
2. 涨跌停只处理“开盘接近涨跌停/一字板”，未建模盘中开板、排队与成交概率。
3. ST 涨跌停 5% 需调用方显式传 `is_st`；事件引擎当前未自动识别 ST。
4. `ttm_roe` 的净资产为 `净利润 / ROE` 反推的近似值；单季/YTD 为启发式识别。
5. 缺 `pubDate` 的财报按 +90 天回退，属于保守假设，可能与真实公告日仍有偏差。
6. 幸存者偏差、ETF 复权/分红、ETF 容量/折溢价、退市标的仍未治理。
7. 快速引擎与研究框架共用实现，但事件驱动引擎仍是第二套实现，长期需收敛。
8. 组合再平衡是 sleeve 级资金再平衡，不做个股级跨 sleeve 调仓；行业集中度上限未接入组合层。
9. 再平衡成本按 bps 近似，未建模 ETF 折溢价、冲击成本与容量。
10. walk-forward 固定参数、不自动选参；参数平台稳定性对区间敏感，不能外推为跨周期结论。
11. P2 复评中 ma_cross 使用纯技术快照，未包含生产版资金流确认，不能直接等同生产 CLI 结果；
    数据审计只能提示问题，不能修复数据。
