# Alpha量化 — 需求详细分解

> 自顶向下需求分解，Phase 1 细化到可测试粒度，Phase 2/3 保留高层描述。
> 最后更新：2026-05-29
>
> **项目基础信息与阶段规划见 `docs/project.md`**
> **架构设计见 `docs/agent.md`**

---

## 一、顶层目标分解

### 1.1 最终目标

在风控约束内，系统自主完成从市场感知到交易执行的全流程，人工只做监控和异常干预。

### 1.2 四个核心能力域

从最终目标向下拆解，系统需要四个相互依赖的核心能力：

```
目标：全自动化量化交易
  │
  ├── C1 市场感知能力    知道市场在发生什么
  │     └── 数据采集、存储、验证、更新
  │
  ├── C2 分析判断能力    判断哪些机会值得参与
  │     ├── 策略信号生成（技术面/基本面/资金面/宏观面）
  │     ├── 多维度信号合并与评分
  │     └── 回测验证与策略评估
  │
  ├── C3 决策风控能力    决定怎么参与、风险多大
  │     ├── 硬性风控约束（仓位/止损/行业集中度/标的质量）
  │     ├── 仓位管理建议
  │     └── 执行结果反馈闭环
  │
  └── C4 执行交互能力    执行并跟踪结果
        ├── Phase 1/2：指令单生成，人工执行
        ├── Phase 2：券商 API 接入，人工确认后自动执行
        └── Phase 3：全自动执行，人工监控
```

### 1.3 阶段与能力域的对应关系

| 能力域 | Phase 1 | Phase 2 | Phase 3 |
|---|---|---|---|
| C1 市场感知 | 全量实现（日线） | 扩展实时价 | 扩展分钟线 |
| C2 分析判断 | 信号生成 + 辅助分析 | Agent 自主策略优化 | 持续自适应 |
| C3 决策风控 | 硬性约束 + 指令单建议 | 自动风控过滤 | 全自动风控 |
| C4 执行交互 | 指令单 + 人工执行 | 指令单推送 + 一键人工确认执行 | 券商 API 接入 + 全自动执行 |

---

## 二、C1 市场感知能力需求

### 2.1 为什么需要这些数据

系统的所有判断都依赖数据，不同用途对数据有不同要求：

| 用途 | 需要什么数据 | 对数据的核心要求 |
|---|---|---|
| 策略信号生成 | 价格/成交量/技术指标 | 完整、准确、及时（收盘后当日可用） |
| 回测验证 | 至少 5 年历史价格数据 | 完整、复权正确、无幸存者偏差 |
| 辅助决策 | 基本面/资金流/行业背景 | 准确、有时效性（季频/日频） |
| 风控过滤 | 标的质量标签（ST/新股）、财务健康度 | 准确，宁可误杀不可漏杀 |
| 市场环境判断 | 宏观指标、行业轮动、北向资金 | 趋势性准确，允许轻微延迟 |
| 事件规避与利用 | 定期事件日历、企业公告、突发事件信号 | 及时、可结构化查询；突发事件以代理指标为主 |

### 2.2 数据分类与更新周期需求

数据按变化频率和用途分类，不同类别有不同的获取和存储需求：

**D1 — 行情数据（日频，核心）**

- 需求来源：策略信号生成、回测验证的基础输入
- 覆盖范围：A 股全市场（~5200只）、主要指数（~540只）、ETF（~1500只）
- 历史深度：至少 5 年（覆盖完整牛熊周期），尽量追溯到 2010 年
- 时效要求：每个交易日收盘后，当日数据必须在收盘后 2 小时内可用
- 字段要求：OHLCV + 换手率 + 复权因子 + 估值指标（PE/PB）+ 质量标签（isST）
- 复权要求：前复权（策略计算用），原始价格（展示用）同时保留

**D2 — 基本面数据（季频）**

- 需求来源：辅助决策（基本面摘要）、风控过滤（财务健康度）、质量因子策略
- 覆盖范围：A 股全市场
- 历史深度：至少 5 年（20个季度）
- 时效要求：季报披露后 1 周内更新
- 字段要求：盈利能力（ROE/净利率/毛利率）、偿债能力（负债率/流动比率）、成长能力（营收/净利润同比）、现金流质量

**D3a — 个股资金流（日频）**

- 需求来源：辅助决策（个股资金流摘要）、资金面策略信号（主力意图判断）
- 覆盖范围：A 股全市场个股
- 历史深度：至少 2 年；Tushare 可追溯到 2010 年，优先获取完整历史
- 时效要求：每个交易日收盘后当日可用
- 字段要求：
  - 超大单净流入（额/占比）：单笔 ≥ 100 万，反映机构/游资主力行为
  - 大单净流入（额/占比）：单笔 50~100 万，反映中大资金
  - 中单净流入（额/占比）：单笔 10~50 万
  - 小单净流入（额/占比）：单笔 < 10 万，反映散户行为
  - 主力净流入（超大单 + 大单合计，额/占比）：策略信号的核心指标
- 时间聚合：需支持按标的查询当日、近 5 日、近 20 日的累计净流入

**D3b — 行业资金流（日频）**

- 需求来源：行业背景展示（行业资金流向排名）、行业轮动策略信号
- 覆盖范围：申万一级行业（28个）
- 历史深度：至少 1 年
- 时效要求：每个交易日收盘后当日可用
- 字段要求：行业名称、主力净流入（额/占比）、行业涨跌幅、排名

**D3c — 市场资金流（日频）**

- 需求来源：市场环境判断（多空情绪）、大盘择时策略
- 覆盖范围：北向资金（沪股通 + 深股通）、沪深两市大盘主力净流入合计
- 历史深度：至少 2 年
- 时效要求：每个交易日收盘后当日可用
- 字段要求：
  - 北向：当日净买入额、5日/10日累计净买入
  - 大盘：主力净流入合计、超大单净流入合计

**D7 — 情绪因子数据（日频）**

- 需求来源：市场情绪判断、风控过滤（杠杆风险预警）、情绪面策略信号
- 与资金流分开管理的原因：融资融券反映的是**杠杆资金行为和市场情绪**，更新逻辑、数据源、使用场景均与资金流不同
- 覆盖范围：A 股全市场个股 + 市场合计
- 历史深度：至少 2 年（Tushare 可追溯）
- 时效要求：每个交易日收盘后当日可用
- 字段要求：
  - 个股级别：融资余额、融资买入额、融券余量、融券卖出量
  - 市场级别：两融余额合计、融资余额合计、融券余量合计
- 用途说明：
  - 融资余额持续增加 → 杠杆资金入场，情绪偏乐观
  - 融资余额快速下降 → 去杠杆，风险信号
  - 融券余量大幅增加 → 机构看空，可作为反向指标

**D8 — 事件数据（混合频率，三类）**

事件数据与时间序列数据的本质区别：时间序列每天都有值，缺了是缺失；事件大部分时间不存在，发生时有强烈的不连续影响，需要独立的存储结构和更新逻辑。

**D8a — 定期事件日历（E1，可结构化预入库）**

- 需求来源：规避交易窗口期（财报季/议息日不开新仓）；事件前后异动监测；辅助决策展示
- 覆盖范围：
  - 宏观数据发布日历：CPI/PPI/GDP/PMI 发布日、美联储议息日（FOMC）
  - 政策会议日历：中央经济工作会议、两会、货币政策委员会例会
  - 上市公司定期披露：财报季窗口期（一季报/半年报/三季报/年报）、分红除权日
- 时效要求：提前 1 个月入库；每季度更新一次日历；事件时间确认后立即更新
- 字段要求：event_id、event_type（macro/policy/earnings）、event_name、date、end_date、impact_scope（global/cn/sector）、pre_window（事件前降权天数）、post_window（事件后观察天数）、confirmed（时间是否已确认）
- Phase 1 实现：结构化日历以 JSON 维护，支持按日期范围查询当前窗口期内的事件
- 数据来源：人工维护 + AKShare 部分接口（如财报披露日期）

**D8b — 企业事件（E2，结构化接口优先）**

- 需求来源：风控过滤（大股东减持窗口期不买入）；企业事件驱动策略信号
- 覆盖范围：A 股全市场
  - Phase 1（结构化接口）：大股东减持计划、增持计划、股权质押、分红送转
  - Phase 2（NLP 解析）：重大资产重组公告、实控人变更、战略合作协议等非结构化公告
- 时效要求：每个交易日收盘后更新当日新增公告
- 字段要求：event_id、code、event_type（reduce/increase/pledge/dividend/restructure）、date、detail（结构化事件参数）、source（tushare/nlp）、phase（1=结构化，2=NLP）
- Phase 1 数据来源：Tushare（减持计划 `stk_holdertrade`、股权质押 `pledge_stat`）

**D8c — 突发事件（E3，LLM 驱动识别）**

