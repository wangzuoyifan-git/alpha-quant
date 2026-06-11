# Alpha量化 — Agent 层文档

> Agent 层架构设计、Tool Layer 规范、LLM 接入方案、阶段规划。
> 最后更新：2026-05-29
>
> **项目基础信息见 `docs/project.md`**
> **架构兼容性约束完整定义（AC1~AC9）见 `docs/requirements.md` 第七章**

---

## 一、设计原则

1. **不重写业务逻辑**：Agent 层是在现有引擎层、策略层之上新增的协作层，Tool Layer 只做薄适配，不复制实现
2. **结构化输入输出**：所有 Tool 返回 `dict`，不依赖 stdout 解析，Agent 可直接处理
3. **人在回路（Human-in-the-loop）**：Phase 1/2 阶段，交易执行必须经过人工确认节点，不允许 Agent 直接下单
4. **可观测**：每个 Agent 节点的输入、输出、决策理由都要持久化，便于事后审计和调试

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────┐
│                  Orchestrator Agent                      │
│  agents/orchestrator.py（LangGraph StateGraph）          │
│  职责：任务分解、Agent 调度、结果汇总、异常处理、状态管理  │
└──────────┬──────────┬──────────┬────────────────────────┘
           │          │          │
    ┌──────▼──┐  ┌────▼────┐  ┌──▼──────────┐
    │  Data   │  │Strategy │  │ Risk/Trade  │
    │  Agent  │  │  Agent  │  │   Agent     │
    └────┬────┘  └────┬────┘  └──────┬──────┘
         │            │              │
    ┌────▼────────────▼──────────────▼──────┐
    │              Tool Layer               │
    │           agent_tools/                │
    │  data_tools.py  strategy_tools.py     │
    │  trade_tools.py                       │
    └───────────────────────────────────────┘
         │            │              │
    ┌────▼──┐   ┌─────▼──┐   ┌──────▼─────┐
    │data/  │   │engine/ │   │trader/     │
    │store* │   │scanner │   │assistant   │
    │down-  │   │backtest│   │notifier/   │
    │loader*│   │/engine │   │push        │
    └───────┘   └────────┘   └────────────┘
```

---

## 三、Tool Layer 规范（agent_tools/）

Tool Layer 是 Agent 与现有代码之间的适配层，遵循以下规范：

### 3.1 接口规范

```python
# 所有 Tool 函数：
# - 参数类型明确，有默认值
# - 返回 dict，包含 success 字段
# - 异常在内部捕获，通过 error 字段返回，不向上抛出
# - 不依赖全局状态，可独立调用

def some_tool(param: str, ...) -> dict:
    """
    一句话描述工具用途。

    Returns:
        {
            "success": bool,
            "data": ...,       # 成功时的结果
            "error": str,      # 失败时的错误信息
            "elapsed": float,  # 耗时（秒）
        }
    """
```

### 3.2 data_tools.py

```python
def check_data_freshness() -> dict:
    """
    聚合各域最新日期，返回数据健康状态。
    Agent 用于判断哪些域需要更新再扫描。

    Returns:
        {
            "success": True,
            "domains": {
                "market/stock": "2026-05-28",
                "market/etf":   "2026-05-28",
                "meta/calendar": "2026-05-26",
                "factor/northbound": "2026-05-27",
                "tushare/moneyflow": "2026-05-28",
                ...
            },
            "stale": ["factor/northbound"],  # 落后超过1个交易日的域
        }
    """

def update_domain(domain: str) -> dict:
    """
    按需更新单个域，返回结构化结果。
    domain 取值：market / meta / fundamental / factor / macro / tushare / events

    Returns:
        {"success": bool, "rows_added": int, "error": str}
    """

def validate_data(domain: str, sample_codes: list[str] = None) -> dict:
    """
    校验数据完整性，抽样检查关键列非空、行数合理。

    Returns:
        {"success": bool, "issues": [{"code": str, "issue": str}], "checked": int}
    """

def query_kline(code: str, start: str, end: str,
                asset_type: str = "stock") -> dict:
    """
    查询 K 线数据，返回结构化摘要（不返回完整 DataFrame，避免 token 爆炸）。

    Returns:
        {"success": bool, "rows": int, "start": str, "end": str,
         "columns": list, "sample": list[dict]}  # 最后3行样本
    """

def get_active_event_windows(date: str = None) -> dict:
    """
    查询指定日期处于事件窗口期内的所有 E1 事件，供策略降权和辅助分析使用。

    Returns:
        {"success": bool, "date": str,
         "windows": [{"event_name": str, "event_type": str,
                      "days_to_event": int, "impact_scope": str}]}
    """

