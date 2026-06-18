# HANDOFF — L2行业评分升级 + ETF独立评分体系设计

> 交接日期：2026-06-18
> 上一 session 最新 commit：`a51cadd`（fix: 配置仓策略价格过滤适配hfq口径）
> 工作区状态：干净，仅 `data/portfolio.json` 个人数据未提交（正常）

---

## 一、本 session 已完成的工作（直接续上无需重做）

### 1.1 持仓管理模块（上一 session 完整交付，已commit）

见 `docs/archive/HANDOFF_持仓管理实现_2026-06-18.md`，核心：
- `strategies/position_manager.py`：ATR/Supertrend/仓位/委托价底层函数
- `BaseStrategy.compute_exit_levels()`：策略自描述出场逻辑
- `portfolio` 命令：分级操作表展示，ETF 自动探测，三条出场路径
- `exit-levels` / `position-size` CLI 命令
- 7个内置策略各自特化出场逻辑

### 1.2 本 session 新增（已commit）

| commit | 内容 |
|---|---|
| `6e3d861` | portfolio ETF持仓管理：自动探测 asset_type + 三条出场路径（momentum/industry_allocation/macro_allocation） |
| `30c09d2` | docs: ops.md 更新 ETF 持仓说明 |
| `a51cadd` | fix: 配置仓策略价格过滤 `price>500` 改为 `price<=0`，修复高价股（如宁德时代 hfq≈1万）被误过滤 |

### 1.3 data/portfolio.json 当前状态（未提交，个人数据）

已更新为新格式，7只股票 + 8只ETF待填入：

```json
{
  "total_capital": 2000000,
  "holdings": [
    {"code":"300750","name":"宁德时代","cost":391.03,"shares":200,"position_type":"config","style":"growth"},
    {"code":"000876","name":"新希望","cost":11.405,"shares":2000,"position_type":"trading","style":"momentum"},
    {"code":"601899","name":"紫金矿业","cost":28.545,"shares":5000,"position_type":"config","style":"value"},
    {"code":"601988","name":"中国银行","cost":5.772,"shares":10000,"position_type":"config","style":"value"},
    {"code":"601398","name":"工商银行","cost":7.726,"shares":30000,"position_type":"config","style":"value"},
    {"code":"000426","name":"兴业银锡","cost":61.460,"shares":1000,"position_type":"config","style":"value"},
    {"code":"601600","name":"中国铝业","cost":10.102,"shares":2000,"position_type":"config","style":"value"}
  ]
}
```

**待用户填入的8只ETF**（cost/shares 待填，style/industry 已确认）：

| 代码 | 名称 | style | industry/备注 |
|---|---|---|---|
| 159108 | 博时工业软件ETF | 待定（见二.2节） | 细分主题，动量为主 |
| 159516 | 半导体材料设备ETF | 待定 | 细分主题 |
| 159622 | 创新药ETF沪港深 | 待定 | 细分主题 |
| 159667 | 机床ETF | 待定 | 细分主题 |
| 159703 | 新材料ETF | 待定 | 细分主题 |
| 159934 | 黄金ETF | macro_allocation | 宏观对冲，L1 regime驱动 |
| 512710 | 军工龙头ETF | 待定 | 细分主题 |
| 562590 | 半导体材料设备ETF | 待定 | 细分主题（同159516） |

这8只ETF的 style 待定，原因见下方二.2节。

---

## 二、本 session 讨论的核心议题（未实现，待下一session决策+实现）

### 2.1 当前 L2 行业评分的根本局限（已达成共识）

**现状**：L2 三维评分用 31 个申万 L1 大行业，用成分股聚合。

**问题**：粒度太粗，"电子"行业内半导体设备强、消费电子弱时，信号相互抵消，评分显示"平稳"——这是导致细分主题 ETF 无法被 L2 正确评估的根因。

**共识**：
- L2 评分应升级到申万 L2（134个行业），粒度提升4倍
- ETF 评估不是和 L2 评分并列，而是补充——ETF 是对细分行业/主题的市场定价代理，评估结果应双向影响：向上修正 L2 大行业评分精度，向下指导 L3 在细分主题内选股
- 信息流变为：`L1宏观(5种) → L2行业评分(31个) → 细分主题评分(ETF代理) → L3个股选择`

### 2.2 ETF 出场逻辑的四个维度（已达成共识）

对于细分主题 ETF（工业软件/半导体/机床/创新药等），单纯用动量或申万 L1 行业评分都过于简单化。合理的四个维度：

**维度1：技术面（动量/趋势）**
- 用 ETF 自身价格序列（而非成分股聚合）
- 指标：绝对动量（N日涨幅 vs 无风险利率）、相对动量（vs 沪深300）、趋势强度（ADX）
- 数据：本地 `market/cn/etf/` 已有，**完全可行**

**维度2：行业基本面/估值**
- ETF 隐含 PE 历史分位（成分股加权 PE）
- 数据瓶颈：`fund_portfolio`（ETF成分股）Tushare 无权限；`sw_daily`（申万行业PE）Tushare 无权限
- 替代方案：a) 升级 Tushare 积分（干净）；b) 人工维护 ETF 对应指数的 PE 分位（手工）；c) 用 L2 成员股 pe_ttm 自己聚合（对细分ETF不够精确）
- **待决策**：见三.1

