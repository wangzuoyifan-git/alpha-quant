# Alpha量化 — 新策略开发上下文文档

> 供新 Session 开发 ETF 策略及后续功能使用
> 更新时间：2026-05-26
>
> **项目基础信息（环境、Git、目录结构、CLI、数据库状态）见 `docs/project.md`**

---

## 二、当前数据库状态

```
data/market/cn/
  stock/   5207 只   294 万行   348 MB   字段含 PE/PB/换手率/isST
  index/    541 只    30 万行    25 MB
  etf/     1535 只    13 万行    17 MB   ⚠️ 见下方已知问题
  ipo_dates.json      8624 只上市日期索引

数据时间范围：2024-01-02 ~ 2026-05-26（每日更新）
```

### ETF 数据已知问题 ⚠️

**ETF 数据只有 2026 年的数据（约 92 行），不是预期的 2024 年起。**

原因：`downloader.py` 的 `_download_one` 对所有类型统一用 `adjustflag="1"`（后复权），baostock 对 ETF 后复权数据起始日期很晚。

**修复方案**：ETF 改用 `adjustflag="3"`（不复权，ETF 本身已是净值，不需要复权），然后重新下载 ETF 数据：

```python
# data/downloader.py _download_one 中按类型区分复权方式
adjustflag = "3" if asset_type == "etf" else "1"
```

修复后需要重新下载 ETF：
```bash
venv/bin/python3 main.py init --types etf --force
```

---

## 三、策略开发指南

### 3.1 策略基类

所有策略继承 `strategies/base.py` 的 `BaseStrategy`：

```python
from strategies.base import BaseStrategy, Signal
from typing import List
import pandas as pd

class MyStrategy(BaseStrategy):
    name = "策略名称"
    description = "策略描述"
    version = "1.0.0"

    def requires_bars(self) -> int:
        return 60  # 需要的最少 K 线数

    def generate_signals(self, df: pd.DataFrame,
                         code: str, name: str) -> List[Signal]:
        """
        df 列（股票）：date, open, high, low, close, preclose,
                       volume, amount, turn, trade_status,
                       pct_chg, pe_ttm, pb_mrq, ps_ttm, pcf_ttm, is_st
        df 列（ETF）：date, open, high, low, close, preclose,
                      volume, amount, pct_chg
        注意：df 的 date 列是普通列（不是 index），已 reset_index
        """
        signals = []
        # ... 策略逻辑 ...
        signals.append(Signal(
            code=code,
            name=name,
            signal="BUY",   # BUY / SELL / WATCH
            price=float(df.iloc[-1]["close"]),
            reason="触发原因描述",
            score=80.0,     # 0~100，越高越优先
        ))
        return signals
```

### 3.2 注册策略

在 `strategies/builtin/__init__.py` 注册：

```python
from .my_strategy import MyStrategy

BUILTIN_STRATEGIES = {
    "ma_cross":           MACrossStrategy,
    "macd_rsi":           MACDRSIStrategy,
    "vol_price_breakout": VolPriceBreakoutStrategy,
    "my_strategy":        MyStrategy,   # 新增
}
```

自定义策略放 `strategies/custom/`，不影响内置策略。

### 3.3 现有内置策略

| key | 类名 | BUY 条件 | SELL 条件 | requires_bars |
|---|---|---|---|---|
| `ma_cross` | MACrossStrategy | MA5 上穿 MA20 + 放量 | MA5 下穿 MA20 | max(slow×2, 60) |
| `macd_rsi` | MACDRSIStrategy | MACD 金叉 + RSI 超卖回升（双重必要条件） | MACD 死叉 | 60 |
| `vol_price_breakout` | VolPriceBreakoutStrategy | 突破近 N 日高点 + 放量 | 跌破近 N 日低点 | lookback+10 |

---

## 四、ETF 策略建议

### 4.1 动量轮动策略（优先实现）

**逻辑**：定期选近期涨幅最强的 Top K 只 ETF 持有，卖出不在 Top K 的持仓。

```python
class ETFMomentumStrategy(BaseStrategy):
    """
    参数：
      lookback:    动量计算周期（默认20日）
      top_k:       持有数量（默认5只）
      rebalance:   再平衡频率（默认每周，即5个交易日）
    """
    name = "ETF动量轮动"

    def generate_signals(self, df, code, name):
        lookback = self.params.get("lookback", 20)
        if len(df) < lookback + 1:
            return []
        # 计算动量（近 lookback 日涨幅）
        momentum = (df.iloc[-1]["close"] / df.iloc[-lookback]["close"] - 1) * 100
        # 返回动量分数，由扫描引擎汇总后选 Top K
        return [Signal(code=code, name=name, signal="WATCH",
                       price=float(df.iloc[-1]["close"]),
                       reason=f"{lookback}日动量: {momentum:.1f}%",
                       score=max(0, min(100, 50 + momentum * 2)))]
```

**注意**：动量轮动需要在扫描引擎层面做 Top K 筛选，单只策略只返回分数，由引擎汇总。

### 4.2 其他适合 ETF 的策略

| 策略 | 适用场景 | 关键参数 |
|---|---|---|
| **均值回归（RSI）** | 宽基 ETF（沪深300/中证500） | RSI 周期、超买超卖阈值 |
| **趋势跟踪（长均线）** | 所有 ETF | 60/120 日均线 |
| **网格交易** | 低波动宽基 ETF | 网格间距、上下边界 |
| **跨资产轮动** | 股票 ETF + 债券 ETF | 切换信号（如 PE 分位数） |

