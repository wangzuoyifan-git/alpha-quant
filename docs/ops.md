# Alpha量化 — 运维手册

> CLI 速查、定时脚本、环境变量配置、Git 规范。
> 最后更新：2026-06-21（L2行业评分升级到申万L2/131个细分行业；ETF独立三维评分体系 etf_agent+etf_config.json；portfolio 新增 theme_etf 出场路径；L3联动强势主题ETF补入选股池）
>
> **项目基础信息见 `docs/project.md`**
> **持仓管理设计详见 `docs/position_management.md`**

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
# market 域（主源：Tushare，自动降级 BaoStock）
python3 main.py init                              # 全量下载（stock+index+etf，2024-01-02起）
python3 main.py init --start 2023-01-01           # 指定起始日期
python3 main.py init --types stock                # 只下载A股
python3 main.py init --force                      # 强制覆盖（迁移/重建用，baostock基线备份在 data/market_baostock/）
python3 main.py update                            # 每日增量更新到今天
python3 main.py dbstats                           # 查看 market 域统计

# 扩展域
python3 main.py init-meta                         # 行业分类+申万指数+交易日历
python3 main.py init-fundamental                  # 季频财务数据（2024起）
python3 main.py init-fundamental --start-year 2022
python3 main.py init-macro                        # 宏观经济数据
python3 main.py update-macro                      # 增量更新宏观（含汇率/境外指标）
python3 main.py update-events                     # 增量更新事件域（E1 日历 + E2 企业事件）
python3 main.py init-moneyflow                    # 资金流与情绪（stock 2010起，其余 2024起）
python3 main.py init-moneyflow --types stock      # 只下载个股资金流
python3 main.py update-moneyflow                  # 增量更新 moneyflow 域
python3 main.py update-hk-hold --codes 000001 600519   # 北向个股持股（hk_hold，季度披露，按代码）
python3 main.py update-hk-hold --from-portfolio       # 标的取自当前持仓
python3 main.py update-hk-hold --from-lhb 30          # 标的取自最近30个龙虎榜上榜交易日
python3 main.py init-hk                           # 首次下载港股 K 线（默认仅港股通标的）
python3 main.py update-hk                         # 增量更新港股 K 线
python3 main.py update-hk-valuation               # 更新港股 PE/PB/PCF（港股通，合并进 K 线）
python3 main.py update-hk-moneyflow               # 更新港股个股资金流（东财，滚动约100个交易日）
python3 main.py update-hk-financials              # 更新港股财务（三大报表+成长/杜邦）
python3 main.py update-hk-events                  # 更新港股分红事件（events E2）
python3 main.py update-cn-pcf                     # 补充 A 股 pcf_ttm（百度市现率）

# 全域更新
python3 main.py update-all                        # 全域：A股/港股K线+估值、meta、fundamental、factor、macro、moneyflow、events、A股PCF（耗时长）
```

### 数据直查（inspect）

```bash
# K线行情（支持代码或名称，名称模糊匹配；多候选时提示用户选择）
python3 main.py inspect kline 贵州茅台 --n 20
python3 main.py inspect kline 600519 --start 2026-01-01 --end 2026-06-16
python3 main.py inspect kline 000300 --type index
python3 main.py inspect kline 510300 --type etf --plot
python3 main.py inspect kline --codes 贵州茅台 000001 宁德时代         # 多只横向对比
python3 main.py inspect kline --codes 茅台 平安银行 --metric pct_chg   # 指定对比指标

# 个股资金流
python3 main.py inspect mf 茅台 --n 10
python3 main.py inspect mf --codes 贵州茅台 000001 --start 2026-05-01  # 多只对比
python3 main.py inspect mf 00700 --market hk --n 10                    # 港股资金流（主力/超大/大/中/小单）

# 行业资金流（支持名称精确/模糊匹配）
python3 main.py inspect ind 食品饮料 --n 20
python3 main.py inspect ind --codes 食品饮料 银行 半导体                # 多行业对比
python3 main.py inspect ind --all                                      # 全行业排名汇总（最新一天）
python3 main.py inspect ind --all --start 2026-06-01                   # 指定日期全行业汇总

