# 分层决策架构 — 台阶二交接文档

> 生成时间：2026-06-16
> 用途：跨 session 接续台阶二（数据域重构 + L2 三维评分 + 配置仓约束 + L3-B 基本面策略）实现
> 阅读顺序：本文档 → docs/data_refactor_design.md → docs/requirements.md 第八章 → docs/strategy.md 2.4 节

---

## 一、当前状态（截至 commit 3efa308）

**测试**：369 passed, 3 skipped（含 3 个 integration skip）

**起手确认环境健康**：
```bash
cd /home/wz/alpha量化
venv/bin/python -m pytest tests/ -q --ignore=tests/test_downloader.py
# 期望：369 passed, 3 skipped

# 分层 workflow 验证（快速，指定两个一级行业）
venv/bin/python main.py workflow --industries 801080 801770

# 二级行业验证（个股资金流聚合）
venv/bin/python main.py workflow --industry-level L2 --industries 801081
```

---

## 二、本轮（台阶一收尾 + 阶段四）提交链

| commit | 内容 |
|---|---|
| `ea0d314` | feat: 阶段四 LLM 接入地基（洋葱架构 + 6维画像 + regime研判） |
| `435f637` | feat: 台阶一剩余任务（decision_context多周期字段 + RC7 + WATCH落盘 + 测试补全） |
| `e58ded8` | feat: 分层 workflow CLI入口（可视化 + 行业范围限定） |
| `f395dfe` | feat: 申万二/三级行业支持（L2-R5，Tushare 代码匹配 + 个股资金流聚合） |
| `3efa308` | docs: 数据域重构设计方案（确立按类型分类原则 + moneyflow 独立域） |

---

## 三、台阶一已完成清单（全部 ✅）

- [x] LLM 接入地基（llm_clients 洋葱架构，autonomy_level=OFF 默认静默）
- [x] 策略 6 维能力画像 + select_strategies 画像匹配
- [x] Signal 多周期字段（position_type/time_horizon）
- [x] decision_context 完整多周期字段（allocation/l2_weight_config/allowed_styles/allowed_time_horizons/industry_detail/industry_horizon_map）
- [x] L2→L3 约束通道打通（industry_rotation_node 回填新字段 → select_strategies 重算）
- [x] L3-B WATCH 信号落盘（scan_node → watch_pool）
- [x] RC7 减持窗口期拦截
- [x] 申万二/三级行业支持（分类下载 + L2 聚合评分 + scanner 过滤）
- [x] workflow CLI 可视化
- [x] 测试覆盖：GAP-01/02/03/05/06/35/36 已关闭

---

## 四、台阶二待做（按优先级）

### 4.1 数据域物理重构（P3，第一步，最高优先）✅ 已完成（2026-06-16）

> 完整方案见 `docs/data_refactor_design.md`（P3 执行结果已回填）

- ✅ 新建 `moneyflow` 域（类型名：stock/margin/industry/top_list/market/northbound）
- ✅ 解散 `tushare` 域（删除 store_tushare.py + downloader_tushare.py），`factor` 域重新定位为"计算型因子"
- ✅ **已修复 bug**：`engine/analysis.py:_get_fund_flow_summary()` 改指向 `moneyflow/stock/`，资金流摘要功能恢复
- ✅ 物理迁移 641M 数据 + 删除死占位（tushare/daily, daily_basic, factor/moneyflow）
- ✅ 全回归绿灯（361 passed, 2 skipped）

### 4.2 L2 景气度维度（①）接入 ✅ 已完成（2026-06-16）

