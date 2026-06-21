# Alpha量化 — 需求与测试追溯矩阵

> 需求编号 → 测试用例的完整双向追溯，包含覆盖缺口分析。
> 最后更新：2026-06-11（新增：分层决策架构追溯）
>
> **需求详细描述见 `docs/requirements.md`**
> **测试运行规范见 `docs/testing.md`**

---

## 一、如何使用本文档

- **开发前**：查"需求编号"列，确认该需求有哪些测试用例需要同步实现
- **提交前**：查"变更场景"列，确认需要运行哪些级别的测试
- **发现缺口**：查"覆盖状态"列，`❌ 无覆盖` 的需求是当前测试盲区
- **新增需求**：在本文档补充对应行，同步在 requirements.md 添加需求编号

---

## 二、测试级别说明

| 级别 | 触发时机 |
|---|---|
| L1 单元测试 | 每次 git commit 前自动运行，< 1 分钟 |
| L2 集成测试 | 每次 git commit 前自动运行，< 10 分钟 |
| L3 系统测试 | 重大变更后、发布前手动触发，< 30 分钟 |
| L4 验收测试 | Phase 1 上线前，跨真实交易日观察 |

---

## 三、需求与测试用例追溯矩阵

### 3.1 C1 市场感知 — 数据分类与更新周期

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| D1-范围 | A股/指数/ETF 全市场覆盖 | — | — | ST-DU-01 | AT-06 | ✅ L3+L4 |
| D1-深度 | 历史深度 ≥ 5 年 | — | — | — | AT-06 | ✅ L4 |
| D1-时效 | 收盘后 2 小时内可用 | — | IT-DF-01 | ST-DU-01 | AT-01/AT-02 | ✅ L2+L3+L4 |
| D1-字段 | OHLCV+换手率+PE/PB+isST | UT-DS-01 | IT-SC-03 | — | — | ✅ L1+L2 |
| D1-复权 | 前复权与原始价同时保留 | — | — | — | — | ❌ 无覆盖 |
| D2-时效 | 季报披露后 1 周内更新 | — | — | — | — | ❌ 无覆盖 |
| D2-字段 | ROE/净利率/毛利率/负债率等 | — | IT-AA-01 | — | AT-03 | ✅ L2+L4 |
| D3a-时效 | 个股资金流收盘后当日可用 | — | — | ST-DU-01 | AT-01 | ✅ L3+L4 |
| D3a-字段 | 超大单/大单/中单/小单/主力净流入额占比 | — | IT-AA-05 | — | AT-03 | ✅ L2+L4 |
| D3a-聚合 | 支持当日/近5日/近20日累计净流入查询 | UT-DS-06 | IT-AA-05 | — | — | ✅ L1+L2 |
| D3b-时效 | 行业资金流收盘后当日可用 | — | — | ST-DU-01 | AT-01 | ✅ L3+L4 |
| D3b-字段 | 行业主力净流入额/占比/涨跌幅/排名 | — | IT-AA-01 | — | AT-03 | ✅ L2+L4 |
| D3c-时效 | 市场资金流收盘后当日可用 | — | — | ST-DU-01 | AT-01 | ✅ L3+L4 |
| D3c-字段 | 北向当日/5日/10日净买入、大盘主力净流入 | — | IT-AA-05 | — | AT-03 | ✅ L2+L4 |
| D7-时效 | 融资融券数据收盘后当日可用 | — | — | — | — | ❌ 无覆盖 |
| D7-个股字段 | 融资余额/融资买入额/融券余量/融券卖出量 | — | — | — | — | ❌ 无覆盖 |
| D7-市场字段 | 两融余额合计/融资余额合计/融券余量合计 | — | — | — | — | ❌ 无覆盖 |
| D4-时效 | 每周更新一次 | — | — | — | — | ❌ 无覆盖 |
| D4-字段 | 股票→行业映射、行业指数 | — | IT-AA-01 | — | AT-03 | ✅ L2+L4 |
| D5a-时效-月 | 月度数据（M2/社融/CPI/PMI）发布后 1 周内更新 | — | — | — | — | ❌ 无覆盖 |
| D5a-时效-日 | 日频数据（国债收益率/汇率）收盘后当日可用 | — | — | ST-DU-01 | AT-01 | ✅ L3+L4 |
| D5a-字段-月 | M2同比/社融增量/CPI/PPI/PMI当期值及同比 | — | — | — | — | ❌ 无覆盖 |
| D5a-字段-日 | 国债收益率2Y/10Y、人民币汇率CNY/USD | — | — | — | — | ❌ 无覆盖 |
| D5b-时效-日 | 日频数据（VIX/美元/原油/铜/美股/港股）次日可用 | — | — | — | — | ❌ 无覆盖 |
| D5b-时效-事件 | 美联储利率变动后 3 日内更新 | — | — | — | — | ❌ 无覆盖 |
| D5b-字段 | 美联储利率/美债2Y10Y/VIX/DXY/原油/铜/标普500/恒生 | — | — | — | — | ❌ 无覆盖 |
| D6-覆盖 | A 股交易日历含节假日 | — | — | ST-CA-01/ST-CA-02 | AT-05 | ✅ L3+L4 |
| D8a-内容 | E1 定期事件日历字段完整（宏观/政策/财报季） | — | — | — | — | ❌ 无覆盖 |
| D8a-窗口查询 | get_active_event_windows 返回当日窗口期事件 | UT-AT-01 | — | — | — | ✅ L1 |
| D8b-内容 | E2 企业事件字段完整（reduce/pledge 等） | — | — | — | — | ❌ 无覆盖 |
| D8b-RC7联动 | has_reduce_window 正确识别减持窗口期 | UT-AT-02 | — | — | — | ✅ L1（经 check_reduce_window 工具封装验证） |
| D8c-结构 | E3 突发事件存储结构可读写 | — | — | — | — | ❌ 无覆盖 |

### 3.2 C1 市场感知 — 数据可信度

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| DR1-交叉 | 两源收盘价偏差 ≤ 0.1% 触发告警 | — | — | ST-DR-01 | — | ✅ L3 |
| DR1-成交量 | 两源成交量偏差 ≤ 1% 触发告警 | — | — | ST-DR-02 | — | ✅ L3 |
| DR1-主源优先 | 验证失败以主源为准，记录差异 | — | — | ST-DR-01 | — | ⚠️ 部分覆盖（未验证记录行为） |
| DR2-价格异常 | 涨跌幅超 ±20% 且无停牌标记，告警 | UT-DR-01 | — | — | — | ✅ L1 |
| DR2-成交量异常 | 成交量为 0 且无停牌标记，标记缺失 | UT-DR-02 | IT-SC-06 | — | — | ✅ L1+L2 |
| DR2-字段缺失 | close/volume 为 NaN，不参与扫描 | UT-DR-03 | IT-SC-06 | — | — | ✅ L1+L2 |
| DR2-序列断裂 | 间隔超 5 自然日，告警 | UT-DR-04 | — | — | — | ✅ L1 |
| DR2-复权异常 | 复权后非除权日跳变，告警 | — | — | — | — | ❌ 无覆盖 |
| DR3-关键域过期 | D1 过期则中止扫描并告警 | — | IT-DF-02 | — | AT-04 | ✅ L2+L4 |
| DR3-非关键域过期 | D2/D5a/D5b/D7 过期记录日志不中止 | — | IT-DF-03 | — | — | ⚠️ 部分覆盖（IT-DF-03 仅验证 D2，D5a/D5b/D7 未覆盖） |
| DR3-正常通过 | D1 最新日期=上一交易日，检查通过 | — | IT-DF-01 | — | — | ✅ L2 |

