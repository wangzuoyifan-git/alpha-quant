# Alpha量化 — 运维手册

> CLI 速查、定时脚本、环境变量配置、Git 规范。
> 最后更新：2026-05-29
>
> **项目基础信息见 `docs/project.md`**

---

## 一、环境配置

### 1.1 环境变量（.env）

从 `.env.example` 复制后填写，不纳入 git：

```bash
cp .env.example .env
```

```bash
# 数据源
TUSHARE_TOKEN=        # Tushare Pro Token（已付费，必填）

# 推送渠道（至少配置一个）
WECOM_WEBHOOK=        # 企业微信机器人 Webhook
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
EMAIL_USER=
EMAIL_PASS=
EMAIL_TO=
TELEGRAM_TOKEN=       # 可选
TELEGRAM_CHAT_ID=     # 可选

# LLM（Agent 层，config/settings.py 已配置；接入 LLM 推理时填写）
DEEPSEEK_API_KEY=
QWEN_API_KEY=
WENXIN_API_KEY=       # 文心一言（备用）
OPENAI_API_KEY=       # 通过代理备用
OPENAI_BASE_URL=      # 代理地址
```

### 1.2 激活虚拟环境

```bash
cd /home/wz/alpha量化
source venv/bin/activate
```

---

## 二、CLI 速查

### 数据管理

```bash
# market 域
python3 main.py init                              # 全量下载（stock+index+etf，2024-01-02起）
python3 main.py init --start 2023-01-01           # 指定起始日期
python3 main.py init --types stock                # 只下载A股
python3 main.py init --force                      # 强制重新下载（覆盖已有数据）
python3 main.py update                            # 每日增量更新到今天
python3 main.py dbstats                           # 查看 market 域统计

# 扩展域
python3 main.py init-meta                         # 行业分类+申万指数+交易日历
python3 main.py init-fundamental                  # 季频财务数据（2024起）
python3 main.py init-fundamental --start-year 2022
python3 main.py init-factor                       # D3a 个股资金流+D3c 北向资金+行业资金流
python3 main.py init-macro                         # D5a 国内宏观（国债/M2/CPI/PMI/社融/汇率）
                                                  # D5b 境外宏观（VIX/DXY/原油WTI/标普/恒生）；LME铜/美联储利率待下载

# 事件数据（D8，store/downloader 已建设，暂无 CLI 入口）
# events 域目前通过 Python 调用：
#   from data.downloader_events import init_events, update_events
#   init_events()      # E1 日历 + E2 企业事件全量
#   update_events()    # E1 补充 + E2 近3月增量

# Tushare 域
python3 main.py init-tushare                      # 全部类型（moneyflow 2010起，其余 2024起）
python3 main.py init-tushare --types moneyflow    # 只下载个股资金流向（D3a 历史）
python3 main.py init-tushare --types margin       # 只下载融资融券（D7 情绪因子）
python3 main.py init-tushare --types moneyflow_ind  # 行业资金流向（D3b）
python3 main.py init-tushare --moneyflow-start 2015-01-01
python3 main.py update-tushare                    # 增量更新 tushare 域

# 全域更新
python3 main.py update-all                        # 更新 market+meta+fundamental+factor+macro+tushare（不含 events）
```

### Agent 工作流（暂无 CLI 入口）

```python
# agents 已实现（规则版），目前通过 Python 导入调用，尚未接入 main.py CLI：
from agents import run_daily_workflow
run_daily_workflow()              # check_data→scan→risk_filter→approval→orders→notify
from agents import check_and_update, recommend_daily_plan
```

### 分析

```bash
# 扫描
python3 main.py scan                              # 今日全市场扫描（A股）
python3 main.py scan --date 2025-01-10            # 指定日期
python3 main.py scan --types etf                  # 扫描 ETF
python3 main.py scan --types stock etf            # 同时扫描 A股 + ETF

# 回测
python3 main.py backtest --strategy macd_rsi
python3 main.py backtest --strategy ma_cross --start 2024-01-02 --end 2026-05-25
python3 main.py backtest --codes 600519 000001 300750
```

### 交易辅助

```bash
python3 main.py orders                            # 查看待确认指令单
python3 main.py confirm <order_id>                # 确认指令单
python3 main.py cancel <order_id>                 # 取消指令单
```

### 服务

```bash
python3 main.py web                               # Web 看板 http://localhost:8888
python3 main.py schedule                          # 启动定时任务（收盘后自动更新+扫描）
```

