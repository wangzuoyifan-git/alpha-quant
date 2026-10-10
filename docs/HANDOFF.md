# Alpha量化 — Session 交接文档

> 创建：2026-06-21
> 适用：承接本轮（阶段 A~D）工作的新 Session
> 配套：`docs/roadmap.md`（路线图，权威进度）、`docs/traceability.md`（GAP 编号与测试追溯）

---

## 一、本轮已完成（阶段 A~D）

基于全量代码与文档 review，按 `docs/roadmap.md` 推进，已提交 5 个 commit：

| commit | 阶段 | 内容 |
|---|---|---|
| b18f1d4 | A | 成交额过滤(GAP-04)、回测持仓上限改读配置、deep_value 行业排除修复、check_data 新鲜度采样 |
| d9e39cb | B | generate_orders 返回 total_amount + TradeOrder 券商预留字段(GAP-29)、list_strategies 契约对齐 |
| 8e93444 | B | 进度归档（B-3/B-4 核实为误报撤回） |
| 7a97e45 | C | A2 P0 辅助分析聚合(`agent_tools/analysis.py`) + 推送/Web 看板展示 |
| 0b1cc64 | D | GAP-07/08/09 交易闭环测试补齐 |

**测试基线**：667 passed / 2 skipped（运行须加 `--timeout=30`；另有既有 `test_smart_money::test_assess_routes` 失败，与 2026-10 数据层优化无关）。

### 关键修复细节（新 Session 需知）
- **deep_value 行业排除**：原依赖 `df.attrs["industry_code"]`，但 scanner 从未注入该列，过滤永久失效。已改为 `data/store_meta.py:get_stock_l1_industry(code)` 反查申万一级行业（带模块级缓存 `_STOCK_L1_CACHE`）。
- **成交额过滤(GAP-04)**：后复权价导致 `volume×close ≠ 成交额`，必须用 K 线 `amount` 列。配置项 `SCAN["min_amount"]`（默认 3000 万元，0=关闭）。
- **A2 辅助分析**：`agent_tools/analysis.py`。信号经 `enrich_signal_objects()` 写入 `signal.extra["analysis"]`，随 `signals/manager.py:save_signals` 持久化，Web 看板 `/signals` 与推送 `notifier/push.py` 共同消费。资金流 `main_net_inflow_5d` 单位是**万元**（展示时 /10000 转亿元）。

### 撤回项（重要：避免新 Session 重复踩坑）
- **B-3（select_strategies horizon 过滤）**：经核实**不是 bug**。horizon 是有意设计的选择维度，测试 `test_medium_horizon_selects_b1_b3_not_b2` 明确编码 medium→B1/B3、long→B2。`["short"]` 仅在"有景气数据且偏低+高资金流"纯动量行业触发，此时不选长期配置仓策略是正确行为，且第 180 行有筛空回退全集兜底。**不要再改**。
- **B-4（evaluate 节点接入 workflow）**：`compare_strategies`/`run_backtest` 是回测研究能力，不属于收盘后实盘扫描的日常工作流；绩效驱动策略选择本就规划在阶段五（GAP-48）。当前接入是无消费方的过早抽象。**留待阶段五**。

---

## 二、待办（按优先级）

### 优先级 1：穿插补测（不阻塞主线，纯测试，风险低）

对照 `docs/traceability.md` 第四节，以下 GAP 多为"能力已实现、缺测试"。**动手前务必先读实现确认现状**，避免给未实现功能写测试。

