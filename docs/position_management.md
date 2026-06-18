# Alpha量化 — 持仓管理设计文档

> 创建：2026-06-18
> 状态：设计稿（待实现）
> 关联：`docs/strategy.md`（正交框架）/ `docs/project.md`（架构总览）/ `docs/requirements.md`（RC1~RC8）

---

## 一、设计动机与边界

### 1.1 当前问题

当前 L3 策略只做"进场信号"（`generate_signals`），没有"持仓管理"逻辑：

- `trader/assistant.py` 用 `成本价 × (1 ± 固定比例)` 计算止损止盈，与策略逻辑完全脱节
- 不同策略应有不同的出场逻辑：突破策略要紧止损，趋势策略要让利润奔跑，配置仓不该用价格止损
- `portfolio` 命令展示的止损止盈与实际策略逻辑不匹配
- 回测引擎的出场逻辑（固定百分比）与实际操作逻辑不一致，回测结果失去参考意义

### 1.2 核心设计原则

1. **进场与出场解耦**：`generate_signals` 负责"该不该买"，`compute_exit_levels` 负责"买了在哪出"，两者独立
2. **策略自描述出场逻辑**：每个策略知道自己该怎么出场，不由外部风控层强加
3. **风控层降级为兜底**：策略给了出场框架，风控用策略的；策略没给，风控才用固定百分比兜底
4. **回测等价于实际**：回测引擎消费与 portfolio 命令同一套出场逻辑，绩效数字才有意义
5. **交易仓/配置仓严格分路径**：`position_type` 是出场逻辑的第一个分叉点，不混用

### 1.3 设计范围

**本文档覆盖：**
- `compute_exit_levels` 方法规范（BaseStrategy 扩展）
- `ExitLevels` 数据结构（含分级操作表 `scale_levels`）
- `position_manager` 模块（仓位管理计算器）
- `portfolio.json` 格式扩展（`style` / `position_type` / `total_capital`）
- 回测引擎改造要点
- 配置参数（`RISK_CONTROL` 扩展）

**本文档不覆盖：**
- 自动下单执行（Phase 3）
- 分钟线止损（数据未接入）
- Volume Profile（需分钟线数据）

---

## 二、核心数据结构

### 2.1 ExitLevels（出场价位框架）

```python
@dataclass
class ScaleLevel:
    """分级操作表的单条记录"""
    price: float            # 触发价位（移动止损时为 None，用 trail_* 逻辑动态计算）
    level_type: str         # "hard_stop" / "soft_stop" / "take_1" / "take_2" / "trail"
    action: str             # "stop_loss" / "reduce" / "take_profit" / "trail_stop"
    size_pct: float         # 仓位变化比例，负数=减仓（-1.0=清仓，-0.5=减半，-0.33=减1/3）
    reason: str             # 触发依据（供展示）
    # OCA 语义：触发后对立方向的档位自动失效
    cancels_group: str      # "stop" 或 "profit"；触发时取消同组其他档位

@dataclass
class ExitLevels:
    """策略给出的出场价位框架，塞入 Signal.extra["exit_levels"]"""
    # 必填（兜底，任何策略都必须给）
    stop_loss: float         # 硬止损价（hard_stop 对应的价格，清仓线）
    take_profit: float       # 主要止盈目标价（take_1 对应的价格）
    atr: float               # 当日 ATR(14)，供 position_manager 计算仓位股数

    # 可选（策略给了更好，没给由 position_manager 用 ATR 推算）
    support: float = 0.0     # 近支撑位（soft_stop 依据）
    resistance: float = 0.0  # 近压力位（take_1 依据）
    exit_logic: str = "atr"  # 退出逻辑类型标签，见 2.2 节

    # 分级操作表（策略可选填，不填由 position_manager 按标准模板推算）
    scale_levels: list = field(default_factory=list)  # List[ScaleLevel]

    # 基本面出场条件（配置仓专用，技术仓留空）
    pe_exit_pct: float = 0.0    # PE历史分位超过此值开始减仓（如 0.75）
    fundamental_exit: str = ""   # 基本面出场描述（供展示）
```

### 2.2 exit_logic 标签枚举

