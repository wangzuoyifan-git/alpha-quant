# Alpha量化 — 数据缺口分析与扩展规划

> 从策略因子开发角度，梳理当前已有数据和待补充数据
> 更新时间：2026-05-26

---

## 一、当前已有数据

| 域 | 类型 | 数量 | 时间范围 | 关键字段 |
|---|---|---|---|---|
| market/cn/stock | A股日线 | 5207只 | 2024-01-02~今 | OHLCV + 换手率/PE/PB/PS/PCF/isST |
| market/cn/index | 指数日线 | 541只 | 2024-01-02~今 | OHLCV + pct_chg |
| market/cn/etf | ETF日线 | 1535只 | **⚠️ 仅2026年** | OHLCV + pct_chg |
| ipo_dates.json | 上市日期 | 8624只 | — | ipo_date/type/name |

**ETF 数据问题**：`adjustflag="1"` 导致数据只有 2026 年，需改为 `"3"` 重新下载。

---

## 二、免费可获取（BaoStock，优先级最高）

稳定、无限流、接口清晰，直接扩展 `downloader.py` 即可。

### 2.1 fundamental 域 — 季频财务数据

| 数据 | BaoStock 接口 | 关键字段 | 更新频率 |
|---|---|---|---|
| 盈利能力 | `query_profit_data` | ROE/净利润/净利润同比/净利率 | 季频 |
| 营运能力 | `query_operation_data` | 应收账款周转率/存货周转率 | 季频 |
| 成长能力 | `query_growth_data` | 营收同比/净利润同比/净资产同比 | 季频 |
| 偿债能力 | `query_balance_data` | 资产负债率/流动比率/速动比率 | 季频 |
| 现金流 | `query_cash_flow_data` | 经营/投资/筹资现金流 | 季频 |
| 杜邦分析 | `query_dupont_data` | ROE拆解（净利率×周转率×杠杆） | 季频 |

**支持的策略因子**：价值因子（低 PB/PE）、质量因子（高 ROE/低负债）、成长因子（营收加速）

存储建议：
```
data/fundamental/
  profit/   {code}.parquet    # 盈利能力，季频
  balance/  {code}.parquet    # 资产负债，季频
  cashflow/ {code}.parquet    # 现金流，季频
  growth/   {code}.parquet    # 成长能力，季频
```

### 2.2 meta 域 — 行业分类

| 数据 | BaoStock 接口 | 说明 |
|---|---|---|
| 证监会行业分类 | `query_stock_industry` | 行业代码/名称，每只股票一条记录 |

存储建议：`data/meta/industry_csrc.parquet`

---

## 三、AKShare 限流后可重试获取

需要加退避重试（`time.sleep(2**attempt)`），单次请求量不要太大。

### 3.1 meta 域 — 申万行业分类（行业轮动必需）

| 数据 | AKShare 接口 | 说明 |
|---|---|---|
| 申万行业成分股 | `sw_index_cons` | 申万一/二/三级，含成分股列表 |
| 申万行业指数日线 | `sw_index_daily` | 行业指数涨跌，用于行业轮动 |

存储建议：
```
data/meta/
  industry_sw.parquet      # 申万行业分类（股票→行业映射）
  sw_index/  {code}.parquet  # 申万行业指数日线
```

### 3.2 factor 域 — 资金流向（选股增强信号）

| 数据 | AKShare 接口 | 关键字段 | 粒度 |
|---|---|---|---|
| 个股资金流向 | `stock_individual_fund_flow` | 主力净流入/超大单/大单净额 | 日频 |
| 行业资金流向 | `stock_sector_fund_flow_rank` | 行业主力净流入排名 | 日频 |
| 大盘资金流向 | `stock_market_fund_flow` | 沪深两市主力净流入合计 | 日频 |
| 北向资金 | `stock_hsgt_north_net_flow_in_em` | 陆股通北向净流入 | 日频 |

存储建议：
```
data/factor/
  moneyflow/ {code}.parquet   # 个股资金流向，日频
  moneyflow_sector.parquet    # 行业资金流向，日频
  moneyflow_market.parquet    # 大盘资金流向，日频
  northbound.parquet          # 北向资金，日频
```

### 3.3 宏观数据（跨资产轮动策略）

| 数据 | AKShare 接口 | 关键字段 | 粒度 |
|---|---|---|---|
| 国债收益率 | `bond_zh_us_rate` | 中美10年期国债收益率 | 日频 |
| M2货币供应 | `macro_china_m2` | M2同比增速 | 月频 |
| CPI/PPI | `macro_china_cpi/ppi` | 通胀指标 | 月频 |
| PMI | `macro_china_pmi` | 制造业/非制造业PMI | 月频 |

存储建议：`data/macro/{indicator}.parquet`

### 3.4 ETF 基本信息

