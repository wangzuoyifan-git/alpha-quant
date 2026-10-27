# Alpha量化 — 项目总览

> 所有 Session 的共同基础参考。专项内容见各模块文档。
> 最后更新：2026-10-27（缠论 P1+P2+P3：`chan/` 纯计算层（包含/分型/笔/中枢/背驰/1-2-3类买卖点/日线+周线确认）+ `chan_theory` L3-A 策略 + `chan` CLI + `agent_tools/chan_tools.py` + A2 缠论摘要 + K线图叠加；详见 `docs/strategy.md` 2.2.1）
> 2026-10-10（update_daily 提速与可靠性：moneyflow `stock`/`margin` 每日增量改按 `trade_date` 批量（~1.1万次→每交易日2次）；events E2 全市场改按日期批量（~2.2万次→百余次，约1.5h→1~2min）；港股资金流加 1.5s/只限速+连续失败熔断；批量步骤失败率过高以非零退出码上报（`_check_bulk_result`）；`update_daily.sh` 港股跳过窗口改为仅盘中 09:30–16:15、跳过计入汇总并退出码1）
> 2026-09-30（资金画像 Phase 2：板块/ETF（申万+ETF）—— `agents/sector_aggregator.py` 聚合 + `data/store_fund.py`/`downloader_fund.py`（index_weight/fund_share/fund_nav）+ `capital-profiler --level sector_etf`）
> 2026-09-29（资金画像 Phase1 增强：席位×主力一致性 + 席位级事件流合力/分歧子信号；产业资本接入 Tushare `repurchase`（E2 `buyback`）；`--hidden-main` 默认关闭）
> 2026-09-29（资金画像 Phase1 增强：席位×主力一致性；产业资本接入 Tushare `repurchase`（E2 `buyback`））
> 2026-09-29（席位字典软标签收紧 + 来源标注：纯度门槛 0.6、软标签最低置信 0.7、身份列带 `[硬]/[软]/[规则]/[推断]` 前缀）
> 2026-09-29（资金画像 Phase 1：`agents/capital_profiler.py` 个股级（主导资金/反馈/持续性/结构评分/无法判断）+ `capital-profiler` CLI + 设计文档 `docs/capital_profiler.md`）
> 2026-09-29（席位字典（游资/量化）：`agents/seat_profiler.py` 三层（xlsx 硬标签 + 四特征聚类软标签 + 次日跟投回测）+ `seat-dict` CLI + 每周脚本重估；盘前龙虎榜扫描 `smart-money --recent-lhb`）
> 2026-09-29（盘前龙虎榜扫描：`smart-money --recent-lhb --days N` 输出近期标的 + 席位跨股历史画像 + 本股历史其他上榜记录；画像计算向量化提速）
> 2026-09-28（新增资金主体画像：moneyflow 域 top_inst/block_trade/hk_hold + `agents/smart_money_agent.py` 三支柱 + `agent_tools/smart_money_tools.py` + `smart-money` CLI）
> 2026-09-22（macro 域修复：`update_db()` 补齐汇率/境外指标 + `update-macro` CLI；CPI/PPI、社融、汇率、DXY、WTI 换源，修复 oil_wti 误存黄金数据）
> 2026-08-13（港股 K 线接入：`data/downloader_hk.py` + `init-hk/update-hk`，港股通标的优先，AKShare Sina 主源）
> 2026-06-21（L2行业评分升级到申万L2/131个细分行业；ETF独立三维评分体系 agents/etf_agent.py + data/etf_config.json；portfolio theme_etf 出场路径；L3联动强势主题ETF补入选股池；阶段A~D：成交额过滤/行业排除修复/Tool契约对齐/A2辅助分析 agent_tools/analysis.py）

---

## 一、项目定位

| 项目 | 说明 |
|---|---|
| 项目名称 | Alpha量化 — A股量化分析与自动交易系统 |
| 根目录 | `/home/wz/alpha量化` |
| Python | 3.12，虚拟环境 `venv/` |
| 运行方式 | `venv/bin/python3 main.py <command>` |
| 主入口 | `main.py`（CLI 分发） |
| 配置文件 | `config/settings.py` |
| 环境变量 | `.env`（从 `.env.example` 复制，不纳入 git） |

---

## 二、目标、需求与约束

