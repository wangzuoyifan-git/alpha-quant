# Alpha量化 — 策略层文档

> 策略基类接口、内置策略说明、开发指南、待开发清单。
> 最后更新：2026-06-17（新增：正交策略组合框架——少而精、正交、上层可选，作为策略层演进的设计约束）
>
> **项目基础信息见 `docs/project.md`**
> **策略信号需求定义（S1~S4 含信号本质、多维度合并、扫描过滤规则）见 `docs/requirements.md` 第三章 3.1 节**
> **正交策略组合框架（策略层顶层设计纲领）见本文档第〇章**

---

## 〇、正交策略组合框架（策略层设计纲领）

> 新增于 2026-06-17。本章是策略层的**顶层设计约束**，统领内置策略的取舍、优化方向和上层选择逻辑。
> 后续新增/优化策略必须先对照本章框架，确认其占据的正交维度与上层选择关系。

### 0.1 核心理念：少而精、正交、上层可选

策略层不追求数量，追求**正交覆盖**。设计原则：

1. **少而精**：每个仓位类型（配置仓/交易仓）只保留少数几个策略，每个策略捕捉一个**独立的 Alpha 来源**。
2. **正交性**：策略之间捕捉的收益来源互不重叠。同一标的被多个正交策略同时命中，代表"多个独立视角都看好"，才值得在信号合并层加成——若策略本身重叠（如两个都是趋势动量），同时命中是冗余而非共振。
3. **上层可选**：策略本身不做择时判断，**由 L1（regime）控开关、L2（行业特性）控偏好**来动态激活合适的策略子集。策略之间互不知道彼此存在。

**对立性是正交框架的关键**：交易仓里"趋势跟踪（追涨）"与"均值回归（抄底）"是天然对立的，同时全开会相互抵消。上层的核心价值就是根据 regime 只激活其中一类——这正是分层决策架构（L1/L2 约束 L3）存在的意义。

### 0.2 配置仓正交维度（L3-B，WATCH，中长期持有）

对应 Fama-French 经典因子，三个维度是学术验证的长期正交 Alpha 来源：

| 维度 | 捕捉的 Alpha | 适用行业特性 | 适用 regime | 当前策略 |
|---|---|---|---|---|
| **质量**（Quality） | 高 ROE、盈利稳定性溢价 | 消费/医药/白马 | 全 regime | `fundamental_value`（B1 GARP） |
| **价值**（Value） | 低估值均值回归溢价 | 银行/地产/周期低谷 | crisis/risk_off | `deep_value`（B2） |
| **成长**（Growth） | 盈利增长预期溢价 | 科技/新能源/成长赛道 | risk_on/balanced | `growth_pick`（B3） |

配置仓三策略已覆盖三个正交维度，方向正确，**保持 3 个不增加**，仅做针对性质量优化（见 0.4）。

### 0.3 交易仓正交维度（L3-A，BUY，短期波段）

| 维度 | 捕捉的 Alpha | 适用行业特性 | 适用 regime | 当前归属 |
|---|---|---|---|---|
| **趋势跟踪**（Trend） | 价格动量延续溢价 | 任何行业趋势期 | risk_on/balanced | `ma_cross`（弱，需升级） |
| **均值回归**（Mean Reversion） | 超跌反弹、技术超卖修复 | 高波动行业底部 | risk_off/volatile | `macd_rsi` |
| **突破**（Breakout） | 结构突破后的趋势加速 | 活跃赛道/新高策略 | risk_on | `vol_price_breakout`（需修复） |
| **ETF 轮动**（Trend-ETF） | ETF 相对动量轮动 | 宽基/行业 ETF | risk_on/balanced | `etf_momentum` |

**资金流（Smart Money）的定位调整**：当前 `money_flow` 是纯单因子选股，存在系统性缺陷（详见 0.4）。在正交框架下，资金流**不应作为独立选股策略**，而应降级为**趋势/突破策略的内置确认过滤层**（主力净流入确认 = 提高信号置信度），或作为辅助评分维度。原因：资金流缺乏方向判断（主力净流入可能是出货前对倒），单独使用 Alpha 极弱；与技术面结合才有价值。

### 0.4 当前策略 → 正交框架映射与优化方向

