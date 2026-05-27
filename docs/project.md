# Alpha量化 — 项目基础信息文档

> 本文档是所有 Session 的共同基础参考，包含项目环境、数据库状态、目录结构、CLI 命令等核心信息。
> 专项文档直接引用本文档，不再重复基础信息。
>
> **维护说明**：每次有重要变更（数据库更新、新模块、新命令、依赖变化）时更新本文档。
> 最后更新：2026-05-26

---

## 一、项目概览

| 项目 | 说明 |
|---|---|
| 项目名称 | Alpha量化 — A股日线级别量化分析系统 |
| 根目录 | `/home/wz/alpha量化` |
| Python | 3.12，虚拟环境 `venv/` |
| 运行方式 | `venv/bin/python3 main.py <command>`（必须用 venv） |
| 主入口 | `main.py`（CLI 分发） |
| 配置文件 | `config/settings.py`（全局配置） |
| 环境变量 | `.env`（不纳入 git，从 `.env.example` 复制） |

---

## 二、Git 仓库

| 项目 | 说明 |
|---|---|
| 仓库类型 | 本地 Git 仓库 |
| 主分支 | `main` |
| user.name | `wz` |
| user.email | `wz@alpha` |

### 提交规范

```
feat:     新功能
fix:      Bug 修复
refactor: 重构（不改变功能）
test:     测试相关
docs:     文档更新
chore:    构建/依赖/配置等杂项
```

### 常用命令

```bash
git status && git diff          # 查看改动
git add <file> && git commit -m "fix: 描述"
git log --oneline               # 查看历史
git checkout <hash> -- <file>   # 回滚单个文件
```

### .gitignore 说明

以下内容不纳入版本控制：
- `data/market/`、`data/kline/`、`data/fundamental/`、`data/meta/`、`data/factor/`、`data/macro/` — 数据文件
- `venv/` — 虚拟环境
- `.env` — 含密钥的环境变量
- `logs/` — 运行日志
- `__pycache__/`、`*.pyc` — Python 缓存

---

## 三、当前数据库状态

> 最后更新：2026-05-26

### market 域（已完成）

| 类型 | 数量 | 行数 | 大小 | 时间范围 | 关键字段 |
|---|---|---|---|---|---|
| cn/stock | 5207只 | 294万行 | 348MB | 2024-01-02~今 | OHLCV+换手率+PE/PB/PS/PCF+isST |
| cn/index | 541只 | 30万行 | 25MB | 2024-01-02~今 | OHLCV+pct_chg |
| cn/etf | 1535只 | 65万行 | 42MB | 2024-01-02~今 | OHLCV+pct_chg |
| ipo_dates.json | 8624只 | — | — | — | ipo_date/market/type/name |

### 其他域（待开发）

| 域 | 状态 | 数据源 | 优先级 |
|---|---|---|---|
| fundamental/ | 🔴 未开发 | BaoStock（免费） | P1 |
| meta/ | 🔴 未开发 | BaoStock + AKShare | P0 |
| factor/ | 🔴 未开发 | AKShare | P2 |
| macro/ | 🔴 未开发 | AKShare | P3 |

> 详细缺口分析见 `docs/data_gap_analysis.md`

---

## 四、目录结构

```
alpha量化/
├── main.py                      # CLI 主入口
├── requirements.txt             # 依赖清单
├── .env.example                 # 环境变量模板
│
├── config/
│   └── settings.py              # 全局配置（SCAN/BACKTEST/NOTIFIER等）
│
├── data/
│   ├── store.py                 # market 域存取（勿随意修改）
│   ├── fetcher.py               # 网络数据获取（AKShare/BaoStock）
│   ├── downloader.py            # K线批量下载器
│   ├── market/                  # 行情数据（已完成）
│   │   ├── cn/stock/            # A股日线 Parquet
│   │   ├── cn/index/            # 指数日线
│   │   ├── cn/etf/              # ETF日线
│   │   └── ipo_dates.json       # 上市日期索引
│   ├── fundamental/             # 财务数据（待开发）
│   ├── meta/                    # 行业分类（待开发）
│   ├── factor/                  # 因子数据（待开发）
│   ├── macro/                   # 宏观数据（待开发）
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
│   ├── base.py                  # BaseStrategy + Signal 基类
│   ├── builtin/
│   │   ├── __init__.py          # 策略注册表
│   │   ├── ma_cross.py          # 均线金叉策略
│   │   ├── macd_rsi.py          # MACD+RSI 策略
│   │   └── vol_price_breakout.py # 量价突破策略
│   └── custom/                  # 自定义策略目录
│
├── notifier/push.py             # 多渠道推送（企微/邮件/Telegram）
├── trader/assistant.py          # 半自动交易辅助
├── signals/manager.py           # 信号持久化
│
├── tools/
│   ├── plot_kline.py            # K线图验证工具
│   └── test_init_flow.py        # init 流程端到端测试
│
├── tests/                       # 单元测试（61个）
├── docs/                        # 文档目录
└── logs/                        # 运行日志
```

