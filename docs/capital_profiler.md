# Alpha量化 — 资金画像系统设计（Capital Profiler）

> 本文档基于外部参考 `reference/资金画像0.1.md`（v3.3）刷新，**目标需求与逻辑方法沿用参考文档**，
> 但**数据源、模块落位、接口与目录全部匹配本项目现有架构**，并明确标注本项目的降级/缺口。
> 最后更新：2026-09-29（Phase 1 实现 + 席位字典接入）

---

## 一、定位与范围

回答三个问题（三种分析对象，结构一致：`dominant_capital` / `feedback` / `persistence`）：

| 对象 | level | 核心问题 | 输入示例 |
|---|---|---|---|
| 个股 | `stock` | 当前被什么资金主导？正/负反馈？持续性？ | `capital-profiler profile --level stock --code 688256 --date 2026-09-01` |
| 板块/ETF | `sector_etf` | 同上（Phase 2） | `--level sector_etf --code 512760` |
| 大类 | `sector_macro` | 同上（Phase 3） | `--level sector_macro --code tech` |

**核心原则**：数据不足时**如实返回“无法判断”**并给出原因与建议，不强行给结论（见第四章）。

> 与 `smart_money_agent`（席位/北向/合力分歧，描述性）和 `seat_profiler`（游资/量化字典）的关系：
> 本模块是**面向“个股/板块/大类”的综合资金画像上层应用**，**消费**席位字典与底层数据域。

---

## 二、数据源映射（关键：匹配本项目）

| 参考文档中的数据需求 | 本项目落位 | 状态 |
|---|---|---|
| 龙虎榜买卖席位明细 | `data/moneyflow/top_inst` | ✅ |
| 席位身份（游资/量化/机构/陆股通） | `agents/seat_profiler.py` 席位字典 + `classify_seat` | ✅（游资/量化含置信度） |
| 两融（融资余额/买入/融券） | `data/moneyflow/margin` | ✅ |
| 北向持股（季度） | `data/moneyflow/hk_hold` | ✅（季度披露） |
| 北向日频代理 | 龙虎榜 `沪股通专用/深股通专用` 席位净额 | ✅ |
| 大宗交易 | `data/moneyflow/block_trade` | ✅ |
| 回购/增减持（产业资本） | `data/store_events`（E2 企业事件） | ✅ |
| 行情 OHLCV | `data/market`（后复权） | ✅ |
| 个股日频资金流（净流入） | `data/moneyflow/stock`（`net_mf_amount`） | ✅（用作资金流代理） |
| 股东户数（季频） | — | ❌ 未接入（Phase 1 不用/降级） |
| ETF 申赎 / 成分股权重 / 大类映射 | — | ❌ 未接入（Phase 2/3 依赖） |
| 个股期权/衍生品 | — | ❌ 不适用 |

**Phase 1 仅做“个股级”**，且只用上表 ✅ 数据；❌ 数据在输出中标注“无数据/降级”，并进入无法判断规范。

---

## 三、输出契约（结构化 dict，JSON 可直接序列化）

```python
{
  "meta": {"level","code","name","date","lookback_days"},
  "answer": {
      "status": "resolved" | "undetermined",
      "dominant_capital": str|None,     # resolved 时有效
      "feedback": str|None,             # 正反馈/负反馈/中性/条件依赖
      "persistence": str|None,          # 极弱(1日)/弱(1-3日)/中(1-2周)/长(1月+)
      "undetermined_reason": str|None,  # undetermined 时
      "suggestions": [str]|None,        # undetermined 时
      "partial_findings": dict|None     # 即使无法判断也列出已确认信息
  },
  "structure_score": float|None,        # 0-100，无法判断时 None
  "structure_grade": str|None,          # 健康/一般/脆弱/危险
  "capital_details": {
      "<category>": {"net_buy","direction","proxy_metric","confidence","note"}
  },
  "key_signals": [{"signal","severity","implication"}],
  "warnings": [str],
  "data_quality": {"completeness","accuracy","timeliness"},
  "disclaimer": str
}
```

### 3.1 资金类别（Phase 1 个股级）

