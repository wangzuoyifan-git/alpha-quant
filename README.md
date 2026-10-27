# Alpha量化 - A股日线级别量化分析系统

## 项目结构

```
alpha量化/
├── main.py                 # 主入口（CLI）
├── requirements.txt        # 依赖清单
├── .env.example            # 环境变量模板
├── config/
│   └── settings.py         # 全局配置
├── chan/                   # 缠论结构分析（包含/分型/笔/中枢/买卖点/周线确认）
├── agent_tools/            # Agent Tool Layer（含 chan_tools）
├── data/
│   ├── fetcher.py          # 多源数据获取（AKShare/BaoStock/Tushare）
│   ├── cache/              # 本地缓存
│   ├── raw/                # 原始数据
│   └── processed/          # 处理后数据 & 信号存档
├── strategies/
│   ├── base.py             # 策略基类 & Signal 数据结构
│   ├── builtin/
│   │   ├── ma_cross.py          # 均线金叉 / 趋势跟踪
│   │   ├── macd_rsi.py          # MACD+RSI 均值回归
│   │   ├── vol_price_breakout.py # 量价突破
│   │   ├── etf_momentum.py      # ETF 动量轮动
│   │   ├── chan_theory.py       # 缠论三买/三卖
│   │   ├── fundamental_value.py # 质量价值（B1）
│   │   ├── deep_value.py        # 深度价值（B2）
│   │   └── growth_pick.py       # 成长筛选（B3）
│   └── custom/             # 放置自定义策略（继承 BaseStrategy）
├── engine/
│   └── scanner.py          # 全市场并发扫描引擎
├── backtest/
│   └── engine.py           # 历史回测引擎
├── signals/
│   └── manager.py          # 信号持久化管理
├── notifier/
│   └── push.py             # 多渠道推送（企业微信/邮件/Telegram）
├── trader/
│   └── assistant.py        # 半自动交易辅助（指令单管理）
├── web/                    # Web 看板静态资源
├── logs/                   # 运行日志
└── data/orders/            # 交易指令单存档
```

## 快速开始

### 1. 安装依赖

```bash
cd alpha量化
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写 Token、Webhook、邮件等配置
```

### 3. 常用命令

```bash
# 全市场扫描（今日）
python main.py scan

# 指定日期扫描
python main.py scan --date 2024-11-01

# 策略回测（默认 MACD+RSI）
python main.py backtest

# 指定策略和时间段回测
python main.py backtest --strategy ma_cross --start 2022-01-01 --end 2024-01-01

# 缠论结构分析（日线 + 周线确认）
python main.py chan --code 000001
python main.py chan --code 000001 --level weekly --json
python main.py scan --strategy chan_theory --codes 000001
python main.py backtest --strategy chan_theory --codes 000004 --start 2024-01-01

# 可选策略: ma_cross | macd_rsi | vol_price_breakout | etf_momentum |
#           chan_theory | fundamental_value | deep_value | growth_pick

# 查看待确认指令单
python main.py orders

# 确认 / 取消指令单
python main.py confirm <order_id>
python main.py cancel  <order_id>

# 启动 Web 看板（默认 http://localhost:8888）
python main.py web

# 启动定时调度（收盘后自动扫描）
python main.py schedule
```

## 自定义策略

在 `strategies/custom/` 下新建 Python 文件，继承 `BaseStrategy`：

```python
from strategies.base import BaseStrategy, Signal
from typing import List
import pandas as pd

class MyStrategy(BaseStrategy):
    name = "我的策略"
    description = "自定义策略描述"

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        # df 包含列: date, open, high, low, close, volume, amount, pct_chg
        last = df.iloc[-1]
        signals = []
        # 写你的选股逻辑...
        return signals
```

然后在 `main.py` 中注册：

```python
from strategies.custom.my_strategy import MyStrategy
engine.register_strategy("my", MyStrategy())
```

## 内置策略说明

| 策略 Key | 名称 | 核心逻辑 |
|---|---|---|
| `ma_cross` | 趋势跟踪 | MA5 上穿 MA20 + ADX 趋势确认 + 资金流过滤 |
| `macd_rsi` | 均值回归 | MACD + RSI 超卖/背离确认 |
| `vol_price_breakout` | 量价突破 | 突破近 N 日高点 + 缩量蓄力 + 放量确认 |
| `etf_momentum` | ETF 轮动 | 相对动量 + 绝对动量过滤（仅 ETF） |
| `chan_theory` | 缠论三买三卖 | 日线中枢三买/三卖 + 周线方向确认 + 中枢出场 |
| `fundamental_value` | 质量价值 | ROE/毛利率/PE 历史分位（配置仓 WATCH） |
| `deep_value` | 深度价值 | 低 PB/PE + 股息 + 连续盈利（配置仓 WATCH） |
| `growth_pick` | 成长筛选 | 净利润/净资产增长 + PEG 控估值（配置仓 WATCH） |

## 注意事项

- 本系统仅供学习研究，**不构成投资建议**
- 半自动交易模块生成指令单后需**人工确认**才执行，不会自动下单
- 建议先用历史回测验证策略后再上线
- 数据源以 AKShare 为主，网络异常时自动切换 BaoStock
