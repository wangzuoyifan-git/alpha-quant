# 数据域重构设计方案

> 状态：设计阶段（仅方案，不含代码改动）
> 创建：2026-06-15
> 背景：台阶二开发前的数据基础整理
> 决策：① 仅出设计方案，暂不物理迁移 ② 资金流拆为独立 `moneyflow` 域

---

## 一、问题诊断

### 1.1 核心问题：分类原则不一致

当前 7 个存储域混用两套分类依据：

| 域 | 分类依据 | 说明 |
|---|---|---|
| `market` | 数据类型（行情） | ✅ 按类型 |
| `fundamental` | 数据类型（财务） | ✅ 按类型 |
| `macro` | 数据类型（宏观） | ✅ 按类型 |
| `meta` | 数据类型（元数据） | ✅ 按类型 |
| `events` | 数据类型（事件） | ✅ 按类型 |
| `factor` | 数据类型（因子） | ✅ 按类型 |
| `tushare` | **数据来源（供应商）** | ❌ 按来源 |

`tushare` 域是唯一按"数据来源"命名的域，与其余 6 个按"数据类型"命名的域原则冲突。这是迭代过程中为快速接入 Tushare 数据而临时设立的，属于技术债。

### 1.2 由此产生的三个具体后果

**后果 A：资金流数据分裂在两个域**

| 路径 | 来源 | 实际状态 |
|---|---|---|
| `factor/moneyflow/` | AKShare 个股 | **空（0 文件）** — 死占位 |
| `factor/moneyflow_sector.parquet` | AKShare 行业 | 不存在 — 死占位 |
| `factor/moneyflow_market.parquet` | AKShare 大盘 | 不存在 — 死占位 |
| `factor/northbound.parquet` | AKShare 北向 | ✅ 有数据（2014~，2682 行） |
| `tushare/moneyflow/` | Tushare 个股 | ✅ 主力（5406 只 × 583 天） |
| `tushare/moneyflow_ind/` | Tushare 东财行业 | ✅ 有数据（583 天） |
| `tushare/moneyflow_mkt.parquet` | Tushare 大盘 | ✅ 有数据 |

同类数据（资金流）因来源不同被劈到两个域，且 `factor` 域里的资金流全是死占位（AKShare 个股资金流仅近 60 天，从未真正下载使用）。

**后果 B：`tushare` 域内部混装了多种类型，应归属不同域**

| `tushare` 子项 | 实际类型 | 应属域 | 实际状态 |
|---|---|---|---|
| `daily/` | 行情日线 | market | **1 文件（死占位）** |
| `daily_basic/` | 行情指标(PE/PB) | market | **1 文件（死占位）** |
| `moneyflow/` | 个股资金流 | moneyflow | ✅ 主力 |
| `moneyflow_ind/` | 行业资金流 | moneyflow | ✅ 有数据 |
| `moneyflow_mkt.parquet` | 大盘资金流 | moneyflow | ✅ 有数据 |
| `margin/` | 融资融券 | moneyflow（情绪） | ✅ 有数据（仅近 21 天） |
| `top_list/` | 龙虎榜 | moneyflow（情绪） | ✅ 有数据（11 天） |

**后果 C：死占位误导认知**

`factor/moneyflow`（空）、`tushare/daily`（1 文件，无代码引用）、`tushare/daily_basic`（1 文件）这类"规划了但未真正使用"的路径，review 时极易误判，增加维护负担。

---

## 二、设计原则（确立）

> **数据域一律按"数据类型 / 用途"分类，不按数据来源分类。**
> 数据来源（AKShare / Tushare / BaoStock）是 `downloader` 层的实现细节，不进域命名。
> 同一类型数据若有多来源，由 downloader 内部选择主备源，对 store 层透明。

---

## 三、目标数据域设计

### 3.1 目标域划分（8 个，纯按类型）