| category | 代理指标 | 本项目数据 | 方向阈值 |
|---|---|---|---|
| `hot_money` | 知名游资席位窗口净买入 | top_inst + 席位字典 | \|净额\|>5000万 |
| `quant` | 量化席位窗口净买卖 | top_inst + 席位字典 | \|净额\|>5000万 |
| `institutional` | 机构专用席位窗口净买卖 | top_inst | \|净额\|>5000万 |
| `northbound` | 沪/深股通专用席位窗口净买入（日频代理）；季度持股变动兜底 | top_inst + hk_hold | \|净额\|>3000万 |
| `margin` | 融资余额变动率 | margin | \|变动\|>3% |
| `industrial_capital` | 回购/增持金额 | events E2 | \|金额\|>1亿 |

> 阈值对齐业界通用（`reference/资金画像0.1.md` 11.3 `thresholds.yaml`）：龙虎榜 5000万 / 陆股通 3000万 / 两融 3% / 回购增持 1亿。

direction 取值：`buy` / `sell` / `mixed` / `stable`(有数据但低于阈值) / `absent`(窗口内无该信号) / `unknown`。

### 3.2 反馈性质 `feedback`
用窗口内**价格日收益**与**主力资金净流入**（`moneyflow/stock`：`(buy_lg+buy_elg)-(sell_lg+sell_elg)`，缺则回退 `net_mf_amount`）的相关判定：
`corr>0.6`→强正反馈；`corr<-0.4`→强负反馈；`-0.4~0.2`→中性；其余→条件依赖。输出中文标签。

> 说明：`moneyflow/stock` 按**单笔成交金额档位**分类，**不区分席位/资金主体**；席位级方向由 `dominant_capital`（席位字典等）负责，feedback 只做价量反馈的连续代理。

### 3.3 席位视图 × 主力视图一致性（Phase 1 增强）

两个正交视图结合：
- **席位视图 A**（谁）：`top_inst` + 席位字典 → 席位类合计净额方向；**上榜事件级、稀疏**。
- **主力视图 B**（多少钱）：`moneyflow/stock` 大单+超大单净额 → 窗口主力净流入；**日频连续**。

交叉**以“上榜事件”为锚点**（无上榜 = 无交叉）：

| 席位 A | 主力 B | 判定 | 含义 |
|---|---|---|---|
| 净买入 | 主力净流入 | 合力向上 | 置信度提升 |
| 净卖出 | 主力净流出 | 合力向下 | 一致看空 |
| 净买入 | 主力净流出 | 对倒/诱多 | 上榜买但大钱在走，警惕拉高出货 |
| 净卖出 | 主力净流入 | 隐性吸筹 | 上榜卖但大钱在进，可能换手/承接 |

三层交叉（输出 `seat_vs_main` + `seat_main_signals`）：
1. **窗口聚合**（`seat_vs_main`）：窗口内席位类合计净额 vs 窗口主力净流入合计。
2. **上榜前后**（`seat_main_signals`）：上榜前 5 日主力净流入 → 提前吸筹后上榜；上榜后 5 日主力转净流出 → 拉高出货风险。
3. 上榜当日为窗口聚合的特例。

输出另含 `main_flow_daily`（窗口主力净流入合计、连续同向天数）作为背景。

**席位级事件流合力/分歧**（`report["consensus"]`）：复用 `smart_money_agent.assess_consensus` 的四路
（北向 / 机构席位 / 融资盘 / 大宗买方）方向，判定“资金合力 / 资金分歧 / 证据不足”，并入 `key_signals`/`warnings`。
与 `seat_vs_main` 的区别：`seat_vs_main` = 席位事件 × 日频主力；`consensus` = 多路席位/融资/大宗事件之间。

> 未上榜股票：默认仍走 S3；加 `--hidden-main` 时，若主力日频连续同向（≥3 日）则给“隐性主力（日频，弱推断）”弱结论（`answer.weak=true`）。

### 3.4 持续性 `persistence`
按「对象 × 主导资金 × 反馈」查表（沿用参考文档 2.4 节），Phase 1 覆盖个股级：
游资/打板→极弱(1日)；量化→弱(1-3日)；陆股通→中(1-2周)；机构专用→中(1-2周)；产业资本→强(1月+)；两融→中(1-2周)。