def check_reduce_window(code: str, date: str = None) -> dict:
    """
    检查标的是否处于大股东减持窗口期（对应 RC7）。

    Returns:
        {"success": bool, "code": str, "in_window": bool,
         "detail": {"holder": str, "end_date": str}}
    """
```

### 3.3 strategy_tools.py

```python
def run_scan(date: str = None,
             strategies: list[str] = None) -> dict:
    """
    执行全市场扫描，返回结构化信号列表。
    strategies: 策略 key 列表，默认全部内置策略。

    Returns:
        {"success": bool, "signals": [{"code","name","signal",
          "price","score","reason"}],
         "count": int, "elapsed": float}
    """

def run_backtest(strategy_key: str,
                 start: str, end: str,
                 codes: list[str] = None,
                 params: dict = None) -> dict:
    """
    运行回测，返回标准化绩效指标。

    Returns:
        {"success": bool, "strategy": str, "total_return": float,
         "annual_return": float, "max_drawdown": float,
         "sharpe_ratio": float, "win_rate": float,
         "total_trades": int, "error": str}
    """

def list_strategies() -> dict:
    """
    列出所有可用策略及其元数据。

    Returns:
        {"strategies": [{"key": str, "name": str, "description": str,
                          "version": str, "requires_bars": int}]}
    """

def compare_strategies(strategy_keys: list[str],
                       start: str, end: str) -> dict:
    """
    并行回测多个策略，返回对比结果。
    Strategy Agent 用于自动选择最优策略组合。
    """
```

### 3.4 trade_tools.py

```python
def filter_signals_by_risk(signals: list[dict],
                            current_equity: float = None,
                            vix: float = None,
                            index_drop: float = None) -> dict:
    """
    RC1~RC8 完整风控过滤。风控阈值（持仓上限、单笔上限、行业集中度、
    回撤、VIX/指数熔断）全部从 config.settings.RISK_CONTROL 读取，
    不在签名中硬编码（符合 AC2）。

    入参 signals: [{"code","name","signal","price","score","reason"}]
    current_equity: 当前账户权益（RC5 回撤判断），缺省用初始资金
    vix / index_drop: RC8 熔断输入

    Returns:
        {"success": bool, "passed": [signal_dict], "blocked_count": int}
    """

def generate_orders(signals: list[dict],
                    capital: float = None) -> dict:
    """
    根据信号生成指令单，返回待确认的订单列表。
    order_dict 结构预留券商 API 字段（AC6），Phase 2 接入时无需重构：
      broker_order_id: str   券商返回的订单号（Phase 2 填充）
      filled_price: float    实际成交价（Phase 2 填充）
      filled_qty: int        实际成交数量（Phase 2 填充）
      filled_at: str         成交时间（Phase 2 填充）

    Returns:
        {"success": bool, "orders": [order_dict], "total_amount": float}
    """

def get_pending_orders() -> dict:
    """返回所有待确认指令单"""

def confirm_order(order_id: str) -> dict:
    """
    确认指令单，状态变为"已确认"，记录确认时间戳和预期买入价。
    Phase 2 Agent 可程序化调用此接口替代人工确认（对应 AC3）。

    Returns:
        {"success": bool, "order_id": str, "confirmed_at": str, "error": str}
    """

def cancel_order(order_id: str, reason: str = None) -> dict:
    """
    取消指令单，状态变为"已取消"，历史记录保留不删除。

    Returns:
        {"success": bool, "order_id": str, "error": str}
    """

def notify_signals(signals: list[dict], title: str = None) -> dict:
    """推送信号到配置的渠道（企微/邮件/Telegram）"""
