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

### 4.1 Orchestrator（agents/orchestrator.py）

使用 LangGraph `StateGraph` 实现，状态在节点间流转：

```python
from langgraph.graph import StateGraph
from typing import TypedDict

class WorkflowState(TypedDict):
    date: str
    data_status: dict       # check_data_freshness 结果
    scan_results: dict      # run_scan 结果
    backtest_results: dict  # compare_strategies 结果（Phase 2+）
    filtered_signals: list  # filter_signals_by_risk 结果
    orders: list            # generate_orders 结果
    human_approved: bool    # 人工确认标志（Phase 1/2 必须为 True 才能推进）
    error: str              # 异常信息

# 工作流节点
graph = StateGraph(WorkflowState)
graph.add_node("check_data",      check_data_node)
graph.add_node("update_data",     update_data_node)
graph.add_node("run_scan",        scan_node)
graph.add_node("evaluate",        strategy_eval_node)   # Phase 2+
graph.add_node("risk_filter",     risk_filter_node)
graph.add_node("await_approval",  human_approval_node)  # Phase 1/2
graph.add_node("notify",          notify_node)
graph.add_node("generate_orders", order_node)

# 条件边：数据不新鲜 → 先更新
graph.add_conditional_edges(
    "check_data",
    lambda s: "update_data" if s["data_status"].get("stale") else "run_scan"
)
```

### 4.2 Data Agent（agents/data_agent.py）

职责：数据更新、质量检查、缺口补全

- 每日收盘后检查各域数据新鲜度
- 发现缺口时自动触发对应 downloader
- 校验更新结果，异常时告警

### 4.3 Strategy Agent（agents/strategy_agent.py）

职责：策略选择、参数优化、回测评估（Phase 2 核心）

- 读取近期回测结果，判断当前市场环境（趋势/震荡/高波动）
- 根据环境选择合适的策略组合和参数
- 将优选参数写入运行时配置，下次扫描生效

### 4.4 Risk/Trade Agent（agents/trade_agent.py）

职责：风控过滤、指令单生成、推送通知

- 对扫描信号做仓位、行业集中度、评分过滤
- 生成结构化指令单，等待人工确认（Phase 1/2）
- Phase 3 在风控规则约束内自主执行

---

## 五、LLM 接入

### 5.1 配置

```python
# config/settings.py（待新增）
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
}
```

### 5.2 框架选型

| 框架 | 用途 | 说明 |
|---|---|---|
| LangGraph | Orchestrator 工作流 | 有状态、有条件分支、支持 Human-in-the-loop |
| LangChain | Tool 封装、LLM 调用 | 与 LangGraph 配套，统一接口 |
| opencode | 开发辅助 | 策略编写、Bug 修复、文档维护；不用于生产运行时 |

---

## 六、阶段规划

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

**交付物：**
- `agent_tools/data_tools.py`（含 `get_active_event_windows` / `check_reduce_window`）
- `agent_tools/strategy_tools.py`（scan 部分）
- `agent_tools/trade_tools.py`（notify + generate_orders）
- `agents/orchestrator.py`（基础工作流）
- `agents/data_agent.py`
- `data/store_events.py`（E1/E2/E3 存储接口）

### Phase 2：半自动交易

**新增能力：**
- Strategy Agent 自主评估策略绩效，调整参数组合
- 指令单生成后推送给人工，人工一键确认执行
- Orchestrator 支持多策略并行评估和对比
- D8b 扩展：接入新闻 API，LLM 解析非结构化企业公告（重组/实控人变更）
- E3 升级：LLM 对新闻做事件分类和市场影响评估，自动生成 D8c 记录

**交付物：**
- `agents/strategy_agent.py`
- `agents/event_agent.py`（新增，负责 E2 NLP 解析和 E3 识别）
- `agent_tools/strategy_tools.py`（backtest + compare 部分）
- Orchestrator 增加 `evaluate` 节点、`await_approval` 节点、`event_monitor` 节点

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
- `agents/trade_agent.py`
- 风控规则引擎
- 券商 API 适配层

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