### 3.5 结构健康度评分 `structure_score`
参考文档 10.1 的个股级公式，**缺失维度按权重重归一**（本项目无股东户数）：

```
speculative_ratio   = |hot_money_net + quant_net| / total_flow
institutional_ratio = |institutional_net + industrial_capital| / total_flow
margin_ratio        = margin_change_rate
score = w1*(1-spec)*100 + w2*inst*100 + w3*(1-margin_ratio/0.15)*100
（权重对齐参考文档 10.1：投机 0.30 / 机构 0.35 / 两融 0.20（+股东户数 0.15 本项无数据），
 依可用维度重归一）
```
等级：80+ 健康 / 60-79 一般 / 40-59 脆弱 / <40 危险 / N/A 无法判断。

---

## 四、无法判断规范（沿用参考文档第三章）

| 场景 | 触发条件 | 状态 | 原因 |
|---|---|---|---|
| S1 停牌 | 窗口内无有效行情 | undetermined | 当日无交易数据（停牌） |
| S2 数据源缺失 | 关键字段缺失率>20% | undetermined | 数据源缺失 |
| S3 信号全缺席 | 所有类别 direction ∈ {absent,stable} | undetermined | 所有资金信号缺席/稳定 |
| S4 信号矛盾 | 同时存在超阈买入与超阈卖出类别 | undetermined | 正负反馈资金同时活跃 |
| S5 样本不足 | 窗口内有效交易日<20 | undetermined | 回看窗口不足 |
| S6 历史不足 | 分位数所需历史<60日 | undetermined | （Phase 1 用固定阈值，一般不触发） |

每条 undetermined 必须带 `suggestions` 与 `partial_findings`。

---

## 五、模块落位与接口

```text
agents/capital_profiler.py        # 画像引擎（Phase 1）
├── profile(level, code, date, lookback_days) -> dict
├── _capital_details(...)         # 六类资金明细
├── _detect_feedback(...)         # 反馈性质
├── _persistence(...)             # 持续性
├── _structure_score(...)         # 结构评分
└── _undetermined(...)            # S1~S6 处理
main.py  capital-profiler profile --level stock --code ... --date ... [--format json|text]
```

消费：席位字典（`seat_profiler`）→ 游资/量化净额；`smart_money_agent` 的 `load_seat_episodes` 复用。

---

## 六、阶段路线

| 阶段 | 交付 | 依赖 | 状态 |
|---|---|---|---|
| **Phase 1** | 个股级画像 + CLI + 无法判断 | top_inst/席位字典/margin/hk_hold/block_trade/events/market | ✅ 本次实现 |
| Phase 2 | 板块/ETF 画像 | ETF 成分权重 + ETF 申赎数据 | 待数据 |
| Phase 3 | 大类画像 + IC/IR 评估 | 大类映射 + 历史积累 | 待开发 |
| Phase 4 | 监控 + 阈值自适应 | Phase 3 | 待开发 |

---

## 七、与参考文档的差异（重要）

1. **落位**：参考文档面向独立 `capital_profiler/` 包；本项目实现为 `agents/capital_profiler.py` + `main.py` 子命令，复用现有数据域与 Agent 层。
2. **数据**：参考文档假设 Pandadata 12+ 接口；本项目用本地 Tushare/AKShare 落地的 6 域数据，缺失项（ETF 申赎、股东户数、ETF 权重）明确降级。
3. **反馈判定**：参考用“龙虎榜+两融+大宗”合成的资金流；本项目 Phase 1 用 `moneyflow/stock.net_mf_amount` 作为日频资金流代理（更连续），后续可切换。
4. **席位识别**：参考用规则字典；本项目用 `seat_profiler` 的“硬标签+聚类软标签+回测”三层字典，带置信度与生效时间。
5. **评分**：参考含股东户数维度；本项目 Phase 1 无该数据，按权重重归一。

---

## 八、免责声明

> 本画像基于公开数据与规则化分析生成，仅供研究参考，不构成任何投资建议。
> 席位/资金标签为规则匹配或聚类推断，非官方认定。