### 2.1 最终目标

**全自动化量化交易系统**：从数据更新、信号生成、策略评估到交易执行，全流程由系统自主完成，人工只做监控和异常干预。

### 2.2 阶段规划

| 阶段 | 目标 | Agent 自主程度 | 当前状态 |
|---|---|---|---|
| Phase 1（当前） | 辅助决策 | 数据更新、扫描、信号生成自动化；交易决策由人工判断后手动执行 | 已完成（含 agents 规则版编排） |
| Phase 2 | 半自动交易 | Agent 可自主调整策略参数、选择策略组合；生成指令单后人工确认再执行 | **台阶二完成**（L3-B基本面策略/L2三维评分/allocation约束/LLM-R6候选点评；回测驱动待阶段五） |
| Phase 3 | 全自动交易 | Agent 在风控规则约束内自主完成从分析到下单的全流程 | 待开发 |

### 2.3 功能需求边界

**在范围内：**
- A 股日线级别量化分析（当前），未来扩展至日内（分钟线）
- 多策略并行扫描与信号合并
- 策略回测与绩效评估
- 多渠道信号推送（企微/邮件/Telegram）
- 半自动交易指令单生成与确认
- 多 Agent 协作：数据 Agent、策略 Agent、风控/交易 Agent

**不在范围内（当前阶段）：**
- 美股（store 层已预留结构，数据未接入；港股 K 线已接入，见 `docs/data.md` 3.2 节）
- 分钟线策略（数据未接入）
- 合规报备（个人投资，暂不考虑）

### 2.4 技术约束

| 约束 | 说明 |
|---|---|
| LLM 接入 | 国内 API 为主（DeepSeek / Qwen / 文心），OpenAI/Anthropic 通过代理备用；API 格式兼容 OpenAI |
| 数据源 | BaoStock（免费，A股/基本面主力）、AKShare（免费，ETF/宏观/因子主力）、Tushare Pro（已付费，积分充足）；详细限制见 `docs/data.md` 第五章 |
| 运行环境 | Linux，Python 3.12，本地部署，无 K8s/云原生要求 |
| 开发工具 | opencode 作为主要编码辅助，不依赖 Claude Code |
| 时间周期 | 当前日线，未来扩展日内 |

---

## 三、系统架构

> 架构设计原则与约束见 `docs/architecture.md`
> 分层决策详细设计见 `docs/agent.md` 第六章、`docs/requirements.md` 第八章