### 开发工具

```bash
python3 tools/plot_kline.py 600519 000001 --start 2024-01-01   # K线图
python3 tools/test_init_flow.py                                 # init 流程端到端验证
```

---

## 三、定时脚本

脚本位于 `scripts/`，需先激活虚拟环境。

### 每日（收盘后，约 15:30）

```bash
scripts/update_daily.sh
# 执行：update(market) → meta(周一init/其余update) → factor → update-tushare → scan(stock)
# 注意：脚本未走 update-all，不含 fundamental/macro 域（分别由季度/每周脚本覆盖）
```

### 每周（周六 10:00）

```bash
scripts/update_weekly.sh
# 执行：meta 域 update_db（申万行业指数） → macro 域 update_db（月频宏观）
```

### 每季度（季报披露后，约1月/4月/8月/10月）

```bash
scripts/update_quarterly.sh
# 执行：fundamental 域 update_db（更新财务数据）
```

### 用 crontab 配置自动运行

```bash
crontab -e

# 每个交易日 15:35 运行每日更新
35 15 * * 1-5 /home/wz/alpha量化/scripts/update_daily.sh >> /home/wz/alpha量化/logs/update_daily.log 2>&1

# 每周六 10:00 运行每周更新（与脚本头注释一致）
0 10 * * 6 /home/wz/alpha量化/scripts/update_weekly.sh >> /home/wz/alpha量化/logs/update_weekly.log 2>&1

# 每季度首月1日 09:00 运行季度更新
0 9 1 1,4,8,10 * /home/wz/alpha量化/scripts/update_quarterly.sh >> /home/wz/alpha量化/logs/update_quarterly.log 2>&1
```

### 用 APScheduler 运行（内置调度器）

```bash
python3 main.py schedule
# 在 tmux 中后台运行：
tmux new -s alpha_schedule
python3 main.py schedule
# Ctrl+B D 退出 tmux，保持后台运行
tmux attach -t alpha_schedule   # 重新连接
```

---

## 四、Git 规范

### 提交格式

```
feat:     新功能
fix:      Bug 修复
refactor: 重构（不改变功能）
test:     测试相关
docs:     文档更新
chore:    构建/依赖/配置等杂项
```

示例：
```bash
git add strategies/builtin/etf_momentum.py tests/test_strategies.py
git commit -m "feat: 新增 ETF 动量轮动策略"

git add docs/strategy.md
git commit -m "docs: 更新策略层文档，补充 ETF 动量策略说明"
```

### .gitignore 排除项

```
data/market/
data/kline/
data/fundamental/
data/meta/
data/factor/
data/macro/
data/tushare/
data/processed/
data/orders/
data/.cache/
venv/
.env
logs/
__pycache__/
*.pyc
*.pyo
```

---

## 五、依赖管理

```bash
# 安装全部依赖
pip install -r requirements.txt

# 新增依赖后更新 requirements.txt
pip freeze > requirements.txt

# 主要依赖版本
akshare>=1.12.0
baostock>=0.8.8
pandas>=2.0.0
numpy>=1.24.0
pyarrow>=12.0.0
mplfinance>=0.12.0
matplotlib>=3.7.0
flask>=3.0.0
apscheduler<4
pytz
requests>=2.31.0
python-dotenv>=1.0.0
rich>=13.0.0
pytest
pytest-cov

# Agent 层依赖（已安装，agents/ 使用 langgraph 编排）
# langgraph            # 已安装；⚠️ 尚未写入 requirements.txt，需补充
# langchain            # LLM 接入时安装
# langchain-openai     # 兼容 DeepSeek/Qwen 的 OpenAI 格式接口（LLM 接入时安装）
```

---

## 六、常见问题

**BaoStock 连接超时**
```bash
# 检查网络，重试即可；后台进程中 bs.login() 可能死循环，
# downloader_fundamental.py 已用 threading.Thread + join(timeout) 包装
```

**AKShare 接口限流（429）**
```bash
# 正常现象，downloader 内置指数退避重试（最多3次）
# 如频繁失败，适当增大 sleep 间隔
```

**扫描耗时过长**
```bash
# 默认 20 线程，全市场约 83 秒
# 可在 config/settings.py 的 SCAN 中调整 workers（需同步修改 ScanEngine 初始化）
```

**Web 看板指令单重复显示**
```bash
# 已知问题，trader/assistant.py 的持久化逻辑待优化
# 临时处理：清空 data/orders/ 目录
```