| 标签 | 含义 | 适用策略 |
|---|---|---|
| `atr` | ATR 动态止损，固定 R 倍数止盈 | 所有策略的兜底 |
| `trail_supertrend` | Supertrend 追踪，不破不出 | `ma_cross`（趋势跟踪） |
| `trail_ma20` | 跌破 MA20 出场 | `ma_cross`（宽止损变体） |
| `swing_low` | 近期摆动低点止损 | `vol_price_breakout`（突破） |
| `bollinger_lower` | 跌破布林带下轨止损 | `macd_rsi`（均值回归） |
| `fundamental` | PE分位/ROE变化触发，无价格止损 | `fundamental_value` / `deep_value` / `growth_pick` |
| `momentum_relative` | 相对动量排名跌出 Top N | `etf_momentum` |

### 2.3 Signal.extra 扩展约定

`generate_signals` 调用 `compute_exit_levels` 后，将结果序列化写入 `Signal.extra`：

```python
Signal(
    ...,
    extra={
        "exit_levels": {          # ExitLevels 序列化
            "stop_loss": 385.0,
            "take_profit": 440.0,
            "atr": 12.5,
            "support": 388.0,
            "resistance": 435.0,
            "exit_logic": "trail_supertrend",
            "scale_levels": [
                {"price": 440.0, "level_type": "take_1",    "action": "take_profit",
                 "size_pct": -0.33, "reason": "前高压力位", "cancels_group": ""},
                {"price": 460.0, "level_type": "take_2",    "action": "take_profit",
                 "size_pct": -0.50, "reason": "Supertrend上轨", "cancels_group": ""},
                {"price": 388.0, "level_type": "soft_stop", "action": "reduce",
                 "size_pct": -0.50, "reason": "跌破近支撑", "cancels_group": "stop"},
                {"price": 380.0, "level_type": "hard_stop", "action": "stop_loss",
                 "size_pct": -1.00, "reason": "ATR止损位",  "cancels_group": "stop"},
            ],
        },
        "r_ratio": 2.4,           # 预期 R 倍数 = (take_profit - price) / (price - stop_loss)
        "industry": "食品饮料",
    }
)
```

---

## 三、BaseStrategy 扩展：compute_exit_levels

### 3.1 方法签名

```python
class BaseStrategy(ABC):

    def compute_exit_levels(
        self,
        df: pd.DataFrame,
        entry_price: float,
        fundamental_data: dict = None,  # 配置仓策略使用，由调用方传入已读的基本面数据
    ) -> "ExitLevels":
        """
        计算持仓出场价位框架。默认实现：ATR 动态止损 + 固定 R=2 止盈。
        各策略可重写以提供更精确的出场逻辑。

        参数：
            df              : 同 generate_signals 的 df（已含技术指标）
            entry_price     : 进场价（不复权口径），用于计算 R 倍数
            fundamental_data: 配置仓策略可用，键值对（已由调用方从 store 读取）
        返回：
            ExitLevels 实例（stop_loss / take_profit / atr 三个字段必须有值）
        """
        return self._default_atr_exit(df, entry_price)

    def _default_atr_exit(self, df: pd.DataFrame, entry_price: float) -> "ExitLevels":
        """ATR 动态止损兜底实现（所有策略共用）"""
        from strategies.position_manager import calc_atr, calc_swing_low
        atr = calc_atr(df)
        atr_multiplier = self.params.get("atr_stop_multiplier", 2.5)
        stop_loss = round(entry_price - atr * atr_multiplier, 2)
        take_profit = round(entry_price + atr * atr_multiplier * 2, 2)  # R=2
        support = calc_swing_low(df, n=20)
        resistance = calc_swing_high(df, n=20)
        return ExitLevels(
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr,
            support=support,
            resistance=resistance,
            exit_logic="atr",
            scale_levels=_build_standard_scale_levels(stop_loss, support, take_profit, resistance),
        )
```

### 3.2 各策略的 compute_exit_levels 实现规范