### 3.3 C1 市场感知 — 数据存储

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| DS1-域隔离 | 不同域物理隔离，互不影响 | UT-DS-08 | — | ST-ND-01/ST-ND-02 | — | ✅ L1+L3 |
| DS1-独立更新 | 不同数据源可独立更新 | — | — | ST-ND-02 | — | ✅ L3 |
| DS1-增量追加 | 每日只写入新增数据 | UT-DS-01/UT-DS-02 | — | ST-DU-01 | — | ✅ L1+L3 |
| DS1-按时间查询 | 支持按标的/时间范围查询 | UT-DS-06/UT-DS-07 | — | — | — | ✅ L1 |
| DS2-原子写入 | 写入失败不破坏已有数据 | UT-DS-03 | — | ST-DU-02 | — | ✅ L1+L3 |
| DS2-断点续传 | 中断后从断点继续，不重复下载 | UT-DS-04/UT-DS-05 | — | ST-DU-03 | — | ✅ L1+L3 |
| DS2-防静默覆盖 | 无 force 标志不允许覆盖历史 | UT-DS-04 | — | — | — | ✅ L1 |
| DS3-单只读取性能 | 单只全量历史读取 < 100ms | — | — | — | — | ❌ 无覆盖 |
| DS3-列表查询性能 | 全市场列表查询 < 1s | — | — | — | — | ❌ 无覆盖 |
| DS3-统计缓存 | 统计数据允许缓存，不需实时 | — | — | — | — | ❌ 无覆盖（设计约定，不需测试） |

### 3.4 C2 分析判断 — 策略信号生成

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| S1-信号分类 | 输出 BUY/SELL/WATCH 分类信号 | UT-ST-02 | — | — | — | ✅ L1 |
| S1-评分 | 置信度评分 0~100 | UT-ST-03 | — | — | — | ✅ L1 |
| S1-多维独立 | 不同维度策略各自独立输出 | UT-MG-03 | — | — | — | ✅ L1 |
| S2-技术面 | 均线/MACD/RSI/量价突破信号 | UT-ST-07/UT-ST-08 | IT-SC-01 | ST-NS-01 | AT-03 | ✅ L1+L2+L3+L4 |
| S2-基本面 | ROE/成长性/估值分位数信号 | TestB1QualityValue/TestB2DeepValue/TestB3GrowthPick | ST-NS-02 | — | — | ✅ 台阶二（B1/B2/B3） |
| S2-资金面 | 主力净流入/北向资金信号 | UT-ST-09（money_flow） | ST-NS-02 | — | — | ✅ L1 |
| S3-加权合并 | 多维度按权重合并为综合评分 | UT-MG-01 | IT-SC-01 | ST-NS-04 | — | ✅ L1+L2+L3 |
| S3-合并可配置 | 合并权重可配置，不硬编码 | — | — | — | — | ❌ 无覆盖 |
| S3-保留原始信号 | 合并后保留各维度原始信号 | UT-MG-02 | — | — | — | ✅ L1 |
| S3-共振加成 | 多技术面策略共振时评分加成 | UT-MG-01 | — | — | — | ⚠️ 部分覆盖（未验证加成逻辑） |
| S4-ST过滤 | ST 标的不参与扫描 | UT-ST-04 | IT-SC-03 | — | — | ✅ L1+L2 |
| S4-停牌过滤 | 停牌标的不参与扫描 | UT-ST-05 | IT-SC-04 | — | — | ✅ L1+L2 |
| S4-北交所过滤 | 北交所标的不参与扫描 | — | — | — | — | ❌ 无覆盖 |
| S4-新股过滤 | 上市不足60日不参与扫描 | — | — | — | — | ❌ 无覆盖 |
| S4-成交额过滤 | 成交额低于阈值不参与扫描 | — | — | — | — | ❌ 无覆盖 |
| S4-数据充分性 | 数据不足 requires_bars 返回空 | UT-ST-01 | — | — | — | ✅ L1 |

### 3.5 C2 分析判断 — 回测验证

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| B2-无未来泄露 | 信号只用截止当日数据 | UT-BT-01 | — | — | — | ✅ L1 |
| B2-交易成本 | 佣金+滑点纳入计算 | UT-BT-02 | — | — | — | ✅ L1 |
| B2-复权正确 | 除权日前后价格连续 | — | — | — | — | ❌ 无覆盖 |
| B2-夏普比率 | 扣除无风险利率 | UT-BT-03 | — | — | — | ✅ L1 |
| B2-输出指标 | 总收益/年化/最大回撤/胜率/盈亏比 | — | — | ST-NS-03 | — | ✅ L3 |
| B3-覆盖率标注 | 结果标注数据覆盖率 | UT-BT-04 | — | ST-NS-02 | — | ✅ L1+L3 |
| B3-低覆盖率警告 | 覆盖率 < 80% 标注参考价值有限 | UT-BT-05 | — | — | — | ✅ L1 |
| B3-结果持久化 | 回测结果持久化供胜率展示 | — | — | — | — | ❌ 无覆盖 |
| B1-策略对比 | 多策略对比选择最优组合 | — | — | — | — | ❌ 无覆盖（Phase 2 Strategy Agent） |
| B1-止损止盈验证 | 验证止损止盈参数合理性 | UT-RC-04/UT-RC-05 | — | — | — | ⚠️ 部分覆盖（计算正确性，未验证参数优化） |