```
配置仓（3个，覆盖完整，仅优化质量）：
  B1 fundamental_value → Quality ✅  优化：加 PE 历史分位过滤（替代固定 PE≤30），防高估市买入
  B2 deep_value        → Value   ✅  优化：行业排除（金融/地产走独立逻辑）+ 股息率验证，防价值陷阱
  B3 growth_pick       → Growth  ✅  优化：加收入增长维度 + PEG≤阈值控估值，防高位接盘

交易仓（精简到 4 个，清晰正交）：
  趋势跟踪（个股）  → 升级 ma_cross：加 ADX≥20 趋势确认 + 资金流内置过滤（吸收 money_flow）
  均值回归          → 保留 macd_rsi：放宽 RSI 阈值 + 改 RSI 背离判断
  突破              → 修复 vol_price_breakout：加蓄力确认（突破前缩量）+ 明确 SELL 语义
  ETF 轮动          → 保留 etf_momentum：加绝对动量过滤（跑不赢无风险利率不发信号）
  money_flow        → 取消独立策略，能力并入趋势跟踪作为确认过滤层
```

**当前问题诊断**：
- `ma_cross` 与 `etf_momentum` 都是趋势动量，对个股 `etf_momentum` 不适用、`ma_cross` 又太弱，职责边界模糊 → 升级 `ma_cross` 成为个股趋势跟踪主力
- `money_flow` 纯单因子、评分按绝对流入额偏向大盘股、无技术面确认 → 降级为过滤层
- 交易仓缺一个干净有效的个股趋势跟踪 → 由升级后的 `ma_cross` 填补

### 0.5 上层选择逻辑（L1/L2 如何驱动策略激活）

策略层不做择时，激活由上层约束决定：

```
L1 regime       →   激活的交易仓策略（控开关）
─────────────────────────────────────────────
risk_on         →   趋势跟踪 + 突破（追涨进攻）
balanced        →   趋势跟踪（保守参数）
risk_off        →   均值回归（超跌抄底）
volatile        →   均值回归（高波动寻底）
crisis          →   交易仓全关，仅保留防御型配置仓

L2 行业特性     →   配置仓策略偏好（控偏好）
─────────────────────────────────────────────
高景气 + 低估    →   B1 Quality（time_horizon=long）
高成长赛道       →   B3 Growth（time_horizon=medium）
熊市防御         →   B2 Value + 高股息
```

实现载体：`select_strategies(decision_context)` 按 `regime` + `allowed_styles` + `allowed_time_horizons` 三维画像匹配（见 2.3 节）。**新增策略只要正确声明 `style` / `applicable_regimes` / `time_horizon` 画像，即可被上层自动纳入选择，无需改动 select_strategies 逻辑。**

### 0.6 业界对标（优化时的参考基准）

| 维度 | 业界最佳实践 | 当前差距 |
|---|---|---|
| 质量（B1） | Piotroski F-Score（9 项财务健康度） | 仅单指标阈值过滤 |
| 价值（B2） | EV/EBITDA + 连续 3 年盈利 + 股息 | PE/PB 组合，无行业区分 |
| 成长（B3） | CAN SLIM（盈利+收入+新高+机构+行业景气） | 仅财务比率，无 PEG/收入 |
| 趋势 | ADX 趋势强度过滤 + 资金流共振 | MA5/MA20 裸金叉 |
| 均值回归 | RSI 背离 > RSI 阈值穿越 | 简单阈值穿越 |
| 突破 | ATR 标准化 + N 日缩量蓄力确认 | 固定百分比 + 当日量比 |
| ETF 动量 | Dual Momentum（绝对+相对） | 仅相对动量 |

> 优化时不要求一次到位对齐业界，按"先补正交空白 → 再提单策略质量"的顺序推进。

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

    # ⑦ 适用资产类型 — 扫描引擎按此过滤，空列表=不限制
    applicable_asset_types: list[str] = []
    #   取值：stock / etf（如 ["etf"] 则只在 ETF 扫描时运行）

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
            "position_type": "config" if self.time_horizon in ("medium", "long") else "trading",
            "applicable_asset_types": self.applicable_asset_types,
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