| 策略 | exit_logic | stop_loss 依据 | take_profit 依据 | 配置仓特殊逻辑 |
|---|---|---|---|---|
| `ma_cross` | `trail_supertrend` | Supertrend 下轨（当日） | Supertrend 上轨 / 前期摆动高点 | 无 |
| `macd_rsi` | `bollinger_lower` | 布林带下轨 | 布林带中轨 / 前期摆动高点 | 无 |
| `vol_price_breakout` | `swing_low` | 突破点下方 1×ATR（假突破快走） | 目标 = 箱体高度上移 | 无 |
| `etf_momentum` | `momentum_relative` | ATR 兜底（ETF波动小，用 2×ATR） | 相对动量排名跌出 Top N 时出 | 无 |
| `fundamental_value` | `fundamental` | 无价格止损；基本面恶化触发 | PE历史分位 > 75% 开始减仓 | pe_exit_pct=0.75 |
| `deep_value` | `fundamental` | 无价格止损；连续亏损触发 | PE历史分位 > 80% / PB > 2.0 | pe_exit_pct=0.80 |
| `growth_pick` | `fundamental` | 无价格止损；净利增速转负触发 | PEG > 2.0 或 PE历史分位 > 75% | pe_exit_pct=0.75 |

**配置仓硬性约束**：`exit_logic="fundamental"` 时，`stop_loss` 字段填 `0.0`（表示无价格止损），`position_manager` 遇到 `stop_loss=0.0` 时跳过价格止损检查，只检查基本面出场条件。

---

## 四、position_manager 模块

### 4.1 职责

`strategies/position_manager.py` 是独立计算模块（无状态），职责：

1. 提供 `calc_atr` / `calc_swing_low` / `calc_swing_high` / `calc_supertrend` 等底层价位计算函数（被各策略的 `compute_exit_levels` 调用）
2. `build_operation_table(holding, exit_levels, capital, risk_per_trade)` → 输出分级操作表（供 portfolio 命令展示）
3. `calc_position_size(entry_price, stop_loss, capital, risk_per_trade)` → 基于风险的建议股数
4. `check_exit_triggered(holding, current_price, exit_levels)` → 检查当前价是否触发任一档位（供回测引擎逐根K线调用）

### 4.2 基于风险的仓位计算

```
建议股数 = floor( (total_capital × RISK_PER_TRADE) / (entry_price - stop_loss) / 100 ) × 100

其中：
  total_capital    从 portfolio.json["total_capital"] 读取，不填时用 config 默认值（2,000,000）
  RISK_PER_TRADE   从 RISK_CONTROL["risk_per_trade"] 读取，默认 0.01（即 1%）
  entry_price      进场价（不复权）
  stop_loss        策略给的硬止损价（ExitLevels.stop_loss）

注意：配置仓（stop_loss=0.0）无法用此公式，退化为固定比例（position_size × total_capital）
```

R 倍数（开仓质量评估）：
```
r_ratio = (take_profit - entry_price) / (entry_price - stop_loss)
建议：r_ratio < 1.5 的信号在 portfolio 展示时标注警告，回测引擎可选择过滤
```

### 4.3 标准分级操作表模板

当策略未提供 `scale_levels` 时，`position_manager` 按以下模板推算：

```
技术仓标准模板（4档）：
  take_1    : resistance 或 entry + 2×ATR   → 减仓 -33%（cancels_group="")
  take_2    : entry + 3×ATR                 → 减仓 -50%（cancels_group="")
  soft_stop : support 或 entry - 1×ATR      → 减仓 -50%（cancels_group="stop"）
  hard_stop : stop_loss（entry - 2.5×ATR）  → 清仓 -100%（cancels_group="stop"）

配置仓标准模板（基本面触发，无价格止损）：
  take_1    : PE历史分位 > pe_exit_pct       → 减仓 -33%
  take_2    : PE历史分位 > pe_exit_pct+0.15  → 减仓 -50%
  fundamental_exit : 基本面恶化条件          → 清仓 -100%
```

OCA 语义：`cancels_group="stop"` 的档位被触发时，另一个 `cancels_group="stop"` 的档位自动标记为 `cancelled`。止盈档位触发时不互相取消（允许分批止盈）。

### 4.4 移动止损更新

回测引擎每日收盘后对 `exit_logic` 为 `trail_*` 的持仓调用：

```python
def update_trail_stop(holding, df, exit_levels) -> ExitLevels:
    """
    更新移动止损位（止损只能上移，不能下移）。
    trail_supertrend: 重新计算 Supertrend 下轨，取 max(old_stop, new_supertrend_lower)
    trail_ma20:       取 max(old_stop, 当日 MA20)
    """
```

---

## 五、portfolio.json 格式扩展