### 3.6 C2 分析判断 — 辅助分析

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| A2-技术面摘要(P0) | 触发原因/涨跌幅/量比/换手率/价格区间 | — | IT-AA-01/IT-AA-04 | ST-E2E-02 | AT-03 | ✅ L2+L3+L4 |
| A2-资金流摘要(P0) | 近5日个股主力净流入（D3a）、近5日北向净买入（D3c） | — | IT-AA-01/IT-AA-05 | ST-E2E-02 | AT-03 | ✅ L2+L3+L4 |
| A2-基本面摘要(P0) | ROE/净利润同比/毛利率/负债率/PE/PB | — | IT-AA-01/IT-AA-02 | ST-E2E-02 | AT-03 | ✅ L2+L3+L4 |
| A2-行业背景(P1) | 申万行业/行业近5日涨跌/行业资金流排名（D3b） | — | IT-AA-01 | — | AT-03 | ⚠️ 部分覆盖（字段存在，未验证准确性） |
| A2-情绪因子摘要(P1) | 融资余额近5日变化、融资买入额占成交比（D7） | — | — | — | — | ❌ 无覆盖 |
| A2-事件背景(P1) | 当前是否处于事件窗口期、近期相关事件摘要（D8） | — | — | — | — | ❌ 无覆盖 |
| A2-策略历史表现(P1) | 历史胜率/盈亏比（回测缓存） | — | — | — | — | ❌ 无覆盖 |
| A2-估值分位数(P2) | PE/PB 近5年历史分位数 | — | — | — | — | ❌ 无覆盖（P2，可延后） |
| A3-缺失标注 | 数据缺失时标注"数据不可用" | — | IT-AA-02/IT-AA-03 | — | — | ✅ L2 |
| A3-不做主观判断 | 只呈现客观数据 | — | — | — | — | ❌ 无覆盖（设计约定，不需自动化测试） |

### 3.7 C3 决策风控 — 硬性约束

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| RC1-单笔仓位上限 | 超限时指令单标注违规 | UT-RC-01 | IT-OD-04 | — | — | ✅ L1+L2 |
| RC2-总仓位数上限 | 超限时指令单标注违规 | UT-RC-02 | IT-OD-04 | — | — | ✅ L1+L2 |
| RC3-行业集中度上限 | 超限时指令单标注违规 | UT-RC-03 | IT-OD-04 | — | — | ✅ L1+L2 |
| RC4-单笔止损线 | 止损价计算正确 | UT-RC-04 | — | — | — | ✅ L1 |
| RC5-总账户回撤上限 | 超限时暂停新开仓 | — | — | — | — | ❌ 无覆盖 |
| RC6-标的质量过滤 | ST/北交所/新股/停牌强制过滤 | UT-ST-04/UT-ST-05 | IT-SC-03/IT-SC-04 | — | — | ⚠️ 部分覆盖（ST/停牌已覆盖，北交所/新股未覆盖） |
| RC7-减持窗口期 | 大股东减持窗口期内不新开多仓 | — | — | — | — | ❌ 无覆盖 |
| RC8-突发事件熔断 | VIX>30 或指数单日跌幅>3% 时暂停新开仓 | — | — | — | — | ❌ 无覆盖 |
| AC2-配置驱动 | RC1~RC5 从配置读取不硬编码 | UT-RC-06 | — | — | — | ✅ L1 |

### 3.8 C3 决策风控 — 指令单

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| O1-字段完整性 | 8个必要字段全部存在 | — | IT-OD-01 | — | — | ✅ L2 |
| O1-数量计算 | 取整到100股 | — | IT-OD-02 | — | — | ✅ L2 |
| O1-金额计算 | 价格×数量，误差<0.01% | — | IT-OD-03 | — | — | ✅ L2 |
| O1-止损价计算 | 最低价×(1-止损比例) | UT-RC-04 | — | — | — | ✅ L1 |
| O1-止盈价计算 | 收盘价×(1+止盈比例) | UT-RC-05 | — | — | — | ✅ L1 |
| O1-风控检查结果 | 包含RC1~RC6各项状态 | UT-RC-01~03 | IT-OD-04 | — | — | ✅ L1+L2 |
| O2-初始状态 | 新建为"待确认" | UT-OD-01 | — | — | — | ✅ L1 |
| O2-确认流程 | confirm 后状态变更+时间戳 | UT-OD-02 | — | — | — | ✅ L1 |
| O2-取消流程 | cancel 后状态变更+历史保留 | UT-OD-03 | — | — | — | ✅ L1 |
| O2-持久化 | 持久化后重载字段一致 | UT-OD-04 | — | — | — | ✅ L1 |
| AC3-接口可调用 | 确认流程可被程序调用 | — | — | — | — | ❌ 无覆盖 |

### 3.9 C3 决策风控 — 执行结果反馈闭环

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| F1-预期价记录 | 确认后记录预期买入价 | — | — | — | — | ❌ 无覆盖 |
| F1-实际成交录入 | 支持手动录入实际成交价/数量 | — | — | — | — | ❌ 无覆盖 |
| F2-成交偏差对比 | 自动对比信号价与实际成交价 | — | — | — | — | ❌ 无覆盖 |
| F2-持仓盈亏更新 | 每日更新持仓盈亏 | — | — | — | — | ❌ 无覆盖 |
| F2-平仓记录 | 平仓后记录完整交易记录 | — | — | — | — | ❌ 无覆盖 |
| F2-策略汇总 | 交易记录按策略分类汇总 | — | — | — | — | ❌ 无覆盖 |
| AC6-预留API字段 | 存储结构预留券商API字段 | — | — | — | — | ❌ 无覆盖 |

### 3.10 C4 执行交互

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| I1-推送内容 | 信号摘要+辅助分析精简版 | — | — | ST-E2E-01 | AT-02/AT-03 | ✅ L3+L4 |
| I1-字符限制 | 推送内容 ≤ 2000 字符 | — | — | — | — | ❌ 无覆盖 |
| I1-告警独立 | 异常告警独立推送，不与信号混合 | — | — | — | AT-04 | ⚠️ 部分覆盖（L4 验证，无自动化） |
| I1-推送失败重试 | 失败时重试一次，不影响其他渠道 | — | — | — | — | ❌ 无覆盖 |
| I2-Web看板 | 信号列表+辅助分析+指令单管理 | — | — | — | — | ❌ 无覆盖（手动验证） |
| I2-CLI完整性 | 所有功能可通过CLI操作 | — | — | ST-E2E-01 | AT-01 | ✅ L3+L4 |
| I2-历史回溯 | 历史信号可按日期查询 | — | IT-SC-05 | — | — | ✅ L2 |
| AC4-状态持久化 | 每次运行状态持久化到文件 | — | IT-SC-05 | ST-E2E-01 | AT-01 | ✅ L2+L3+L4 |