- 需求来源：市场环境判断（黑天鹅预警）；事后归因分析
- Phase 1：不做 LLM 建模，依赖 D5b 代理指标（VIX > 30 或 A 股指数单日跌幅 > 3%）触发熔断告警，事后人工记录事件
- Phase 2：接入新闻 API（如东财、新浪财经 RSS），LLM 对新闻标题做分类和影响评估，输出结构化事件记录
- Phase 3：实时新闻流 + LLM Agent 持续监控，自动调整仓位
- 字段要求：event_id、date、event_type（geopolitical/natural/tech_shock/policy_shock）、description、trigger（proxy_indicator/llm_news/manual）、market_impact（指数涨跌幅/受影响板块）、source_url
- 时效要求：Phase 1 事后记录即可；Phase 2+ 要求事件发生后 30 分钟内入库

**D4 — 行业分类数据（准静态，每周更新）**

- 需求来源：行业背景展示、行业集中度风控、行业轮动策略
- 覆盖范围：申万一级行业分类（股票→行业映射）、申万行业指数日线
- 时效要求：每周更新一次（行业成分股变化不频繁）
- 字段要求：股票→行业映射、行业指数 OHLCV

**D5a — 国内宏观数据（混频）**

- 需求来源：市场环境判断（信用周期、通胀周期）、大盘择时策略
- 收录说明：优先收录对 A 股有直接传导路径的指标，房地产数据暂不收录（Phase 1 策略边际贡献有限）
- 数据项：

| 指标 | 频率 | 数据源 | 对 A 股的传导路径 |
|---|---|---|---|
| M2 货币供应同比 | 月频 | AKShare | 流动性宽松→估值扩张 |
| 社融规模增量/存量同比 | 月频 | AKShare | 信用扩张是牛市核心驱动 |
| CPI/PPI 同比 | 月频 | AKShare | 通胀压力影响货币政策预期 |
| 制造业/非制造业 PMI | 月频 | AKShare | 经济景气度先行指标 |
| 中国国债收益率（2Y/10Y） | 日频 | AKShare | 无风险利率，影响股票估值 |
| 人民币汇率（CNY/USD） | 日频 | AKShare/Tushare | 贬值压力影响外资流向和北向资金 |

- 时效要求：月度数据发布后 1 周内更新；日频数据收盘后当日可用
- 字段要求：各指标当期值、同比/环比变化（月频）；日期+收盘值（日频）

**D5b — 境外宏观数据（混频）**

- 需求来源：市场环境判断（全球流动性、风险偏好）；Phase 1 先收集，用途在策略设计阶段再确定
- 收录原则：只收录对 A 股有明确传导路径、数据可免费/低成本获取的指标
- 数据项：

| 指标 | 频率 | 数据源 | 对 A 股的传导路径 |
|---|---|---|---|
| 美联储基准利率 | 事件触发（每次议息后） | AKShare | 全球流动性锚，加息压制估值和北向流入 |
| 美债收益率（2Y/10Y） | 日频 | AKShare | 中美利差影响人民币汇率和外资行为 |
| VIX 恐慌指数 | 日频 | AKShare | 全球风险偏好，VIX 飙升时 A 股跟跌 |
| 美元指数（DXY） | 日频 | AKShare | 强美元压制人民币和新兴市场资产 |
| 原油价格（WTI/布伦特） | 日频 | AKShare | 直接影响能源板块；成本端影响工业企业 |
| 铜价（LME） | 日频 | AKShare | 工业景气度领先指标，影响有色板块 |
| 标普500指数 | 日频 | AKShare | 全球风险情绪传导，科技板块联动 |
| 恒生指数 | 日频 | AKShare | 港股与 A 股联动，AH 溢价参考 |

- 时效要求：日频数据收盘后次日可用（境外市场时区差异）；月频/事件触发数据发布后 3 日内更新
- 字段要求：日期+指标值；利率类同时记录变动幅度

**D6 — 交易日历（准静态，每年更新）**

- 需求来源：判断是否为交易日（避免节假日触发无效更新）、回测日期序列生成
- 覆盖范围：A 股交易日历，含节假日
- 时效要求：每年年初更新一次，节假日调整时补充更新

### 2.3 数据可信度需求

**DR1 — 多源交叉验证**

- 关键字段（收盘价、成交量）需要至少两个数据源可以互相验证
- 两源数据偏差超过阈值（收盘价偏差 > 0.1%，成交量偏差 > 1%）时触发告警
- 验证失败时以主源数据为准，记录差异，不自动修正

**DR2 — 单源异常检测**

即使只有单一数据源，也需要检测以下异常：

| 异常类型 | 检测规则 | 处理方式 |
|---|---|---|
| 价格异常 | 单日涨跌幅超过 ±20%（非一字板）且无停牌标记 | 标记可疑，告警 |
| 成交量异常 | 成交量为 0 但无停牌标记 | 标记为数据缺失，告警 |
| 时间序列断裂 | 相邻两条记录间隔超过 5 个自然日（非节假日） | 标记缺口，告警 |
| 字段缺失 | 关键字段（close/volume）为 NaN | 标记缺失，不参与当日扫描 |
| 复权异常 | 复权后价格序列出现跳变（非除权日） | 标记可疑，告警 |

**DR3 — 数据新鲜度检查**

- 每次扫描前检查 D1（行情数据）的最新日期
- 若最新日期落后于上一个交易日，判定为数据过期
- 过期数据的处理：关键域（D1）过期则中止扫描并告警；非关键域（D2/D5）过期则记录日志，不中止

### 2.4 数据存储需求

**DS1 — 存储结构需求**

- 不同更新周期的数据必须物理隔离存储，互不影响（日频更新不触碰季频数据）
- 不同数据源的数据必须可以独立更新（BaoStock 故障不影响 AKShare 数据的更新）
- 支持增量追加（每日只写入新增数据，不重写历史）
- 支持按标的、按时间范围快速查询（不需要全量扫描文件）

**DS2 — 存储可靠性需求**

- 写入操作必须是原子的（写入失败不破坏已有数据）
- 支持断点续传（批量下载中断后，重启时从断点继续，不重复下载已有数据）
- 历史数据一旦写入不允许静默覆盖（需要 `force` 标志显式确认）

**DS3 — 查询性能需求**

- 单只标的全量历史数据读取 < 100ms
- 全市场标的列表查询 < 1s
- 数据统计（各域行数/大小）不需要实时计算，允许缓存

---

## 三、C2 分析判断能力需求

### 3.1 策略信号生成需求

**S1 — 信号的本质定义**

- 策略输出的是**分类信号**（BUY/SELL/WATCH）加**置信度评分**（0~100）
- 不同维度的策略（技术面/基本面/资金面）各自输出独立信号
- 最终由信号合并层将多维度信号合并为一个综合评分

**S2 — 策略维度需求**

| 维度 | 信号来源 | Phase 1 状态 |
|---|---|---|
| 技术面 | 均线/MACD/RSI/量价突破等 | 已有（3个策略） |
| 基本面 | ROE/成长性/估值分位数 | 待开发 |
| 资金面 | 主力净流入/北向资金/融资余额 | 待开发 |
| 宏观面 | PMI/利率/M2 | Phase 2+ |

**S3 — 多维度信号合并需求**

- 同一标的的多个策略信号，按维度加权合并为综合评分
- 合并规则必须可配置（权重可调），不硬编码
- 合并后保留各维度的原始信号和评分，供辅助分析展示
- 多个技术面策略同时看好同一标的，视为共振，综合评分加成

**S4 — 扫描范围与过滤需求**

扫描前必须过滤以下标的（不参与信号生成）：
- ST / *ST 标的（退市风险）
- 北交所标的（流动性差）
- 上市不足 60 个交易日的新股（历史数据不足）
- 当日停牌标的（无法执行）
- 成交额低于阈值的标的（流动性不足，可配置）

### 3.2 回测验证需求

**B1 — 回测的核心目的**

回测服务于四个目的，需求优先级依次降低：
1. 验证策略逻辑正确性（信号生成符合预期）
2. 评估策略在不同市场环境下的收益表现
3. 对比多个策略，选择当前环境下最优组合
4. 验证止损止盈参数的合理性

**B2 — 回测准确性需求**

- 不能有未来数据泄露（信号生成只能使用截止当日的数据）
- 交易成本必须纳入计算（佣金 + 滑点，可配置）
- 复权处理必须正确（除权日前后价格连续）
- 输出指标：总收益率、年化收益率、最大回撤、夏普比率（扣除无风险利率）、胜率、盈亏比

**B3 — 回测结果可信度需求**

- 回测结果必须标注数据覆盖率（实际参与回测的标的数 / 目标标的数）
- 覆盖率低于 80% 时，回测结果标注为"参考价值有限"
- 回测结果持久化存储，供策略历史胜率展示和 Phase 2 Strategy Agent 使用