### 4.3 扫描 ETF 的方式

当前 `scan` 命令只扫描 A 股，需要扩展支持 ETF：

```python
# engine/scanner.py _scan_single 中 asset_type 目前硬编码为 "stock"
# 需要增加 ETF 扫描入口，或在 scan() 中增加 asset_type 参数
```

建议新增 `scan_etf` 命令或给 `scan` 加 `--types` 参数。

---

## 五、扫描引擎现状

```python
# 当前扫描流程
python3 main.py scan

# 扫描范围：本地 stock 库，4937 只（排除 ST/北交所/新股）
# 数据窗口：最近 90 天 K 线（max_bars × 1.5）
# 并发：20 线程，全部读本地 Parquet（毫秒级）
# 耗时：约 83 秒
# 输出：Top 20 信号（按评分降序，同股同方向已合并去重）
# 持久化：data/processed/signals_YYYY-MM-DD.json
# 指令单：data/orders/
```

**当前 SELL 信号极少的原因**：三个内置策略都是趋势跟踪型，SELL 条件（死叉/跌破低点）需要趋势明确转弱才触发，短期下跌不够。

---

## 六、回测引擎现状

```python
# 全市场回测
python3 main.py backtest --strategy macd_rsi

# 指定时间范围和股票池
python3 main.py backtest --strategy ma_cross \
    --start 2024-01-02 --end 2026-05-25 \
    --codes 600519 000001 300750

# 可选策略：ma_cross / macd_rsi / vol_price_breakout
```

回测引擎只支持 stock，ETF 回测需要扩展 `backtest/engine.py` 的 `asset_type` 参数。

---

## 七、待开发功能清单

### 高优先级
- [ ] **修复 ETF 数据**：改 `adjustflag="3"` 重新下载，获取 2024 年起完整数据
- [ ] **ETF 动量轮动策略**：`strategies/builtin/etf_momentum.py`
- [ ] **扫描支持 ETF**：`scan --types etf` 或新增 `scan_etf` 命令
- [ ] **回测支持 ETF**：`backtest --asset_type etf`

### 中优先级
- [ ] **fundamental 域**：BaoStock 季频财务数据（ROE/毛利率/净利润）
- [ ] **meta 域**：行业分类（申万/证监会），用于行业轮动策略
- [ ] **回测基准对比**：与沪深300对比超额收益
- [ ] **回测图表**：权益曲线可视化

### 低优先级
- [ ] **factor 域**：资金流向（AKShare）
- [ ] **港股/美股数据**：store 已支持 hk/us，需要对应 downloader
- [ ] **Web 看板优化**：指令单重复显示问题

---

## 八、关键文件速查

```
strategies/
  base.py                    # BaseStrategy + Signal 定义
  builtin/
    __init__.py              # 策略注册表（新策略在此注册）
    ma_cross.py              # 均线金叉策略（参考模板）
    macd_rsi.py              # MACD+RSI 策略
    vol_price_breakout.py    # 量价突破策略
  custom/                    # 自定义策略放这里

data/
  store.py                   # 存取接口：load/save/list_codes/stats
  downloader.py              # KlineDownloader：init_db/update_db
  fetcher.py                 # DataFetcher：网络数据获取

engine/scanner.py            # ScanEngine：全市场扫描
backtest/engine.py           # Backtester：历史回测
config/settings.py           # 全局配置（SCAN/BACKTEST/NOTIFIER等）
main.py                      # CLI 入口
tools/
  plot_kline.py              # K线图验证工具
  test_init_flow.py          # init 流程端到端测试
```

---

## 九、store.py 核心接口速查

```python
import data.store as store

# 读取 K 线（返回 DataFrame，index=date）
df = store.load("510300", asset_type="etf", market="cn")
df = store.load("600519", asset_type="stock", market="cn",
                start="2024-01-01", end="2026-05-26")

# 获取所有代码
etf_codes  = store.list_codes("etf",   market="cn")  # 1535 只
stock_codes = store.list_codes("stock", market="cn")  # 5207 只
index_codes = store.list_codes("index", market="cn")  #  541 只

# 上市日期
ipo = store.load_ipo_dates()
info = ipo.get("cn:510300", {})
# info = {"ipo_date": "2012-05-04", "market": "cn", "type": "etf", "name": "沪深300ETF"}

# 统计
s = store.stats()
# s["cn"]["etf"] = {"count": 1535, "rows": 133741, "size_mb": 16.7}
```

---

## 十、常用命令

```bash
cd ~/alpha量化

# 数据管理
venv/bin/python3 main.py init              # 全量初始化（断点续传）
venv/bin/python3 main.py init --types etf --force  # 强制重新下载 ETF
venv/bin/python3 main.py update            # 每日增量更新
venv/bin/python3 main.py dbstats           # 数据库统计

# 分析
venv/bin/python3 main.py scan              # 全市场扫描（A股）
venv/bin/python3 main.py backtest --strategy macd_rsi
venv/bin/python3 main.py orders            # 查看指令单

# 工具
venv/bin/python3 tools/plot_kline.py 600519 000001
venv/bin/python3 tools/test_init_flow.py   # init 流程验证

# 监控（tmux）
tmux attach -t alpha_init                  # 查看后台任务
```