### 3.11 架构兼容性约束

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| AC1-Tool封装 | 核心逻辑通过agent_tools封装，返回dict | UT-AT-01~09 | — | ST-E2E-01 | — | ✅ L1+L3（接口契约测试 `tests/test_agent_tools.py` + 端到端验证） |
| AC2-配置驱动 | 风控参数从配置读取 | UT-RC-06 | — | — | — | ✅ L1 |
| AC3-接口可调用 | 指令单确认可被程序调用 | UT-AT-09 | — | — | — | ✅ L1（confirm_order 工具返回结构化 dict + confirmed_at） |
| AC4-状态持久化 | 运行状态持久化 | UT-OD-04 | IT-SC-05 | ST-E2E-01 | AT-01 | ✅ L1+L2+L3+L4 |
| AC5-策略元数据 | 策略携带market_condition字段 | UT-ST-06 | — | — | — | ✅ L1 |
| AC6-预留API字段 | 交易记录预留券商API字段 | — | — | — | — | ❌ 无覆盖 |
| AC7-域隔离 | 数据存储按域物理隔离 | UT-DS-08 | — | ST-ND-01/ST-ND-02 | — | ✅ L1+L3 |
| AC8-双价格存储 | 同时保留原始价和复权价 | — | — | — | — | ❌ 无覆盖 |
| AC9-D8预留字段 | 事件存储预留NLP解析字段 | — | — | — | — | ❌ 无覆盖 |

### 3.12 分层决策架构（阶段一，2026-06-11 新增）

> 对应 `docs/requirements.md` 第八章，测试文件 `tests/test_macro_regime.py`

| 需求编号 | 需求描述 | L1 测试用例 | L2 测试用例 | L3 测试用例 | L4 测试用例 | 覆盖状态 |
|---|---|---|---|---|---|---|
| **L1 宏观场景层** |
| L1-R1 | evaluate_market_condition 结构化输出（regime/risk_mode/confidence/trigger_reason） | NT-L1-01 | — | — | — | ✅ L1 |
| L1-R2 | Regime 到 risk_mode 映射规则（5 种 regime 正确映射） | NT-L1-01b/c/d/e | — | — | — | ✅ L1 |
| L1-R3 | decision_context 结构定义（7 个必须字段） | NT-L1-03a | — | — | — | ✅ L1 |
| L1-R4 | 条件边路由 route_after_macro 逻辑 | NT-L1-02c | — | — | — | ✅ L1 |
| L1-R5 | 阶段演进约束（规则→LLM 辅助→HMM 融合） | — | — | — | — | ❌ 无覆盖（设计约束，非功能性需求） |
| **L2 行业轮动层（阶段二，tests/test_industry_rotation.py）** |
| L2-R1/R1a | 行业资金流数据源 + 东财/申万名称对齐 | NT-L2-01 | — | — | — | ✅ L1 |
| L2-R2 | 行业资金流评分维度 | NT-L2-03 | — | — | — | ✅ L1 |
| L2-R3 | 行业池输出（申万代码，宽度随 risk_mode） | NT-L2-01/02 | — | — | — | ✅ L1 |
| L2-R3-容错 | 数据缺失返回空池，L3 退化全市场 | NT-L2-04 | — | — | — | ✅ L1 |
| L2-R4 | 扫描约束前置穿透引擎层（scanner 标的池收窄） | NT-L2-05/07 | NT-L2-INT | — | — | ✅ L1+L2 |
| L2-R4-透传 | run_scan→scan→_get_filtered_stock_list 透传 | NT-L2-06/08 | — | — | — | ✅ L1 |
| L2-R5 | 申万二/三级行业支持（Tushare 代码匹配 + 个股资金流聚合 + scanner L2/L3 过滤） | NT-L5-01~05 | — | — | — | ✅ L1（`tests/test_sw_classify.py`） |
| **L3 个股选择层** |
| L3-R1 | select_strategies 按 risk_mode 返回策略组合 | NT-L1-02a/NT-L1-02b | — | — | — | ✅ L1 |
| L3-R2 | scan_node 从 decision_context 读取 strategy_keys/allowed_industries | NT-L1-03b | NT-L2-INT | — | — | ✅ L1+L2 |
| **决策可观测与回流（阶段三/五）** |
| OB-R1 | 决策日志结构化输出（含 industry_scores/by_industry/by_strategy/node_audit） | NT-L1-04a/NT-L3-04a/NT-L3-05a/NT-L3-INT | — | — | — | ✅ L1+L3 |
| OB-R2 | 收益回填机制（阶段五） | — | — | — | — | ❌ 无覆盖（Phase 2 待开发） |
| OB-R3 | 绩效驱动策略选择（阶段五） | — | — | — | — | ❌ 无覆盖（Phase 2 待开发） |
| OB-R4 | Checkpoint 断点续跑（SqliteSaver/降级 InMemorySaver/thread_id 恢复） | NT-L3-01a~c/NT-L3-03a~c | NT-L3-REC | — | — | ✅ L1+L2 |
| OB-R5 | 每层节点输入输出审计快照（node_audit） | NT-L3-06a~c | — | — | — | ✅ L1 |
| **架构兼容性约束（新增）** |
| AC10 | WorkflowState 新字段 total=False（向后兼容） | NT-L1-03b | — | — | — | ✅ L1 |
| AC11 | 条件边路由函数必须有 default 分支 | NT-L1-02c | — | — | — | ✅ L1（代码检查） |
| AC12 | L2 行业筛选独立封装为 industry_agent.py | NT-L2-01 | — | — | — | ✅ L1（已交付 agents/industry_agent.py） |
| AC13 | run_scan 的 industry_filter 参数可选（默认 None） | NT-L2-06/08 | — | — | — | ✅ L1（已交付，向后兼容验证） |
| AC14 | build_workflow 的 checkpointer 参数可选（默认 None） | NT-L3-03a~c | — | — | — | ✅ L1 |
| AC15 | checkpoint 依赖缺失时降级 InMemorySaver | NT-L3-02b | — | — | — | ✅ L1 |

**测试用例编号说明（`tests/test_macro_regime.py`）：**
- `NT-L1-01`：`TestMacroRegimeOutput::test_evaluate_market_condition_structure` — 结构化输出验证
- `NT-L1-01b`：`TestMacroRegimeOutput::test_regime_defensive_scenario` — 防御模式映射（VIX=35, PMI=47）
- `NT-L1-01c`：`TestMacroRegimeOutput::test_regime_aggressive_scenario` — 进攻模式映射（VIX=15, PMI=53）
- `NT-L1-01d`：`TestMacroRegimeOutput::test_regime_volatile_by_vix_std` — volatile（VIX 20日标准差>5）
- `NT-L1-01e`：`TestMacroRegimeOutput::test_regime_crisis_by_index_drop` — crisis（A股单日跌幅>5%）
- `NT-L1-02a`：`TestStrategySelection::test_select_strategies_defensive` — 防御策略组合
- `NT-L1-02b`：`TestStrategySelection::test_select_strategies_aggressive` — 进攻策略组合
- `NT-L1-02c`：`TestStrategySelection::test_route_after_macro_logic` — 路由逻辑验证
- `NT-L1-03a`：`TestDecisionContext::test_decision_context_structure` — decision_context 数据契约
- `NT-L1-03b`：`TestDecisionContext::test_workflow_state_extension` — WorkflowState 扩展向后兼容
- `NT-L1-04a`：`TestDecisionLogPersistence::test_log_result_structure` — 决策日志结构
- `NT-L1-04b`：`TestDecisionLogPersistence::test_log_file_naming_convention` — 日志命名规范
- `NT-L1-INT`：`TestWorkflowIntegration::test_full_workflow_with_macro_regime` — 完整 workflow 集成测试（⏭️ Skipped）