### 3.3 辅助分析需求

**A1 — 辅助分析的目的**

用户收到信号后需要在 5 分钟内完成初步判断，辅助分析必须提供足够的背景信息，避免用户需要手动查询多个来源。

**A2 — 辅助分析内容需求**

每条信号必须附带以下信息（按决策重要性排序）：

| 优先级 | 信息类别 | 具体字段 | 数据来源 |
|---|---|---|---|
| P0 | 技术面摘要 | 信号触发原因、当日涨跌幅、量比、换手率、近20日价格区间 | D1 |
| P0 | 资金流摘要 | 近5日主力净流入额/占比（个股）、近5日北向净买入 | D3a + D3c |
| P0 | 基本面摘要 | ROE、净利润同比、毛利率、资产负债率、PE/PB当前值 | D2 + D1 |
| P1 | 行业背景 | 所属申万行业、行业近5日涨跌、行业资金流向排名 | D4 + D3b |
| P1 | 情绪因子摘要 | 融资余额近5日变化、融资买入额占成交比 | D7 |
| P1 | 策略历史表现 | 触发策略的历史胜率、盈亏比（来自回测缓存） | 回测结果 |
| P1 | 事件背景 | 当前是否处于事件窗口期（财报季/议息日/减持窗口）、近期相关事件摘要 | D8 |
| P2 | 估值分位数 | 当前 PE/PB 在近5年的历史分位数 | D1 历史数据 |

**A3 — 辅助分析的可用性需求**

- 辅助分析数据缺失时（如基本面数据未更新），必须明确标注"数据不可用"，不展示空白或错误数据
- 辅助分析不做主观判断，只呈现客观数据，判断由用户完成

---

## 四、C3 决策风控能力需求

### 4.1 硬性风控约束

以下约束为硬性规则，任何阶段均不得绕过：

| 约束编号 | 约束内容 | 默认值 | 可配置 |
|---|---|---|---|
| RC1 | 单笔仓位上限（占总资金比例） | 10% | 是 |
| RC2 | 同时持有标的数上限 | 10只 | 是 |
| RC3 | 同一申万一级行业持仓上限（占总仓位比例） | 30% | 是 |
| RC4 | 单笔止损线（从买入价下跌触发） | 7% | 是 |
| RC5 | 总账户最大回撤上限（触发后暂停新开仓） | 15% | 是 |
| RC6 | 标的质量过滤（ST/北交所/新股/停牌） | 强制过滤 | 否 |
| RC7 | 大股东减持窗口期过滤（D8b 中 reduce 类事件有效期内不新开多仓） | 强制过滤 | 是（可配置豁免名单） |
| RC8 | 突发事件熔断（VIX > 30 或 A 股指数单日跌幅 > 3% 时暂停新开仓） | 触发后暂停，人工恢复 | 是（阈值可配置） |

### 4.2 指令单需求

**O1 — 指令单内容**

每条 BUY/SELL 信号生成一份指令单，必须包含：

| 字段 | 计算方式 | 说明 |
|---|---|---|
| 建议价格 | 信号触发日收盘价 | Phase 2 改为实时价 |
| 建议数量 | 按仓位建议和价格计算，取整到100股 | |
| 预计金额 | 价格 × 数量 | |
| 止损价 | 信号触发日最低价 × (1 - 止损比例) | 默认 7%，可配置 |
| 止盈价 | 信号触发日收盘价 × (1 + 止盈比例) | 默认 20%，可配置 |
| 仓位建议 | 占总资金比例，不超过 RC1 | |
| 风控检查结果 | 是否通过 RC1~RC6 全部约束 | 未通过时标注原因 |
| 信号来源 | 触发策略名称 + 触发原因 + 综合评分 | |

**O2 — 指令单生命周期**

- 生成后状态为"待确认"
- 用户确认后状态变为"已确认"，记录确认时间
- 用户取消后状态变为"已取消"，记录取消原因（可选）
- 所有状态变更保留历史记录，不物理删除

### 4.3 执行结果反馈闭环需求

**F1 — 反馈数据需求**

- 用户确认指令单后，系统记录"预期买入价"
- 用户需要能够录入"实际成交价"和"实际成交数量"（Phase 1 手动录入）
- Phase 2 通过券商 API 自动获取成交回报

**F2 — 闭环分析需求**

- 系统自动对比信号触发价与实际成交价的偏差
- 持仓期间每日更新持仓盈亏（基于当日收盘价）
- 平仓后记录完整交易记录（买入价/卖出价/持仓天数/盈亏额/盈亏率）
- 交易记录按策略分类汇总，供策略历史胜率计算使用

---

## 五、C4 执行交互能力需求

### 5.1 Phase 1 — 人工执行辅助

**I1 — 推送通知需求**

- 每次扫描完成后，推送包含信号摘要和辅助分析精简版的通知
- 推送内容必须在移动端可读（企微/Telegram），不超过 2000 字符
- 异常告警独立推送，不与信号混合，告警级别明确（ERROR/WARNING）
- 推送失败时写日志并重试一次，不影响其他渠道

**I2 — 查询交互需求**

- Web 看板：信号列表 + 辅助分析展开 + 指令单管理 + 数据状态
- CLI：所有功能均可通过命令行操作，不强依赖 Web 看板
- 历史信号可按日期回溯查询

### 5.2 Phase 2 — 半自动执行（高层描述）

- 接入券商 API，系统可自动提交订单，但需人工在推送消息中一键确认
- 确认超时（默认 30 分钟）则自动取消，不执行
- D8b 扩展：接入新闻 API，LLM 对公告做 NLP 解析，提取非结构化企业事件（重组/实控人变更等）
- E3 代理熔断升级为 LLM 新闻分类，自动识别突发事件类型并评估影响范围

### 5.3 Phase 3 — 全自动执行（高层描述）

- 在 RC1~RC8 全部约束满足的前提下，系统自主执行下单
- 每笔自动执行的交易实时推送通知
- 提供紧急暂停开关，人工可随时中止自动交易
- 实时新闻流 + LLM Agent 持续监控突发事件，自动触发熔断或仓位调整

---

## 六、验收标准与测试方案

### 6.0 测试分级定义

按执行成本和覆盖范围，测试分为四级：

| 级别 | 名称 | 执行时间 | 依赖 | 触发时机 |
|---|---|---|---|---|
| L1 | 单元测试 | < 1 分钟 | 无外部依赖，全部 mock | 每次 `git commit` 前自动运行 |
| L2 | 集成测试 | < 10 分钟 | 依赖本地已有数据，不走网络 | 每次 `git commit` 前自动运行 |
| L3 | 系统测试 | < 30 分钟 | 允许访问真实数据源（有超时保护） | 重大变更后、每次发布前手动触发 |
| L4 | 验收测试 | 跨多个交易日 | 依赖真实运行环境 | Phase 1 上线前执行一次 |

**场景与级别的对应关系：**

| 变更场景 | 必须通过 | 建议运行 |
|---|---|---|
| 单个 Bug 修复 | L1（相关模块） | L2（相关模块） |
| 系统级 Bug 修复（影响多模块） | L1 全量 + L2 全量 | L3 |
| 新策略开发 | L1（策略单元）+ L2（扫描集成） | L3（回测验证） |
| 新数据类型添加 | L1（store/downloader）+ L2（数据完整性） | L3（真实数据源） |
| 设计/代码重构 | L1 全量 + L2 全量 | L3 + 人工抽查 |
| Phase 1 发布 | L1 + L2 + L3 全量 | L4（连续5个交易日） |

---

### 6.1 L1 单元测试（需求追溯）

每条测试用例标注对应的需求编号，确保需求与测试双向可追溯。

#### 数据存储（对应需求 DS1/DS2）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| UT-DS-01 | `store.save()` 后 `load()` 数据与原始一致 | DS1（增量追加） | 字段值、行数、类型完全一致 |
| UT-DS-02 | 同一标的重复 `save()` 不产生重复行 | DS1（增量追加） | load 后行数不变，无重复日期 |
| UT-DS-03 | `save()` 中途抛异常，已有数据不损坏 | DS2（原子写入） | 异常前的数据完整可读 |
| UT-DS-04 | `force=False` 时跳过已有数据，不覆盖 | DS2（断点续传） | 已有数据 MD5 不变 |
| UT-DS-05 | `force=True` 时覆盖已有数据 | DS2（断点续传） | 新数据替换旧数据 |
| UT-DS-06 | `load(start, end)` 只返回区间内数据 | DS1（按时间查询） | 返回行的日期全部在 [start, end] 内 |
| UT-DS-07 | `last_date()` 在空目录时返回 None | DS1 | 返回值为 None，不抛异常 |
| UT-DS-08 | 不同域的 store 物理路径互不重叠 | DS1（域隔离） | 各域数据目录无交集 |