```

---

## 四、Agent 实现

> **实现状态（截至最新）**：`agents/` 4 个文件均已实现，为**纯规则编排**（LangGraph + 阈值判断），**尚未接入 LLM 推理**（LLM 配置已就绪，见第五章）。
> 导出接口：`run_daily_workflow / build_workflow / WorkflowState`（orchestrator），`check_and_update`（data），`evaluate_market_condition / select_strategies / recommend_daily_plan`（strategy），`process_signals / confirm_trade / cancel_trade`（trade）。
> 注意：当前 agents 无 CLI 入口，需通过 Python 导入调用（如 `from agents import run_daily_workflow`）。

### 4.1 Orchestrator（agents/orchestrator.py）✅ 已实现

使用 LangGraph `StateGraph` 实现，状态在节点间流转。以下为**实际实现**：

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional

class WorkflowState(TypedDict, total=False):
    date: str
    asset_types: list[str]
    strategy_keys: list[str]
    data_status: dict       # check_data_freshness 结果
    scan_results: dict      # run_scan 结果
    filtered_signals: list  # filter_signals_by_risk 结果
    orders: list            # generate_orders 结果
    human_approved: bool    # 人工确认标志（Phase 1/2）
    error: Optional[str]    # 异常信息
    log: list[str]          # 各节点执行日志

# 实际节点（build_workflow）：
#   check_data → (update_data?) → run_scan → risk_filter
#   → await_approval → generate_orders → notify → log_result → END
graph = StateGraph(WorkflowState)
graph.add_node("check_data",      check_data_node)
graph.add_node("update_data",     update_data_node)
graph.add_node("run_scan",        scan_node)
graph.add_node("risk_filter",     risk_filter_node)
graph.add_node("await_approval",  human_approval_node)   # Phase 1/2 自动放行，人工在 CLI 确认
graph.add_node("generate_orders", order_node)
graph.add_node("notify",          notify_node)
graph.add_node("log_result",      log_result_node)       # 持久化运行日志到 data/processed/

# 条件边：market/stock 过期 → 先更新
graph.add_conditional_edges("check_data", route_after_check, {...})
```

> **尚未接入工作流的节点**：`evaluate`（策略评估）当前未注册进 `build_workflow()`。Strategy Agent 的评估能力以独立函数存在（见 4.3），尚未串入 Orchestrator。

### 4.2 Data Agent（agents/data_agent.py）✅ 已实现

职责：数据更新、质量检查。入口 `check_and_update()`。

- 调 `check_data_freshness` 检查各域新鲜度
- 对 `stale` 域逐个调 `update_domain` 增量更新
- 返回 `{all_fresh, domains_checked, stale_before, updated, issues, stats_summary}`

### 4.3 Strategy Agent（agents/strategy_agent.py）✅ 已实现（规则版）

职责：市场环境评估、策略选择。

- `evaluate_market_condition()`：读宏观指标（国债利差/PMI/M2/VIX），按阈值规则判断 bull/bear/range/volatile
- `select_strategies(market_condition)`：按策略的 `market_condition` 元数据（AC5）筛选适用策略组合
- `recommend_daily_plan()`：评估环境 → 选策略 → 给出扫描参数建议

> Phase 2 计划：升级为基于历史回测绩效动态选择 + 参数优化（当前为静态阈值规则）。

### 4.4 Risk/Trade Agent（agents/trade_agent.py）✅ 已实现

职责：风控过滤、指令单生成、确认/取消。

- `process_signals(signals, current_equity, vix)`：调 `filter_signals_by_risk`（RC1~RC8）+ `generate_orders`
- `confirm_trade(order_id)` / `cancel_trade(order_id)`：指令单生命周期
- Phase 3 计划：在风控规则约束内自主执行下单

---

## 五、LLM 接入

> **状态**：LLM 配置已写入 `config/settings.py`（见下），但 **agents 当前为纯规则实现，尚未实际调用 LLM**。本节描述的是预留的接入方案，待 Phase 2 将 LLM 推理接入 Strategy Agent / Event Agent。

### 5.1 配置（已存在于 config/settings.py）

```python
# config/settings.py（已配置）
LLM = {
    "primary": {
        "provider": "deepseek",          # deepseek / qwen / wenxin
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
    },
    "fallback": {
        "provider": "openai",            # 通过代理访问
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",  # 代理地址
    },
    "temperature": 0.1,   # 量化决策场景，低温度
    "timeout": 30,
    "max_retries": 2,
}
```

### 5.2 框架选型

| 框架 | 用途 | 说明 |
|---|---|---|
| LangGraph | Orchestrator 工作流 | 有状态、有条件分支、支持 Human-in-the-loop |
| LangChain | Tool 封装、LLM 调用 | 与 LangGraph 配套，统一接口 |
| opencode | 开发辅助 | 策略编写、Bug 修复、文档维护；不用于生产运行时 |

---

## 六、分层决策架构落地路径（2026-06-11 更新）

> 基于 TradingAgents 多 Agent 辩论、Qlib Nested Decision 思想，设计"宏观→行业→个股"三层架构。
> 详细需求定义见 `docs/requirements.md` 第八章。

### 阶段一（P0）：分层决策骨架 — L1 宏观层结构化