### 5.1 新增字段

```json
{
  "total_capital": 2000000,
  "holdings": [
    {
      "code": "601899",
      "name": "紫金矿业",
      "cost": 28.55,
      "shares": 5000,
      "position_type": "config",
      "style": "value"
    },
    {
      "code": "600519",
      "name": "贵州茅台",
      "cost": 1520.0,
      "shares": 100,
      "position_type": "trading",
      "style": "momentum"
    }
  ]
}
```

**字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `total_capital` | float | 否 | 账户总资产（含持仓+现金），不填时用 `RISK_CONTROL.default_capital`（2,000,000） |
| `position_type` | string | 否 | `trading`（技术仓）/ `config`（配置仓）；不填时按 `style` 推断（value/quality/growth → config，其余 → trading） |
| `style` | string | 否 | 持仓性质，用于选择出场逻辑路径；不填时由 portfolio 命令反向运行策略自动推断 |

### 5.2 三层降级：出场逻辑选择

对于无进场标注的手动持仓，按以下优先级选出场逻辑：

```
优先级 1（显式声明）：
  持仓有 position_type + style → 直接按 style 映射对应策略的 compute_exit_levels

优先级 2（自动推断）：
  portfolio 命令反向运行所有 L3 策略，找"认领"该持仓的策略
  认领条件：该策略对该持仓 generate_signals 产出 BUY 或 WATCH 信号
  多个策略认领时：按 position_type 归属（config > trading）+ score 最高者优先

优先级 3（兜底）：
  style 未声明且无策略认领 → position_type=trading 时用 ATR 兜底，position_type=config 时用 PE历史分位兜底
```

---

## 六、回测引擎改造

### 6.1 改造要点

回测引擎（`backtest/engine.py`）当前出场逻辑：
```python
# 当前（错误）：固定百分比
if price <= entry_price * (1 - stop_loss_ratio): sell()
if price >= entry_price * (1 + take_profit_ratio): sell()
```

改造目标：
```python
# 目标：消费策略的 ExitLevels
exit_levels = strategy.compute_exit_levels(df_at_entry, entry_price)
# 每日检查
triggered = position_manager.check_exit_triggered(holding, today_close, exit_levels)
if triggered:
    sell(triggered.size_pct)  # 分批出场
# 移动止损更新
if exit_levels.exit_logic.startswith("trail_"):
    exit_levels = position_manager.update_trail_stop(holding, df_today, exit_levels)
```

### 6.2 回测兼容性约束

- 改造后旧的 `stop_loss_ratio` / `take_profit_ratio` 参数降级为兜底（策略未给 ExitLevels 时使用）
- ETF 回测（`etf_momentum`）的出场逻辑为 `momentum_relative`，回测引擎需支持"相对排名跌出触发出场"

### 6.3 分批出场对回测的影响

分批出场会产生多笔卖出记录。回测引擎需按 `size_pct` 计算实际出场股数：
```
出场股数 = floor(当前持仓股数 × abs(size_pct) / 100) × 100
```
若剩余股数 < 100，则全部出场（避免碎股）。

---

## 七、RISK_CONTROL 配置扩展

```python
RISK_CONTROL = {
    # ... 原有 RC1~RC8 字段不变 ...

    # ── 持仓管理新增参数 ────────────────────────────────────────
    # 基于风险的仓位管理
    "risk_per_trade": 0.01,          # 单笔最大亏损占总资金比例（1%）
    "default_capital": 2_000_000,    # portfolio.json 未填 total_capital 时的默认值

    # R 倍数过滤（扫描时过滤低质量信号）
    "min_r_ratio": 1.5,              # R < 1.5 的信号在 portfolio 展示时标注警告

    # 分批参数
    "scale_out": {
        "take_1_pct": 0.33,          # 第一止盈目标减仓比例
        "take_2_pct": 0.50,          # 第二止盈目标减仓比例
        "soft_stop_pct": 0.50,       # 近支撑止损减仓比例
    },

    # ATR 止损倍数（各策略默认值，策略 params 可覆盖）
    "atr_stop_multiplier": 2.5,      # 止损距 = ATR × 此值
    "atr_profit_multiplier": 5.0,    # 止盈距 = ATR × 此值（≈ R=2）
}
```

---

## 八、portfolio 命令改造