#### 数据异常检测（对应需求 DR2）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| UT-DR-01 | 单日涨跌幅超 ±20% 且无停牌标记，触发告警 | DR2（价格异常） | 告警日志包含标的代码和异常值 |
| UT-DR-02 | 成交量为 0 且无停牌标记，标记为数据缺失 | DR2（成交量异常） | 该行被标记，不参与当日扫描 |
| UT-DR-03 | 关键字段（close/volume）为 NaN，标记缺失 | DR2（字段缺失） | 该标的不出现在扫描结果中 |
| UT-DR-04 | 时间序列断裂超过 5 个自然日，触发告警 | DR2（序列断裂） | 告警包含断裂起止日期 |

#### 策略信号生成（对应需求 S1/S2/S4）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| UT-ST-01 | 数据行数不足 `requires_bars()`，返回空列表 | S4（数据充分性） | 返回 `[]`，不抛异常 |
| UT-ST-02 | 信号 `signal` 字段只能是 BUY/SELL/WATCH | S1（信号定义） | 非法值时抛 ValueError |
| UT-ST-03 | 信号 `score` 在 0~100 范围内 | S1（评分定义） | score 超范围时抛 ValueError |
| UT-ST-04 | ST 标的不产生 BUY 信号 | S4（标的过滤） | is_st=1 的标的无 BUY 输出 |
| UT-ST-05 | 停牌标的（trade_status=0）不产生信号 | S4（标的过滤） | 停牌标的无任何信号输出 |
| UT-ST-06 | 策略携带 `market_condition` 元数据字段 | AC5（架构兼容） | 字段存在，值为合法枚举 |
| UT-ST-07 | 构造上穿均线数据，MA 策略输出 BUY | S2（技术面信号） | 返回 signal="BUY" 且 score > 0 |
| UT-ST-08 | 构造下穿均线数据，MA 策略输出 SELL | S2（技术面信号） | 返回 signal="SELL" |

#### 信号合并（对应需求 S3）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| UT-MG-01 | 同标的同方向多个信号合并，取最高分 | S3（多维度合并） | 合并后 score = max(原始scores) |
| UT-MG-02 | 合并后原因字段包含所有原始原因 | S3（合并保留信息） | reason 字符串包含各策略原因 |
| UT-MG-03 | 同标的不同方向信号不合并，分别保留 | S3 | BUY 和 SELL 各自独立存在 |

#### 风控约束（对应需求 RC1~RC6）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| UT-RC-01 | 单笔金额超过总资金 RC1 比例，指令单标注违规 | RC1 | order.risk_flags 包含 "RC1" |
| UT-RC-02 | 持仓数已达 RC2 上限，新信号指令单标注违规 | RC2 | order.risk_flags 包含 "RC2" |
| UT-RC-03 | 同行业持仓超 RC3 比例，指令单标注违规 | RC3 | order.risk_flags 包含 "RC3" |
| UT-RC-04 | 止损价 = 最低价 × (1 - stop_loss_ratio) | RC4（止损计算） | 计算值与预期误差 < 0.01% |
| UT-RC-05 | 止盈价 = 收盘价 × (1 + take_profit_ratio) | O1（止盈计算） | 计算值与预期误差 < 0.01% |
| UT-RC-06 | RC1~RC5 参数从配置读取，不硬编码 | AC2（配置驱动） | 修改配置后计算结果随之变化 |

#### 指令单生命周期（对应需求 O2）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| UT-OD-01 | 新建指令单状态为"待确认" | O2 | status == "pending" |
| UT-OD-02 | confirm 后状态变为"已确认"，记录时间戳 | O2 | status == "confirmed"，confirmed_at 非空 |
| UT-OD-03 | cancel 后状态变为"已取消"，历史记录保留 | O2 | status == "cancelled"，记录不删除 |
| UT-OD-04 | 指令单持久化后重新加载，字段完整一致 | O2 + AC4 | 所有字段值与原始一致 |

#### 回测准确性（对应需求 B1/B2）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| UT-BT-01 | 信号生成只使用截止当日数据，无未来泄露 | B2（无未来泄露） | 用 t 日数据生成信号，不包含 t+1 日字段 |
| UT-BT-02 | 交易成本（佣金+滑点）纳入收益计算 | B2（交易成本） | 有成本时 total_return < 无成本时 |
| UT-BT-03 | 夏普比率计算扣除无风险利率 | B2（夏普比率） | sharpe = (年化收益 - 无风险利率) / 年化波动率 |
| UT-BT-04 | 回测结果标注数据覆盖率 | B3（覆盖率） | result.coverage_rate 字段存在且 ∈ [0,1] |
| UT-BT-05 | 覆盖率 < 80% 时结果标注"参考价值有限" | B3 | result.reliability == "limited" |

---

### 6.2 L2 集成测试（需求追溯）

依赖本地已有数据，不走网络，验证跨模块协作。

#### 数据新鲜度检查（对应需求 DR3）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| IT-DF-01 | D1 数据最新日期 = 上一交易日，检查通过 | DR3 | check_data_freshness() 返回 stale=[] |
| IT-DF-02 | D1 数据落后 2 个交易日，检查失败并中止扫描 | DR3（关键域过期） | 扫描未执行，告警已推送 |
| IT-DF-03 | D2 数据落后，检查记录日志但不中止扫描 | DR3（非关键域） | 扫描正常执行，日志含警告 |

#### 扫描流程（对应需求 S4/S3/DR2）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| IT-SC-01 | 扫描结果按综合评分降序排列 | S3（合并评分） | signals[i].score >= signals[i+1].score |
| IT-SC-02 | 扫描结果数量不超过配置的 top_n | S4 | len(signals) <= SCAN["top_n"] |
| IT-SC-03 | ST 标的不出现在扫描结果中 | S4（标的过滤） | 结果中无 is_st=1 的标的 |
| IT-SC-04 | 当日停牌标的不出现在扫描结果中 | S4（标的过滤） | 结果中无 trade_status=0 的标的 |
| IT-SC-05 | 扫描结果持久化到 signals_YYYY-MM-DD.json | AC4（状态持久化） | 文件存在，内容可反序列化为 Signal 列表 |
| IT-SC-06 | 数据缺失标的（关键字段 NaN）不出现在结果中 | DR2（字段缺失） | 构造缺失数据，验证不出现在结果中 |

#### 辅助分析数据聚合（对应需求 A2/A3）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| IT-AA-01 | 信号辅助分析包含 P0 级别全部字段 | A2（P0字段） | 6个P0字段全部非空 |
| IT-AA-02 | 基本面数据缺失时，对应字段标注"数据不可用" | A3（缺失处理） | 字段值为 "N/A"，不为 None 或空字符串 |
| IT-AA-03 | 资金流数据缺失时，对应字段标注"数据不可用" | A3（缺失处理） | 同上 |
| IT-AA-04 | 量比 = 当日成交量 / 近5日均量，计算正确 | A2（技术面摘要） | 误差 < 0.1% |
| IT-AA-05 | 近5日主力净流入 = 近5条 moneyflow 记录之和 | A2（资金流摘要） | 与手动计算结果一致 |

#### 指令单生成（对应需求 O1/RC1~RC6）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| IT-OD-01 | BUY 信号生成指令单，所有字段完整 | O1（字段完整性） | O1 表中全部字段非空 |
| IT-OD-02 | 建议数量取整到 100 股 | O1（数量计算） | quantity % 100 == 0 |
| IT-OD-03 | 预计金额 = 价格 × 数量，误差 < 0.01% | O1（金额计算） | abs(amount - price*qty) < 0.01% |
| IT-OD-04 | 风控检查结果字段包含 RC1~RC6 各项状态 | O1（风控检查） | risk_check 包含6个约束的通过/失败状态 |

---

### 6.3 L3 系统测试（需求追溯）

允许访问真实数据源，有超时保护（单个请求 ≤ 30s），验证端到端流程。

#### 数据源可信度（对应需求 DR1）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| ST-DR-01 | 同一标的同一日期，AKShare 与 BaoStock 收盘价偏差 ≤ 0.1% | DR1（多源交叉） | 抽查10只股票，全部通过 |
| ST-DR-02 | 两源成交量偏差 ≤ 1%，超出时告警 | DR1（多源交叉） | 偏差超标时 warning 日志存在 |

#### 数据更新完整性（对应需求 D1/DS2）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| ST-DU-01 | 增量更新后，新增日期的数据行数与预期一致 | D1（时效性） | 新增行数 = 标的数 × 新增交易日数（允许 ±1%缺失） |
| ST-DU-02 | 增量更新不修改历史数据（MD5 校验） | DS2（原子写入） | 更新前后历史数据 MD5 不变 |
| ST-DU-03 | 断点续传：中断后重启，从断点继续，不重复下载 | DS2（断点续传） | 重启后已下载标的跳过，最终结果与完整下载一致 |