# 宏观数据
python3 main.py inspect macro list                                     # 列出可用指标及新鲜度
python3 main.py inspect macro bond_yield --n 20
python3 main.py inspect macro vix --start 2026-01-01 --plot

# 基本面（季频财务）
python3 main.py inspect fundamental 贵州茅台                           # 默认 profit 表
python3 main.py inspect fundamental 茅台 --metric growth               # 成长指标
python3 main.py inspect fundamental 600519 --metric dupont --n 8       # 杜邦分析
python3 main.py inspect fundamental --metric list                      # 列出可用表
python3 main.py inspect fundamental --codes 茅台 平安银行 --metric profit  # 多只对比
python3 main.py inspect fundamental 00700 --market hk --metric dupont  # 港股财务（5 表：profit/balance/cashflow/growth/dupont）
```

inspect 通用参数：
- `--n N`：显示最近N条（默认20，不设 start/end 时生效）
- `--start YYYY-MM-DD` / `--end YYYY-MM-DD`：时间范围（优先于 --n）
- `--plot`：显示 matplotlib 图表（需安装 matplotlib）
- `--codes`：多个代码/名称，空格分隔
- `--metric`：对比指标（kline默认close，mf默认net_mf_amount，ind默认net_amount）

### Agent 工作流（暂无 CLI 入口）

```python
# agents 已实现（规则版），目前通过 Python 导入调用，尚未接入 main.py CLI：
from agents import run_daily_workflow
run_daily_workflow()              # check_data→scan→risk_filter→approval→orders→notify
from agents import check_and_update, recommend_daily_plan
```

### 分析

```bash
# 全市场扫描
python3 main.py scan                              # 今日全市场扫描（A股，全部策略）
python3 main.py scan --date 2026-06-16            # 指定日期
python3 main.py scan --types etf                  # 扫描 ETF（只跑 etf_momentum）
python3 main.py scan --types stock etf            # 同时扫描 A股 + ETF

# L3 独立验证（不经过 L1/L2）
python3 main.py scan --strategy ma_cross          # 单策略全市场
python3 main.py scan --strategy fundamental_value --industries 银行
                                                  # 单策略 + 行业名称（精确/模糊匹配）
python3 main.py scan --strategy growth_pick --industries 半导体 --industry-level L2
                                                  # 单策略 + 申万二级行业
python3 main.py scan --codes 招商银行 贵州茅台 宁德时代
                                                  # 所有策略 + 指定股票（支持名称）
python3 main.py scan --strategy ma_cross --codes 600519 000858
                                                  # 单策略 + 指定股票（代码或名称均可）
python3 main.py scan --industries 电子 医药生物   # 多行业（L1，名称自动解析）
```

scan 输出（BUY/WATCH 信号含操作建议列）：

| 列 | 说明 |
|---|---|
| 委托参考价 | 按信号类型智能计算（见委托价规则），附说明文字 |
| 建议股数 | `--codes` 模式：精确计算（含R倍数）；全市场模式：用7%止损估算（标注"估"） |
| 止损价 | `--codes` 模式：策略出场框架的 hard_stop；全市场模式：7%估算 |

配置仓 WATCH 信号：建议股数显示"配置仓"，止损价显示"基本面"。

# 参数说明
# --strategy: 策略 key（ma_cross/macd_rsi/vol_price_breakout/etf_momentum/
#              fundamental_value/deep_value/growth_pick），不传=全部
# --codes: 6位股票代码或名称，空格分隔；名称模糊匹配，多候选时列出供选择
# --industries: 申万行业代码或名称，与 --codes 互斥；模糊匹配多候选时列出
# --industry-level: L1/L2/L3（默认 L1）
# --date: 指定扫描日期（默认今日）

# 回测（支持基准对比 vs 沪深300）
python3 main.py backtest --strategy ma_cross
python3 main.py backtest --strategy ma_cross --start 2024-01-02 --end 2026-06-16
python3 main.py backtest --strategy etf_momentum --codes 510300 159919  # ETF回测
# 输出：总收益/年化/最大回撤/夏普/胜率/盈亏比 + 基准收益/超额收益(alpha)/beta
```

