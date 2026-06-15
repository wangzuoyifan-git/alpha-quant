# 分层决策架构 — 阶段四交接文档

> 生成时间：2026-06-12
> 用途：跨 session 接续阶段四（LLM 接入地基 + 自主度框架）实现
> 阅读顺序：本文档 → docs/agent.md → docs/requirements.md 第八章

---

## 一、当前状态（截至 commit 3911ad9）

### 已完成阶段

| 阶段 | commit | 核心交付 |
|---|---|---|
| 阶段一 L1 宏观层 | c7153d0~5d53ab4 | evaluate_market_condition / build_decision_context / macro_regime_node |
| 阶段二 L2 行业轮动 | 9388805 | industry_agent / scanner industry_filter / 标的池 4951→1704 |
| 阶段三 可靠性加固 | 3911ad9 | SqliteSaver checkpoint / OB-R1 决策日志增强 / node_audit 审计 |

### 文档重整（2026-06-12，本 session）

- 永久文档（7个）：agent.md / requirements.md / strategy.md / project.md / architecture.md / testing.md / traceability.md
- 归档：docs/archive/（llm_role.md / REVIEW_分层架构重定位.md / REVIEW_阶段四_LLM接入.md / HANDOFF_分层决策.md）

---

## 二、重大设计决策（本 session 确认，已写入永久文档）

### D-1 系统重定位：多周期配置 + 择时

原来隐含"纯短线"，现明确：
- **L1**（慢变，周/事件级）：模式基调 + 配置/交易仓资金分配
- **L2**（中变，月/季级）：**双重职责**——中长期配置仓决策者 + L3 约束提供者
- **L3**（快变，日级）：L3-A 技术策略→交易仓（BUY）；L3-B 基本面策略→配置仓候选（WATCH）

### D-2 LLM 定位：融合模式，非分工

- 规则 = 证据提供者（结构化"事实"）
- LLM = 综合分析师（融合规则证据 + 非结构化信息 → 带推理链的方案）
- 否决分工模式和分层加权，采用"约束流 + 能力画像"模型

### D-3 autonomy_level：执行权总闸门（5级）

| 级别 | 名称 | 当前默认 |
|---|---|---|
| L0 | OFF | ✅ 全局默认 |
| L1 | ADVISORY | — |
| L2 | ASSIST | — |
| L3 | COPILOT | — |
| L4 | AUTO | — |

配置：`config.settings.LLM["autonomy_level"]`，全局+分层可覆盖，永远可回退 OFF

### D-4 策略 6 维能力画像（已写入 strategy.md BaseStrategy）

激活 4 维（applicable_regimes / style / applicable_scenario / risk_profile）+
占位 2 维（time_horizon / data_dependency）

`applicable_regimes` 是筛选主路径，`risk_profile` 是声明+校验（不独立筛选）

### D-5 Signal 新增仓位归属字段

```python
position_type: str = "trading"   # "trading" / "config"
time_horizon: str = "short"      # "short" / "medium" / "long"
```

### D-6 L2 三维评分（台阶一已定义，景气度台阶二填充）

```
行业评分 = ①景气度×w1 + ②估值×w2 + ③资金流×w3
权重随 L1 regime 动态调整，可配置（config.settings.LLM 内）
台阶一：只跑②③两维（景气度数据不足），①字段留 "pending"
```

### D-7 台阶一边界决策（5个缺口已定）

| 缺口 | 台阶一做法 |
|---|---|
| Signal 仓位归属 | 加 position_type + time_horizon 字段，不改信号流转逻辑 |
| scan 是否拆分 | 不拆，一起跑，输出打标签区分 |
| L2 跑几维 | ②估值+③资金流，①景气度留 pending |
| allocation 是否真正约束 | 只传递记录，不真正约束资金（台阶二） |
| L3-B 候选链路 | 产出候选池落盘，人工看日志，不做交互 UI |

---

## 三、阶段四任务（下一步）

### 3.1 llm_clients/ 洋葱架构（底层，先做）

