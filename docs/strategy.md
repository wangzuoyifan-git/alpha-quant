# Alpha量化 — 策略层文档

> 策略基类接口、内置策略说明、开发指南、待开发清单。
> 最后更新：2026-05-29
>
> **项目基础信息见 `docs/project.md`**
> **策略信号需求定义（S1~S4 含信号本质、多维度合并、扫描过滤规则）见 `docs/requirements.md` 第三章 3.1 节**

---

## 一、策略基类（strategies/base.py）

```python
from dataclasses import dataclass, field
from typing import List, Dict, Any
from abc import ABC, abstractmethod
import pandas as pd

@dataclass
class Signal:
    code: str
    name: str
    signal: str        # "BUY" / "SELL" / "WATCH"（字符串约定，无枚举约束）
    price: float
    reason: str
    score: float = 0.0  # 0~100，越高越优先
    extra: Dict[str, Any] = field(default_factory=dict)

class BaseStrategy(ABC):
    name: str           # 策略名称（类属性）
    description: str    # 策略描述
    version: str        # 版本号

    def __init__(self, params: dict = None):
        self.params = params or {}

    @abstractmethod
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
        ...

    def requires_bars(self) -> int:
        """需要的最少 K 线数，默认 60"""
        return 60
```

---

## 二、内置策略（strategies/builtin/）

注册表在 `strategies/builtin/__init__.py` 的 `BUILTIN_STRATEGIES` 字典中。

| key | 类 | BUY 条件 | SELL 条件 | requires_bars |
|---|---|---|---|---|
| `ma_cross` | `MACrossStrategy` | MA5 上穿 MA20 + 量能放大 | MA5 下穿 MA20 | `max(slow*2, 60)` |
| `macd_rsi` | `MACDRSIStrategy` | MACD 金叉 **且** RSI 超卖回升（双重必要条件） | MACD 死叉 | 60 |
| `vol_price_breakout` | `VolPriceBreakoutStrategy` | 突破近N日高点 + 放量 | 跌破近N日低点 | `lookback+10` |
| `etf_momentum` | `ETFMomentumStrategy` | 动量为正 → WATCH（Top K 候选） | 动量为负 → SELL | `max(lookback+5, ma_trend+5)` |

### 已知问题

| 策略 | 问题 | 优先级 |
|---|---|---|
| `vol_price_breakout` | SELL 信号逻辑存在缺陷，回测中持仓可能不平仓 | 中 |
| `macd_rsi` | RSI 超卖回升条件在某些边界情况下不生效 | 中 |
| `ma_cross` | reason 字符串格式化已修复（f-string） | 已修复 |

---

## 三、策略开发指南

### 3.1 新建策略

```python
# strategies/builtin/my_strategy.py 或 strategies/custom/my_strategy.py
from strategies.base import BaseStrategy, Signal
from typing import List
import pandas as pd

class MyStrategy(BaseStrategy):
    name = "策略名称"
    description = "策略描述"
    version = "1.0.0"

    def requires_bars(self) -> int:
        return 60

    def generate_signals(self, df: pd.DataFrame,
                         code: str, name: str) -> List[Signal]:
        if len(df) < self.requires_bars():
            return []

        signals = []
        # ... 策略逻辑 ...
        signals.append(Signal(
            code=code,
            name=name,
            signal="BUY",       # BUY / SELL / WATCH
            price=float(df.iloc[-1]["close"]),
            reason="触发原因描述",
            score=80.0,         # 0~100
        ))
        return signals
```

### 3.2 注册策略

在 `strategies/builtin/__init__.py` 注册内置策略：

```python
from .my_strategy import MyStrategy

BUILTIN_STRATEGIES = {
    "ma_cross":           MACrossStrategy,
    "macd_rsi":           MACDRSIStrategy,
    "vol_price_breakout": VolPriceBreakoutStrategy,
    "etf_momentum":       ETFMomentumStrategy,
    "my_strategy":        MyStrategy,   # 新增
}
```

自定义策略放 `strategies/custom/`，不影响内置策略，需在 `main.py` 中手动加载。

### 3.3 关键约定

- `df` 的 `date` 列是普通列（不是 index），已 `reset_index()`
- `score` 范围 0~100，扫描引擎按 score 降序截取 Top N
- 同标的同方向信号会被扫描引擎合并（取最高分，原因拼接）
- ETF 策略的 `df` 没有 `pe_ttm / pb_mrq / turn / is_st` 等字段，需做字段存在性检查
- `signal` 字段目前无枚举约束，拼写错误不会报错，需自行保证

### 3.4 参数化策略

通过 `params` 字典传入参数，支持在扫描时动态调整：

```python
# 使用默认参数
strategy = MACrossStrategy()

# 使用自定义参数
strategy = MACrossStrategy(params={"fast": 10, "slow": 30})

# 在策略内部读取
fast = self.params.get("fast", 5)   # 默认值 5
slow = self.params.get("slow", 20)  # 默认值 20
```

---

## 四、扫描引擎与策略的交互

```python
# engine/scanner.py
engine = ScanEngine(fetcher, SCAN)
engine.load_strategies(BUILTIN_STRATEGIES)          # 加载所有内置策略
engine.register_strategy("my_key", my_strategy)    # 注册单个策略实例

signals = engine.scan(date=None, asset_types=["stock", "etf"])
```

扫描流程：
1. 按 `max(requires_bars) * 1.5` 计算数据窗口
2. `ThreadPoolExecutor(max_workers=20)` 并发执行每只标的
3. stock 优先读本地 Parquet，本地不足30行才回退网络；ETF 只读本地
4. 同标的同方向信号合并，按 score 降序截取 `top_n`（默认20）

---

## 五、回测引擎与策略的交互

```python
# backtest/engine.py
backtester = Backtester(fetcher, BACKTEST)
result = backtester.run(strategy, stock_codes=[], start="2024-01-02", end="2026-05-25")
print(result.summary())
```

回测约定：
- `stock_codes` 为空时自动使用本地全市场数据
- 最多同时持仓10只（当前硬编码，待从 config 读取）
- 按 `position_size`（默认10%）分配资金
- 夏普比率当前未扣除无风险利率

---

## 六、待开发清单

### 高优先级
- [ ] 修复 `vol_price_breakout` SELL 信号逻辑
- [ ] 回测支持 ETF（`backtest --asset_type etf`）
- [ ] 回测基准对比（与沪深300对比超额收益）

### 中优先级
- [ ] 行业轮动策略（依赖 meta 域申万行业数据）
- [ ] 均值回归策略（RSI 超买超卖，适合宽基 ETF）
- [ ] 趋势跟踪策略（60/120日均线，适合所有 ETF）
- [ ] 回测图表：权益曲线可视化

### 低优先级
- [ ] `signal` 字段改为枚举类型，避免拼写错误
- [ ] 策略元数据扩展：描述适用市场环境（趋势/震荡/高波动），供 Agent 做策略选择
- [ ] 网格交易策略（低波动宽基 ETF）
- [ ] 跨资产轮动策略（股票 ETF + 债券 ETF）