| 数据 | AKShare 接口 | 关键字段 |
|---|---|---|
| ETF 规模/份额 | `fund_etf_fund_info_em` | 基金规模/份额/跟踪误差/管理费率 |
| ETF 持仓（前十） | `fund_portfolio_hold_em` | 季频持仓明细，用于判断 ETF 风格 |

### 3.5 融资融券（情绪因子）

| 数据 | AKShare 接口 | 关键字段 |
|---|---|---|
| 融资融券余额 | `stock_margin_sse/szse` | 融资买入额/融券卖出量/余额 | 

---

## 四、Tushare 付费可获取

当前 `settings.py` 已预留 `TUSHARE_TOKEN`，充值积分后可用。

### 4.1 分钟级 K 线（日内策略基础）

| 数据 | 接口 | 积分要求 | 说明 |
|---|---|---|---|
| 1/5/15/30/60分钟K线 | `stk_mins` | 2000积分 | 当前只有日线，分钟线支持更精细止损 |

### 4.2 龙虎榜（游资跟踪）

| 数据 | 接口 | 关键字段 |
|---|---|---|
| 龙虎榜每日明细 | `top_list` | 上榜原因/净买入/买卖席位 |
| 龙虎榜机构席位 | `top_inst` | 机构买卖方向 |
| 游资名录 | `hm_list` | 知名游资席位识别 |

### 4.3 股东数据（机构持仓跟踪）

| 数据 | 接口 | 关键字段 |
|---|---|---|
| 十大流通股东 | `top10_floatholders` | 季频，机构持仓变化 |
| 股东人数 | `stk_holdernumber` | 户均持股，判断筹码集中度 |
| 股权质押 | `pledge_stat` | 质押比例，风险预警 |

### 4.4 港股/美股日线

| 数据 | 接口 | 说明 |
|---|---|---|
| 港股日线 | `hk_daily` | store.py 已支持 hk 市场结构 |
| 美股日线 | `us_daily` | store.py 已支持 us 市场结构 |

---

## 五、其他来源

| 数据 | 来源 | 说明 |
|---|---|---|
| 可转债行情 | AKShare `bond_zh_hs_cov_daily` | 需扩展 store.py 的 asset_type |
| 可转债基本信息 | AKShare `bond_zh_hs_cov` | 转股价/溢价率/到期日 |
| 分析师预期 | Wind/Bloomberg | 需机构授权，个人无法获取 |
| 期权隐含波动率 | AKShare 部分接口 | 数据完整性一般 |

---

## 六、优先级建议

按「开发成本低 + 策略收益大」排序：

| 优先级 | 数据 | 来源 | 支持的策略 |
|---|---|---|---|
| 🔴 P0 | ETF 数据修复 | BaoStock（已有，改参数） | 所有 ETF 策略 |
| 🔴 P0 | 申万行业分类 | AKShare | 行业轮动、ETF 动量轮动 |
| 🟡 P1 | 季频财务数据 | BaoStock（免费稳定） | 价值因子、质量因子、成长因子 |
| 🟡 P1 | 个股资金流向 | AKShare | 主力资金因子，增强选股信号 |
| 🟡 P1 | 北向资金 | AKShare | 外资情绪因子 |
| 🟢 P2 | 宏观数据 | AKShare | 跨资产轮动、择时策略 |
| 🟢 P2 | ETF 规模/持仓 | AKShare | ETF 筛选（排除迷你 ETF） |
| 🟢 P2 | 融资融券 | AKShare/Tushare | 情绪因子、风险预警 |
| 🔵 P3 | 龙虎榜/游资 | Tushare | 短线游资跟踪策略 |
| 🔵 P3 | 分钟K线 | Tushare | 日内策略、精细止损 |
| 🔵 P3 | 港股/美股 | Tushare | 跨市场套利、AH 溢价策略 |

---

## 七、数据域扩展规划

```
data/
  market/          ✅ 已完成（stock/index/etf）
  fundamental/     🟡 待开发（BaoStock 季频财务）
    profit/
    balance/
    cashflow/
    growth/
  meta/            🟡 待开发（行业分类）
    industry_csrc.parquet    证监会行业
    industry_sw.parquet      申万行业
    sw_index/                申万行业指数
    calendar.parquet         交易日历
  factor/          🟢 待开发（日频因子）
    moneyflow/               个股资金流向
    moneyflow_sector.parquet 行业资金流向
    northbound.parquet       北向资金
    margin/                  融资融券
  macro/           🟢 待开发（月频宏观）
    bond_yield.parquet       国债收益率
    m2.parquet               M2
    cpi_ppi.parquet          CPI/PPI
    pmi.parquet              PMI
  event/           🔵 待开发（事件驱动）
    announce/                公告
    lhb/                     龙虎榜
```