**测试用例编号说明（`tests/test_industry_rotation.py`，阶段二）：**
- `NT-L2-01`：`TestIndustryRotationOutput::test_evaluate_industry_rotation_structure` — 输出结构（申万代码）
- `NT-L2-02`：`TestIndustryRotationOutput::test_defensive_fewer_industries_than_aggressive` — 行业池宽度随 risk_mode
- `NT-L2-03`：`TestIndustryRotationOutput::test_high_inflow_industries_ranked_first` — 资金流评分排名
- `NT-L2-04`：`TestIndustryRotationOutput::test_empty_data_returns_empty_pool` — 数据缺失容错
- `NT-L2-05`：`TestScannerIndustryFilter::test_get_filtered_stock_list_with_industry` — 引擎层标的池收窄
- `NT-L2-06`：`TestScannerIndustryFilter::test_industry_filter_none_keeps_all` — None=全市场（向后兼容）
- `NT-L2-07`：`TestScannerIndustryFilter::test_scan_passes_industry_filter` — scan 透传 industry_filter
- `NT-L2-08`：`TestRunScanIndustryFilter::test_run_scan_accepts_industry_filter` — Tool 层透传
- `NT-L2-INT`：L1→L2→L3 mock 集成验证（扫描标的 4951→1704，真实数据手动验证）

**阶段二覆盖缺口：**
- LLM 接入需求（LLM-R1~R3）待阶段四补充测试
- 收益回填与绩效驱动（OB-R2/OB-R3）待阶段五补充测试
- 财务维度行业聚合（L2-R2 可选维度）暂未实现，待数据覆盖率提升后补充

**测试用例编号说明（`tests/test_checkpoint_recovery.py`，阶段三）：**
- `NT-L3-01a`：`TestCheckpointRecovery::test_checkpoint_saves_state_between_invocations` — 状态持久化
- `NT-L3-01b`：`TestCheckpointRecovery::test_checkpoint_recovery_after_node_failure` — 断点恢复（核心验收，mock 节点失败重试）
- `NT-L3-01c`：`TestCheckpointStorageLocation::test_default_checkpoint_path_in_cache` — 存储位置
- `NT-L3-02a`：`TestCheckpointerFactory::test_make_checkpointer_sqlite_available` — SqliteSaver 创建
- `NT-L3-02b`：`TestCheckpointerFactory::test_make_checkpointer_fallback_on_import_error` — 降级 InMemorySaver（AC15）
- `NT-L3-02c`：`TestCheckpointerFactory::test_make_checkpointer_none_returns_none` — None 不启用
- `NT-L3-03a`：`TestBuildWorkflowBackwardCompat::test_build_workflow_no_args` — 无参向后兼容（AC14）
- `NT-L3-03b`：`TestBuildWorkflowBackwardCompat::test_build_workflow_checkpointer_none` — 显式 None（AC14）
- `NT-L3-03c`：`TestBuildWorkflowBackwardCompat::test_build_workflow_with_checkpointer` — 注入 checkpointer
- `NT-L3-REC`：集成验证（NT-L3-01b 含真实扫描，约 50s）

**测试用例编号说明（`tests/test_decision_log_enhanced.py`，阶段三）：**
- `NT-L3-04a`：`TestIndustryScoresDetail::test_industry_scores_from_decision_context` — industry_scores 明细（OB-R1）
- `NT-L3-04b`：`TestIndustryScoresDetail::test_industry_scores_fallback_empty` — 空数据容错
- `NT-L3-05a`：`TestScanResultsSummary::test_scan_summary_by_industry_and_strategy` — by_industry/by_strategy 分组（OB-R1）
- `NT-L3-05b`：`TestScanResultsSummary::test_scan_summary_empty_signals` — 空信号容错
- `NT-L3-05c`：`TestScanResultsSummary::test_scan_summary_industry_join` — 行业 join 验证
- `NT-L3-06a`：`TestNodeAudit::test_audit_helper_structure` — 审计快照结构（OB-R5）
- `NT-L3-06b`：`TestNodeAudit::test_audit_multiple_nodes` — 多节点累积
- `NT-L3-06c`：`TestNodeAudit::test_audit_persisted_in_decision_log` — 审计落盘
- `NT-L3-INT`：`TestDecisionLogFullIntegration::test_decision_log_schema_complete` — 完整 schema 集成验证

**阶段三关键实现说明：**
- checkpoint 持久化用 msgpack，pandas 产出的 numpy.float64 不可序列化，用 `_to_native()` 在状态入 state 前递归转换
- by_strategy 统计通过 reason 文本匹配策略名（粗粒度，因信号已跨策略合并）
- by_industry 统计通过信号 code join industry_sw 获得行业名分组


---

### 3.13 LLM 接入地基（阶段四，2026-06-12 新增）

> 对应 `docs/requirements.md` 第八章 8.7/8.10，测试文件 `tests/test_llm_clients.py` + `tests/test_strategy_schema.py`

| 需求编号 | 需求描述 | 测试用例 | 覆盖状态 |
|---|---|---|---|
| LLM-R1 | 严格禁入核心路径（只研判/点评，不生成信号/风控/回测） | NT-L4-06（regime/risk_mode 不被改）+ AC17 | ✅ L1 |
| LLM-R2 | Grounding 强制（数值来自 store，prompt 禁止 LLM 生成数字） | 代码审查（prompts.py grounding 约束）+ NT-L4-05 | ✅ L1（约束写入 prompt 模板） |
| LLM-R3 | 可降级（超时/限流/key 缺失自动回退规则版） | NT-L4-01/03/04 | ✅ L1 |
| LLM-R4 | llm_clients 洋葱架构（L0 core / L1 client / L2 __init__） | NT-L4-02/03/11 | ✅ L1 |
| LLM-R5 | L1 regime LLM 研判约束（只调 confidence，regime 永不变） | NT-L4-04/05/06/10 | ✅ L1 |
| LLM-R6 | L3 候选 LLM 点评约束（Top N，不改字段，落盘） | TestLLMCandidateReview + TestLLMCandidateReviewNode | ✅ 台阶二 |
| LLM-R7 | 结构化推理强制（推理链非自由文本） | NT-L4-05 | ✅ L1 |
| LLM-R8 | autonomy_level 可配机制（默认 OFF，可回退） | NT-L4-01/04（OFF 行为等价验证） | ✅ L1 |
| AC16 | LLM 调用集中管控（禁止 agents 直接 import openai/anthropic） | 代码审查 + NT-L4-01 | ✅ L1 |
| AC17 | LLM 输出隔离（只写 llm_review，不进约束字段） | NT-L4-06/10 | ✅ L1 |
| D-4 | 策略 6 维能力画像 schema | NT-L4-07 | ✅ L1 |
| D-5 | Signal 仓位归属字段（position_type/time_horizon） | NT-L4-08 | ✅ L1 |
| L3-R1↑ | select_strategies 画像匹配升级 | NT-L4-09 | ✅ L1 |

