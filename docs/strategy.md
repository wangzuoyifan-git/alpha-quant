# Alpha量化 — 策略层文档

> 策略基类接口、内置策略说明、开发指南、待开发清单。
> 最后更新：2026-06-12（新增：6维策略能力画像 schema、多周期配置定位、Signal 仓位归属字段）
>
> **项目基础信息见 `docs/project.md`**
> **策略信号需求定义（S1~S4 含信号本质、多维度合并、扫描过滤规则）见 `docs/requirements.md` 第三章 3.1 节**

---

## 一、策略基类（strategies/base.py）

### Signal 数据结构

```python
@dataclass
class Signal:
    code: str
    name: str
    signal: str          # "BUY"（交易仓）/ "SELL" / "WATCH"（配置仓候选）
    price: float
    reason: str
    score: float = 0.0   # 0~100，越高越优先
    extra: Dict[str, Any] = field(default_factory=dict)
    # 多周期配置新增字段（阶段四台阶一）
    position_type: str = "trading"   # "trading"（交易仓）/ "config"（配置仓候选）
    time_horizon: str = "short"      # "short"(日~周) / "medium"(半年+) / "long"(1年+)
    # @deprecated: 用 applicable_regimes 替代（策略级元数据，不在 Signal 上用）
    market_condition: str = ""
```

**字段说明**：
- `signal="BUY"` + `position_type="trading"`：技术信号，自动进交易仓
- `signal="WATCH"` + `position_type="config"`：基本面候选，经 L2 行业池过滤后供人工确认进配置仓
- `time_horizon`：策略对本信号的持有期意图，下游仓位管理按此决定退出逻辑

### BaseStrategy 基类定义