---

## 五、CLI 命令速查

```bash
cd ~/alpha量化

# ---- 数据管理 ----
venv/bin/python3 main.py init                        # 全量初始化（stock+index+etf）
venv/bin/python3 main.py init --types stock          # 只下载 A股
venv/bin/python3 main.py init --types etf --force    # 强制重新下载 ETF
venv/bin/python3 main.py update                      # 每日增量更新
venv/bin/python3 main.py dbstats                     # 数据库统计

# ---- 分析 ----
venv/bin/python3 main.py scan                        # 全市场扫描（A股，约83秒）
venv/bin/python3 main.py scan --date 2026-05-20      # 指定日期扫描
venv/bin/python3 main.py backtest --strategy macd_rsi
venv/bin/python3 main.py backtest --strategy ma_cross \
    --start 2024-01-02 --end 2026-05-25 \
    --codes 600519 000001 300750

# ---- 交易辅助 ----
venv/bin/python3 main.py orders                      # 查看待确认指令单
venv/bin/python3 main.py confirm <order_id>
venv/bin/python3 main.py cancel <order_id>

# ---- 服务 ----
venv/bin/python3 main.py web                         # Web看板 http://localhost:8888
venv/bin/python3 main.py schedule                    # 定时任务（收盘后自动更新+扫描）

# ---- 工具 ----
venv/bin/python3 tools/plot_kline.py 600519 000001   # 绘制K线图
venv/bin/python3 tools/test_init_flow.py             # 验证 init 流程

# ---- 后台运行（推荐用 tmux）----
tmux new-session -d -s alpha "cd ~/alpha量化 && venv/bin/python3 -u main.py init > logs/init.log 2>&1"
tmux attach -t alpha
tail -f logs/init.log
```

---

## 六、数据源说明

### BaoStock（主力数据源）

- **特点**：免费、稳定、专为量化设计、支持历史数据
- **覆盖**：A股/指数/ETF 日线、季频财务数据、行业分类
- **限制**：不支持实时行情、不支持港美股、socket 非线程安全（必须串行）
- **使用注意**：
  ```python
  import baostock as bs
  import socket
  socket.setdefaulttimeout(60)   # 全量查询用 60s
  bs.login()
  # login 后立即设置已连接 socket 的 timeout
  import baostock.common.context as bs_ctx
  bs_ctx.default_socket.settimeout(10)  # 单只查询用 10s
  # ... 查询 ...
  bs.logout()
  time.sleep(3)  # logout 后等 3 秒再下次 login，避免限流
  ```

### AKShare（补充数据源）

- **特点**：免费、覆盖面广（资金流/宏观/公告等）
- **限制**：依赖东方财富等第三方网站，接口可能失效，有频率限制
- **使用注意**：每次请求后 `time.sleep(0.5~1)`，失败时指数退避重试（最多3次）

### Tushare Pro（付费数据源）

- **特点**：数据质量高、覆盖分钟线/龙虎榜/港美股等高级数据
- **配置**：在 `.env` 中设置 `TUSHARE_TOKEN=your_token`
- **当前状态**：Token 未配置，相关功能未开发

---

## 七、依赖清单

```
akshare>=1.12.0       # 主数据源（免费）
baostock>=0.8.8       # 备用数据源（免费）
tushare>=1.2.89       # 付费数据源（可选）
pandas>=2.0.0
numpy>=1.24.0
pyarrow>=12.0.0       # Parquet 存储
mplfinance>=0.12.0    # K线图
matplotlib>=3.7.0
flask>=3.0.0          # Web 看板
apscheduler<4         # 定时任务
pytz
requests>=2.31.0
python-dotenv>=1.0.0
rich>=13.0.0          # 终端美化
```

---

## 八、环境变量（.env）

```bash
# 数据源
TUSHARE_TOKEN=          # Tushare Pro Token（可选）

# 企业微信机器人（填真实 key 才会推送）
WECOM_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY

# 邮件（填真实账号才会推送）
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
EMAIL_USER=your@qq.com
EMAIL_PASS=your_smtp_auth_code
EMAIL_TO=recipient@example.com

# Telegram（可选）
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
```

> 推送渠道自动检测：占位符（含 `your_key`/`example.com`）视为未配置，自动跳过，不会报错。

---

## 九、专项文档索引

| 文档 | 用途 |
|---|---|
| `docs/project.md` | **本文档**，项目基础信息，所有 Session 共用 |
| `docs/review_context.md` | 代码 Review 上下文：模块接口、已知 Bug、单元测试建议 |
| `docs/strategy_dev_context.md` | 策略开发上下文：BaseStrategy 用法、ETF 策略建议、待开发清单 |
| `docs/data_gap_analysis.md` | 数据缺口分析：各数据源覆盖范围、优先级建议 |
| `docs/data_init_session_context.md` | 扩展数据初始化：fundamental/meta/factor/macro 域的 API 和实现方案 |