### 资金主体画像（smart-money）

> 描述性行为追踪：识别"谁在买卖"与"他们一贯怎么做"，非预测信号，不构成投资建议。
> 依赖数据：`top_inst`（席位明细）、`block_trade`（大宗）、`hk_hold`（北向个股持股）。
> 前置：`update-moneyflow --types top_inst block_trade`；北向持股按需 `update-hk-hold --codes ...` 或加 `--fetch`。

```bash
# 个股完整三支柱画像（席位身份 + 北向跨期 + 资金合力/分歧）
python3 main.py smart-money 600721
python3 main.py smart-money 600721 --window 6            # 回溯6个月
python3 main.py smart-money 600721 --fetch               # 北向持股缺失时按需联网下载
python3 main.py smart-money 600721 --mode consensus      # 只看资金合力/分歧
python3 main.py smart-money 600721 --mode northbound     # 只看北向跨期行为
python3 main.py smart-money 600721 --markdown            # 输出 Markdown 报告
python3 main.py smart-money 600721 --json                # 输出原始 JSON

# 按资金主体（席位）画像：上榜频次/累计净买卖/上榜后N日胜率/持有周期/偏好板块
python3 main.py smart-money 机构专用 --type seat
python3 main.py smart-money 深股通专用 --type seat --horizons 5,10,20

# 近期活跃资金主体榜（活动名册，非买入清单）
python3 main.py smart-money --rank --window 3 --top 20

# 盘前：最近龙虎榜标的 + 席位历史画像（本次席位 / 跨股历史画像 / 本股历史其他上榜）
python3 main.py smart-money --recent-lhb --days 1              # 最近1个龙虎榜披露日
python3 main.py smart-money --recent-lhb --days 3 --top 10     # 近3日，每股前10席位
python3 main.py smart-money --recent-lhb --days 1 --markdown   # 输出 Markdown
python3 main.py smart-money --recent-lhb --days 1 --json       # 输出原始 JSON
# 注：--history-months 控制席位历史画像回溯窗口，0=全部本地 top_inst（默认0，数据多时略慢）
```

### 交易辅助

```bash
python3 main.py orders                            # 查看待确认指令单
python3 main.py confirm <order_id>                # 确认指令单
python3 main.py cancel <order_id>                 # 取消指令单
```

### 持仓评估

持仓文件 `data/portfolio.json`（新格式，兼容旧格式纯列表）：

```json
{
  "total_capital": 2000000,
  "holdings": [
    {"code": "600519", "cost": 1520.0, "shares": 100,
     "position_type": "trading", "style": "momentum"},
    {"code": "601899", "cost": 28.55, "shares": 5000,
     "position_type": "config", "style": "value"},
    {"code": "512480", "cost": 2.10, "shares": 30000,
     "position_type": "trading", "style": "momentum"},
    {"code": "510300", "cost": 3.92, "shares": 20000,
     "position_type": "config", "style": "macro_allocation"},
     {"code": "512010", "cost": 1.65, "shares": 50000,
      "position_type": "config", "style": "industry_allocation",
      "industry": "801151.SI", "industry_level": "L2"},
     {"code": "159516", "cost": 1.00, "shares": 10000,
      "position_type": "config", "style": "theme_etf"}
   ]
}
```

可选字段说明：

| 字段 | 取值 | 说明 |
|---|---|---|
| `total_capital` | float | 账户总资金，用于仓位计算；不填默认 200 万 |
| `position_type` | trading/config | 不填按 style 推断 |
| `style` | 见下表 | 决定出场逻辑路径 |
| `asset_type` | stock/etf | 不填自动探测（先试 stock，再试 etf） |
| `industry` | 申万行业代码如 801151.SI | `industry_allocation` ETF 必填，用于读行业评分 |
| `industry_level` | L1/L2/L3 | `industry_allocation` 行业代码层级，默认 L2 |