**测试用例编号说明（`tests/test_llm_clients.py`，阶段四）：**
- `NT-L4-01`：`TestLLMClientNoKey::test_get_client_no_key_silent` — 无 key 静默降级（LLM-R3/R8/AC16）
- `NT-L4-02`：`TestProvider::test_openai_compat_provider_chat` — OpenAICompatProvider 协议（LLM-R4）
- `NT-L4-03`：`TestLLMClient::test_three_level_fallback` — 三级降级链（LLM-R3/R4）
- `NT-L4-04`：`TestRegimeReview::test_llm_review_regime_no_key_equals_off` — 无 key 行为等价 OFF（LLM-R3/R5）
- `NT-L4-05`：`TestRegimeReview::test_llm_review_regime_normal` — 正常研判结构 + clamp（LLM-R5/R7）
- `NT-L4-06`：`TestRegimeReview::test_llm_never_changes_regime` — regime/risk_mode 不被改（LLM-R5/AC17）
- `NT-L4-10`：`TestMacroNodeLLM::test_macro_node_stores_llm_review` — llm_review 存入 state（LLM-R5/AC17）
- `NT-L4-11`：`TestProvider::test_anthropic_provider_chat` — AnthropicProvider 协议（LLM-R4）

**测试用例编号说明（`tests/test_strategy_schema.py`，阶段四）：**
- `NT-L4-07`：`TestStrategyProfile::test_all_builtin_strategies_have_profile` — 6 维画像完整（D-4）
- `NT-L4-08`：`TestSignalPositionFields::test_signal_default_position_fields` — Signal 仓位字段默认值（D-5）
- `NT-L4-09`：`TestSelectStrategiesProfileMatch::test_select_by_regime` — 画像匹配筛选（L3-R1 升级）

**阶段四关键实现说明：**
- llm_clients 洋葱架构：L0（core.py）零项目依赖，Provider 通过构造参数注入；L2（__init__.py）是唯一读 config.settings.LLM 的地方
- `api_style` 解耦协议与模型：openai 分支覆盖 DeepSeek/Qwen/GLM 直连 + yunwu.ai 等聚合平台（含访问 Claude）；anthropic 分支仅 Anthropic 官方原生
- 无 key / autonomy_level=OFF 时，整个 workflow 行为与阶段三完全一致（LLM 仅写咨询字段）
- select_strategies 兼容双签名：旧 `risk_mode=`/`market_condition=` 与新 `decision_context` dict


---

## 四、覆盖缺口汇总

### 4.1 必须补充的测试用例（Phase 1 验收前）

以下需求当前无任何测试覆盖，且属于 Phase 1 P0 功能，**必须在发布前补充**：

| 缺口编号 | 需求编号 | 需求描述 | 建议测试级别 | 建议测试方式 |
|---|---|---|---|---|
| ✅ GAP-01 | RC5 | 总账户回撤超限时暂停新开仓 | L1 | ✅ 已补充（`tests/test_risk_control.py` TestRC5TotalDrawdown） |
| ✅ GAP-02 | S4-北交所过滤 | 北交所标的不参与扫描 | L1+L2 | ✅ 已补充（`tests/test_scanner_filters.py` TestNorthExchangeFilter） |
| ✅ GAP-03 | S4-新股过滤 | 上市不足60日不参与扫描 | L1+L2 | ✅ 已补充（`tests/test_scanner_filters.py` TestNewStockFilter） |
| ✅ GAP-04 | S4-成交额过滤 | 成交额低于阈值不参与扫描 | L1+L2 | ✅ 已补充（scanner `min_amount` 按 `amount`/元 过滤；`tests/test_scanner_filters.py::TestAmountFilter`） |
| ✅ GAP-05 | F1-预期价记录 | 确认指令单后记录预期买入价 | L1 | ✅ 已补充（`tests/test_risk_control.py` TestF1ExpectedPrice） |
| ✅ GAP-06 | F1-实际成交录入 | 支持手动录入实际成交价/数量 | L1+L2 | ✅ 已补充（`tests/test_risk_control.py` TestF1F2Execution） |
| GAP-07 | F2-持仓盈亏更新 | 每日更新持仓盈亏 | L2 | ⏳ 依赖本地行情数据，待补充 |
| GAP-08 | F2-平仓记录 | 平仓后记录完整交易记录 | L1+L2 | 验证交易记录字段完整性和持久化 |
| GAP-09 | B3-结果持久化 | 回测结果持久化供胜率展示 | L1+L2 | 验证持久化文件格式和读取一致性 |
| GAP-10 | AC3-接口可调用 | 指令单确认流程可被程序调用 | L1 | ✅ 已覆盖（UT-AT-09，confirm_order 返回结构化 dict + confirmed_at） |
| GAP-11 | D7-时效 | 融资融券数据收盘后当日可用 | L2 | 依赖本地已有 tushare/margin 数据，验证 last_date |
| GAP-12 | D7-个股字段 | 融资余额/融资买入额/融券余量字段完整 | L1+L2 | store 接口 save/load 往返一致性；IT-AA 补充情绪因子字段验证 |
| GAP-13 | D7-市场字段 | 两融余额合计等市场级字段完整 | L1+L2 | 同上 |
| GAP-14 | A2-情绪因子摘要 | 融资余额近5日变化、占成交比计算正确 | L2 | 构造 margin 数据，验证计算逻辑 |
| GAP-15 | D5a-字段-月 | M2/社融/CPI/PMI 月度字段完整性 | L1+L2 | store 接口往返一致性；验证同比/环比字段非空 |
| GAP-16 | D5a-字段-日 | 国债收益率/汇率日频字段完整性 | L1+L2 | 同上；验证日期序列无断裂 |
| GAP-17 | D5b-字段 | 境外宏观8个指标字段完整性 | L1+L2 | store 接口往返一致性；抽查近5条记录非空 |
| GAP-18 | D5b-时效-事件 | 美联储议息后利率记录在3日内更新 | L3 | 历史议息日期核对，验证数据存在 |
| GAP-31 | D8a-内容 | E1 定期事件日历 CRUD 接口和字段完整性 | L1+L2 | store_events 接口往返一致性；构造测试事件验证窗口期查询 |
| GAP-32 | D8a-窗口查询 | get_active_event_windows 返回当日窗口期事件 | L1 | ✅ 已覆盖（UT-AT-01，`tests/test_agent_tools.py`） |
| GAP-33 | D8b-内容 | E2 企业事件 CRUD 接口和字段完整性 | L1+L2 | store_events 企业事件接口往返一致性 |
| GAP-34 | D8b-RC7联动 | has_reduce_window 正确识别减持窗口期 | L1 | ✅ 已覆盖（UT-AT-02，经 check_reduce_window 工具封装验证） |
| ✅ GAP-35 | RC7-减持窗口期 | 减持窗口期内 BUY 信号指令单标注 RC7 违规 | L1+L2 | ✅ 已补充（`tests/test_risk_control.py` TestRC7ReduceWindow + trader/assistant.py RC7 实现） |
| ✅ GAP-36 | RC8-突发事件熔断 | VIX 超阈值时 filter_signals_by_risk 拦截全部新开仓 | L1 | ✅ 已补充（`tests/test_risk_control.py` TestRC8CircuitBreaker） |
| GAP-37 | D8c-结构 | E3 突发事件存储结构可读写，字段完整 | L1 | store_events 突发事件接口往返一致性 |