### 3.1 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                    应用层 / CLI                          │
│              main.py（argparse 分发，38个子命令）         │
│              web/（Flask 看板）                          │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   Agent 层（分层决策 + LLM 融合）                    │
│  Orchestrator Agent（LangGraph 编排，规则+LLM）          │
│                                                         │
│  ┌─────────────────────────────────────────────┐       │
│  │ L1 宏观场景层（慢变：周/事件级）✅ 阶段一+三  │       │
│  │   evaluate_market_condition → regime/risk_mode       │
│  │   输出：decision_context（模式基调+资金分配+L2权重）  │
│  └────────────────┬────────────────────────────┘       │
│                   │                                     │
│  ┌────────────────▼───────────────────────────┐        │
│  │ L2 行业轮动层（中变：月/季级）✅ 阶段二       │       │
│  │   三维评分（景气/估值/资金流，权重随regime）           │
│  │   双重职责：①中长期配置仓决策 ②向L3传约束            │
│  │   输出：行业池+行业详情+allowed_styles+horizon_map   │
│  └────────────────┬────────────────────────────┘       │
│                   │                                     │
│  ┌────────────────▼───────────────────────────┐        │
│  │ L3 个股选择层（快变：日级）✅ 阶段一          │       │
│  │   L3-A 技术策略→交易仓(BUY)                          │
│  │   L3-B 基本面策略→配置仓候选(WATCH)→人工确认         │
│  └─────────────────────────────────────────────┘       │
│                                                         │
│  独立 Agent 模块：                                       │
│  ├── Data Agent      数据更新、质量检查      ✅ 规则版    │
│  ├── Strategy Agent  L1宏观评估、L3策略选择  ✅ 规则版    │
│  ├── Industry Agent  L2行业轮动              ✅ 规则版    │
│  ├── Risk/Trade Agent 风控过滤、指令单生成   ✅ 规则版    │
│  └── Smart Money Agent 资金主体识别与跨期画像 ✅ 规则版（2026-09） │
│                                                         │
│  Tool Layer（agent_tools/）✅ 已完成                     │
│  薄适配层，将下层能力封装为结构化 Tool 供 Agent 调用       │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   引擎层                                 │
│  engine/scanner.py    全市场并发扫描（20线程）             │
│  backtest/engine.py   事件驱动回测                        │
│  signals/manager.py   信号持久化                          │
│  trader/assistant.py  指令单生成与管理                    │
│  notifier/push.py     多渠道推送                          │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   策略层                                 │
│  strategies/base.py          BaseStrategy + Signal       │
│  strategies/builtin/         内置策略（8个，正交框架）        │
│  strategies/custom/          自定义策略目录               │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   数据层                                 │
│  data/store*.py       本地存储（Parquet，6个域）           │
│  data/downloader*.py  批量下载器（7个域）                  │
│  data/fetcher.py      网络获取（Tushare主/AKShare/BaoStock备）│
└─────────────────────────────────────────────────────────┘
```

**分层决策架构说明（2026-06-12 重定位）：**

**系统定位：多周期配置 + 择时**（非纯短线）。各层按决策粒度划分，时间周期是正交属性。

Agent 层内部实现**三层决策架构**，每层职责如下：

- **L1 宏观层**（慢变，周/事件级）：评估市场环境，设定模式基调，输出 decision_context（总风险约束 + 配置/交易仓资金分配 + L2 权重配置）
- **L2 行业层**（中变，月/季级）：**双重职责**——① 中长期配置仓决策者（三维评分：景气/估值/资金流，权重随 regime 动态调整）② L3 约束提供者（行业池 + 允许的策略风格空间）
- **L3 个股层**（快变，日级）：L3-A 技术策略产出交易仓信号（BUY），L3-B 基本面策略产出配置仓候选（WATCH，经 L2 行业池过滤后供人工确认）

**仓位分层**：
```
总仓位（L1 管总上限）
├── 配置仓（中长期）：L2 行业配置 + L3-B 个股价值投资
└── 交易仓（短期）：L3-A 技术择时
```

**关键设计原则**：
1. 上层输出的是**约束上下文**（decision_context），不是直接信号
2. 下层在进入前就被收窄动作空间（对应 Qlib Nested Decision 思想）
3. 每层决策可回流，支持绩效驱动优化（阶段五）

详细设计见 `docs/agent.md` 第六章、`docs/requirements.md` 第八章。

### 3.2 数据域概览

| 域 | 路径 | 状态 | 说明 |
|---|---|---|---|
| market | `data/market/` | 完成 | A股/指数/ETF 日线（Tushare hfq），5531/760/2032只，2024起；**港股日线 `data/market/hk/`（AKShare Sina 主源，港股通标的优先，2026-08 接入）**；baostock基线备份在 `data/market_baostock/` |
| meta | `data/meta/` | 完成 | 申万SW2021三级（L1=31/L2=134/L3=346）/证监会行业分类/交易日历 |
| fundamental | `data/fundamental/` | 完成 | 季频财务（利润/资产/现金流/成长/杜邦） |
| factor | `data/factor/` | 重新定位 | 计算型因子（动量/价值/质量等衍生值），台阶二前暂空 |
| macro | `data/macro/` | 完成 | 国内 6 项 + 境外 8 项（含美债 `us_bond_yield` 派生、`copper_lme`、`fed_rate`）全部落地；`update_db()`/`update-macro` 覆盖全部下载项 |
| moneyflow | `data/moneyflow/` | 完成 | 资金流与情绪域：stock/industry/market/northbound/margin/top_list + 资金主体类 top_inst/block_trade/hk_hold；行业资金流用东财BK体系（覆盖申万L1全部/L2约95%） |
| events | `data/events/` | 已建设 | E1 定期事件日历、E2 企业事件（减持/增持/质押/分红/回购）、E3 突发事件归档 |
| fund | `data/fund/` | 已建设 | Phase2：ETF 列表/份额/净值/指数权重/ETF→指数映射（Tushare fund_basic/fund_share/fund_nav/index_weight） |

> 详细接口和数据结构见 `docs/data.md`

### 3.3 模块依赖关系

```
main.py
  ├── config/settings.py
  ├── data/fetcher.DataFetcher      → akshare / baostock
  ├── data/downloader.KlineDownloader → fetcher + store
  ├── engine/scanner.ScanEngine     → fetcher + store + strategies
  ├── backtest/engine.Backtester    → fetcher + store + strategies
  ├── strategies/builtin            → strategies/base
  ├── notifier/push.Notifier        → strategies/base.Signal
  ├── trader/assistant.TradeAssistant → strategies/base.Signal
  └── signals/manager               → strategies/base.Signal