#### 节假日识别（对应需求 D6/F1.1）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| ST-CA-01 | 已知节假日（如春节）不触发更新 | D6（交易日历） | 节假日当天 `_is_trading_day()` 返回 False |
| ST-CA-02 | 调休工作日正确识别为交易日 | D6（交易日历） | 调休日 `_is_trading_day()` 返回 True |

#### 全流程端到端（对应需求 C1→C2→C3→C4）

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| ST-E2E-01 | 指定历史日期运行完整流程：更新→扫描→辅助分析→指令单 | 全链路 | 各环节无异常，输出文件完整 |
| ST-E2E-02 | 扫描结果中随机抽取3条信号，辅助分析数据与数据源一致 | A2（数据准确性） | 手动核对3条，全部字段值正确 |
| ST-E2E-03 | 全市场扫描完成时间 ≤ 120 秒 | PV1（性能） | 计时验证 |
| ST-E2E-04 | 单只标的辅助分析加载时间 ≤ 500ms | PV3（性能） | 计时验证，抽查10只 |

#### 新策略专项（对应需求 S1~S4/B1~B3）

新策略开发完成后，除 L1 单元测试外，必须通过以下系统测试：

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| ST-NS-01 | 新策略在全市场历史数据上运行，无异常抛出 | S2（策略健壮性） | 5000只股票全部处理完，无 exception |
| ST-NS-02 | 新策略回测覆盖率 ≥ 80% | B3（覆盖率） | result.coverage_rate >= 0.8 |
| ST-NS-03 | 新策略回测结果包含全部必要指标 | B2（回测指标） | total_return/max_drawdown/sharpe/win_rate 全部非空 |
| ST-NS-04 | 新策略信号与现有策略信号合并后，排序逻辑正确 | S3（多维度合并） | 合并后综合评分 ≥ 各维度单独评分 |

#### 新数据类型专项（对应需求 DS1~DS3/DR1~DR3）

新增数据域或数据类型后，必须通过以下系统测试：

| 测试ID | 测试描述 | 验证的需求 | 通过条件 |
|---|---|---|---|
| ST-ND-01 | 新数据域与已有域物理路径无交集 | DS1（域隔离） | 目录结构检查通过 |
| ST-ND-02 | 新数据域的 init 和 update 可独立运行，不影响其他域 | DS1（独立更新） | 单独运行 init/update，其他域数据 MD5 不变 |
| ST-ND-03 | 新数据域的 store 接口通过 L1 单元测试全部用例 | DS1/DS2 | UT-DS-01 ~ UT-DS-08 全部通过 |
| ST-ND-04 | 新数据域数据与数据源抽样核对，关键字段误差 ≤ 0.1% | DR1（数据准确性） | 抽查5只标的，全部通过 |

---

### 6.4 L4 验收测试（Phase 1 上线前）

连续观察真实运行环境，不可用自动化替代。

| 测试ID | 测试描述 | 验证的需求 | 通过条件 | 持续时间 |
|---|---|---|---|---|
| AT-01 | 连续5个交易日，全流程无人工干预自动完成 | V1（全流程自动） | 5天全部成功，run_log.json 记录完整 | 5个交易日 |
| AT-02 | 连续5个交易日，每日推送消息准时到达（收盘后30分钟内） | I1（推送时效） | 5天全部在时限内到达 | 5个交易日 |
| AT-03 | 连续5个交易日，辅助分析数据与手动查询结果一致 | A2（数据准确性） | 每天抽查2条信号，共10条全部通过 | 5个交易日 |
| AT-04 | 模拟数据更新失败（断网），告警推送在5分钟内到达 | DR3/I1（异常告警） | 告警内容包含域名和错误信息 | 单次 |
| AT-05 | 节假日当天系统不运行，节后第一个交易日自动恢复 | D6（节假日） | 节假日无日志，节后正常运行 | 跨节假日 |
| AT-06 | 行情数据覆盖率 ≥ 99%，历史深度 ≥ 5 年 | DV1/DV2 | dbstats 输出验证 | 单次 |

---

## 七、架构兼容性约束

以下约束确保 Phase 1 的实现不阻碍 Phase 2/3 的演进，**在 Phase 1 开发时必须遵守**：

| 编号 | 约束 | 原因 |
|---|---|---|
| AC1 | 所有核心逻辑通过 `agent_tools/` 封装，返回结构化 dict，不依赖 stdout | Phase 2 Agent 需要直接调用这些能力 |
| AC2 | 风控约束（RC1~RC6）必须从配置读取，不硬编码 | Phase 2/3 需要动态调整风控参数 |
| AC3 | 指令单确认流程设计为可被程序调用的接口 | Phase 2 Agent 需要接管确认逻辑 |
| AC4 | 每次运行的状态（数据新鲜度/扫描结果/推送结果）持久化到文件 | Orchestrator 需要读取上次运行状态 |
| AC5 | 策略必须携带"适用市场环境"元数据字段（即使 Phase 1 不使用） | Phase 2 Strategy Agent 需要此字段做策略选择 |
| AC6 | 交易记录（含实际成交价/盈亏）存储结构预留券商 API 字段 | Phase 2 接入券商 API 时不需要重构存储结构 |
| AC7 | 数据存储按域物理隔离，各域可独立更新 | 不同更新周期的数据互不影响，Agent 可按需触发单域更新 |
| AC8 | 价格数据存储同时保留原始价和复权价 | Phase 2 实时价接入时，复权因子计算不依赖历史重算 |
| AC9 | D8 事件存储结构预留 Phase 2 NLP 解析字段（source/phase/nlp_confidence） | Phase 1 结构化事件和 Phase 2 NLP 解析事件使用同一存储结构，无需迁移 |

---

## 八、分层决策架构需求（Nested Decision Framework）

> 新增于 2026-06-11，对应阶段一~六落地路径
> 参考：TradingAgents 多 Agent 辩论结构、Qlib Nested Decision 思想

### 8.1 总体设计原则

**DP1 — 混合架构原则**
- 规则引擎负责广度（全市场扫描 5207 只标的，快速、确定性、可回测）
- LLM 负责深度（少量候选标的定性分析、事件解读、辅助研判）
- **LLM 永不进入核心信号生成路径**，确保回测可复现性

**DP2 — 分层约束传递原则**
- 上层输出的是**约束上下文**（decision_context），而非直接信号
- 下层在进入前就被收窄动作空间（如 L2 筛出行业池，L3 只扫该池内标的）
- 约束必须前置，不能"扫完再过滤"

**DP3 — 决策可回流原则**
- 每层决策结果持久化，供下次运行参考（决策日志）
- 下层执行结果可反馈回上层（收益回填、绩效驱动）

**DP4 — 复用现有架构原则**
- 基于现有 LangGraph `WorkflowState` 和条件边机制扩展
- 复用现有 `run_scan` / `filter_signals_by_risk` 接口
- 不引入 Qlib 等外部依赖

### 8.2 三层决策架构定义（多周期配置+择时，2026-06-12 重定位）

> **系统定位：多周期配置 + 择时**（非纯短线）。各层按决策粒度划分，时间周期是正交属性。

```
L1 宏观场景层（Macro Regime Layer，慢变：周/事件级）
  │ 职责：设定市场模式基调 + 总仓位约束 + 配置仓:交易仓资金分配
  │ 输入：宏观指标（bond/PMI/M2/VIX/DXY）+ 重大事件（阶段六接入）
  │ 输出：regime / risk_mode / max_position / allocation（配置仓:交易仓比例）
  │       + 对 L2 的模式约束（l2_weight_config 各维度权重）
  ▼

L2 行业轮动层（Industry Rotation Layer，中变：月/季级）
  │ 职责（双重）：
  │   1. 中长期配置仓决策者：判断行业长/中/短期价值 → 驱动配置仓
  │   2. L3 约束提供者：向 L3 传行业池 + 收窄后的策略风格约束
  │ 输入：L1 约束 + 行业资金流（③）+ 行业估值（②）+ 景气度（①，台阶二）
  │ 三维评分：①景气度 × w1 + ②估值 × w2 + ③资金流 × w3（权重随 regime 动态调整，可配置）
  │ 输出：allowed_industries / industry_detail（含 time_horizon）/ allowed_styles /
  │       industry_horizon_map / allocation（回传给执行层）
  ▼

L3 个股选择层（Stock Selection Layer，快变：日级）
  │ 职责：在 L1+L2 约束内，按 time_horizon 产出两类输出：
  │   · 交易仓（L3-A 技术策略）：BUY/SELL 信号，position_type=trading，time_horizon=short
  │   · 配置仓候选（L3-B 基本面策略）：WATCH 信号，position_type=config，time_horizon=medium/long
  │     经 L2 行业池过滤后，供人工确认进配置仓
  │ 输入：decision_context（行业池、allowed_styles、allowed_time_horizons）
  │ 输出：scan_results（区分交易仓信号和配置仓候选）
  ▼

风控+交易执行（现有流程，仓位分层管理台阶一最小版）
```