| key | style | applicable_regimes | risk_profile | time_horizon | 版本 | 正交维度 |
|---|---|---|---|---|---|---|
| `ma_cross` | momentum | [balanced, risk_on] | balanced | short | v2.0 | 趋势跟踪（个股主力，ADX+资金流） |
| `macd_rsi` | reversal | [risk_off, volatile] | defensive | short | v2.0 | 均值回归（RSI背离+超卖） |
| `vol_price_breakout` | breakout | [risk_on] | aggressive | short | v2.0 | 突破（蓄力确认+ATR标准化） |
| `etf_momentum` | momentum | [risk_on, balanced] | balanced | short | v2.0 | ETF 轮动（绝对+相对动量，仅ETF） |
| `fundamental_value` | quality | [crisis,risk_off,balanced,risk_on,volatile] | defensive | medium | v3.0 | 质量（Quality，PE历史分位） |
| `deep_value` | value | [crisis,risk_off,balanced,risk_on,volatile] | defensive | long | v2.0 | 价值（Value，行业排除+股息） |
| `growth_pick` | growth | [crisis,risk_off,balanced,risk_on,volatile] | balanced | medium | v2.0 | 成长（Growth，PEG控估值） |
| ~~`money_flow`~~ | moneyflow | — | — | — | 降级 | 已降级为趋势策略内置过滤层，不注册 |

> **正交维度列**：标注每个策略在第〇章正交框架中占据的 Alpha 来源。`money_flow` 不构成独立正交维度，规划降级为趋势/突破策略的内置确认过滤层。

**L3-B（B1/B2/B3）applicable_regimes 设计**：配置仓候选不依赖择时，任何 regime 都应产出选股，
故全 5 种 regime 适用。区别由 L2 行业池 + allowed_styles 收窄，而非 regime。

### 2.1.1 L3-B 基本面策略设计（台阶二）

> **数据量纲（关键）**：`store_fundamental` 来自 BaoStock，财务比率均为**小数**（ROE 0.1057=10.57%），
> 列名为 BaoStock 风格。估值（pe_ttm/pb_mrq）来自 market 域 K线 df（scanner 已传入）。

**字段映射**（修正历史 bug：旧代码读 Tushare 列名 roew/netprofit_yoy/debt_assets_ratio 全不存在）：

| 指标 | 域 | 列名 | 量纲 |
|---|---|---|---|
| ROE | fundamental/profit | `roeAvg` | 小数 |
| 毛利率 | fundamental/profit | `gpMargin` | 小数 |
| 净利率 | fundamental/profit | `npMargin` | 小数 |
| 净利润同比 | fundamental/growth | `YOYNI` | 小数 |
| 净资产同比 | fundamental/growth | `YOYEquity` | 小数 |
| 资产负债率 | fundamental/balance | `liabilityToAsset` | 小数 |
| PE/PB | market（K线 df） | `pe_ttm` / `pb_mrq` | 倍数 |

**三个策略阈值（默认值，全部 self.params 可配置）**：

| 策略 | key | 默认阈值 | 输出 |
|---|---|---|---|
| B1 质量价值(GARP) | `fundamental_value` | min_roe=0.15, max_pe=30, max_pb=5, min_growth=0（排除亏损 PE≤0） | WATCH/config/medium |
| B2 深度价值 | `deep_value` | max_pb=1.5, max_pe=15(>0), max_debt=0.6, 净利为正 | WATCH/config/long |
| B3 成长筛选 | `growth_pick` | min_ni_growth=0.25, min_equity_growth=0.15, min_roe=0.10 | WATCH/config/medium |

三者均 `signal=WATCH`, `position_type=config`，经 scan_node 落盘 watch_pool，人工确认后进配置仓。

### 2.2 L3 策略分类（正交框架定位下）

系统定位"多周期配置+择时"，L3 策略按仓位类型分两大类，每类按正交维度组织（详见第〇章）：

**L3-A 技术策略**（全自动，交易仓，BUY，`position_type=trading`，`time_horizon=short`）

按正交维度组织，目标精简到 4 个：

| 正交维度 | 策略 key | 状态 |
|---|---|---|
| 趋势跟踪（个股） | `ma_cross`（v2：ADX≥20 + 资金流内置过滤） | ✅ 已升级 |
| 均值回归 | `macd_rsi`（v2：RSI 阈值放宽至40 + RSI背离判断） | ✅ 已优化 |
| 突破 | `vol_price_breakout`（v2：蓄力确认 + ATR标准化 + SELL语义明确） | ✅ 已修复 |
| ETF 轮动 | `etf_momentum`（v2：绝对动量过滤） | ✅ 已优化 |
| ~~资金流~~ | ~~`money_flow`~~ → 已降级，能力并入趋势跟踪过滤层 | ✅ 已降级 |

**L3-B 基本面策略**（半系统化，配置仓候选，WATCH，`position_type=config`，经 L2 行业池过滤后供人工确认）