**阶段一（2026-06-11）补充：**

| 缺口编号 | 需求编号 | 需求描述 | 建议测试级别 | 状态 |
|---|---|---|---|---|
| ✅ GAP-38 | L1-R1 | evaluate_market_condition 结构化输出 | L1 | ✅ 已补充（NT-L1-01，`tests/test_macro_regime.py`） |
| ✅ GAP-39 | L1-R2 | Regime 到 risk_mode 映射规则 | L1 | ✅ 已补充（NT-L1-01b/c，防御/进攻模式场景测试） |
| ✅ GAP-40 | L1-R3 | decision_context 数据契约 | L1 | ✅ 已补充（NT-L1-03a，结构验证） |
| ✅ GAP-41 | L1-R4 | 条件边路由逻辑 | L1 | ✅ 已补充（NT-L1-02c，route_after_macro） |
| ✅ GAP-42 | L3-R1 | select_strategies 按 risk_mode 选择 | L1 | ✅ 已补充（NT-L1-02a/b，defensive/aggressive） |
| ✅ GAP-43 | OB-R1 | 决策日志结构化输出（阶段三补全 industry_scores/分组/审计） | L1+L3 | ✅ 已补充（NT-L1-04a + NT-L3-04a/05a/INT） |
| ✅ GAP-44 | AC10 | WorkflowState 向后兼容 | L1 | ✅ 已补充（NT-L1-03b，total=False 验证） |
| ✅ GAP-45 | L2-R1~R4 | 行业轮动层需求（资金流+行业分类对齐+行业池+扫描前置） | L1+L2 | ✅ 已补充（NT-L2-01~08，`tests/test_industry_rotation.py`） |
| ✅ GAP-46 | AC13 | run_scan 的 industry_filter 参数（默认 None 向后兼容） | L1+L2 | ✅ 已补充（NT-L2-06/08，scanner+strategy_tools 验证） |
| ✅ GAP-49 | OB-R4 | Checkpoint 断点续跑（SqliteSaver/降级/恢复） | L1+L2 | ✅ 已补充（NT-L3-01~03，`tests/test_checkpoint_recovery.py`） |
| ✅ GAP-50 | OB-R5 | 每层节点输入输出审计快照（node_audit） | L1 | ✅ 已补充（NT-L3-06a~c） |
| ✅ GAP-51 | AC14/AC15 | checkpointer 参数可选 + 依赖缺失降级 | L1 | ✅ 已补充（NT-L3-03a~c/NT-L3-02b） |
| ✅ GAP-47 | LLM-R1~R8/AC16/17 | LLM 接入约束（洋葱架构/降级/禁入核心路径/regime研判/画像匹配） | L1 | ✅ 阶段四补充（NT-L4-01~11，`tests/test_llm_clients.py` + `tests/test_strategy_schema.py`） |
| ⏳ GAP-48 | OB-R2/R3 | 收益回填与绩效驱动策略选择 | L2 | ⏳ 待阶段五补充 |

### 4.2 建议补充的测试用例（质量提升）

以下需求有部分覆盖或属于 P1 功能，建议在 Phase 1 稳定后补充：

| 缺口编号 | 需求编号 | 需求描述 | 建议测试级别 | 说明 |
|---|---|---|---|---|
| GAP-19 | D1-复权 | 前复权与原始价同时保留 | L1+L3 | 验证 store 同时存储两列，L3 验证复权计算正确性 |
| GAP-20 | DR2-复权异常 | 复权后非除权日跳变告警 | L1 | 构造复权异常数据，验证告警触发 |
| GAP-21 | S3-合并可配置 | 合并权重可配置不硬编码 | L1 | 修改权重配置，验证合并结果随之变化 |
| GAP-22 | S3-共振加成 | 多技术面策略共振时评分加成 | L1 | 构造两个策略同时触发场景，验证加成逻辑 |
| GAP-23 | DS3-单只读取性能 | 单只全量历史读取 < 100ms | L3 | 计时测试，抽查10只股票 |
| GAP-24 | DS3-列表查询性能 | 全市场列表查询 < 1s | L3 | 计时测试 |
| GAP-25 | I1-字符限制 | 推送内容 ≤ 2000 字符 | L1 | 验证推送消息构建函数的输出长度 |
| GAP-26 | I1-推送失败重试 | 失败时重试一次不影响其他渠道 | L1 | mock 推送失败，验证重试和渠道隔离 |
| GAP-27 | F2-策略汇总 | 交易记录按策略分类汇总 | L2 | 验证汇总统计的正确性 |
| GAP-28 | AC1-Tool封装 | agent_tools 返回结构化 dict | L1 | ✅ 已覆盖（UT-AT-01~09，`tests/test_agent_tools.py` 接口契约测试） |
| GAP-29 | AC6-预留API字段 | 交易记录预留券商API字段 | L1 | 验证数据结构包含预留字段 |
| GAP-30 | AC8-双价格存储 | 同时保留原始价和复权价 | L1 | 验证 store 存储结构包含两列 |

### 4.3 不需要自动化测试的需求

以下需求属于设计约定或需要人工判断，不适合自动化测试：

| 需求编号 | 需求描述 | 验证方式 |
|---|---|---|
| A3-不做主观判断 | 辅助分析只呈现客观数据 | 代码审查 |
| DS3-统计缓存 | 统计数据允许缓存 | 代码审查 |
| D2/D4/D5a-时效性 | 季报/周/月度更新时效 | L4 运维观察 |
| D5b-时效性 | 境外日频/事件触发更新时效 | L4 运维观察 |
| I2-Web看板 | 看板功能完整性 | 手动验证 |
| I1-告警独立 | 告警不与信号混合 | L4 人工观察 |
| D8c-E3内容 | 突发事件描述准确性（Phase 1 人工记录） | 人工录入后人工校验 |