**仓位分层模型**：
```
总仓位（L1 管总上限 + 配置仓:交易仓资金分配）
├── 配置仓（长/中期持有）
│   ├── L2 驱动：行业配置（行业 ETF 或行业内组合）
│   └── L3-B 驱动：个股价值/质量投资（WATCH→人工确认→进配置仓）
└── 交易仓（短期波段）
    └── L3-A 驱动：个股技术择时（BUY 信号→按策略逻辑自动管理）
```

### 8.3 L1 宏观场景层需求（阶段一）

**L1-R1 — 结构化 Regime 输出**

`evaluate_market_condition()` 必须输出结构化 dict：
```python
{
  "regime": str,           # crisis / risk_off / balanced / risk_on / volatile
  "risk_mode": str,        # defensive / balanced / aggressive
  "confidence": float,     # 0.0~1.0，判断置信度
  "indicators": {          # 原始指标值（可观测）
    "cn_10y": float,
    "pmi": float,
    "m2_yoy": float,
    "vix": float,
    ...
  },
  "trigger_reason": str    # 人类可读的判定理由
}
```

**L1-R2 — Regime 到 Risk Mode 映射规则**

| Regime 状态 | Risk Mode | 说明 |
|---|---|---|
| crisis（金融危机级别） | defensive | VIX > 40 或 A 股单日跌幅 > 5% |
| risk_off（风险收缩） | defensive | VIX > 30 或 PMI < 48 |
| balanced（震荡） | balanced | 上述条件均不满足 |
| risk_on（风险扩张） | aggressive | PMI > 52 且 VIX < 20 |
| volatile（高波动） | defensive | VIX 20日标准差 > 5 |

**L1-R3 — Decision Context 结构定义（2026-06-12 扩展多周期字段）**

`decision_context` 作为全局约束对象，贯穿三层：
```python
decision_context = {
  # ── L1 输出 ──
  "regime": str,                      # crisis/risk_off/balanced/risk_on/volatile
  "risk_mode": str,                   # defensive/balanced/aggressive
  "max_position": float,              # 总仓位上限（0.0~1.0）
  "allocation": {                     # 配置仓/交易仓资金分配（L1 决定）
    "position_pct": float,            # 配置仓占总仓位（如 0.6）
    "trading_pct": float,             # 交易仓占总仓位（如 0.4）
  },
  "l2_weight_config": {               # L1 传给 L2 的三维权重（随 regime 动态）
    "w1": float, "w2": float, "w3": float,  # 景气/估值/资金流
  },

  # ── L2 输出 ──
  "allowed_industries": list[str],    # 通过的行业代码
  "industry_weights": dict[str, float], # 行业评分（向后兼容保留）
  "industry_detail": dict,            # 每个行业的详细评级（含 time_horizon/景气/估值）
  "industry_horizon_map": dict,       # {行业代码: 推荐 time_horizon}

  # ── 逐层收窄的策略空间约束 ──
  "allowed_styles": list[str],        # 允许的策略风格（L2 按行业特性收窄）
  "allowed_time_horizons": list[str], # 允许的配置周期

  # ── 元数据 ──
  "strategy_keys": list[str],         # L3 按画像匹配产出（见下方说明）
  "timestamp": str,                   # 决策时间戳
}
```

> **strategy_keys 归属修正（2026-06-12）**：早期实现中 `strategy_keys` 由 L1 直接决定（L1 跳过 L2 直连 L3 的层次错位）。
> 重定位后，`select_strategies()` 改为**在 L1+L2 约束空间内按策略能力画像匹配**（见 `docs/strategy.md` profile() + select_strategies 重构）。
> 台阶一先打通约束字段通道（allowed_styles/allowed_time_horizons），L2 的"行业→风格"映射先简单或直通，台阶二灌入智能。

> **占位字段说明**：`industry_detail` 的景气度维度、`industry_horizon_map` 的真实判断台阶一用简单规则（高景气+低估值→long），台阶二用基本面聚合+LLM 升级。

**max_position 按 risk_mode 取值（实现于 `strategy_agent._MAX_POSITION_BY_MODE`）：**

| risk_mode | max_position | 说明 |
|---|---|---|
| defensive | 0.3 | 防御模式，总仓位上限 30% |
| balanced | 0.7 | 均衡模式，总仓位上限 70% |
| aggressive | 1.0 | 进攻模式，可满仓 |

**L1-R4 — 条件边路由需求**

仿照现有 `route_after_check`（orchestrator.py:148），新增 `route_after_macro`：
- 输入：`state["macro_regime"]["risk_mode"]`
- 输出：路由到不同的策略配置节点（defensive_path / balanced_path / aggressive_path）
- 不同路径通过不同 `strategy_keys` 参数传给下层

**L1-R5 — 阶段演进约束**
- 阶段一（P0）：纯规则 regime 判定（基于现有阈值）
- 阶段四（P1）：规则 + LLM 辅助研判（LLM 调整置信度，不替代规则）
- 阶段六（P2）：规则 + LLM 事件解读 + HMM 状态识别（三者融合）

### 8.4 L2 行业轮动层需求（阶段二）

**L2-R1 — 行业筛选数据源需求**

必须使用以下数据：
- Tushare `moneyflow_ind`（行业资金流，按日期分片 `data/tushare/moneyflow_ind/{date}.parquet`，已有 583 个交易日，2024-01~2026-06）
- 申万行业分类（`data/meta/industry_sw.parquet` 已有，31 个申万一级行业，stock_code→industry_code 映射）
- 季频财务数据（`data/fundamental/` 已有，按 code 分片，阶段二可选接入）

**L2-R1a — 行业分类体系对齐（关键约束）**

实测发现两套行业分类体系不一致，必须处理对齐：
- `moneyflow_ind`：东财板块体系（1012 个板块，代码如 `BK1036.DC`，含细分和一级板块），字段 `name` 为板块名、`net_amount` 为净流入额（元）
- `industry_sw`：申万一级体系（31 个行业，代码如 `801010`，名称如"农林牧渔"）

**对齐方案**：用**行业名称精确匹配**——东财板块名包含申万一级同名板块（如"电子""有色金属""通信""银行"等 31 个申万一级行业名均能在东财数据中精确匹配）。L2 取这些同名一级板块的 `net_amount` 做资金流评分，并通过 `industry_sw` 反查申万行业代码输出。

**L2-R2 — 行业评分维度（台阶二完整三维）**

L2 评分 = 景气度①×w1 + 估值②×w2 + 资金流③×w3，权重 `l2_weight_config` 由 L1
按 regime 动态产出（见 L1-R3），从 decision_context 传入，不硬编码。

| 维度 | 数据源（按申万成分股聚合，代码匹配） | 指标 | 方向 |
|---|---|---|---|
| ① 景气度 | fundamental: growth.YOYNI + profit.npMargin | 行业净利同比中位数 + 净利率中位数（各归一后均值） | 越高越好 |
| ② 估值 | market K线 pe_ttm | per-stock PE 历史时序分位 → 行业中位数，取**倒数**（越低估越高分） | 越低越好 |
| ③ 资金流 | moneyflow/stock net_mf_amount | 行业成分股 net_mf_amount 累加 | 越高越好 |

**实现要点**：
- 三维各自先归一化到 0~100，再按 w1/w2/w3 加权求总分
- **缺失维度权重重归一**：若某维度全行业无数据（如 w1=0 或财务缺失），将其权重剔除后对剩余维度重新归一化（L2-R2 容错）
- **估值用历史时序分位而非横截面分位**：每只成分股算自身 PE 在历史区间的分位（low=便宜），行业取中位数。规避银行/科技商业模式差异导致的横截面偏差。受限于 market 域历史长度（2024起约2年），分位偏薄但够用
- **L1/L2/L3 统一走聚合路径**：L1 资金流也改为按申万一级成分股聚合（原东财板块名称匹配路径废弃），三层评分逻辑一致

**L2-R2a — horizon 判断（台阶二升级）**

`industry_detail` 的 `time_horizon` 由三维组合判断（替代台阶一的 score≥60→short 简单规则）：
- 高景气（①≥60）+ 低估值（②≥60）→ `long`（配置仓长期价值）
- 高资金流（③≥60）+ 低景气（①<50）→ `short`（交易仓动量，资金驱动非基本面驱动）
- 其余 → `medium`

| 维度 | 权重（defensive） | 权重（aggressive） | 数据来源 | 阶段二状态 |
|---|---|---|---|---|
| 行业资金流趋势 | w3 | w3 | moneyflow/stock 聚合 net_mf_amount | ✅ |
| 行业景气度 | w1 | w1 | growth.YOYNI + profit.npMargin 聚合 | ✅ |
| 行业估值 | w2 | w2 | pe_ttm 历史时序分位聚合 | ✅ |

