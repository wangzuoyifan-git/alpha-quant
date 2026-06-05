# Alpha量化 — 项目总览

> 所有 Session 的共同基础参考。专项内容见各模块文档。
> 最后更新：2026-05-29

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
| Phase 1（当前） | 辅助决策 | 数据更新、扫描、信号生成自动化；交易决策由人工判断后手动执行 | 基础功能已完成，多 Agent 体系建设中 |
| Phase 2 | 半自动交易 | Agent 可自主调整策略参数、选择策略组合；生成指令单后人工确认再执行 | 待开发 |
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
- 港股、美股（store 层已预留结构，数据未接入）
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

### 3.1 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                    应用层 / CLI                          │
│              main.py（argparse 分发，18个子命令）         │
│              web/（Flask 看板）                          │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   Agent 层（建设中）                      │
│  Orchestrator Agent（LangGraph + DeepSeek/Qwen）         │
│  ├── Data Agent      数据更新、质量检查                   │
│  ├── Strategy Agent  策略选择、参数优化、回测评估          │
│  └── Risk/Trade Agent 风控过滤、指令单生成、推送           │
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
│  strategies/builtin/         内置策略（4个）              │
│  strategies/custom/          自定义策略目录               │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                   数据层                                 │
│  data/store*.py       本地存储（Parquet，6个域）           │
│  data/downloader*.py  批量下载器（6个域）                  │
│  data/fetcher.py      网络获取（AKShare主/BaoStock备）     │
└─────────────────────────────────────────────────────────┘
```

### 3.2 数据域概览

| 域 | 路径 | 状态 | 说明 |
|---|---|---|---|
| market | `data/market/` | 完成 | A股/指数/ETF 日线，5207只，2024起 |
| meta | `data/meta/` | 完成 | 申万/证监会行业分类，交易日历 |
| fundamental | `data/fundamental/` | 完成 | 季频财务（利润/资产/现金流/成长/杜邦） |
| factor | `data/factor/` | 部分完成 | 北向资金已有；个股资金流向、行业资金流向待补全 |
| macro | `data/macro/` | 部分完成 | 国内：国债收益率/M2/CPI-PPI/PMI 已有；社融/汇率 待下载。境外：美联储利率/VIX/美债/美元/原油/铜/标普/恒生 待下载 |
| tushare | `data/tushare/` | 部分完成 | moneyflow/moneyflow_ind/moneyflow_mkt 已有；daily/top_list/margin/分钟线 待下载（已付费） |
| events | `data/events/` | 已建设 | E1 定期事件日历、E2 企业事件（减持/质押/分红）、E3 突发事件归档；见 `docs/requirements.md` D8 节 |

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
├── data/                        # 数据层
│   ├── store.py                 # market 域存取
│   ├── store_meta.py
│   ├── store_fundamental.py
│   ├── store_factor.py
│   ├── store_macro.py
│   ├── store_tushare.py
│   ├── fetcher.py               # 网络数据获取
│   ├── downloader.py            # K线批量下载
│   ├── downloader_meta.py
│   ├── downloader_fundamental.py
│   ├── downloader_factor.py
│   ├── downloader_macro.py
│   ├── downloader_tushare.py
│   ├── market/                  # A股/指数/ETF 日线 Parquet
│   ├── fundamental/             # 季频财务数据
│   ├── meta/                    # 行业分类 + 交易日历
│   ├── factor/                  # 因子数据
│   ├── macro/                   # 宏观数据
│   ├── tushare/                 # Tushare 数据
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
│   ├── base.py                  # BaseStrategy + Signal
│   ├── builtin/                 # 内置策略 + 注册表
│   └── custom/                  # 自定义策略
│
├── agent_tools/                 # Tool Layer（已完成，18个工具）
│   ├── data_tools.py            # 数据相关 Tools
│   ├── strategy_tools.py        # 策略/回测 Tools
│   └── trade_tools.py           # 交易/风控 Tools
│
├── agents/                      # Agent 实现（待建设）
│   ├── orchestrator.py          # Orchestrator（LangGraph）
│   ├── data_agent.py
│   ├── strategy_agent.py
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

`.gitignore` 排除：`data/market/`、`data/kline/`、`data/fundamental/`、`data/meta/`、`data/factor/`、`data/macro/`、`venv/`、`.env`、`logs/`、`__pycache__/`

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
| `docs/agent.md` | Agent 层：架构设计、Tool Layer 规范、LLM 接入、阶段规划 |
| `docs/testing.md` | 测试分层策略、覆盖缺口、运行规范 |
| `docs/ops.md` | CLI 速查、定时脚本、环境变量配置 |
| `docs/archive/` | 过程文档归档（不再维护） |