---

## 五、测试用例索引（按测试ID）

| 测试ID | 所在级别 | 验证的需求 |
|---|---|---|
| UT-DS-01 | L1 | DS1-增量追加、D1-字段 |
| UT-DS-02 | L1 | DS1-增量追加 |
| UT-DS-03 | L1 | DS2-原子写入 |
| UT-DS-04 | L1 | DS2-断点续传、DS2-防静默覆盖 |
| UT-DS-05 | L1 | DS2-断点续传 |
| UT-DS-06 | L1 | DS1-按时间查询 |
| UT-DS-07 | L1 | DS1-按时间查询 |
| UT-DS-08 | L1 | DS1-域隔离 |
| UT-DR-01 | L1 | DR2-价格异常 |
| UT-DR-02 | L1 | DR2-成交量异常 |
| UT-DR-03 | L1 | DR2-字段缺失 |
| UT-DR-04 | L1 | DR2-序列断裂 |
| UT-ST-01 | L1 | S4-数据充分性 |
| UT-ST-02 | L1 | S1-信号分类 |
| UT-ST-03 | L1 | S1-评分 |
| UT-ST-04 | L1 | S4-ST过滤、RC6 |
| UT-ST-05 | L1 | S4-停牌过滤、RC6 |
| UT-ST-06 | L1 | AC5-策略元数据 |
| UT-ST-07 | L1 | S2-技术面 |
| UT-ST-08 | L1 | S2-技术面 |
| UT-MG-01 | L1 | S3-加权合并、S3-共振加成 |
| UT-MG-02 | L1 | S3-保留原始信号 |
| UT-MG-03 | L1 | S1-多维独立 |
| UT-RC-01 | L1 | RC1 |
| UT-RC-02 | L1 | RC2 |
| UT-RC-03 | L1 | RC3 |
| UT-RC-04 | L1 | RC4、O1-止损价计算 |
| UT-RC-05 | L1 | O1-止盈价计算 |
| UT-RC-06 | L1 | AC2-配置驱动 |
| UT-OD-01 | L1 | O2-初始状态 |
| UT-OD-02 | L1 | O2-确认流程 |
| UT-OD-03 | L1 | O2-取消流程 |
| UT-OD-04 | L1 | O2-持久化、AC4 |
| UT-BT-01 | L1 | B2-无未来泄露 |
| UT-BT-02 | L1 | B2-交易成本 |
| UT-BT-03 | L1 | B2-夏普比率 |
| UT-BT-04 | L1 | B3-覆盖率标注 |
| UT-BT-05 | L1 | B3-低覆盖率警告 |
| UT-AT-01 | L1 | D8a-窗口查询、AC1-Tool封装 |
| UT-AT-02 | L1 | D8b-RC7联动、AC1-Tool封装 |
| UT-AT-03 | L1 | update_domain 多域路由、AC7-域隔离 |
| UT-AT-04 | L1 | update_domain 非法域校验、AC1-Tool封装 |
| UT-AT-05 | L1 | update_domain 异常捕获、AC1-Tool封装 |
| UT-AT-06 | L1 | query_kline 摘要结构、AC8-双价格存储 |
| UT-AT-07 | L1 | validate_data 数据完整性校验 |
| UT-AT-08 | L1 | get_pending_orders 封装、AC1-Tool封装 |
| UT-AT-09 | L1 | confirm_order/cancel_order 生命周期、AC3-接口可调用 |
| IT-DF-01 | L2 | DR3-正常通过 |
| IT-DF-02 | L2 | DR3-关键域过期 |
| IT-DF-03 | L2 | DR3-非关键域过期 |
| IT-SC-01 | L2 | S3-加权合并、S2-技术面 |
| IT-SC-02 | L2 | S4 |
| IT-SC-03 | L2 | S4-ST过滤、RC6 |
| IT-SC-04 | L2 | S4-停牌过滤、RC6 |
| IT-SC-05 | L2 | AC4-状态持久化、I2-历史回溯 |
| IT-SC-06 | L2 | DR2-成交量异常、DR2-字段缺失 |
| IT-AA-01 | L2 | A2-技术面摘要、A2-资金流摘要、A2-基本面摘要、A2-行业背景 |
| IT-AA-02 | L2 | A3-缺失标注、A2-基本面摘要 |
| IT-AA-03 | L2 | A3-缺失标注 |
| IT-AA-04 | L2 | A2-技术面摘要 |
| IT-AA-05 | L2 | A2-资金流摘要 |
| IT-OD-01 | L2 | O1-字段完整性 |
| IT-OD-02 | L2 | O1-数量计算 |
| IT-OD-03 | L2 | O1-金额计算 |
| IT-OD-04 | L2 | O1-风控检查结果、RC1、RC2、RC3 |
| ST-DR-01 | L3 | DR1-交叉、DR1-主源优先 |
| ST-DR-02 | L3 | DR1-成交量 |
| ST-DU-01 | L3 | D1-范围、D1-时效、D3a-时效、D3b-时效、D3c-时效、DS1-增量追加 |
| ST-DU-02 | L3 | DS2-原子写入 |
| ST-DU-03 | L3 | DS2-断点续传 |
| ST-CA-01 | L3 | D6-覆盖 |
| ST-CA-02 | L3 | D6-覆盖 |
| ST-E2E-01 | L3 | 全链路、I2-CLI完整性、AC4、I1-推送内容 |
| ST-E2E-02 | L3 | A2-技术面摘要、A2-资金流摘要、A2-基本面摘要 |
| ST-E2E-03 | L3 | PV1-扫描性能 |
| ST-E2E-04 | L3 | PV3-辅助分析性能 |
| ST-NS-01 | L3 | S2-技术面（新策略健壮性） |
| ST-NS-02 | L3 | B3-覆盖率标注 |
| ST-NS-03 | L3 | B2-输出指标 |
| ST-NS-04 | L3 | S3-加权合并 |
| ST-ND-01 | L3 | DS1-域隔离 |
| ST-ND-02 | L3 | DS1-独立更新、DS1-域隔离 |
| ST-ND-03 | L3 | DS1/DS2（新数据域store接口） |
| ST-ND-04 | L3 | DR1-交叉（新数据域准确性） |
| AT-01 | L4 | D1-时效、I2-CLI完整性、AC4 |
| AT-02 | L4 | I1-推送内容、D3a-时效、D3b-时效、D3c-时效 |
| AT-03 | L4 | A2-技术面摘要、A2-资金流摘要、A2-基本面摘要、A2-行业背景、S2-技术面 |
| AT-04 | L4 | DR3-关键域过期、I1-告警独立 |
| AT-05 | L4 | D6-覆盖 |
| AT-06 | L4 | D1-范围、D1-深度 |