ETF style 出场路径：

| style | 出场逻辑 | 数据来源 |
|---|---|---|
| `momentum` | ATR 技术止损 + 动量轮出 | etf_momentum 策略 |
| `industry_allocation` | 行业三维评分（景气/估值/资金流），默认 L2 粒度 | industry_agent.get_industry_score(level) |
| `macro_allocation` | L1 宏观 regime（crisis/risk_off 提示减仓） | strategy_agent.evaluate_market_condition() |
| `theme_etf` | ETF 三维综合评分（技术动量+估值代理+政策配置） | etf_agent.evaluate_etf() |

**细分主题 ETF（`theme_etf`）** 需在 `data/etf_config.json` 维护元数据：

```json
{
  "159516": {
    "name": "半导体材料设备ETF",
    "sw_l2_codes": ["801081.SI"],
    "theme": "半导体设备材料",
    "policy_outlook": "positive",
    "policy_tags": ["国产替代", "大基金三期"],
    "note": "判断依据备注",
    "benchmark": "000300"
  }
}
```

- `sw_l2_codes`：申万 L2 行业代码，双重用途——①估值分位代理（成员股 pe_ttm 聚合）②L3 联动（主题强势时在这些行业内选股）
- `policy_outlook`：政策展望 positive/neutral/negative（人工维护，季度更新）
- 估值数据为过渡方案（L2 成员股聚合代理）；后续升级 Tushare 积分可替换为 sw_daily/fund_portfolio 官方数据


```bash
python3 main.py portfolio                         # 评估今日持仓（分级操作表）
python3 main.py portfolio --date 2026-06-16       # 指定日期
python3 main.py portfolio --file my_pf.json       # 指定持仓文件
```

输出内容（改造后）：
- 每只持仓：现价/盈亏% + 出场逻辑类型 + R倍数 + 建议加仓股数
- **分级操作表**（技术仓）：take_1 / take_2 / soft_stop / hard_stop 四档价位，含仓位变化%和依据
- **基本面出场条件**（配置仓）：PE历史分位阈值 + 基本面恶化触发条件，无价格止损
- 委托参考价：按信号类型智能计算（见下方说明）
- 进场信号附加参考（与出场逻辑独立）

出场逻辑三层降级：
1. `position_type`+`style` 显式声明 → 对应策略的 `compute_exit_levels`
2. 策略认领（反向扫描信号最高分策略）
3. ATR 兜底（无认领时）

> **价格口径**：策略用 hfq 计算，展示换算回不复权（进场价用当前 hfq，不用成本推算，避免历史因子错位）。换算方式：用本地数据最新日期去 Tushare 拉同日不复权价算复权因子。

### 持仓管理工具

```bash
# 查看任意股票的出场框架（分级操作表）—— 不需要在持仓里
python3 main.py exit-levels 600519                        # 全部策略，用当前价
python3 main.py exit-levels 600519 --entry 1520           # 指定进场价
python3 main.py exit-levels 600519 --strategy ma_cross    # 只看趋势跟踪策略

# 仓位计算器（基于风险法）
python3 main.py position-size --entry 1680 --stop 1560
python3 main.py position-size --entry 30 --stop 27 --capital 3000000

# position-size 计算逻辑：
#   建议股数 = (total_capital × risk_per_trade) / (entry - stop) / 100 × 100
#   risk_per_trade 默认 1%（RISK_CONTROL.risk_per_trade），同时受单笔10%上限约束
```

### 委托参考价计算规则

scan 和 portfolio 均按以下规则给出买入委托参考价：

| 场景 | 委托价 | 说明 |
|---|---|---|
| 配置仓 WATCH | 昨收 × 0.998 | 分批建仓不追 |
| 涨停（当日涨幅≥9.8%） | 昨收 × 1.10 | 涨停排队价 |
| 强突破/趋势（score≥75） | 昨收 × 1.01 | 优先成交 |
| 均值回归（bollinger） | 昨收 × 0.997 | 限价等回调 |
| ETF 轮动 | 昨收 × 1.003 | 流动性好轻微溢价 |
| 普通信号 | 昨收 × 1.005 | 默认竞价 |