### 8.1 逻辑修正

当前问题：用"有没有新进场信号"判断"持仓要不要出场"，逻辑混淆。

改造后分离：
- **进场信号**（`generate_signals`）：告知"当前有无新信号"，作为附加参考
- **出场评估**（`compute_exit_levels`）：基于当前持仓状态，独立给出出场框架

### 8.2 输出格式：分级操作表

每只持仓展示（替代当前单一止损止盈价）：

```
600519  贵州茅台
  现价: ¥1,685   成本: ¥1,520   持仓盈亏: +10.9%  (100股)
  出场逻辑: trail_supertrend  |  R倍数: 2.3  |  建议仓位: 按当前持仓，无需调整

  分级操作表：
  ┌─────────────┬────────────┬──────────┬──────────┬─────────────────────────┐
  │ 价位        │ 类型       │ 操作     │ 仓位变化 │ 依据                    │
  ├─────────────┼────────────┼──────────┼──────────┼─────────────────────────┤
  │ ¥1,820      │ 止盈1      │ 减仓     │ -33%     │ 前期摆动高点             │
  │ ¥1,950      │ 止盈2      │ 减仓     │ -50%     │ Supertrend上轨          │
  │ ¥1,620      │ 近支撑     │ 减仓     │ -50%     │ 跌破MA20                │
  │ ¥1,560      │ 硬止损     │ 清仓     │ -100%    │ ATR止损位（2.5×ATR）    │
  └─────────────┴────────────┴──────────┴──────────┴─────────────────────────┘

  委托参考：卖出¥1,682（昨收-0.5%）  买入¥1,693（昨收+0.5%）
  进场信号参考：[BUY×2] 趋势跟踪确认，ADX=28  |  [WATCH×1] 质量仓候选
```

---

## 九、实现路径（渐进三步）

### Step 1：基础设施（前置条件）

- [ ] `strategies/position_manager.py`：底层价位计算函数（`calc_atr` / `calc_swing_low` / `calc_swing_high` / `calc_supertrend`）
- [ ] `strategies/base.py`：添加 `ExitLevels` / `ScaleLevel` dataclass，`compute_exit_levels` 默认实现
- [ ] `config/settings.py`：`RISK_CONTROL` 新增持仓管理参数
- [ ] `data/portfolio.json`：添加 `total_capital` 和 `position_type` / `style` 字段

### Step 2：策略层实现

- [ ] `ma_cross`：重写 `compute_exit_levels`（Supertrend 止损 + 摆动高点止盈）
- [ ] `vol_price_breakout`：重写（Swing Low 止损 + 箱体目标止盈）
- [ ] `macd_rsi`：重写（布林带下轨止损）
- [ ] `etf_momentum`：重写（ATR 兜底 + 相对动量出场）
- [ ] `fundamental_value` / `deep_value` / `growth_pick`：重写（基本面出场，`stop_loss=0.0`）

### Step 3：消费层改造

- [ ] `portfolio` 命令：调用 `compute_exit_levels`，展示分级操作表，修正进场/出场逻辑混淆
- [ ] `trader/assistant.py`：`TradeOrder` 优先读 `Signal.extra["exit_levels"]`，固定百分比降级为兜底
- [ ] `backtest/engine.py`：消费 `ExitLevels` 出场逻辑，支持分批出场，支持移动止损更新

---

## 十、关键设计约束（与现有架构的兼容性）

| 约束 | 说明 |
|---|---|
| AC2（不硬编码参数） | 所有止损倍数、风险比例从 `RISK_CONTROL` 读取 |
| 复权口径 | `compute_exit_levels` 的 `df` 是 hfq 数据，输出价格也是 hfq；portfolio 展示时换算回不复权（复用现有 `adj_factor` 逻辑） |
| 自定义策略向后兼容 | `compute_exit_levels` 有默认 ATR 实现，自定义策略不实现也能跑 |
| 配置仓 stop_loss=0.0 | `position_manager` 和回测引擎遇到 `stop_loss=0.0` 跳过价格止损，只做基本面出场检查 |
| 扫描性能 | 扫描时调用 `compute_exit_levels` 的计算量不大（纯向量运算），不影响 20 线程扫描性能；配置仓基本面数据由 `generate_signals` 已读的数据传入，不产生额外 IO |