**目标**：把现有扁平流程改造成分层结构，先打通 L1 宏观层和约束传递机制。

**关键交付：**

1. **扩展 `WorkflowState`**（`agents/orchestrator.py:17`）
   - 新增字段：`macro_regime: dict`（L1 输出）、`decision_context: dict`（逐层约束对象）

2. **升级 `evaluate_market_condition()`**（`agents/strategy_agent.py:11`）
   - 从 4 状态扩展为结构化 regime 输出（见 requirements.md L1-R1）
   - 显式映射 regime → risk_mode（defensive / balanced / aggressive）

3. **改造 `select_strategies()`**（`agents/strategy_agent.py:63`）
   - 接收 `risk_mode`，返回该模式下的策略组合
   - defensive → 防御策略（fundamental_value/红利 ETF）
   - aggressive → 进攻策略（ma_cross/vol_price_breakout/money_flow）

4. **新增 `macro_regime_node` + 条件边路由**（`agents/orchestrator.py`）
   - 在 `check_data` → `run_scan` 之间插入 L1 节点
   - 仿照 `route_after_check`（:148）写 `route_after_macro`，按 risk_mode 路由到不同策略配置

**验收标准：**
- ✅ 运行 `run_daily_workflow()`，日志能看到 regime 判定 + risk_mode + 据此选出的策略组合
- ✅ 不同宏观输入（VIX=35 vs VIX=15）能切换到不同策略集
- ✅ 测试用例 NT-L1-01 ~ NT-L1-04 全部通过（见 requirements.md 8.8 节）

**阶段演进约束：**
- 此阶段 L3 扫描可暂时仍全市场，只验证 L1 决策和路由生效
- L2 收窄留到阶段二，避免一次改动太大

---

### 阶段二（P0）：L2 行业轮动层 — 扫描范围前置收窄

**目标**：插入行业层，用资金流筛出行业池，约束**穿透到引擎层**真正收窄 L3 扫描范围。

**改动层次（按依赖顺序从下往上）：**

1. **引擎层 `engine/scanner.py`**（必须改，约束前置的关键）
   - `ScanEngine.scan()` 新增 `industry_filter: list[str] = None` 参数
   - `_get_filtered_stock_list()` 新增 `industry_filter` 参数，join `industry_sw` 在构建标的池时就收窄

2. **Tool 层 `agent_tools/strategy_tools.py`**
   - `run_scan()` 新增 `industry_filter` 参数，透传给 `ScanEngine.scan()`

3. **新增 Agent `agents/industry_agent.py`**（L2 行业轮动层）
   - 资金流维度：`moneyflow_ind` 近 N 日累计净流入，按申万行业名称精确匹配东财板块排名
   - 结合 `risk_mode` 调整行业池宽窄（defensive 取 Top 5~7，aggressive 取 Top 8~12）
   - 输出 `allowed_industries`（申万行业代码列表）+ `industry_scores`，写入 `decision_context`

4. **Agent 层 `agents/orchestrator.py`**
   - 串入 `industry_rotation_node`，L1 → L2 → L3（condition_context 逐层填充）
   - scan_node 从 `decision_context["allowed_industries"]` 读取，传给 `run_scan`

**数据对齐方案**（已验证）：
- 东财 `moneyflow_ind.name`（1012 板块）与申万一级行业名精确匹配（31/31 可匹配）
- 通过名称匹配取对应申万 `industry_code`，输出标准申万代码供 scanner 使用

**验收标准：**
- ✅ 扫描标的数从 ~4900 只降到行业池内标的数（减少 50%+ ）
- ✅ 切换 risk_mode 时行业池随之变化
- ✅ 日志追溯 L2 行业评分明细
- ✅ `industry_filter=None` 时退化为全市场扫描（向后兼容）

---

### 阶段三（P1）：可靠性与可观测加固 ✅ 已完成

**目标**：分层链路变长，需要断点续跑和决策留痕。

**关键交付：**

1. **LangGraph Checkpoint**（借鉴 TradingAgents）✅
   - `build_workflow(checkpointer=...)` 接受可选 checkpointer（默认 None，向后兼容 AC14）
   - `make_checkpointer()` 工厂：优先 `SqliteSaver`（落盘 `data/cache/checkpoints/workflow.sqlite`），
     依赖缺失时降级 `InMemorySaver`（AC15）
   - `run_daily_workflow(enable_checkpoint=True)` 用 `thread_id=<date>` 标识运行
   - 崩溃后用相同 thread_id 重跑从最后成功节点恢复

