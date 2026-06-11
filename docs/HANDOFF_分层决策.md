# 分层决策架构 — Session 交接文档

> 生成时间：2026-06-11
> 用途：跨 session 接续分层决策架构的后续阶段实现
> 阅读顺序：先读本文档 → 再读 docs/agent.md 第六章 → docs/requirements.md 第八章

---

## 一、项目背景（30秒理解）

Alpha量化项目正在实现一套**三层决策架构**（用户需求："宏观→行业→个股"分层策略，上层信号变化切换下层策略模式）。

设计参考：TradingAgents（多Agent辩论）+ Qlib Nested Decision（约束前置、分层嵌套）。

**核心架构**：
```
L1 宏观场景层（regime/risk_mode）→ 输出 decision_context（约束上下文）
   ↓ 约束传递
L2 行业轮动层（资金流筛行业池）→ 填充 allowed_industries
   ↓ 约束前置（关键：必须穿透到引擎层）
L3 个股选择层（行业池内规则扫描）→ 产出信号
```

**最重要的设计原则**（贯穿所有阶段）：
1. **约束前置**：上层收窄下层动作空间，下层在进入前就被约束（不是扫完再过滤）
2. **混合架构**：规则负责广度（全市场扫描），LLM 负责深度（候选分析），LLM 永不进核心信号路径
3. **文档先行 + TDD**：先更新文档 → 写测试 → 实现 → 验证 → 提交
4. **架构级文档同步**：分层改造影响多个文档，不只改 agent.md

---

## 二、已完成阶段（commit 记录）

### ✅ 阶段一：L1 宏观场景层（commits c7153d0, 46f1e8e, bb2d618, 5d53ab4）

**实现**：
- `agents/strategy_agent.py`：
  - `evaluate_market_condition()` → 结构化 regime 输出（regime/risk_mode/confidence/indicators/trigger_reason）
  - 5 种 regime：crisis/risk_off/balanced/risk_on/volatile
  - regime→risk_mode 映射：defensive/balanced/aggressive
  - crisis 判定：VIX>40 或 沪深300单日跌幅>5%（跨域读 data.store 000300，容错回退）
  - volatile 判定：VIX 20日标准差>5（用2076行VIX历史数据）
  - `select_strategies(risk_mode=...)`：defensive→[fundamental_value,etf_momentum], aggressive→[ma_cross,vol_price_breakout,money_flow], balanced→全部6个
  - `build_decision_context()`：构建贯穿三层的约束对象，max_position按risk_mode（defensive=0.3/balanced=0.7/aggressive=1.0）
- `agents/orchestrator.py`：
  - WorkflowState 新增 macro_regime + decision_context 字段（total=False向后兼容）
  - macro_regime_node（L1节点）+ route_after_macro 条件边
  - log_result_node 输出 decision_log_<date>.json
- `tests/test_macro_regime.py`：12 passed, 1 skipped

**复查发现并修正的偏差**（5d53ab4）：阶段一最初实现把 volatile 简化为"VIX 20~30区间"、crisis 漏判A股跌幅、max_position文档缺失，复查后全部补齐。**教训：文档先行容易出现"实现简化但没回写文档"的漂移，每阶段结束要对照文档复查。**

### ✅ 阶段二：L2 行业轮动层（commit 9388805）

**实现**（按依赖从底往上，确保约束穿透引擎层）：
- `engine/scanner.py`（引擎层，关键）：
  - `scan(date, asset_types, industry_filter)` 新增 industry_filter
  - `_get_filtered_stock_list(industry_filter)` join industry_sw 在标的池构建阶段收窄
- `agent_tools/strategy_tools.py`：`run_scan(date, strategies, industry_filter)` 透传
- `agents/industry_agent.py`（新增 L2）：
  - `evaluate_industry_rotation(risk_mode, date)` 主接口
  - `_load_moneyflow_window()` / `_score_industries()` / `_load_latest_moneyflow_ind()`
  - 数据对齐：东财moneyflow_ind板块名 精确匹配 申万一级行业名（31/31匹配）
  - 行业池宽度：defensive取Top7/balanced取Top10/aggressive取Top12
  - 数据缺失容错：返回空池+fallback=True，L3退化全市场