卖出委托参考价：昨收 × 0.995（统一）

### 数据状态

```bash
python3 main.py data-status                       # 各域数据新鲜度 + 后台更新进程实时进度
```

输出：各域最新日期/距今天数/状态（✓≤3天 / ⚠4~14天 / ✗>14天）  
若有后台 update/update-moneyflow 进程在跑，顶部实时显示其日志最后一行（含进度条）

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

### 每日（收盘后，建议 17:30）

```bash
scripts/update_daily.sh
# 执行：[1]market K线 → [2]meta(周一init/其余update) → [3]moneyflow
#         (个股/龙虎榜汇总+席位明细 top_inst/大宗 block_trade/两融/行业/大盘/北向)
#       → [4]events(约1h)
#       → [5]macro(含汇率/境外) → [6]update-hk(港股通) → [7]update-hk-valuation(约30m)
#       → [8]update-hk-moneyflow(约15m) → [9]update-hk-events(约15m)
#       → [10]factor(台阶二前空跑) → scan(stock，默认注释)
# 注：北向个股持股 hk_hold 为季度披露且按代码逐只，不在每日脚本，见季度脚本
# 注：单步失败不中断，末尾汇总，有失败时退出码 1；输出同时写入 logs/update_daily.log
#     脚本带 flock 并发锁（上一轮未结束时新执行直接退出）
#     港股 4 个步骤（K线/估值/资金流/分红）在 16:15 前统一跳过（避免盘中残缺 bar）
#     17:30 运行可拿到港股当日最终值；macro 的港股/境外当日 bar 次日刷新会自动覆盖修正
```

### 每周（周六 10:00）

```bash
scripts/update_weekly.sh
# 执行：meta 域 update_db → macro 域 update_db（含汇率/境外，日频数据兜底）
#       → 港股财务 update-hk-financials（约1h） → A股 PCF update-cn-pcf（约1-1.5h）
# 注：events 域在每日脚本中更新；单步失败不中断，末尾汇总；带 flock 并发锁
```

### 每季度（季报披露后，约1月/4月/8月/10月）

```bash
scripts/update_quarterly.sh
# 执行：[1]A股 fundamental 域 update_db（更新财务数据）
#       → [2]北向个股持股 update-hk-hold --from-portfolio --from-lhb 30（季度披露，约数百只）
# 注：港股财务在每周脚本中更新；单步失败不中断，末尾汇总；带 flock 并发锁
```

### 用 crontab 配置自动运行

```bash
crontab -e

# 每个交易日 17:30 运行每日更新（脚本内部自带日志与 flock 并发锁）
30 17 * * 1-5 /home/wz/alpha-quant/scripts/update_daily.sh

# 每周六 10:00 运行每周更新
0 10 * * 6 /home/wz/alpha-quant/scripts/update_weekly.sh

# 每季度首月1日 09:00 运行季度更新
0 9 1 1,4,8,10 /home/wz/alpha-quant/scripts/update_quarterly.sh
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

**K线下载卡死/进度停滞**
```bash
# 用 setsid 彻底脱离终端，避免被 job control 暂停
setsid venv/bin/python3 main.py init --force > logs/init_full.log 2>&1 < /dev/null &
tail -f logs/init_full.log
# 进度每50只打一次日志，watch 文件数监控：
watch -n 30 'ls data/market/cn/stock/*.parquet | wc -l'
```

**BaoStock 连接超时（降级模式）**
```bash
# 无 TUSHARE_TOKEN 时才走 baostock，配置 token 即可切回 tushare 主源
```

**AKShare 接口限流（429）**
```bash
# 正常现象，downloader 内置指数退避重试（最多3次）
```

**扫描耗时过长**
```bash
# 默认 20 线程，全市场约 83 秒
```

**Web 看板指令单重复显示**
```bash
# 临时处理：清空 data/orders/ 目录
```