2. **决策日志结构化** ✅
   - `log_result_node` 按 OB-R1 完整 schema 输出
   - `industry_scores`：从 decision_context 回写 L2 评分明细
   - `scan_results_summary.by_industry / by_strategy`：对扫描信号按行业/策略分组计数

3. **每层输入输出审计** ✅
   - 新增 `_audit(state, node, input_summary, output_summary)` helper
   - 每个决策节点向 `state["node_audit"]` 追加 input/output 摘要快照（OB-R5）
   - node_audit 随 decision_log 落盘

**实现位置：**
- `agents/orchestrator.py`：`make_checkpointer()` / `_audit()` / 各节点审计埋点 / `log_result_node` 增强
- `tests/test_checkpoint_recovery.py`：NT-L3-01/02/03
- `tests/test_decision_log_enhanced.py`：NT-L3-04/05/06

**验收标准：**
- ✅ 人为中断后重跑能从断点恢复
- ✅ 决策日志可追溯每层的判断依据
- ✅ `data/processed/decision_log_YYYYMMDD.json` 结构完整（含 industry_scores/by_industry/by_strategy/node_audit）

---

### 阶段四（P1）：LLM 接入（仅外围，不碰核心信号）

**目标**：把 `config/settings.py` 已配置的 LLM 用起来，只在外围环节。

**关键交付：**

1. **L1 regime 辅助研判**（借鉴 TradingAgents Bull/Bear 辩论）
   - LLM 对宏观指标做定性多空研判，作为规则 regime 的**校验/置信度调整**
   - 务必 **grounding**：数值来自 store 层，LLM 只做定性解读

2. **L3 候选标的深度分析**
   - 规则扫描筛出 Top N 候选后，仅对这少量标的用 LLM 做基本面/消息面分析

3. **建立 `llm_clients` 适配层**
   - 封装 primary(DeepSeek)/fallback(OpenAI) 为统一 client
   - 支持多 provider（Qwen/GLM/MiniMax）

**验收标准：**
- ✅ LLM 调用全部可降级（失败回退规则）
- ✅ 核心信号生成路径不含任何 LLM 调用，回测仍完全确定可复现
- ✅ LLM 输出符合 grounding 要求（不生成数字）

---

### 阶段五（P2）：反思闭环 — 激活绩效驱动

**目标**：实现 Phase 2 承诺的"绩效驱动策略选择"，落地 TradingAgents 的决策记忆模式。

**关键交付：**

1. **收益回填**
   - 基于阶段三的决策日志，下次运行时回填上次信号的实际收益（raw + alpha vs 沪深300）

2. **反思生成**
   - 对每个 regime/策略组合生成"什么有效/无效"的反思

3. **绩效驱动 `select_strategies`**
   - 从"静态阈值映射"升级为"按历史 regime 下实际绩效动态选择"

**验收标准：**
- ✅ 策略选择能体现历史表现差异
- ✅ 反思记录可解释选择理由

---

### 阶段六（P2）：宏观事件感知 — 补全第 1 层

**目标**：补上"地缘政治/央行政策"这层（业界无开源现成品，需自研）。

**关键交付：**

1. **结构化事件先行**
   - FOMC 日历、利率期货隐含概率、重要经济数据发布日（对应 `store_events` E1）

2. **LLM 事件解读**
   - 接新闻 API，LLM 对突发事件做分类 + 市场影响评估，产出 regime 调整信号

3. **熔断联动**
   - 重大风险事件 → 直接触发 L1 切到 defensive，联动现有 RC8 VIX 熔断

**验收标准：**
- ✅ FOMC/重大政策日能自动识别并影响 regime
- ✅ 突发利空能触发防御模式切换

---

## 七、原 Phase 1/2/3 规划（保留）

> 以下为原有阶段规划，与上述阶段一~六并行，不冲突。
> 阶段一~六聚焦"分层决策架构"；原 Phase 1/2/3 聚焦"Agent 能力边界演进"。

### Phase 1（当前目标）：自动化辅助决策

**Agent 能做：**
- 每日自动检查数据新鲜度，触发增量更新（含 events/calendar 更新）
- 自动执行全市场扫描，生成信号
- 扫描前查询当日事件窗口期，对窗口期内信号做降权处理
- RC7/RC8 风控过滤（减持窗口期、VIX 代理熔断）
- 推送信号到企微/邮件（含事件背景字段）
- 生成指令单，等待人工确认

**Agent 不能做：**
- 自主调整策略参数
- 自主执行交易
- E3 突发事件自动识别（Phase 1 依赖人工记录）