```
market       行情数据（日线 OHLCV + 估值/换手率）
             ├── cn/{stock,index,etf}/{code}.parquet
             └── （吸收原 tushare/daily, daily_basic — 若未来启用）

fundamental  季频财务（profit/balance/cashflow/growth/dupont）

macro        宏观经济（利率/M2/CPI/PMI/VIX/汇率...）

meta         元数据（行业分类三级/成分股/交易日历/申万指数）

events       企业事件（减持/质押/分红）+ 定期事件 + 突发事件

moneyflow    资金流与情绪（新域 ← 拆分自 factor + tushare）★
             ├── stock/{code}.parquet      个股资金流（Tushare）
             ├── industry/{date}.parquet    行业资金流（Tushare 东财）
             ├── market.parquet             大盘资金流（Tushare）
             ├── northbound.parquet         北向资金（AKShare）
             ├── margin/{code}.parquet       融资融券（Tushare）
             └── top_list/{date}.parquet     龙虎榜（Tushare）

factor       计算型因子（未来：动量/价值/质量等因子值）★
             └── （重新定位：只放"计算/衍生"的因子，不放原始资金流）

（tushare 域解散）
```

### 3.2 新增 `moneyflow` 域的理由（采纳"拆出独立域"决策）

- 资金流是分层决策的核心输入（L2 行业轮动、L3 资金流策略、未来 L1 情绪），数据量大（个股 479M + 行业 32M），独立成域职责清晰
- 与"计算型因子"（factor 重新定位的目标）区分：moneyflow 是**原始市场数据**，factor 是**衍生计算值**，两者性质不同
- `northbound` / `margin` / `top_list` 本质都是"资金/情绪"类原始数据，归并到 moneyflow 域语义自洽

### 3.3 `factor` 域重新定位

原 `factor` 域名不副实（只有 northbound 有数据，资金流全空）。重构后：
- 原始资金流类 → 迁往 `moneyflow` 域
- `factor` 域保留给**真正的计算型因子**（台阶二/后续：行业景气度因子、估值分位数、动量因子等衍生值）
- 台阶二前 `factor` 域可暂时为空（占位但语义清晰）

---

## 四、迁移映射表（物理迁移路线图，暂不执行）

| 现路径 | 目标路径 | 动作 | 备注 |
|---|---|---|---|
| `factor/moneyflow/`（空） | — | **删除** | 死占位 |
| `factor/moneyflow_sector`（无） | — | **删除规划** | 死占位 |
| `factor/moneyflow_market`（无） | — | **删除规划** | 死占位 |
| `factor/northbound.parquet` | `moneyflow/northbound.parquet` | 迁移 | 有数据 |
| `tushare/moneyflow/` | `moneyflow/stock/` | 迁移 | 479M 主力 |
| `tushare/moneyflow_ind/` | `moneyflow/industry/` | 迁移 | 32M |
| `tushare/moneyflow_mkt.parquet` | `moneyflow/market.parquet` | 迁移 | |
| `tushare/margin/` | `moneyflow/margin/` | 迁移 | 131M |
| `tushare/top_list/` | `moneyflow/top_list/` | 迁移 | |
| `tushare/daily/`（1 文件） | `market/cn/stock/` 或删除 | **删除** | 死占位，无引用 |
| `tushare/daily_basic/`（1 文件） | 删除或并入 market | **删除** | 死占位 |

---

## 五、代码改动清单（物理迁移时需要改的，预估影响面）

> 本节用于评估物理迁移成本，本次设计阶段不执行。

### 5.1 store 层
- 新建 `data/store_moneyflow.py`（吸收资金流类型）
- 删除 `data/store_tushare.py`（解散）
- 调整 `data/store_factor.py`（移除资金流，保留因子占位）

### 5.2 downloader 层
- `data/downloader_tushare.py` → 拆分：资金流相关方法迁入新的 `downloader_moneyflow.py`
- 行情类（daily/daily_basic）方法删除或并入 `downloader.py`

### 5.3 调用方（实际引用点，已盘点）
| 文件 | 当前引用 | 改动 |
|---|---|---|
| `agents/industry_agent.py:39` | `store_tushare` | → `store_moneyflow` |
| `agent_tools/data_tools.py:12` | `store_tushare as store_ts` | → `store_moneyflow` |
| `agent_tools/data_tools.py:10` | `store_factor` | 资金流调用改指向 moneyflow |
| `engine/analysis.py:21,113` | `store_factor` / `store_tushare` | → `store_moneyflow` |