| GAP | 内容 | 建议测试位置 | 备注 |
|---|---|---|---|
| GAP-11 | margin 数据收盘后当日可用（时效） | `tests/test_store_moneyflow.py` | 验证 last_date |
| GAP-12/13 | margin 个股/市场级字段完整 | 同上 | store save/load 往返 |
| GAP-14 | A2 情绪因子（融资余额近5日变化/占成交比） | `tests/test_analysis.py` | **需先在 analysis.py 实现 margin 摘要**（当前未实现，属 A2 P1） |
| GAP-15/16/17 | macro 月/日/境外字段完整性 | `tests/test_store_macro.py` | 往返一致 + 非空抽查 |
| GAP-18 | 美联储议息利率3日内更新 | — | **fed_rate 数据未下载**（data.md L410），暂不可测 |
| GAP-25 | 推送 ≤ 2000 字符 | `tests/test_notifier.py`（新建或并入） | 已有 MAX_PUSH_CHARS=2000，验证截断逻辑 |
| GAP-26 | 推送失败重试 + 渠道隔离 | 同上 | mock 失败，验证 `_send_fallback` |
| GAP-27 | 交易记录按策略汇总 | `tests/test_trader.py` | **GAP-07 的 TestF2PnlSummary 已部分覆盖**，核实是否够 |

### 优先级 2：需先核实实现现状的项

- **GAP-19/30（复权双价存储）**：traceability 要求 store 同时保留原始价和复权价两列。当前 `data/market/` 是 Tushare hfq（后复权）单列，`data/market_baostock/` 是另一套基线。**需先确认是否真有双列存储需求，还是文档过时**。这可能涉及数据层改造，非纯补测，动手前与用户确认。
- **GAP-20（复权异常告警）**：需确认 downloader 是否已有跳变检测逻辑。
- **GAP-21/22（信号合并权重可配置/共振加成）**：核实 `engine/scanner.py` 的 `_merge_signals` 是否已实现，再补测。
- **GAP-23/24（性能：单只<100ms/列表<1s）**：计时测试，注意环境波动，用宽松阈值。

### 优先级 3：阶段五大特性（独立，工作量大，建议单独规划）

- **GAP-48 / OB-R2/R3 收益回填 + 绩效驱动 select_strategies**：详见 `docs/agent.md` 阶段五章、`docs/requirements.md` 8.6 节（OB-R2/R3）。需要：决策日志回读 → 计算实际 5 日收益/alpha(vs 沪深300) → 反思生成 → select_strategies 从静态映射升级为查历史 regime 胜率。**建议先产出设计文档再实现**。

### 优先级 4：数据域接入（P2，部分受限）

`docs/data.md` L410「待下载」：铜价 LME、美联储利率、美债独立文件、分钟 K 线、十大流通股东、股东人数。注意 `sw_daily`/`fund_portfolio` **Tushare 账号无权限**，ETF/L2 估值现用成员股 pe_ttm 聚合代理（过渡方案）。

---

## 三、必须遵守的约定

1. **运行测试一律加 `--timeout=30`**：`venv/bin/python3 -m pytest tests/ -q -p no:cacheprovider --timeout=30`。无超时时收集阶段可能偶发 IO 抖动假死（本轮踩过坑，非代码问题）。全量约 26s。
2. **永不提交 `data/portfolio.json`**：被 git 跟踪但含真实持仓数据，工作区有未提交的格式迁移改动。每次 `git add` 须显式排除。**遗留待办**：建议 `git rm --cached data/portfolio.json` 并加入 `.gitignore`（需用户确认）。
3. **提交前对照文档**：改动行为契约时同步更新 `docs/`（roadmap/traceability/对应模块文档）。
4. **诚实核实**：review 结论可能有误判（本轮 B-3/B-4 即是）。动手改"bug"前先读实现 + 跑现有测试确认真是 bug。
5. **运行入口**：`venv/bin/python3 main.py <command>`。配置在 `config/settings.py`。

---

## 四、项目状态速览

- 阶段定位：Phase 1 完成、Phase 2 台阶二完成（见 `docs/project.md`）。
- 三层决策：L1 宏观(strategy_agent) / L2 行业轮动(industry_agent) / L3 个股(技术 L3-A + 基本面 L3-B)，均规则版已实现。
- LLM：洋葱架构 `llm_clients/`，降级链完整，仅用于辅助研判（禁入核心路径）。
- 数据域：market/meta/fundamental/macro/moneyflow/events 基本完成；factor 域台阶二前暂空。

文档索引见 `docs/project.md` 第六章。