按 Fama-French 三因子正交组织，保持 3 个不增加：

| 正交维度 | 策略 key | time_horizon | 状态 |
|---|---|---|---|
| 质量（Quality） | `fundamental_value`（B1 GARP，v3） | medium | ✅ 已加 PE 历史分位过滤 |
| 价值（Value） | `deep_value`（B2，v2） | long | ✅ 已加行业排除+股息+连续盈利 |
| 成长（Growth） | `growth_pick`（B3，v2） | medium | ✅ 已加净资产增长+PEG控估值 |

台阶二已完整实现（详见 2.1.1），正交化优化已落地（见第〇章 0.4）。

**L3-C 事件驱动策略**（待开发）
- C1 高股息/回购（medium，配置仓）/ C2 业绩超预期（short~medium，交易仓）
- 注：事件驱动是独立于上述正交维度的第三类 Alpha 来源（事件冲击），依赖 E2/E3 数据完善

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
| select_strategies | 部分从硬编码迁移到画像匹配 | ✅ 完整画像匹配（style+regime+time_horizon 三维），自动兼容新策略 |
| market_condition | 旧字段保留向后兼容 | 完全弃用，删除 |
| fundamental_value time_horizon | 填 short（当前是短线实现） | ✅ 重构为 B1 质量价值，medium，WATCH/config |
| L3-B 完整策略 | 只有雏形（fundamental_value） | ✅ B1/B2/B3 完整实现（台阶二） |

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
result = backtester.run(strategy, stock_codes=[], start="2024-01-02", end="2026-06-16",
                        asset_type="stock")  # asset_type="etf" 支持 ETF 回测
print(result.summary())
```

回测约定：
- `stock_codes` 为空时自动使用本地全市场数据
- `asset_type` 支持 "stock"（默认）/ "etf"
- 最多同时持仓10只（当前硬编码，待从 config 读取）
- 按 `position_size`（默认10%）分配资金
- 夏普比率已扣除无风险利率（2%）
- **基准对比**：`BacktestResult` 含 `benchmark_return`（沪深300同期收益）/ `alpha`（超额年化）/ `beta`，
  `summary()` 有基准数据时展示「基准收益/超额收益(alpha)/beta」

---

## 六、待开发清单

> 总纲：按第〇章正交策略组合框架推进，顺序为「先补正交空白 → 再提单策略质量」。

### 正交化改造（对应第〇章 0.4）✅ 已完成

**交易仓精简与升级：**
- [x] 升级 `ma_cross` → 个股趋势跟踪主力：ADX≥20 趋势强度过滤 + 资金流内置确认（v2.0）
- [x] 降级 `money_flow`：取消独立注册，能力并入趋势跟踪过滤层（类保留供回测兼容）
- [x] 修复 `vol_price_breakout`：突破前缩量蓄力确认 + ATR标准化 + SELL语义明确（scan_sell 控制）（v2.0）
- [x] 优化 `macd_rsi`：RSI阈值放宽至40 + RSI底背离判断 + 强制超卖/背离确认（v2.0）
- [x] 优化 `etf_momentum`：绝对动量过滤（跑不赢无风险利率不发WATCH信号）（v2.0）

**配置仓质量优化（保持 3 个，对标业界）：**
- [x] `fundamental_value`（B1，v3）：PE历史分位过滤（≤60分位）+ 固定阈值兜底
- [x] `deep_value`（B2，v2）：金融/地产行业排除 + 股息率验证 + 连续2季盈利
- [x] `growth_pick`（B3，v2）：净资产增长双验证 + PEG≤1.5控估值

**上层选择联动：**
- [x] `profile()` 补 `position_type` 字段返回（按 time_horizon 推导）
- [ ] `select_strategies()` 校验：精简/降级后确认画像匹配仍能正确激活正交子集（待 L2 联调）

### 工程支撑

- [x] 回测支持 ETF（`Backtester.run(asset_type="etf")`）
- [x] 回测基准对比（沪深300 alpha/beta/超额收益）
- [ ] 回测图表：权益曲线可视化
- [ ] backtest CLI 暴露 `--asset-type` 参数

### 远期（低优先级）
- [ ] L3-C 事件驱动策略（C1 高股息/回购、C2 业绩超预期），依赖 E2/E3 数据
- [ ] `signal` 字段改为枚举类型，避免拼写错误
- [ ] 跨资产轮动策略（股票 ETF + 债券 ETF）