- ✅ 三维评分 = 景气度①×w1 + 估值②×w2 + 资金流③×w3（权重从 decision_context.l2_weight_config 传入）
- ✅ 景气度①：成分股 YOYNI（净利同比）+ npMargin（净利率）行业中位数聚合
- ✅ 估值②：per-stock PE 历史时序分位 → 行业中位数（规避横截面行业偏差）
- ✅ 资金流③：moneyflow/stock net_mf_amount 行业累加（原有逻辑）
- ✅ L1/L2/L3 统一走申万成分股聚合路径（东财名称匹配路径废弃）
- ✅ w1 激活（defensive regime: w1=0.4/w2=0.35/w3=0.25）
- ✅ horizon 升级：高景气+低估→long，高资金流+低景气→short，其余→medium
- ✅ 每日缓存（data/cache/l2_scores/）：首次 ~50s，重复 <0.1s
- ✅ 全回归绿灯（409 passed，unit+integration）

### 4.3 allocation 真正约束资金

- 台阶一只传递记录（position_pct/trading_pct），不真正约束
- 台阶二让配置仓/交易仓资金分配在指令单生成时真正生效

### 4.4 L3-B 完整基本面策略

- B1 质量价值（高ROE+合理PE/PB，GARP核心）
- B2 深度价值
- B3 成长筛选
- 输出 signal=WATCH, position_type=config, time_horizon=medium/long
- fundamental_value 是 B1 雏形，需完整化

### 4.5 select_strategies 完整画像匹配

- allowed_styles 由 L2 行业特性收窄（台阶一直通，空列表）
- industry_horizon_map 智能判断（台阶一用 score≥60→short 简单规则）

### 4.6 LLM-R6 L3 候选 LLM 点评

- 只分析 Top N（candidate_top_n，默认5），控制成本
- 不修改 signal/score/position_type 字段
- 输出落盘 decision_log.llm_candidate_reviews（台阶一不进推送）

---

## 五、关键设计决策（已锁定，必须遵守）

| 决策 | 内容 |
|---|---|
| D-2 LLM 融合模式 | LLM 是综合分析师，规则是证据提供者；非分工/非加权 |
| D-3 autonomy_level | 默认 OFF，纯规则可用是底线，永远可一键回退 |
| D-7 台阶一边界 | allocation 只传递不约束、scan 不拆分、L3-B 只落盘不交互（台阶二逐项解除） |
| 数据域分类原则 | 按类型分类，不按来源；moneyflow 独立域 |
| L2 资金流方案 | L1 保持东财板块，L2/L3 用个股资金流按申万成分股聚合（代码匹配，非名称匹配） |
| 流程规范 | 文档先行 → TDD（先红）→ 从底往上 → 验证 → 复查 → 提交 |

---

## 六、关键文件地图

| 文件 | 台阶二可能改动 |
|---|---|
| `data/store_moneyflow.py` | 新建（迁移自 store_tushare 的资金流部分） |
| `data/downloader_moneyflow.py` | 新建（迁移自 downloader_tushare） |
| `data/store_factor.py` | 重新定位为计算型因子 |
| `engine/analysis.py` | 修复 _get_fund_flow_summary bug，改指向 moneyflow 域 |
| `agents/industry_agent.py` | L2 三维评分接入景气度①，数据源改 moneyflow 域 |
| `agents/strategy_agent.py` | allocation 约束逻辑 + allowed_styles 收窄 |
| `strategies/builtin/` | 新增 B1/B2/B3 基本面策略 |
| `trader/assistant.py` | allocation 资金分配约束 |
| `main.py` | init-tushare/update-tushare → init-moneyflow/update-moneyflow |
| `config/settings.py` | candidate_top_n 等 LLM-R6 配置 |

---

## 七、注意事项

- 全市场扫描很慢，验证逻辑用 mock 或指定小范围行业（`--industries`），不跑真实全市场
- LLM 测试全部 mock（不调真实 API）
- autonomy_level=OFF 时整个 workflow 必须与纯规则版行为完全一致
- 数据域物理迁移前先在设计文档确认迁移映射表，迁移 640M+364M 物理数据需谨慎
- 申万三级分类数据已下载（`init-sw-classify`，511 行业 + 5530 股票映射）