- `agents/orchestrator.py`：industry_rotation_node + 流程串联 L1→L2→L3
- `tests/test_industry_rotation.py`：8 passed

**关键验收**：真实数据扫描标的池 4951→1704只（34.4%），证明约束在引擎层生效。

**重要数据问题（已解决并记录）**：东财行业（1012个BK板块）vs 申万一级（31个801代码）是两套不兼容体系，用名称精确匹配对齐。财务维度（行业收入/利润聚合）标注为**可选**，阶段二第一版只用资金流，待数据覆盖率提升后补。

---

## 三、阶段三任务（下一步，P1）：可靠性与可观测加固

> 详见 docs/agent.md「阶段三（P1）：可靠性与可观测加固」

**目标**：分层链路变长（L1→L2→L3→风控→订单），需要断点续跑和决策留痕。

**关键交付**：

### 3.1 LangGraph Checkpoint 断点续跑（借鉴 TradingAgents）
- 给 `build_workflow()` 加 `checkpointer`（SQLite）
- LangGraph 原生支持：`graph.compile(checkpointer=SqliteSaver(...))`
- 崩溃/中断后从最后成功节点恢复，避免整轮重来（全市场扫描耗时长，重来代价大）
- 参考：TradingAgents 的 per-ticker SQLite checkpoint，`~/.tradingagents/cache/checkpoints/`
- 建议存储位置：`data/processed/checkpoints/` 或 `data/cache/checkpoints/`
- **依赖状态（已核查）**：langgraph 1.2.4 + langgraph-checkpoint 4.1.1 已装，
  但 `langgraph.checkpoint.sqlite` 子包**未装**（`ModuleNotFoundError`）。
  阶段三第一步需 `venv/bin/pip install langgraph-checkpoint-sqlite`。
  或用 langgraph-checkpoint 自带的 `InMemorySaver` 先验证逻辑，再决定持久化方案。

### 3.2 决策日志结构化增强（OB-R1 已部分实现，需补全）
- 当前 `log_result_node` 已输出 decision_log_<date>.json，含 macro_regime/decision_context
- **待补充**：industry_scores 明细（L2评分）、scan_results_summary 按行业/策略分组统计
- 参考 requirements.md OB-R1 的完整 schema

### 3.3 每层输入输出审计
- 每个节点的决策理由持久化（对应 architecture.md「可观测」原则）
- 当前已有 log 列表记录，可考虑结构化成每节点 input/output 快照

**验收标准**（写进测试）：
- 人为中断后重跑能从断点恢复（mock 一个节点抛异常，验证 checkpoint 保存）
- decision_log 结构完整，可追溯每层判断依据
- 现有测试不破坏

**对应文档需要更新**：
- requirements.md：OB-R1 补充 checkpoint 需求（可新增 OB-R4）
- agent.md：阶段三标记完成状态
- testing.md / traceability.md：新增测试用例映射
- architecture.md：3.1 节"原子写入与断点续传"可关联

---

## 四、后续阶段（P1/P2，简述）

### 阶段四（P1）：LLM 接入（仅外围，不碰核心信号）
- L1 regime LLM 辅助研判（Bull/Bear 辩论思路，调整置信度，不替代规则）
- L3 候选标的（Top N）LLM 深度分析
- 建立 `llm_clients/` 统一适配层（封装现有 config/settings.py 的 DeepSeek primary + OpenAI fallback）
- **强制约束**：grounding（数值来自store层，LLM不生成数字）、可降级（失败回退规则）、禁入核心信号路径
- config/settings.py 已有 LLM 配置但未实际调用

### 阶段五（P2）：反思闭环（绩效驱动）
- 收益回填：基于 decision_log 回填上次信号实际收益（raw + alpha vs 沪深300）
- 反思生成：每个 regime/策略组合生成"什么有效/无效"
- select_strategies 从静态映射升级为按历史 regime 绩效动态选择
- 解决阶段一遗留的"risk_mode 硬编码 vs market_condition 元数据语义未对齐"问题（见 docs/strategy.md）