```python
class BaseStrategy(ABC):
    # ── 基础属性（原有）──
    name: str = "未命名策略"
    description: str = ""              # 自然语言简述
    version: str = "1.0.0"

    # ══════ 激活维度（4个，有消费逻辑）══════

    # ① 适用 regime（多值）— 策略筛选的事实来源，替代旧 market_condition
    applicable_regimes: list[str] = ["balanced"]
    #   取值：crisis / risk_off / balanced / risk_on / volatile（对齐 L1 regime 5种）

    # ② 策略风格 — 供 L2→L3 约束匹配 + LLM 语境匹配 + 人看
    style: str = ""
    #   取值：momentum / value / quality / reversal / moneyflow / breakout

    # ③ 适用场景（自然语言）— 供 LLM 语境推理
    applicable_scenario: str = ""
    #   如："趋势启动初期量价配合时表现最佳；震荡市易假突破"

    # ④ 风险风格 — 声明性标签 + 一致性校验（不独立筛选）
    risk_profile: str = "balanced"
    #   取值：defensive / balanced / aggressive
    #   一致性约束：risk_profile 应与 applicable_regimes 自洽
    #   （如 aggressive 不应同时适用 crisis，否则校验报警）

    # ══════ 占位维度（2个，仅留位，本期不造消费逻辑）══════

    # ⑤ 时间周期 — 为中长期策略留位（当前策略全填 short）
    time_horizon: str = "short"
    #   取值：short(日内~周) / medium(半年+) / long(1年+)
    #   注：系统已定位"多周期配置+择时"，此字段为真实维度而非摆设

    # ⑥ 数据依赖 — 为未来鲁棒性（数据缺失屏蔽策略）留位
    data_dependency: list[str] = ["technical"]
    #   取值：technical / fundamental / moneyflow / macro

    # ── 旧字段：保留向后兼容 ──
    market_condition: str = "range"   # @deprecated 用 applicable_regimes 替代

    def profile(self) -> dict:
        """
        返回策略结构化画像，供 select_strategies / LLM / list_strategies 消费。
        上层约束流（L1→L2→L3）通过画像做策略空间筛选。
        """
        return {
            "name": self.name,
            "style": self.style,
            "applicable_regimes": self.applicable_regimes,
            "risk_profile": self.risk_profile,
            "time_horizon": self.time_horizon,
            "data_dependency": self.data_dependency,
            "applicable_scenario": self.applicable_scenario,
            "description": self.description,
        }

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

**6 维设计说明**：
- **applicable_regimes（主筛）**：多值，替代旧的单值 market_condition（表达力不足）
- **risk_profile（声明+校验，不独立筛选）**：解决 regime（5种）与 risk_mode（3种）双路径冲突，不参与实际筛选逻辑，只做"策略自描述 + 防错校验"
- **time_horizon（真维度）**：因系统定位多周期配置，此字段在台阶一先留位（全填 short），台阶二随中长期策略和仓位分层管理激活消费逻辑
- **占位字段**（time_horizon/data_dependency）：只定义+填默认+标注预留，本期不造消费逻辑

---

## 二、内置策略（strategies/builtin/）

注册表在 `strategies/builtin/__init__.py` 的 `BUILTIN_STRATEGIES` 字典中。

### 2.1 策略能力画像（6 维 schema）

> **策略标签草案**：applicable_regimes 是筛选事实来源，strategy_keys 由 `select_strategies()` 按画像匹配产出。
> 具体标签值待更多策略开发后随实践校准，不应拘泥于当前 6 个内置策略。

| key | style | applicable_regimes | risk_profile | time_horizon | 说明 |
|---|---|---|---|---|---|
| `ma_cross` | momentum | [balanced, risk_on] | balanced | short | 趋势启动期均线金叉 |
| `macd_rsi` | reversal | [risk_off, volatile] | defensive | short | 超跌反弹，熊市底部 |
| `vol_price_breakout` | breakout | [risk_on] | aggressive | short | 强势突破，牛市趋势 |
| `etf_momentum` | momentum | [risk_on, balanced] | balanced | short | ETF 动量轮动 |
| `fundamental_value` | value | [risk_off, balanced, crisis] | defensive | short* | 财务健康低估标的 |
| `money_flow` | moneyflow | [risk_on, balanced] | aggressive | short | 主力资金净流入 |

*fundamental_value 当前为日线短期实现；台阶二升级为中长期持有时改 medium/long

### 2.2 L3 策略分类（多周期配置定位下）

系统定位"多周期配置+择时"，L3 策略按用途分三类：

**L3-A 技术策略**（当前已有，全自动，交易仓）
- ma_cross / macd_rsi / vol_price_breakout / etf_momentum / money_flow
- 输出：`signal=BUY`, `position_type=trading`, `time_horizon=short`

**L3-B 基本面策略**（半系统化，配置仓候选，经 L2 行业池过滤后供人工确认）
- 子类：B1 质量价值（高ROE+合理PE/PB，GARP核心）/ B2 深度价值 / B3 成长筛选
- 输出：`signal=WATCH`, `position_type=config`, `time_horizon=medium/long`
- fundamental_value 是 B1 的雏形；完整 B1/B2/B3 策略待台阶二开发

**L3-C 事件驱动策略**（待开发）
- C1 高股息/回购（medium，配置仓）/ C2 业绩超预期（short~medium，交易仓）

### 2.3 与分层决策集成（约束流）

`select_strategies()` 从硬编码列表升级为**画像匹配**：

```python
# 旧（硬编码，已过时）
if risk_mode == "defensive":
    return ["fundamental_value", "etf_momentum"]

# 新（画像匹配，台阶一实现）
def select_strategies(decision_context: dict) -> list[str]:
    regime = decision_context["regime"]
    allowed_styles = decision_context.get("allowed_styles", [])
    allowed_horizons = decision_context.get("allowed_time_horizons", ["short"])
    # 按 applicable_regimes / style / time_horizon 三维筛选
    ...
```

L2→L3 约束传递（decision_context 新字段）：
- `allowed_styles`：L2 基于行业特性进一步收窄的策略风格空间
- `industry_horizon_map`：每个行业推荐的 time_horizon
- `allowed_time_horizons`：当前允许的配置周期列表

### 2.4 已知过渡状态（台阶一→台阶二待解决）

| 项目 | 台阶一现状 | 台阶二目标 |
|---|---|---|
| select_strategies | 部分从硬编码迁移到画像匹配 | 完整画像匹配，自动兼容新策略 |
| market_condition | 旧字段保留向后兼容 | 完全弃用，删除 |
| fundamental_value time_horizon | 填 short（当前是短线实现） | 升级为中长期时改 medium/long |
| L3-B 完整策略 | 只有雏形（fundamental_value） | 开发 quality/GARP/高股息 |

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
- [x] 策略元数据扩展：`market_condition` 字段（趋势/震荡/高波动），供 Agent 做策略选择 — 已实现（base.py + strategy_agent）
- [ ] 网格交易策略（低波动宽基 ETF）
- [ ] 跨资产轮动策略（股票 ETF + 债券 ETF）
