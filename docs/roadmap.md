# Alpha量化 — 后续工作路线图（Roadmap）

> 基于 2026-06-21 全量代码与文档交叉 review 产出。
> 关联文档：`docs/project.md`（总览）、`docs/requirements.md`（需求编号）、`docs/traceability.md`（GAP 编号）、`docs/agent.md`（Agent 契约）。
> 本文档为动态文档，每完成一个阶段勾选对应条目并更新状态。

---

## 一、当前状态结论

| 层 / 模块 | 状态 | 说明 |
|---|---|---|
| L1 宏观层（strategy_agent） | 已完成 | regime 判定、decision_context、LLM 研判均实现 |
| L2 行业轮动层（industry_agent） | 已完成 | 三维评分、申万 L1/L2/L3、缓存 |
| L3-A 技术策略 | 已完成 | 4 个策略升级 v2.0 |
| L3-B 基本面策略 | 已完成 | B1/B2/B3，WATCH 路径通 |
| ETF Agent | 已完成 | 三维评分 + L3 联动 |
| Orchestrator（LangGraph） | 基本完成 | evaluate 节点未接入；存在编排缺口 |
| LLM 客户端 | 已完成 | 洋葱架构、降级链、prompts |
| Tool Layer（18 工具） | 基本完成 | 若干字段不符契约 |
| 回测引擎 | 基本完成 | max_positions 硬编码等已知 TODO |
| 扫描引擎 | 基本完成 | 成交额过滤缺失（GAP-04） |
| 交易助手 | 基本完成 | 缺券商预留字段（GAP-29） |
| 数据域 store_*.py | 已完成 | 部分 P2 数据待接入 |
| 测试覆盖 | 部分完成 | GAP-07~30、GAP-48 待补 |

总体：Phase 1 已完成、Phase 2 台阶二完成，与代码实际吻合。剩余工作集中在已知缺口消除、Agent 契约对齐、A2 辅助分析、绩效驱动预备（阶段五）。

---

## 二、缺口清单（已实际核对）

### A 类：功能缺口（影响可用性，优先）

| 编号 | 缺口 | 位置 | 现状 |
|---|---|---|---|
| GAP-04 | 成交额过滤未实现 | `engine/scanner.py:200` | 只按 `min_volume`（股数）过滤，S4 要求按成交额（amount/元）过滤 |
| A2 | 信号辅助分析字段缺失 | `agent_tools/strategy_tools.py:52` | 缺技术面/资金流近5日净流入/基本面 ROE 等 P0 摘要；推送与看板亦无展示 |
| — | 回测持仓上限硬编码 | `backtest/engine.py:240,251` | 硬编码 `< 10`，未读 `RISK_CONTROL["max_positions"]` |
| — | deep_value 行业排除失效 | `strategies/builtin/deep_value.py:65` | 依赖 `df.attrs["industry_code"]`，scanner 未注入，`exclude_finance` 永不生效 |
| RC6 | is_st 字段不可用 | `scanner.py:288` | Tushare 主源 `is_st` 全量 "0"，仅靠股名过滤，部分 ST 漏网 |

### B 类：契约/字段不一致（影响 Agent 调用规范）

| 缺口 | 位置 | 现状 |
|---|---|---|
| `generate_orders` 返回 `count`，文档规定 `total_amount` | `agent_tools/trade_tools.py:60` | 不符 agent.md 3.4 |
| `list_strategies` 返回废弃 `market_condition`，缺 `version/requires_bars` | `agent_tools/strategy_tools.py` | 不符 agent.md 3.3 |
| TradeOrder 缺 `broker_order_id`（AC6 券商预留） | `trader/assistant.py:59` | F1/F2 反馈字段已有，券商单号未预留（GAP-29） |
| `check_data` 只采样首只股票判新鲜度 | `agent_tools/data_tools.py:40` | 与 scanner 采样 5 只不一致，易误报 |

### C 类：编排缺口