### 阶段六（P2）：宏观事件感知（补全 L1 最难部分）
- 结构化事件先行：FOMC日历、利率期货隐含概率（对应 store_events E1）
- LLM 事件解读：新闻API → 事件分类+市场影响（对应未实现的 event_agent.py）
- 熔断联动：重大风险事件 → L1 切 defensive，联动现有 RC8 VIX熔断

---

## 五、关键文件地图

| 文件 | 作用 | 阶段 |
|---|---|---|
| `agents/orchestrator.py` | LangGraph 工作流编排，L1→L2→L3 串联 | 一/二 |
| `agents/strategy_agent.py` | L1 宏观评估 + L3 策略选择 | 一 |
| `agents/industry_agent.py` | L2 行业轮动 | 二 |
| `agents/trade_agent.py` | 风控过滤 + 指令单（未改） | - |
| `agents/data_agent.py` | 数据更新（未改） | - |
| `engine/scanner.py` | 全市场扫描引擎（industry_filter穿透点）| 二 |
| `agent_tools/strategy_tools.py` | run_scan/run_backtest 等 Tool | 二 |
| `tests/test_macro_regime.py` | L1 测试 | 一 |
| `tests/test_industry_rotation.py` | L2 测试 | 二 |

**核心数据接口**：
- `data.store.load(code, asset_type, market, start, end)` — market域日线（含指数000300）
- `data.store_macro.load(key)` — 宏观（vix/pmi/bond_yield/m2/dxy等）
- `data.store_meta.load("industry_sw")` — 申万行业分类（stock_code/industry_code/industry_name）
- `data.store_tushare.load("moneyflow_ind", date=...)` / `list_dates("moneyflow_ind")` — 行业资金流（东财，583天，2024-01~2026-06，按日期分片）
- `data.store_fundamental.load(code, data_type)` — 季频财务（按code分片）

---

## 六、工作流程规范（每阶段必须遵守）

1. **文档先行**：先更新 requirements.md（需求）+ agent.md（设计），明确接口和约束
2. **影响分析**：分层改造是架构级的，检查是否影响 project.md/architecture.md/strategy.md/testing.md/traceability.md
3. **TDD**：先写测试用例，明确验收标准
4. **从底往上实现**：约束类需求要确保穿透到最底层（如L2约束必须到scanner引擎层）
5. **验证**：运行新测试 + 全回归（`venv/bin/python -m pytest tests/ -q`，约294 passed）
6. **复查**：对照文档逐项核实实现是否符合设计（阶段一的教训）
7. **提交**：详细 commit message，说明改了哪些层、为什么、验收结果

**测试命令**：
```bash
cd /home/wz/alpha量化
venv/bin/python -m pytest tests/test_macro_regime.py tests/test_industry_rotation.py -v  # 分层测试
venv/bin/python -m pytest tests/ -q --ignore=tests/test_downloader.py  # 全回归（downloader需网络）
```

**注意事项**：
- 全市场真实扫描很慢（4951只标的，会超时），验证编排逻辑用 mock，验证收窄效果用 `_get_filtered_stock_list` 单独测
- venv 在 `venv/`，用 `venv/bin/python`
- 运行环境 Linux，commit 用中文 message 符合项目规范

---

## 七、给新 session 的开场建议

新 session 直接说：

> "继续分层决策架构的阶段三（可靠性与可观测加固）。
> 阶段一（L1宏观层）、阶段二（L2行业轮动层）已完成并提交（commit 9388805）。
> 请先阅读 docs/HANDOFF_分层决策.md 了解上下文，然后开始阶段三：
> 1. LangGraph checkpoint 断点续跑
> 2. 决策日志结构化增强（补 industry_scores/scan_summary）
> 3. 每层审计
> 遵循文档先行→TDD→从底往上实现→验证→复查→提交的流程。"