> 权重从 decision_context.l2_weight_config 读取（遵循 AC2）。某维度缺失时权重重归一化。

**L2-R3 — 行业池输出约束**
- 必须输出 `allowed_industries: list[str]`（申万一级行业代码，如 `["801080", "801770"]`）
- defensive 模式：取评分 Top 5~7 行业
- aggressive 模式：取评分 Top 8~12 行业
- balanced 模式：取评分 Top 6~10 行业
- 数据缺失（moneyflow_ind 无当日数据）时：返回空 `allowed_industries`，L3 退化为全市场扫描（容错，不中断）

**L2-R4 — 扫描范围前置收窄需求（关键，必须穿透到引擎层）**
- `agent_tools/strategy_tools.run_scan()` 新增 `industry_filter: list[str]` 参数
- `engine/scanner.ScanEngine.scan()` 新增 `industry_filter` 参数
- `scanner._get_filtered_stock_list()` 在构建标的池时，**join industry_sw 筛出仅属于 allowed_industries 的标的**
- **约束必须在标的进入并发扫描前生效**，禁止全市场扫描后再过滤（否则失去分层意义，性能也无改善）
- `industry_filter=None` 时为全市场扫描（向后兼容，遵循 AC13）

**L2-R5 — 申万二/三级行业支持（2026-06 新增）**

需求背景：一级行业（31个）粒度太粗，需支持二级（134个）/三级（346个）做更精细的行业轮动和扫描范围限定。

- **L2-R5a 数据源**：用 Tushare `index_classify(src=SW2021)` 取三级分类树 + `index_member_all()` 取股票三级归属（代码匹配，非名称匹配）
- **L2-R5b 聚合评分**：放弃东财板块名匹配，改为**个股资金流（tushare moneyflow.net_mf_amount）按申万成分股自底向上聚合到目标层级**。`evaluate_industry_rotation(level="L1"/"L2"/"L3")` 按指定层级聚合评分
- **L2-R5c 扫描过滤**：`scanner._get_filtered_stock_list` 支持传入任意层级的行业代码，自动按 `industry_member` 的对应层级 join 过滤
- **L2-R5d 向后兼容**：`level` 默认 "L1"，行为与现有一致；`industry_member` 缺失时回退原 `industry_sw`（仅一级）
- **L2-R5e workflow 入口**：`run_daily_workflow(industry_level=...)` 和 CLI `--industry-level {L1,L2,L3}` 支持指定级别，`--industries` 传对应级别代码

### 8.5 L3 个股选择层需求（阶段一/二）

**L3-R1 — 策略选择需求**

`select_strategies(risk_mode)` 必须根据 risk_mode 返回不同策略组合：

| Risk Mode | 策略组合 | 理由 |
|---|---|---|
| defensive | `fundamental_value` + `etf_momentum`（红利 ETF） | 偏防御、低波动 |
| balanced | 全部内置策略（含 L3-B 基本面） | 不做筛选 |
| aggressive | `ma_cross` + `vol_price_breakout` + `money_flow` | 偏动量、成长 |

**L3-R2 — 扫描输入约束传递**
- `scan_node` 必须从 `decision_context` 读取 `allowed_industries` 和 `strategy_keys`
- 传递给 `run_scan(strategies=..., industry_filter=...)`

### 8.6 决策可观测与回流需求（阶段三/五）

**OB-R1 — 决策日志结构（阶段三补全）**

扩展现有 `log_result_node`（orchestrator.py），输出结构化日志：
```python
{
  "date": str,
  "macro_regime": {...},           # L1 完整输出
  "decision_context": {...},       # 完整约束上下文
  "industry_scores": [...],        # L2 行业评分明细（阶段三补全：从 decision_context 回写）
  "scan_results_summary": {        # L3 扫描摘要
    "total_signals": int,
    "elapsed": float,
    "by_industry": {...},          # 阶段三补全：按行业分组的信号数
    "by_strategy": {...},          # 阶段三补全：按策略分组的信号数
  },
  "node_audit": [...],             # 阶段三新增：每层节点的 input/output 快照（OB-R5）
  "filtered_signals_count": int,   # 最终通过风控的信号数
  "orders_count": int,
  "logs": [...],                   # 现有执行日志
  "error": str | None,
}
```

**阶段三实现说明：**
- `industry_scores`：阶段二的 L2 评分明细已经写入 `decision_context["industry_weights"]`，
  阶段三在 `log_result_node` 把完整评分（含 industry_name/score/排名）落盘到 `industry_scores`
- `scan_results_summary.by_industry` / `by_strategy`：对 `scan_results["signals"]` 按
  申万行业、策略 key 分组计数，便于事后分析"哪类行业/哪个策略贡献了信号"
- `node_audit`：见 OB-R5

**OB-R4 — Checkpoint 断点续跑需求（阶段三，借鉴 TradingAgents）**

分层链路变长（L1→L2→L3→风控→订单），全市场扫描耗时长，崩溃后整轮重来代价大。
要求工作流支持断点续跑：

- `build_workflow(checkpointer=...)` 接受可选 checkpointer 参数
- 持久化方案：优先 `langgraph.checkpoint.sqlite.SqliteSaver`（SQLite 落盘），
  存储位置 `data/cache/checkpoints/workflow.sqlite`
- 依赖未装时（`langgraph-checkpoint-sqlite`）可降级到 `InMemorySaver`（仅进程内，
  不跨进程恢复，但保证逻辑可用），并记录降级日志
- 运行时通过 `config={"configurable": {"thread_id": <date>}}` 标识一次运行
- 中断（任一节点抛异常）后，用相同 thread_id 重跑，应从最后成功 checkpoint 的状态恢复，
  已完成节点不重复执行
- 向后兼容：`checkpointer=None`（默认）时行为与阶段二一致（无 checkpoint，AC14）

**OB-R5 — 每层节点输入输出审计需求（阶段三）**

对应 architecture.md「可观测」原则，每个决策节点持久化决策理由：
- 每个节点执行后，向 `state["node_audit"]` 追加一条快照：
  ```python
  {
    "node": str,           # 节点名（macro_regime / industry_rotation / run_scan / risk_filter ...）
    "timestamp": str,      # 节点完成时间
    "input_summary": {...},  # 该节点关注的输入字段摘要（不全量复制 state）
    "output_summary": {...}, # 该节点产出的关键字段摘要
  }
  ```
- `node_audit` 随 decision_log 一起落盘，可逐节点回溯链路决策依据
- 审计快照只记摘要（非全量 state），避免日志膨胀

**OB-R2 — 收益回填需求（阶段五）**
- 下次运行时，读取上次决策日志
- 根据 `filtered_signals` 中的标的，计算实际收益（raw + alpha vs 沪深300）
- 追加到决策日志：`{"signal_id": ..., "actual_return_5d": ..., "alpha_5d": ...}`

**OB-R3 — 绩效驱动策略选择需求（阶段五）**
- `select_strategies()` 从"静态映射"升级为"查询历史决策日志，按 regime 分组统计策略胜率"
- 优先选择当前 regime 下历史胜率 > 60% 的策略

### 8.7 LLM 接入约束（阶段四/六，2026-06-12 扩展）

> **详细设计见 `docs/agent.md` 第五（新）章 LLM 角色与演进设计**。本节只记录需求约束和验收标准。

**LLM-R1 — 严格禁入核心路径**
- LLM 只能用于：L1 regime 辅助研判、L3 候选标的深度分析、E3 事件解读
- **禁止用于**：L3 信号生成、风控过滤、回测计算

**LLM-R2 — Grounding 强制要求**
- LLM 对数值型指标做定性解读时，**数值一律来自 store 层，禁止 LLM 生成**
- Prompt 必须明确："你的任务是解读给定的数据，不要生成任何数字"

**LLM-R3 — 可降级要求**
- 所有 LLM 调用必须有规则 fallback
- LLM 失败（超时/限流/错误/key 未配置）时，自动回退规则版，记录降级日志

**LLM-R4 — llm_clients 统一适配层（洋葱架构）**
- L0 核心层（`core.py`）：`Provider` 协议 + `OpenAICompatProvider`（DeepSeek/Qwen/GLM/聚合平台如 yunwu.ai）+ `AnthropicProvider`（Claude 官方原生）
- L1 能力层（`client.py`）：`LLMClient` 三级降级链，依赖倒置（Provider 通过构造参数注入）
- L2 集成层（`__init__.py`）：`get_client()` 是唯一读 `config.settings.LLM` 的函数（项目耦合收敛点）
- `api_style` 字段解耦"协议"（openai/anthropic）与"模型"（claude-opus/deepseek-chat）
  - `api_style=openai`：适用 DeepSeek/Qwen/GLM 直连 + yunwu.ai/OpenRouter 等聚合平台
  - `api_style=anthropic`：仅 Anthropic 官方原生（聚合平台访问 Claude 走 openai 分支）