store_*.py  各自独立，互不依赖
downloader_*.py  各自独立，依赖对应 store_*
```

---

## 四、目录结构

```
alpha量化/
├── main.py                      # CLI 主入口
├── requirements.txt
├── .env.example
│
├── config/settings.py           # 全局配置
│
├── chan/                        # 缠论结构分析（纯计算层，无 IO/策略依赖）
│   ├── engine.py                # ChanEngine.analyze(df, level) -> ChanResult
│   ├── models.py                # Bar/MergedBar/Fractal/Bi/Pivot/BuySellPoint/ChanResult
│   ├── kline.py                 # K线包含处理
│   ├── fractal.py               # 顶/底分型
│   ├── bi.py                    # 笔
│   ├── pivot.py                 # 中枢（ZG/ZD/GG/DD）
│   ├── divergence.py            # MACD 面积背驰
│   ├── buy_sell.py              # 1/2/3 类买卖点
│   ├── multi_level.py           # 日线+周线确认
│   └── adapter.py               # df 标准化 / 周线聚合
│
├── data/                        # 数据层
│   ├── store.py                 # market 域存取
│   ├── store_meta.py
│   ├── store_fundamental.py
│   ├── store_factor.py
│   ├── store_macro.py
│   ├── store_moneyflow.py
│   ├── store_events.py
│   ├── store_fund.py            # fund 域存取（Phase2）
│   ├── fetcher.py               # 网络数据获取
│   ├── downloader.py            # K线批量下载（BaoStock，降级备用）
│   ├── downloader_tushare.py    # K线批量下载（Tushare，主链路）
│   ├── downloader_hk.py         # 港股日线下载（AKShare Sina 主源，港股通标的优先）
│   ├── downloader_meta.py
│   ├── downloader_fundamental.py
│   ├── downloader_factor.py
│   ├── downloader_macro.py
│   ├── downloader_moneyflow.py
│   ├── downloader_events.py
│   ├── downloader_fund.py       # ETF 份额/净值/指数权重下载（Phase2）
│   ├── market/                  # A股/指数/ETF 日线 Parquet（Tushare hfq）
│   ├── market_baostock/         # BaoStock 历史基线（静态快照，交叉验证用）
│   ├── fundamental/             # 季频财务数据
│   ├── meta/                    # 行业分类 + 交易日历
│   ├── factor/                  # 计算型因子（台阶二前暂空）
│   ├── macro/                   # 宏观数据
│   ├── moneyflow/               # 资金流与情绪数据
│   ├── events/                  # 事件数据（E1/E2/E3）
│   ├── fund/                    # ETF/基金数据（份额/净值/指数权重，Phase2）
│   ├── processed/               # 扫描信号 JSON
│   └── orders/                  # 交易指令单 JSON
│
├── engine/
│   └── scanner.py               # 全市场扫描引擎
│
├── backtest/
│   └── engine.py                # 历史回测引擎
│
├── strategies/
│   ├── base.py                  # BaseStrategy + Signal + ExitLevels + ScaleLevel
│   ├── position_manager.py      # 持仓管理计算器（ATR/Supertrend/仓位/出场触发/移动止损）
│   ├── builtin/                 # 内置策略 + 注册表
│   └── custom/                  # 自定义策略
│
├── agent_tools/                 # Tool Layer（已完成，28个工具）
│   ├── data_tools.py            # 数据相关 Tools
│   ├── strategy_tools.py        # 策略/回测 Tools
│   ├── trade_tools.py           # 交易/风控 Tools
│   ├── smart_money_tools.py     # 资金主体识别与跨期画像 Tools
│   ├── chan_tools.py            # 缠论结构 Tools（笔/中枢/买卖点/周线）
│   └── analysis.py              # A2 辅助分析聚合（技术面/资金流/基本面/缠论摘要）
│
├── agents/                      # Agent 实现（已建设，规则版）
│   ├── orchestrator.py          # Orchestrator（LangGraph）
│   ├── data_agent.py
│   ├── strategy_agent.py        # L1 宏观层 + L3 策略选择
│   ├── industry_agent.py        # L2 行业轮动层（申万L2/131个细分行业）
│   ├── etf_agent.py             # ETF 独立三维评分（技术动量+估值代理+政策配置）
│   ├── smart_money_agent.py     # 资金主体识别与跨期画像（龙虎榜席位/北向/合力分歧）
│   ├── seat_profiler.py         # 席位字典（游资/量化）：硬标签+聚类软标签+回测
│   ├── capital_profiler.py      # 资金画像（Phase1 个股 / Phase2 板块ETF）
│   ├── sector_aggregator.py     # 板块/ETF 资金聚合（Phase2）
│   └── trade_agent.py
│
├── notifier/push.py
├── trader/assistant.py
├── signals/manager.py
│
├── scripts/                     # 定期更新脚本
│   ├── update_daily.sh
│   ├── update_weekly.sh
│   └── update_quarterly.sh
│
├── tools/                       # 开发验证工具
│   ├── inspect_data.py          # 数据直查工具（K线/资金流/宏观/基本面）
│   ├── plot_kline.py
│   └── test_init_flow.py
│
├── tests/                       # 单元测试
├── docs/                        # 文档
│   ├── project.md               # 本文档
│   ├── data.md                  # 数据层文档
│   ├── strategy.md              # 策略层文档
│   ├── agent.md                 # Agent 层文档
│   ├── testing.md               # 测试与验证
│   ├── ops.md                   # 运维手册
│   └── archive/                 # 过程文档归档
└── logs/
```

---

## 五、Git 仓库

| 项目 | 说明 |
|---|---|
| 仓库类型 | 本地 Git 仓库 |
| 主分支 | `main` |
| user.name | `wz` |
| user.email | `wz@alpha` |

提交规范：
```
feat:     新功能
fix:      Bug 修复
refactor: 重构（不改变功能）
test:     测试相关
docs:     文档更新
chore:    构建/依赖/配置等杂项
```

`.gitignore` 排除：`data/market/`、`data/kline/`、`data/fundamental/`、`data/meta/`、`data/factor/`、`data/macro/`、`data/moneyflow/`、`data/events/`、`data/processed/`、`data/orders/`、`data/cache/`、`venv/`、`.env`、`logs/`、`__pycache__/`

---

## 六、文档索引

| 文档 | 内容 |
|---|---|---|
| `docs/project.md` | **本文档**：项目总览、目标、架构 |
| `docs/architecture.md` | 架构设计原则、性能/可靠性指标、监控要求、Agent 接口规范 |
| `docs/requirements.md` | 需求详细分解：Phase 1 功能需求、验收标准、架构兼容性约束 |
| `docs/traceability.md` | 需求与测试追溯矩阵：需求编号↔测试用例双向映射、覆盖缺口分析 |
| `docs/data.md` | 数据层：各域结构、store/downloader 接口、数据源、待办缺口 |
| `docs/strategy.md` | 策略层：BaseStrategy 接口、内置策略、开发指南 |
| `docs/position_management.md` | 持仓管理：出场框架设计、仓位管理、分级操作表、回测改造 |
| `docs/agent.md` | Agent 层：架构设计、Tool Layer 规范、LLM 接入、阶段规划 |
| `docs/testing.md` | 测试分层策略、覆盖缺口、运行规范 |
| `docs/ops.md` | CLI 速查、定时脚本、环境变量配置 |
| `docs/capital_profiler.md` | 资金画像系统设计（本项目，Phase1 个股级；基于 reference/资金画像0.1.md 刷新） |
| `docs/capital_profiler_decisions.md` | 资金画像/席位字典的备选方案与决策记录 |
| `reference/资金画像0.1.md` | 资金画像系统设计参考（个股/板块/大类三类对象，v3.3，未来扩展参考） |
| `docs/archive/` | 过程文档归档（不再维护） |