### 5.4 运维接口
- `agent_tools/data_tools.py`：`VALID_DOMAINS` 把 `tushare` 替换为 `moneyflow`
- `check_data_freshness` / `update_domain` 的域分支调整
- `main.py` CLI：`init-tushare` / `update-tushare` → `init-moneyflow` / `update-moneyflow`

### 5.5 测试
- `tests/test_store_tushare.py` → `test_store_moneyflow.py`
- 各测试中 monkeypatch 的 `data.store_tushare` 路径调整
- 影响约 20~30 个测试用例的路径字符串

### 5.6 配置
- `.gitignore`：`data/tushare/` → `data/moneyflow/`
- `docs/data.md`、`docs/project.md` 同步更新

### 5.7 迁移时必须一并修复的已知 bug（2026-06-15 核实发现）

> P1 核实时发现的隐藏 bug，决策为推迟到 P3 物理迁移时统一修复。

- **`engine/analysis.py:_get_fund_flow_summary()` 读错路径**
  - 现状：从 `factor/moneyflow`（空目录）读个股资金流 → `list_moneyflow_codes()` 永远返回空 → 该函数恒返回 `{"available": False, "note": "无个股资金流数据"}`
  - 影响：辅助分析（`analysis.py:43` 主流程调用）的资金流摘要功能**一直失效**
  - 真实数据：在 `tushare/moneyflow`（字段 `net_mf_amount`，5406 只 × 583 天）
  - P3 修复：数据源改指向 `moneyflow/stock/`（迁移后路径），恢复功能
  - 关联：`engine/analysis.py:113` 的 margin 读取也需同步从 `store_tushare` 改为 `store_moneyflow`

### 5.8 死占位清理清单（P1 内容，已决定推迟）
- `data/factor/moneyflow/`（空目录）— 删
- `data/tushare/daily/000001.parquet`（1 死文件，无代码引用）— 删
- `data/tushare/daily_basic/000001.parquet`（1 死文件）— 删
- 注意：`main.py` 中 `daily`/`daily_basic` 是 CLI 默认值且 `init_daily()` 可用，**非死代码，不删**（属"功能可用未启用"）

---

## 六、分阶段执行建议

| 阶段 | 动作 | 风险 | 建议时机 |
|---|---|---|---|
| **P0 设计** | 本文档（确立原则 + 目标设计 + 迁移图） | 无 | ✅ 现在 |
| **P1 清理** | 删死占位（factor/moneyflow 空目录、tushare/daily 死文件）+ 文档标注实际内容 | 极低 | 可现在 |
| **P2 冻结** | 标记 `tushare` 域为 deprecated，新数据不再入；台阶二新资金流数据直接进 `moneyflow` 域设计 | 低 | 台阶二启动时 |
| **P3 迁移** | 物理迁移 + store/downloader/调用方/测试全量改造 | 中高 | 台阶二数据需求明确后，一次性做 |

**核心建议**：P3 物理迁移不要现在做。等台阶二明确新增哪些资金流/因子数据后一次性规划迁移，避免"现在搬一次、台阶二再搬一次"的重复成本。本次只锁定设计原则和目标结构（P0），并可选做 P1 清理。

---

## 七、未决问题（留待台阶二讨论）

1. `market` 域是否真的需要 Tushare daily/daily_basic？当前 BaoStock/AKShare 日线已够用，daily 是死占位。若台阶二需要 Tushare 的 PE/PB 指标，再决定是否启用并入 market。
2. `factor` 域（计算型因子）的具体因子清单 — 取决于台阶二 L2 景气度评分、估值分位数等需求。
3. `events` 域的 JSON → Parquet 迁移（792M，独立技术债，与本次域分类问题正交）。
4. 北向资金（northbound）/ 大盘资金流（market）/ 龙虎榜（top_list）接入决策层的时机 — 当前"存而不用"。