- `evaluate` 节点未接入 `build_workflow()`；`compare_strategies` 工具实现但从未被编排调用。
- `select_strategies` 用 `allowed_time_horizons` 筛选时，若 L2 只产出 `["short"]`，会误杀 B1/B2/B3 配置仓策略（horizon=medium/long）。位置 `strategy_agent.py:166`。

### D 类：数据域待办（data.md L410，均 P2）

铜价 LME、美联储利率、美债独立文件、分钟 K 线、十大流通股东、股东人数、E3 突发事件自动识别。ETF/L2 估值依赖 `sw_daily`/`fund_portfolio` 但 Tushare 账号无权限，当前走成员股 pe_ttm 聚合代理（过渡方案）。

### E 类：测试缺口（traceability GAP）

未覆盖：GAP-07/08（持仓盈亏更新/平仓记录）、GAP-09（回测结果持久化）、GAP-11~18（margin/macro 字段完整性）、GAP-19~30（复权双价/性能/推送字符限制与重试/策略汇总）、GAP-48（OB-R2/R3 收益回填与绩效驱动，阶段五核心）。

---

## 三、阶段规划与里程碑

### 阶段 A — Phase 1 收尾（消除已知 bug）✅ 已完成（2026-06-21）

里程碑：所有 A 类功能缺口修复，回归测试通过（497 passed / 2 skipped）。

- [x] A-1 成交额过滤 GAP-04：scanner 增加 `min_amount` 配置项（默认 3000 万），用 `amount`（真实成交额/元）过滤；新增 `tests/test_scanner_filters.py::TestAmountFilter`
- [x] A-2 回测 `max_positions` 改读配置：`BACKTEST["max_positions"]`（对齐 RC2），`backtest/engine.py` 去除硬编码 10；新增 `TestBacktestMaxPositions`
- [x] A-3 修复 deep_value 行业排除：新增 `store_meta.get_stock_l1_industry(code)` 反查申万一级行业，策略不再依赖未注入的 `industry_code`；新增金融排除/开关测试
- [x] A-4 统一 `check_data` 新鲜度采样逻辑：采样前 5 只取最新日期（与 scanner 一致），避免单只未更新误报
- [x] A-5 补 GAP-04 等对应测试并运行（全量回归通过）

### 阶段 B — Agent 契约对齐

里程碑：Tool 层返回结构符合 agent.md 契约，编排补全。

- [ ] B-1 `generate_orders` 返回补 `total_amount`；TradeOrder 加 `broker_order_id` 预留字段（GAP-29）
- [ ] B-2 `list_strategies` 改返回 `version/requires_bars`，去 `market_condition`
- [ ] B-3 修复 `select_strategies` 对配置仓 horizon 的误杀
- [ ] B-4 接入 `evaluate` 节点 + `compare_strategies` 到 workflow

### 阶段 C — A2 辅助分析（核心可用性）

里程碑：每条信号附带 P0 辅助分析，推送与看板同步展示。

- [ ] C-1 信号聚合技术面/资金流近5日净流入/基本面 ROE 等 P0 摘要
- [ ] C-2 推送（`notifier/push.py`）与 Web 看板同步展示
- [ ] C-3 补 GAP-14（情绪因子计算）等测试

### 阶段 D — 阶段五预备：绩效驱动（GAP-48）

里程碑：交易闭环可回填收益，为绩效驱动策略选择铺路。

- [ ] D-1 F2 持仓盈亏更新（GAP-07）
- [ ] D-2 平仓记录（GAP-08）
- [ ] D-3 回测结果持久化（GAP-09）
- [ ] D-4 OB-R2/R3 收益回填闭环

### 穿插补测（不阻塞主线）

- [ ] GAP-11~18 数据字段完整性
- [ ] GAP-19~30 复权双价/性能/推送字符限制与重试/策略汇总

### 推迟项

D 类数据域（铜价/美联储/分钟线/股东等）均 P2，部分受 Tushare 权限限制，按需推迟，不进入近期里程碑。

---

## 四、变更记录

| 日期 | 变更 |
|---|---|
| 2026-06-21 | 初版：基于全量 review 产出阶段 A~D 规划 |