**维度3：宏观/政策周期**
- 细分主题受政策驱动大（军工/创新药/半导体），当前 L1 regime 粒度不够
- 设计方案：在 `data/etf_config.json` 里为每只 ETF 手工维护 `policy_outlook`（positive/neutral/negative），系统直接消费
- **待决策**：见三.2

**维度4：估值**
- 与维度2部分重叠，独立列出是因为 ETF 的估值判断和个股逻辑不同
- 关键阈值：ETF 隐含 PE 历史分位 > 80% 开始减配

### 2.3 Tushare 接口可用性（已验证）

| 接口 | 用途 | 状态 |
|---|---|---|
| `daily` | 个股K线 | ✓ 正常 |
| `index_classify(L2)` | 申万L2分类（134个） | ✓ 正常 |
| `index_member` | 申万L2成员股 | ✓ 正常 |
| `index_daily` | 申万指数日线 | ✓ 正常 |
| `fund_basic` | ETF基本信息（2104只） | ✓ 正常 |
| `fund_daily` | ETF日线行情 | ✓ 正常 |
| `moneyflow_ind_dc` | 东财行业资金流 | ✗ 无权限 |
| `sw_daily` | 申万行业行情（含PE/PB） | ✗ 无权限 |
| `fund_portfolio` | ETF成分股 | ✗ 无权限 |

**关键结论**：
- L2 行业评分升级到申万 L2（134个）：完全可行，估值用成员股 pe_ttm 自己聚合
- ETF 估值维度：有硬缺口，需决策（升级积分 or 人工维护）

---

## 三、待决策点（新 session 开始时需用户确认）

### 决策1：ETF 估值数据的获取方式

**选项A（推荐）**：升级 Tushare 积分解锁 `fund_portfolio` + `sw_daily`
- 优点：数据干净，一次性解决，申万L2的官方PE/PB也能用
- 需要确认：升级成本、当前积分数量

**选项B**：人工维护 ETF 对应指数的当前 PE 分位
- 在 `data/etf_config.json` 里手填 `pe_percentile_now`（季度更新一次）
- 优点：不依赖积分，立即可用
- 缺点：需要手工维护，时效性差

**选项C（过渡）**：先用 L2 成员股 pe_ttm 聚合作为行业估值代理，ETF 估值暂缺，后续有数据再补
- 对于申万 L2 行业评分升级够用
- 对于细分主题 ETF 不够精确，但可以先做技术面 + 政策面两个维度

### 决策2：政策配置的管理方式

**选项A**：独立文件 `data/etf_config.json`（推荐）
- 覆盖所有 ETF，scan 和 portfolio 都能消费
- 格式：`{"512710": {"policy_outlook": "positive", "policy_tags": ["军工", "国防预算"], "note": "国防预算增加背景下看好"}}`

**选项B**：放在 `portfolio.json` 里
- 只管自己持仓的，更轻量
- 缺点：scan 命令扫全市场 ETF 时无法消费

### 决策3：改造的实施顺序

建议顺序（需确认）：
1. **先做 L2 行业评分升级（申万L1→L2）**：数据全部可用，改动相对独立
2. **再建 ETF 独立评分体系**：依赖决策1的数据方案
3. **最后联动 L3 选股**：细分主题强势时在该主题内优先选股

---

## 四、实现路径预估（供新 session 参考）

### L2 行业评分升级（申万L1→L2）

**改动范围**：
- `data/downloader_meta.py`：下载申万 L2 分类 + 成员股（`index_classify(L2)` + `index_member`）
- `data/store_meta.py`：`SW_L1_ETF_MAP` 补充 L2 映射，`get_member_stocks` 支持 L2
- `agents/industry_agent.py`：`evaluate_industry_rotation` 支持 level='L2'（代码已支持参数，但数据层需补充）
- `data/moneyflow/`：东财 BK 行业资金流需确认 L2 覆盖率（当前约95%，需核实哪些 L2 行业有缺口）

**测试验证**：
```bash
venv/bin/python3 main.py workflow --industry-level L2  # L2粒度的行业轮动评估
```

### ETF 独立评分体系（新模块）

**新增文件**：
- `data/etf_config.json`：ETF 政策配置（手工维护）
- `data/store_etf.py` 或扩展 `store_meta.py`：ETF 基本信息 + 跟踪指数
- `agents/etf_agent.py`（新）：ETF 三维评分（技术动量 + 估值分位 + 政策配置）

**portfolio.json 中 ETF 的 style 更新**：
待 ETF 评分体系建成后，8只细分主题 ETF 的 style 改为 `theme_etf`（新增），出场逻辑走 `etf_agent` 的综合评分。

---

## 五、关键文件位置

| 文件 | 当前状态 | 下一步 |
|---|---|---|
| `agents/industry_agent.py` | L1三维评分，已有 level 参数 | 补充 L2 数据支撑 |
| `data/store_meta.py` | SW_L1_ETF_MAP，get_member_stocks(L1/L2/L3) | 补充 L2 成员股下载 |
| `data/downloader_meta.py` | 下载 L1 分类+成员股 | 扩展到 L2 |
| `data/portfolio.json` | 7只股票（已更新），8只ETF待填 | 填入ETF cost/shares |
| `docs/position_management.md` | 持仓管理设计文档 | 无需改动 |