**交付物（Phase 1 均已交付）：**
- ✅ `agent_tools/data_tools.py`（含 `get_active_event_windows` / `check_reduce_window`）
- ✅ `agent_tools/strategy_tools.py`（scan 部分）
- ✅ `agent_tools/trade_tools.py`（notify + generate_orders）
- ✅ `agents/orchestrator.py`（基础工作流，LangGraph）
- ✅ `agents/data_agent.py`
- ✅ `data/store_events.py`（E1/E2/E3 存储接口）

### Phase 2：半自动交易

**新增能力：**
- Strategy Agent 自主评估策略绩效，调整参数组合（⏳ 当前为静态阈值规则版，待升级为回测绩效驱动 — 对应阶段五）
- 指令单生成后推送给人工，人工一键确认执行
- Orchestrator 支持多策略并行评估和对比（⏳ `evaluate` 节点尚未接入工作流）
- D8b 扩展：接入新闻 API，LLM 解析非结构化企业公告（重组/实控人变更 — 对应阶段六）
- E3 升级：LLM 对新闻做事件分类和市场影响评估（对应阶段六）

**交付物：**
- ✅ `agents/strategy_agent.py`（规则版已实现：`evaluate_market_condition` / `select_strategies` / `recommend_daily_plan`）
- ⏳ `agents/event_agent.py`（新增，负责 E2 NLP 解析和 E3 识别）— 对应阶段六
- ✅ `agent_tools/strategy_tools.py`（backtest + compare 部分）
- ⏳ Orchestrator 增加 `evaluate` 节点、`event_monitor` 节点；`await_approval` 节点已实现
- ⏳ agents 接入 LLM 推理（对应阶段四）

### Phase 3：全自动交易

**新增能力：**
- Risk/Trade Agent 在风控规则约束内自主执行下单
- 实时监控持仓，自主止损止盈
- 异常情况自动告警并暂停
- 实时新闻流 + LLM Agent 持续监控突发事件，自动触发熔断或仓位调整

**前置条件：**
- Phase 1/2 稳定运行至少3个月
- 风控规则经过充分回测验证
- 接入券商 API（需单独评估）

**交付物：**
- ⏳ `agents/trade_agent.py` 自主执行能力（当前仅实现 Phase 1/2 的风控过滤 + 人工确认）
- ⏳ 风控规则引擎
- ⏳ 券商 API 适配层

---

## 七、架构兼容性约束在 Agent 层的落实

完整约束定义见 `docs/requirements.md` 第七章。下表说明各约束在 Agent 层的对应实现位置：

| 约束编号 | 约束内容 | Agent 层落实位置 |
|---|---|---|
| AC1 | 核心逻辑通过 `agent_tools/` 封装，返回结构化 dict | 本文档第三章 Tool Layer 规范（3.1~3.4 节） |
| AC2 | 风控参数（RC1~RC6）从配置读取，不硬编码 | `trade_tools.filter_signals_by_risk` 和 `generate_orders` 的参数均从 `config/settings.py` 读取 |
| AC3 | 指令单确认流程设计为可被程序调用 | `trade_tools.confirm_order()` 返回结构化 dict，Phase 2 Agent 可直接调用替代人工确认 |
| AC4 | 每次运行状态持久化到文件 | `WorkflowState` 各节点输出写入 `data/processed/run_log.json`，Orchestrator 下次启动时读取 |
| AC5 | 策略必须携带 `market_condition` 元数据字段 | Strategy Agent（`agents/strategy_agent.py`）读取此字段做策略选择；`BaseStrategy` 需预留此字段（见 `docs/strategy.md`） |
| AC6 | 交易记录存储结构预留券商 API 字段 | `generate_orders` 返回的 `order_dict` 预留 `broker_order_id / filled_price / filled_qty / filled_at` 字段（见本文档 3.4 节） |
| AC7 | 数据存储按域物理隔离，各域可独立更新 | `data_tools.update_domain(domain)` 接受单域参数，各域下载器互相独立（见 `docs/data.md` 二节） |
| AC8 | 价格数据同时保留原始价和复权价 | `data_tools.query_kline` 返回时区分 `adjust` 参数，Phase 2 实时价接入不需要重算历史复权因子 |
| AC9 | D8 事件存储结构预留 Phase 2 NLP 解析字段 | `store_events` 的 E2 企业事件字段含 `source / phase / nlp_confidence`，Phase 1 结构化接口和 Phase 2 NLP 解析事件使用同一结构 |