**LLM-R5 — L1 regime LLM 研判约束**
- LLM 只调整 `confidence`（`±confidence_adjust_max`，默认 0.2，可配）
- `regime` / `risk_mode` / `strategy_keys` 永不被 LLM 改变
- 降级时 confidence 用规则原值，workflow 行为与 OFF 状态完全一致

**LLM-R6 — L3 候选 LLM 点评约束**
- 只分析 Top N（`candidate_top_n`，默认 5），控制成本
- 不修改 signal/score/position_type 等字段
- 输出落盘 `decision_log.llm_candidate_reviews`（台阶一不进推送）

**LLM-R7 — 结构化推理强制**
- LLM 输出推理链（证据引用/分析要点/结论），非自由文本
- 便于事后归因和绩效反馈（进化飞轮 L-B 经验记忆库的数据来源）

**LLM-R8 — autonomy_level 可配机制**
- `config.settings.LLM["autonomy_level"]` 全局默认（默认 OFF）
- 支持分层覆盖（每层独立配置 autonomy_level）
- 永远可一键回退 OFF（不管 LLM 多智能，纯规则必须可用）
- 五级：OFF / ADVISORY / ASSIST / COPILOT / AUTO（详见 agent.md）

**AC16 — LLM 调用集中管控**
- 所有 LLM 调用必须经 `llm_clients`，禁止 agents 层直接 import `openai` / `anthropic`

**AC17 — LLM 输出隔离**
- LLM 输出只写咨询性字段（`llm_review` / `llm_candidate_reviews`）
- 不进 decision_context 的约束字段（regime/risk_mode/allowed_industries/strategy_keys 等）

### 8.8 测试需求（阶段一验收）

**NT-L1-01 — L1 宏观层单元测试**
- 输入：模拟宏观指标（VIX=35, PMI=47）
- 期望输出：`regime="risk_off"`, `risk_mode="defensive"`, `confidence > 0.7`

**NT-L1-02 — 条件边路由测试**
- 输入：`state["macro_regime"]["risk_mode"] = "defensive"`
- 期望：路由到 defensive_path，`strategy_keys = ["fundamental_value", "etf_momentum"]`

**NT-L1-03 — Decision Context 贯通测试**
- 运行完整 workflow
- 验证：`scan_node` 收到的 `state["decision_context"]` 含 `regime/risk_mode/allowed_industries`（阶段二后）

**NT-L1-04 — 决策日志持久化测试**
- 运行一次 workflow
- 验证：`data/processed/decision_log_YYYYMMDD.json` 存在且结构完整

### 8.8b 测试需求（阶段三验收）

**NT-L3-01 — Checkpoint 断点恢复测试（OB-R4）**
- mock 一个节点首次执行时抛异常，第二次正常
- 用相同 thread_id 两次 invoke
- 验证：第二次从断点恢复，已成功节点不重复执行，最终能跑完

**NT-L3-02 — Checkpointer 工厂降级测试（OB-R4/AC15）**
- 验证：sqlite 依赖可用时返回 SqliteSaver；不可用时降级 InMemorySaver，不抛异常

**NT-L3-03 — build_workflow 向后兼容测试（AC14）**
- 验证：`build_workflow()`（无参）与 `build_workflow(checkpointer=None)` 均正常编译运行

**NT-L3-04 — decision_log industry_scores 明细测试（OB-R1）**
- 构造含 industry_weights 的 decision_context
- 验证：decision_log 含 `industry_scores` 明细

**NT-L3-05 — scan_results_summary 分组统计测试（OB-R1）**
- 构造含多行业/多策略信号的 scan_results
- 验证：`by_industry` / `by_strategy` 分组计数正确

**NT-L3-06 — node_audit 审计快照测试（OB-R5）**
- 运行 workflow（mock 下层）
- 验证：`node_audit` 含各节点快照，每条有 node/timestamp/input_summary/output_summary

### 8.9 架构兼容性约束（新增）

| 编号 | 约束 | 原因 |
|---|---|---|
| AC10 | `WorkflowState` 新增字段必须 `total=False`（可选字段） | 保证现有 workflow 不因新字段报错 |
| AC11 | 条件边路由函数必须有 default 分支 | 避免新增 regime 状态时 workflow 卡死 |
| AC12 | L2 行业筛选逻辑必须独立封装为 `agents/industry_agent.py` | 便于后续升级为 LLM 驱动或因子模型驱动 |
| AC13 | `run_scan` 的 `industry_filter` 参数必须可选（默认 None=全市场） | 保证现有调用方不受影响 |
| AC14 | `build_workflow` 的 `checkpointer` 参数必须可选（默认 None=无 checkpoint） | 保证现有调用方与测试不受影响，checkpoint 为增强而非强制 |
| AC15 | checkpoint 持久化依赖缺失时必须可降级到 InMemorySaver，不得让 workflow 报错 | 依赖（langgraph-checkpoint-sqlite）可能未装，可靠性加固不能引入新的硬失败 |
| AC16 | 所有 LLM 调用必须经 `llm_clients`，禁止 agents 层直接 import `openai` / `anthropic` | LLM 调用集中管控，便于统一降级、监控和切换 |
| AC17 | LLM 输出只写咨询性字段（`llm_review` / `llm_candidate_reviews`），不进约束字段 | regime/risk_mode/strategy_keys 等核心约束字段由规则产出，LLM 不得覆盖 |

### 8.10 测试需求（阶段四验收）

**NT-L4-01 — llm_clients 无 key 时完全静默（LLM-R3/AC16）**
- 环境：不设置任何 LLM API key（DEEPSEEK_API_KEY / YUNWU_API_KEY / ANTHROPIC_API_KEY 均为空）
- 期望：`get_client()` 返回不可用 client；调用 `chat()` 不抛异常，返回降级标识

**NT-L4-02 — OpenAICompatProvider 协议测试（LLM-R4）**
- mock httpx/openai，构造 `OpenAICompatProvider(api_key="k", base_url="u", model="m")`
- 期望：`chat(messages)` 返回 `{"content": str, "model": str, "provider": str}`

**NT-L4-03 — LLMClient 三级降级链（LLM-R3/LLM-R4）**
- 构造 primary 失败 + fallback 失败的场景（mock raise）
- 期望：最终降级返回 `{"available": False}`，不抛异常，记录降级日志

**NT-L4-04 — llm_review_regime 无 key 时行为与 OFF 一致（LLM-R3/LLM-R5）**
- mock `get_client()` 返回不可用 client
- 调用 `llm_review_regime(env)`
- 期望：`{"llm_available": False}`，confidence 不变，workflow 无感知

**NT-L4-05 — llm_review_regime 正常调用结构（LLM-R5/LLM-R7）**
- mock LLM 返回合法 JSON（agree=True, confidence_adjust=0.1, reasoning="xxx"）
- 期望：输出含 `llm_available/agree/confidence_adjust/reasoning/provider`
- 验证：confidence_adjust 被 clamp 在 `±confidence_adjust_max`（默认 0.2）范围内

**NT-L4-06 — llm_review_regime LLM 不改变 regime/risk_mode（LLM-R5）**
- mock LLM 返回包含试图修改 regime 字段的输出
- 期望：strategy_agent 忽略，regime / risk_mode 保持规则原值

**NT-L4-07 — strategy schema 6 维字段完整（D-4）**
- 对每个内置策略实例化后调用 `.profile()`
- 期望：含 `applicable_regimes/style/applicable_scenario/risk_profile/time_horizon/data_dependency` 6 个字段，且均非空

**NT-L4-08 — Signal 仓位归属字段（D-5）**
- 构造 `Signal(code="000001", name="x", signal="BUY", price=10.0, reason="r")`
- 期望：`position_type="trading"`, `time_horizon="short"`（默认值）

**NT-L4-09 — select_strategies 画像匹配（L3-R1 升级）**
- 输入：`decision_context = {"regime": "risk_on", "risk_mode": "aggressive"}`
- 期望：只返回 `applicable_regimes` 包含 `"risk_on"` 的策略 key

**NT-L4-10 — macro_regime_node llm_review 存入 state（LLM-R5/AC17）**
- mock `llm_review_regime` 返回 `{"llm_available": True, "agree": True, "confidence_adjust": 0.1}`
- 运行 `macro_regime_node`
- 期望：`state["macro_regime"]["llm_review"]` 存在；`state["macro_regime"]["regime"]` 保持规则值

**NT-L4-11 — api_style=anthropic 走 AnthropicProvider（LLM-R4）**
- 构造 `AnthropicProvider(api_key="k", model="claude-3-haiku-20240307")`，mock anthropic SDK
- 期望：`chat()` 走 anthropic client，不走 openai