```
llm_clients/
├── __init__.py    # L2: get_client() — 唯一读 config.settings.LLM 的地方
├── core.py        # L0: Provider 协议 + OpenAICompatProvider + AnthropicProvider
├── client.py      # L1: LLMClient 三级降级链（primary→fallback→degraded）
└── prompts.py     # L2: 项目 prompt 模板（grounding 约束写在这）
```

**关键设计**（见 requirements.md LLM-R4）：
- `api_style=openai`：DeepSeek/Qwen/GLM 直连 + yunwu.ai/OpenRouter 等聚合平台
- `api_style=anthropic`：仅 Anthropic 官方原生直连
- 聚合平台访问任何模型（包括 Claude）均走 `api_style=openai`
- L0/L1 零项目依赖，通过构造参数注入（依赖倒置）

### 3.2 autonomy_level 机制（配置层）

- `config/settings.py` LLM 配置扩展（见 requirements.md）
- `.env.example` 补充：DEEPSEEK_API_KEY / YUNWU_API_KEY / YUNWU_BASE_URL / ANTHROPIC_API_KEY
- `requirements.txt` 补充：openai>=2.40.0（显式声明）/ anthropic>=0.40.0（新增）

### 3.3 L1 regime LLM 研判（业务层，首个试点）

接入点：`agents/strategy_agent.py` 新增 `llm_review_regime(env: dict) -> dict`

- 输入：evaluate_market_condition() 结果（含 indicators 数值）
- 输出：`{llm_available, agree, confidence_adjust, reasoning, provider}`
- 只调整 confidence（±confidence_adjust_max，默认 0.2），regime/risk_mode 永不变
- LLM 不可用时静默降级，workflow 行为与 OFF 完全一致

orchestrator.py：macro_regime_node 在规则评估后调 llm_review_regime，结果存 state["macro_regime"]["llm_review"]

### 3.4 strategy.py / base.py：6 维 schema 代码落地（最底层，先做）

文档已定义，代码还未改：
- `strategies/base.py`：加 6 维字段 + profile() 方法 + Signal 加 position_type/time_horizon
- `strategies/builtin/*.py`：各策略填写 applicable_regimes / style / applicable_scenario / risk_profile
- `agents/strategy_agent.py`：select_strategies 重构为画像匹配（替代硬编码列表）

---

## 四、实现顺序（从底往上）

```
1. 文档先行：requirements.md 补 NT-L4-* 测试需求 / traceability.md 更新
2. TDD：tests/test_llm_clients.py + tests/test_llm_regime_review.py（先红）
3. 底层：strategies/base.py（6 维 schema + Signal 字段）
4. 底层：llm_clients/（core → client → __init__ + prompts）
5. 中层：strategy_agent.llm_review_regime + select_strategies 重构
6. 上层：orchestrator.py macro_regime_node 接入 LLM 研判
7. 配置：settings.py + .env.example + requirements.txt
8. 验证：新测试 + 全回归（确保无 key 时 294 测试不破）
9. 复查：核心路径无 LLM / grounding / autonomy_level=OFF 行为不变
10. 提交
```

---

## 五、关键文件地图

| 文件 | 阶段四改动 |
|---|---|
| `strategies/base.py` | 加 6 维字段 + profile() + Signal 新字段 |
| `strategies/builtin/*.py` | 各策略填写能力画像 |
| `agents/strategy_agent.py` | llm_review_regime() + select_strategies 重构 |
| `agents/orchestrator.py` | macro_regime_node 接入 LLM 研判 |
| `llm_clients/` | 新建（L0/L1/L2 洋葱架构） |
| `config/settings.py` | LLM 配置扩展（api_style/autonomy_level 等） |
| `.env.example` | 补充 LLM API key 示例 |
| `requirements.txt` | 补充 openai + anthropic |

**测试命令**：
```bash
# 新测试
venv/bin/python -m pytest tests/test_llm_clients.py tests/test_llm_regime_review.py -v

# 全回归（排除网络依赖）
venv/bin/python -m pytest tests/ -q --ignore=tests/test_downloader.py
# 期望：294+ passed（新增测试通过）
```

**注意**：
- 全市场扫描很慢，验证逻辑用 mock，不跑真实扫描
- LLM 测试全部 mock（不调真实 API）
- 无 key 时整个 workflow 必须与阶段三行为完全一致
