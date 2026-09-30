# Alpha量化 — 数据层文档

> 数据层接口、各域结构、数据源说明、待办缺口。
> 最后更新：2026-09-29（events E2 新增回购 `buyback`（Tushare `repurchase`）；moneyflow 域资金主体数据 `top_inst`/`block_trade`/`hk_hold`）
> 2026-09-28（moneyflow 域新增资金主体数据 `top_inst` 龙虎榜席位明细 / `block_trade` 大宗交易 / `hk_hold` 北向个股持股；下载器 `init_top_inst`/`init_block_trade`/`update_hk_hold`）
> 2026-09-23（港股数据批次1：资金流 `update-hk-moneyflow`、财务 5 表 `update-hk-financials`、分红事件 `update-hk-events`、PCF 估值；store_moneyflow/store_fundamental 增加 market 维度；A股 pcf_ttm 补充 `update-cn-pcf`）
>
> 2026-09-22（macro 域修复：`update_db()` 补齐汇率/境外指标 + 新增 `update-macro` CLI；换源 CPI/PPI→东财、社融→Tushare `sf_month`、汇率→外管局中间价、DXY→东财 100.UDI 直连、WTI→新浪外盘 CL，修复 oil_wti 误存黄金数据；补齐 `copper_lme`（新浪 CAD，LME 铜）与 `fed_rate`（东财数据中心利率目标），macro 14 项全部落地。）
>
> 2026-08-13（新增港股 `init-hk / update-hk` 下载器 `data/downloader_hk.py`，优先支持港股通标的；Tushare `hk_daily` 账号限频 1次/小时 → 主用 AKShare Sina 源。）
>
> 2026-06-21 补充：新增 store_meta.map_to_l1 接口；记录 ETF 估值 Tushare 权限缺口（sw_daily/fund_portfolio）与过渡方案；新增 data/etf_config.json 说明。
>
> **项目基础信息见 `docs/project.md`**
> **数据需求详细定义（D1~D7 含需求来源、字段要求、时效要求）见 `docs/requirements.md` 第二章**

---

## 一、当前数据库状态

```
data/market/cn/                       ← 主数据（Tushare hfq 口径，2026-06-17 全量重建）
  stock/        5531只   318万行   289MB   字段：OHLCV+换手率+PE/PB/PS+isST
  index/         760只    38万行    32MB   字段：OHLCV+pct_chg
  etf/          2032只    93万行    57MB   字段：OHLCV+pct_chg（不复权）
  ipo_dates.json         8957只上市日期+行业+名称索引

data/market/hk/                        ← 港股（2026-08 新增，AKShare Sina 源，不复权）
  stock/        N只                 字段：OHLCV+preclose+pct_chg（volume=股/amount=元HKD）
  ipo_dates.json（hk:* 键）         含 hk_connect 标志（是否港股通标的）

data/market_baostock/cn/              ← BaoStock 历史基线（静态快照，交叉验证用，不再更新）
  stock/        5208只   baostock hfq 口径（2026-06-16 备份）

data/fundamental/
  profit/       5207只   季频盈利数据（2024Q1起）
  balance/      4819只   季频资产负债
  cashflow/     5207只   季频现金流
  growth/       5207只   季频成长能力
  dupont/       5207只   季频杜邦分析
  hk/                    港股财务（东财：三大报表 + 成长/杜邦指标，按 code 分片）

data/meta/
  industry_classify.parquet  申万分类树（L1=31 / L2=134 / L3=346，SW2021，Tushare）
  industry_member.parquet    股票→三级行业映射（5530只，Tushare index_member_all）
  industry_csrc.parquet      证监会行业分类
  industry_sw.parquet        申万行业分类（旧版，向后兼容）
  sw_index/                  申万行业指数日线
  calendar.parquet           交易日历

data/factor/
  （计算型因子域，台阶二前暂空。）

data/macro/
  国内宏观（D5a）：
  bond_yield.parquet     中外国债收益率2Y/5Y/10Y/30Y（含美债字段，日频）
  cny_usd.parquet        人民币汇率CNY/USD（日频）
  m2.parquet             M2货币供应同比（月频）
  shrongji.parquet       社融规模增量/存量同比（月频）
  cpi_ppi.parquet        CPI/PPI同比（月频）
  pmi.parquet            PMI制造业/非制造业（月频）
  境外宏观（D5b）：
  fed_rate.parquet       美联储基金利率目标（上限/下限，事件触发）
  us_bond_yield.parquet  美债收益率2Y/5Y/10Y/30Y（派生自 bond_yield，日频）
  vix.parquet            VIX恐慌指数（VIX期货代理，日频）
  dxy.parquet            美元指数DXY（日频）
  oil_wti.parquet        WTI原油价格（日频）
  copper_lme.parquet     LME铜价（新浪 CAD，美元/吨，日频）
  sp500.parquet          标普500指数（日频）
  hsi.parquet            恒生指数（日频）

data/moneyflow/
  stock/                 个股资金流（Tushare，2010起，按code分片）
  industry/              行业资金流（东财 BK 体系，按date分片，583个交易日）
  market.parquet         大盘资金流（Tushare 东财，单文件）
  northbound.parquet     北向资金（AKShare，日频，2024起）
  margin/                融资融券明细（Tushare，按code分片）
  top_list/              龙虎榜汇总（Tushare，按date分片）
  top_inst/              龙虎榜席位明细（Tushare top_inst，按date分片；资金主体识别）
  block_trade/           大宗交易（Tushare block_trade，按date分片；大宗买方）
  hk_hold/               北向个股持股（Tushare hk_hold，按code分片；普通积分账户季度披露）
  hk/stock/              港股个股资金流（东财，主力/超大/大/中/小单净额+占比，滚动约100个交易日）

data/events/
  calendar.json          E1 定期事件日历
  corporate/             E2 企业事件（按code分片）
  market_shock/          E3 突发事件归档

data/etf_config.json     ← ETF 元数据与政策配置（手工维护，可进 git）
                            名称/跟踪指数/sw_l2_codes映射/政策展望/标签，供 etf_agent 三维评分

data/smart_money/        ← 资金主体/席位字典（生成物多已 gitignore）
  seat_aliases.json        席位别名字典（手工维护，可进 git）
  seat_labels.json         席位硬标签（由 xlsx 导入，gitignore，可重建）
  seat_dictionary.json     席位字典主表（硬标签+软标签，gitignore，可重建）
  seat_dictionary.csv      席位字典 CSV（gitignore）
  seat_dictionary_report.md 构建报告（gitignore）
  seats.json               席位累计画像档案（gitignore）
```

**行业资金流双体系说明：**
- `data/meta/industry_classify` + `industry_member`：申万 SW2021（L1/L2/L3），用于行业归属查询
- `data/moneyflow/industry`：东财 BK 板块资金流（1013个），名称覆盖申万L1全部31个、L2约95%（127/134）、L3约97%（334/346）
- 缺资金流的申万 L2（7个）：国有大型银行Ⅱ/股份制银行Ⅱ/城商行Ⅱ/农商行Ⅱ/其他银行Ⅱ/本地生活服务Ⅱ/社交Ⅱ（东财将银行合并为单一板块）

---

## 二、存储接口

所有 store 模块遵循统一接口风格，Parquet + snappy 压缩。

### 2.1 market 域（data/store.py）

```python
import data.store as store

# 读写
store.save(code, df, asset_type="stock", market="cn") -> None
store.load(code, asset_type="stock", market="cn",
           start=None, end=None) -> Optional[DataFrame]
store.exists(code, asset_type="stock", market="cn") -> bool
store.last_date(code, asset_type="stock", market="cn") -> Optional[str]
store.list_codes(asset_type="stock", market="cn") -> List[str]
store.stats() -> dict  # {"cn": {"stock": {"count", "rows", "size_mb"}, ...}, "total": {...}}

# 上市日期（key 格式 "cn:600519"）
store.load_ipo_dates() -> Dict[str, dict]
store.save_ipo_dates(ipo: Dict[str, dict]) -> None
store.get_ipo_date(code, market="cn") -> Optional[str]
store.trading_days_since_ipo(code, market="cn", as_of=None) -> Optional[int]
```

A股字段：`open, high, low, close, preclose, volume, amount, turn, trade_status, pct_chg, pe_ttm, pb_mrq, ps_ttm, pcf_ttm, is_st`

指数/ETF字段：`open, high, low, close, preclose, volume, amount, pct_chg`

港股字段（`market="hk"`）：`open, high, low, close, preclose, volume, amount, pct_chg, pe_ttm, pb_mrq, pcf_ttm`
- `volume` 单位股、`amount` 单位 HKD 元，直接存储（Tushare hk_daily / AKShare 均为该口径，勿照搬 A 股 ×100/×1000 换算）
- 不复权（原始价）；港股通资格存于 ipo_dates 的 `hk_connect` 标志（`store.get_ipo_date` 同级可读）
- `pe_ttm` / `pb_mrq` / `pcf_ttm`：由 `update-hk-valuation` 从百度股市通抓取后合并进 K 线文件（行情列不被覆盖）；日频、近三年历史（市销率 PS 百度不提供，待批次 2）

注意：`trading_days_since_ipo` 当前用自然日计算，命名有误导性，待修复。

### 2.2 meta 域（data/store_meta.py）

```python
import data.store_meta as store_meta

store_meta.save(data_type, df, code=None) -> None
store_meta.load(data_type, code=None, start=None, end=None) -> Optional[DataFrame]
store_meta.exists(data_type, code=None) -> bool
store_meta.last_date(data_type) -> Optional[str]
store_meta.get_member_stocks(industry_codes, level="L1") -> list[str]
store_meta.get_stock_l1_industry(code) -> Optional[str]
store_meta.get_industry_etfs(industry_codes, available_codes=None) -> list[str]
store_meta.map_to_l1(industry_codes, level="L2") -> list[str]
# get_member_stocks：行业代码 → 成员股列表（正向）
# get_stock_l1_industry：股票代码 → 申万L1行业代码（反向，带模块级缓存；deep_value 金融/地产排除用）
# get_industry_etfs：根据申万L1行业代码列表，返回对应代表性行业ETF代码列表
# SW_L1_ETF_MAP：模块级常量，31个申万L1行业→代表性行业ETF映射表（供L2输出etf_candidates用）
# map_to_l1：沿 industry_classify.parent_code 链将 L2/L3 行业代码上溯到 L1 父级
#            （ETF白名单 SW_L1_ETF_MAP 按 L1 键，L2/L3 行业池需先桥接）
```

`data_type` 取值：`industry_csrc` / `industry_sw` / `sw_index` / `calendar`
              / `industry_classify`（申万三级分类树）/ `industry_member`（股票→三级行业映射）

**申万三级行业分类（2026-06 新增，Tushare SW2021 体系，代码匹配）：**

- `industry_classify`：申万分类层级树（单文件），列 `index_code`（如 801016.SI）/ `industry_name` / `level`（L1/L2/L3）/ `industry_code` / `parent_code`。L1=31、L2=134、L3=346。
- `industry_member`：股票→三级行业映射（单文件，来自 Tushare `index_member_all`），列 `l1_code/l1_name/l2_code/l2_name/l3_code/l3_name/ts_code/name/in_date/out_date/is_new`，一只股票一行直接含三级归属。

> **设计要点**：放弃 AKShare 名称匹配方案，全部用 Tushare 申万代码体系（`index_classify`/`index_member_all`），层级关系靠 `parent_code`，行业归属靠 `l1/l2/l3_code`，无名称匹配的脆弱性。
> 原 `industry_sw`（AKShare 一级映射）保留不删，向后兼容；scanner 行业过滤优先用 `industry_member`，回退 `industry_sw`。


### 2.3 fundamental 域（data/store_fundamental.py）

```python
import data.store_fundamental as store_fund

store_fund.save(code, df, data_type, market="cn") -> None
store_fund.load(code, data_type, start=None, end=None, market="cn") -> Optional[DataFrame]
store_fund.exists(code, data_type, market="cn") -> bool
store_fund.last_date(code, data_type, market="cn") -> Optional[str]
```

`data_type` 取值：`profit` / `balance` / `cashflow` / `growth` / `dupont`
`market` 取值：`cn`（默认，`data/fundamental/{type}/`）/ `hk`（`data/fundamental/hk/{type}/`，来源东财：三表 + 主要指标拆分）

### 2.4 factor 域（data/store_factor.py）

> 台阶二重构：factor 域重新定位为**计算型因子**（动量/价值/质量等衍生值）。
> 原始资金流（个股/行业/大盘/北向）已迁往 moneyflow 域（见 2.6）。

```python
import data.store_factor as store_factor

store_factor.save(factor_name, df, code=None) -> None
store_factor.load(factor_name, code=None, start=None, end=None) -> Optional[DataFrame]
store_factor.exists(factor_name, code=None) -> bool
store_factor.last_date(factor_name, code=None) -> Optional[str]
```

`factor_name` 任意因子名；提供 `code` 时按 `{factor_name}/{code}.parquet` 分片存储，
否则单文件 `{factor_name}.parquet`。台阶二前暂无已落地因子。

### 2.5 macro 域（data/store_macro.py）

```python
import data.store_macro as store_macro

store_macro.save(data_type, df) -> None              # 追加去重（keep="last"）
store_macro.replace(data_type, df) -> None           # 整表覆盖（换源/全量重取）
store_macro.load(data_type, start=None, end=None) -> Optional[DataFrame]
store_macro.exists(data_type) -> bool
store_macro.last_date(data_type) -> Optional[str]
```

`data_type` 取值（全部 14 项均已落地）：`bond_yield`（含美债）/ `m2` / `cpi_ppi` / `pmi` / `shrongji` / `cny_usd` / `us_bond_yield`（派生）/ `vix` / `dxy` / `oil_wti` / `copper_lme` / `sp500` / `hsi` / `fed_rate`

`update_db()` 已覆盖全部下载项（含汇率与境外指标）；CLI：`main.py update-macro`（`update-all` 第 6 步同路径）。
主要来源（2026-09-22 换源后）：
- `cpi_ppi`：东财 `macro_china_cpi` + `macro_china_ppi`（原金十源已停更至 2025-09），整表覆盖写入
- `shrongji`：Tushare `sf_month`（单位：inc_month/inc_cumval 亿元，stk_endval 万亿元），AKShare 商务数据中心为备用
- `cny_usd`：国家外汇管理局中间价 `currency_boc_safe`（列 `usd_cny`，元；原 `currency_boc_sina` 接口内置日期参数已过期）
- `dxy`：东财 push2his 直连 `secid=100.UDI`（备用域名 91 前缀/HTTP，主域名 TLS 常被重置）→ 新浪外汇日K `DINIW`（独立于东财）→ AKShare `index_global_hist_em`
- `oil_wti`：新浪外盘期货 `futures_foreign_hist(symbol="CL")`（WTI，备源 OIL 布伦特）；**已修复历史 bug（上海金基准价曾被误存为 oil_wti）**
- `copper_lme`：新浪外盘期货 `futures_foreign_hist(symbol="CAD")`（LME 铜，美元/吨）
- `fed_rate`：东财数据中心 `RPT_ECONOMICVALUE_USA`（联邦基金利率目标上限/下限，列 `target_upper`/`target_lower`，日期取议息公布日）
- `us_bond_yield`：派生文件，每次下载 `bond_yield` 后自动截取 `us_2y/us_5y/us_10y/us_30y/us_spread_10y_2y/us_gdp_yoy` 写入，`bond_yield` 仍是唯一数据源
- 其余：`bond_yield`=AKShare `bond_zh_us_rate`；`m2`=`macro_china_money_supply`；`pmi`=`macro_china_pmi`；`vix`=`futures_foreign_hist("VX")`；`sp500`=`index_us_stock_sina(".INX")`；`hsi`=`stock_hk_index_daily_sina("HSI")`

### 2.6 moneyflow 域（data/store_moneyflow.py）

> 台阶二新建：资金流与情绪类原始数据独立成域（拆分自原 tushare + factor 域）。

```python
import data.store_moneyflow as store_mf

# 支持 code分片 / date分片 / 单文件 三种存储模式（market: cn 默认 / hk）
store_mf.save(data_type, df, code=None, date=None, market="cn") -> None
store_mf.load(data_type, code=None, date=None, start=None, end=None, market="cn") -> Optional[DataFrame]
store_mf.exists(data_type, code=None, date=None, market="cn") -> bool
store_mf.list_codes(data_type, market="cn") -> List[str]
store_mf.list_dates(data_type, market="cn") -> List[str]
store_mf.last_date(data_type, code=None, market="cn") -> Optional[str]
store_mf.stats() -> dict
```

`data_type` 取值：
- code分片：`stock`（个股资金流）/ `margin`（融资融券）/ `hk_hold`（北向个股持股）
- date分片：`industry`（行业资金流）/ `top_list`（龙虎榜汇总）/ `top_inst`（龙虎榜席位明细）/ `block_trade`（大宗交易）
- 单文件：`market`（大盘资金流）/ `northbound`（北向资金）

**资金主体域字段说明**（来源 Tushare，供 `agents/smart_money_agent.py` 三支柱画像）：

| 类型 | 来源接口 | 关键字段 | 说明 |
|---|---|---|---|
| top_inst | `top_inst` | trade_date, ts_code, exalter, buy, buy_rate, sell, sell_rate, net_buy, side, reason | 龙虎榜买卖席位明细；`exalter` 为席位名（含机构专用/股通专用/营业部），`side` 为买卖榜分组，存在买卖榜镜像重复行，需按 (date,ts_code,exalter,reason,buy,sell) 去重 |
| block_trade | `block_trade` | trade_date, ts_code, price, vol, amount, buyer, seller | 大宗交易逐笔（买卖双方席位）；用于大宗买方资金方向 |
| hk_hold | `hk_hold` | trade_date, ts_code, code, name, vol, ratio, exchange | 北向个股持股（vol/ratio）；**普通积分账户为季度披露**（2025Q1 起），非日频，需按披露点解读 streak |

> 口径：`net = Σbuy - Σsell`（已排除 `side` 镜像重复）；席位标签来自规则匹配/推断，非官方认定。

**margin（D7 情绪因子）字段说明**（来源 Tushare `margin_detail`，均为个股级别；字段需求来源见 `docs/requirements.md` D7 节）：

| 字段 | 说明 |
|---|---|
| ts_code | 股票代码 |
| trade_date | 交易日期 |
| rzye | 融资余额（元） |
| rzmre | 融资买入额（元） |
| rzche | 融资偿还额（元） |
| rqye | 融券余额（元） |
| rqyl | 融券余量（股） |
| rqmcl | 融券卖出量（股） |
| rqchl | 融券偿还量（股） |
| rzrqye | 融资融券余额合计（元） |

> 注：`margin_detail` 不含市场级聚合字段（如全市场两融余额合计）。市场级数据需另用 Tushare `margin` 接口，当前未接入。

### 2.7 events 域（data/store_events.py）

> 事件域需求定义见 `docs/requirements.md` D8 节
> E1/E2/E3 存储接口已建设。E1 用单文件 `calendar.json`，E2 按 `corporate/{code}.json` 分片，E3 按 `market_shock/{event_id}.json` 归档。

```python
import data.store_events as store_events

# E1 定期事件日历
store_events.save_event_calendar(events: list[dict]) -> None      # 增量合并，按 event_id 去重
store_events.load_event_calendar() -> list[dict]                  # 返回全部 E1 事件（无过滤参数）
store_events.get_active_event_windows(date: str = None) -> list[dict]
    # 返回指定日期处于窗口期内的所有事件（pre_window/post_window 范围内）

# E2 企业事件
store_events.save_corp_events(code: str, events: list[dict]) -> None   # 增量合并，默认填 source/phase
store_events.load_corp_events(code: str) -> list[dict]
store_events.has_reduce_window(code: str, date: str = None) -> bool
    # 判断指定股票在指定日期是否处于大股东减持窗口期（对应 RC7）

# E3 突发事件
store_events.save_market_shock(event: dict) -> None
store_events.load_market_shocks(date: str = None) -> list[dict]   # 可按 date 过滤
```

> Tool Layer 封装：`agent_tools.get_active_event_windows()` / `check_reduce_window()` 在上述接口之上返回结构化 dict，供 Agent 调用。

**E1 事件字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| event_id | str | 唯一标识，如 `FED_FOMC_20260612` |
| event_type | str | macro / policy / earnings |
| event_name | str | 事件名称 |
| date | str | 事件开始日期 |
| end_date | str | 事件结束日期（单日事件与 date 相同） |
| impact_scope | str | global / cn / sector:行业名称 |
| pre_window | int | 事件前降权天数 |
| post_window | int | 事件后观察天数 |
| confirmed | bool | 时间是否已确认（预期时间 vs 官方公告） |

**E2 企业事件字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| event_id | str | 唯一标识 |
| code | str | 股票代码 |
| event_type | str | reduce / increase / pledge / dividend / **buyback** / restructure |
| date | str | 事件发生/公告日期 |
| detail | dict | 结构化参数（如减持数量、价格区间） |
| source | str | tushare / nlp |
| phase | int | 1=结构化接口，2=NLP 解析 |
| nlp_confidence | float | NLP 解析置信度（Phase 2 填充） |

**E3 突发事件字段：**

| 字段 | 类型 | 说明 |
|---|---|---|
| event_id | str | 唯一标识 |
| date | str | 事件日期 |
| event_type | str | geopolitical / natural / tech_shock / policy_shock |
| description | str | 事件描述 |
| trigger | str | proxy_indicator / llm_news / manual |
| market_impact | dict | 指数涨跌幅、受影响板块 |
| source_url | str | 新闻来源（Phase 2+） |

---

## 三、下载器接口

### 3.1 market 域（data/downloader_tushare.py — 主链路）

> **2026-06-17 迁移完成**：K线主下载源从 BaoStock 切换为 Tushare。
> `main.py` 的 `_build_kline_downloader()` 自动选择：有 `TUSHARE_TOKEN` 走 tushare，无则降级 baostock。

```python
from data.downloader_tushare import TushareKlineDownloader
import tushare as ts

pro = ts.pro_api(token, timeout=20)   # 单接口硬超时20s
dl = TushareKlineDownloader(pro)
dl.init_db(start="2024-01-02", end=None, force=False,
           types=["stock", "index", "etf"]) -> dict
dl.update_db(end=None, types=["stock", "index", "etf"]) -> dict
```

数据源映射：
- stock：`pro_bar(adj='hfq')` + `daily_basic`（pe/pb/ps/turn）
- index：`index_daily`，preclose 由 `close.shift(1)` 补
- ETF：`pro_bar(asset='FD', adj=None)`，不复权

单位换算（Tushare → 本地 schema）：
- `vol`（手）× 100 → `volume`（股）
- `amount`（千元）× 1000 → `amount`（元）
- `trade_status` 全量置 "1"（tushare 无停牌字段，后续可用 `suspend_d` 精化）
- `is_st` 全量置 "0"（后续可用 `namechange` 精化；scanner 用股票名过滤ST，不依赖此列）
- `pcf_ttm` 全量 NaN（`daily_basic` 不返回 `pcf_ncf_ttm`，无下游依赖）

容错机制：每只下载套 `ThreadPoolExecutor` 超时包装（`_PER_STOCK_TIMEOUT=45s`），防止 TCP 半开连接死卡。

**旧 BaoStock 下载器（data/downloader.py）保留**，作为无 token 时的降级备用，不删除。

BaoStock → store 列名映射（旧）：`tradestatus→trade_status, pctChg→pct_chg, peTTM→pe_ttm, pbMRQ→pb_mrq, psTTM→ps_ttm, pcfNcfTTM→pcf_ttm, isST→is_st`

### 3.2 港股下载器（data/downloader_hk.py，2026-08 新增）

> **主源 AKShare、非 Tushare 的原因**：Tushare `hk_daily` 在当前账号限频 **1次/小时**，远不能满足批量下载；AKShare `stock_hk_daily`（Sina）返回全历史、无明显限频，故设为主源。Tushare `hk_daily` 仅作单只兜底。

```python
from data.downloader_hk import HKDownloader

dl = HKDownloader(connect_only=True)          # True=仅港股通标的（默认）/ False=全部港股
dl.init_db(start="2020-01-02", end=None, force=False)   # 全量初始化（断点续传）
dl.update_db(end=None)                                  # 增量更新
```

- **港股通（南向）标的优先**：默认 `connect_only=True`。成分列表来源 AKShare `stock_hk_ggt_components_em`（东财，失败重试 3 次）→ Tushare `hk_hold`（南向持股近似列表）→ 全市场降级；缓存 `data/meta/hk_connect.json` 当日有效，45 天内缓存可兜底。
- 证券列表 `hk_basic`（Tushare）；上市日期索引写入 `ipo_dates.json` 的 `hk:*` 键，并额外记录 `hk_connect` 标志。
- 时效保护：`init_db`/`update_db` 在 16:15（收盘+数据落定）前运行时，目标日自动回退到昨日；抓取窗口内无新数据视为**跳过**（不计失败，也不回落限频的 Tushare `hk_daily`）。
- 容错：与 A 股下载器一致，单只 wall-clock 硬超时 `_PER_STOCK_TIMEOUT=45s`。

CLI（`main.py`）：
```
python main.py init-hk [--start 2020-01-02] [--force] [--full]   # --full=全部港股
python main.py update-hk [--full]
python main.py update-hk-valuation [--full] [--period 近三年|近一年]  # PE/PB/PCF，合并进 K 线
python main.py update-hk-moneyflow [--full]        # 港股资金流（东财，滚动约100个交易日）
python main.py update-hk-financials [--full]       # 港股财务（三表+成长/杜邦指标）
python main.py update-hk-events [--full]           # 港股分红事件 → events E2
python main.py update-cn-pcf [--codes ...]         # A股 pcf_ttm（百度市现率）
```
`update-all` 已含港股 K 线/估值/资金流/财务/分红事件与 A股 PCF；`inspect kline <code> --market hk` 可直查港股 K 线（含 pe_ttm/pb_mrq/pcf_ttm）。

**港股估值（PE/PB/PCF）**：来源百度股市通 `gushitong.baidu.com/opendata`（需带 UA/Referer，akshare 封装未带头会失败；亿牛网接口已停更至 2022-07，东财 `stock_hk_valuation_comparison_em` 只有快照）。逐只抓取 `市盈率(TTM)`/`市净率`/`市现率` 后按日期合并进 K 线文件；近三年为日频（约 1096 点），港股通约 1011 只、全量约 30 分钟。

**港股其他数据（批次1，2026-09-23）**：
- 资金流：东财 `fflow/daykline`（`secid=116.xxxxx`，91/http 域名回退），主力/超大/大/中/小单净额+占比，滚动约 100 个交易日、逐日累积 → `data/moneyflow/hk/stock/`
- 财务：AKShare 东财 `stock_financial_hk_report_em`（利润表/资产负债表/现金流量表，长表 pivot）→ profit/balance/cashflow；`stock_financial_hk_analysis_indicator_em` 拆成 growth/dupont → `data/fundamental/hk/`
- 分红事件：`stock_hk_dividend_payout_em` → events E2（`event_type=dividend`，`source=akshare`；港股 5 位代码与 A 股 6 位不冲突）
- 暂不可行：港股两融/卖空（需港交所沽空报表，无免费 API）、减持/质押（港交所权益披露无 API）、市销率 PS 历史（百度不提供，可用总市值÷营收TTM 计算，批次2）

### 3.3 其他域下载器

```python
from data.downloader_meta import MetaDownloader
from data.downloader_fundamental import FundamentalDownloader
from data.downloader_factor import FactorDownloader
from data.downloader_macro import MacroDownloader
from data.downloader_tushare import TushareDownloader

MetaDownloader().init_db(start="2024-01-02")
MetaDownloader().update_db()                    # 每周更新

FundamentalDownloader().init_db(start_year=2024, force=False)
FundamentalDownloader().update_db()             # 每季度更新

FactorDownloader().init_db(start="2024-01-02")
FactorDownloader().update_db()                  # 每日更新

MacroDownloader().init_db(start="2024-01-01")
MacroDownloader().update_db()                   # 增量更新（含汇率/境外指标）

TushareDownloader().init_db(start="2024-01-01")     # 全域：moneyflow/daily/top_list/margin/moneyflow_ind/moneyflow_mkt
TushareDownloader().init_moneyflow(start="2010-01-01")
TushareDownloader().init_daily(start="2024-01-01")  # 同步下载 daily + daily_basic
TushareDownloader().init_top_list(start="2024-01-01")
TushareDownloader().init_margin(start="2024-01-01")
TushareDownloader().update_db()                     # 每日增量更新全域
```

moneyflow 域下载器（`data/downloader_moneyflow.py`）新增资金主体类接口：

```python
from data.downloader_moneyflow import MoneyflowDownloader

dl = MoneyflowDownloader()
dl.init_top_inst(start="2024-01-01")        # 龙虎榜席位明细（按交易日分片）
dl.init_block_trade(start="2024-01-01")     # 大宗交易（按交易日分片）
dl.update_hk_hold(["000001", "600519"])     # 北向个股持股（按代码，季度披露，逐只拉取）
# update_db() 已包含 top_inst / block_trade；hk_hold 按需单独拉取（未纳入默认流程）
```

events 域下载器为函数式接口（非类）：

```python
from data.downloader_events import init_events, update_events, build_calendar, download_all_corp_events

init_events(year=None)            # E1 日历生成 + E2 企业事件全量下载
update_events(codes=None)         # E1 补充 + E2 近3月增量（codes=定向更新指定股票）
build_calendar(year=None)         # 仅生成 E1 定期事件日历
download_all_corp_events(codes=None, start="2024-01-01", end=None)  # E2 减持/增持/质押/分红/回购
```

> E2 为**全市场逐只**拉取（每只 4 接口 + `sleep(0.15)`），全市场约 1.5 小时；受 Tushare 限频（~200 次/分钟），
> **不调小 sleep**（下调会显著增加限频失败）。补单只用 `update-events --codes ...` 定向更新。

---

## 四、网络数据获取（data/fetcher.py）

```python
from data.fetcher import DataFetcher

fetcher = DataFetcher(DATA_SOURCE)
fetcher.get_stock_list() -> DataFrame          # 全市场股票列表
fetcher.get_daily(code, start, end, adjust="qfq") -> DataFrame
fetcher.get_realtime(codes: list) -> DataFrame
fetcher.get_financial(code) -> DataFrame
```

- AKShare 为主，BaoStock 为备，`_with_fallback` 自动降级
- 内置 pickle 缓存（`cache_days=1`，路径 `data/.cache/`）
- BaoStock 不是线程安全的，多线程场景下每个线程独立 login/logout

---

## 五、数据源说明

| 数据源 | 用途 | 限制 |
|---|---|---|
| **Tushare Pro（主）** | K线日线（stock/index/ETF）/ fundamental / moneyflow / meta（申万SW2021）/ 港股证券列表 `hk_basic` / macro 社融 `sf_month` | 已付费，积分充足；单接口超时20s；需配置 `TUSHARE_TOKEN`；`hk_daily` 限频 1次/小时 |
| BaoStock（备） | K线降级备用（无 token 时自动切换）；历史基线保留在 `data/market_baostock/` | 免费，连接偶尔不稳定 |
| AKShare | ETF行情（辅）/ macro（CPI/PPI、汇率、VIX/标普/恒生等；DXY 走东财直连、WTI 走新浪外盘）/ northbound / **港股 K 线 `stock_hk_daily`（Sina）与港股通成分列表 `stock_hk_ggt_components_em`** | 免费，东财接口有限流/偶发连接不稳 |

---

## 六、数据缺口与待办

### 已完成（接口已实现并执行）

moneyflow 域 `stock` / `margin` / `top_list` / `top_inst` / `block_trade` / `hk_hold` / `industry` / `market` / `northbound`；macro 域全部 14 项（`cny_usd` / `shrongji` / `vix` / `dxy` / `oil_wti` / `copper_lme` / `sp500` / `hsi` / `fed_rate` / `us_bond_yield` 等，含 `bond_yield` 内的美债字段）；events 域 E1 日历 + E2 减持/增持/质押/分红/**回购** + E3 结构；**港股 K 线下载器 `downloader_hk.py`（`init-hk`/`update-hk`，港股通标的优先，AKShare Sina 主源）**。

### 待下载（仍未实现下载器或未接入）

| 数据 | 域 | 接口 | 优先级 | 说明 |
|---|---|---|---|---|
| 分钟K线 | tushare/mins | Tushare `stk_mins` | P2 | 日内策略基础；日线体系稳定后再接入 |
| 十大流通股东 | tushare/holders | Tushare `top10_floatholders` | P2 | 季频，机构持仓变化 |
| 股东人数 | tushare/holders | Tushare `stk_holdernumber` | P2 | 筹码集中度判断 |
| E3 突发事件自动识别（D8c） | events/market_shock | Phase 2 新闻 API + LLM | P2 | Phase 1 结构已建，内容人工补录 |
| 申万行业 PE/PB | meta/sw_daily | Tushare `sw_daily` | P2 | **Tushare 当前账号无权限**；ETF/L2 估值现用成员股 pe_ttm 聚合代理（见下） |
| ETF 成分股权重 | — | Tushare `fund_portfolio` | P2 | **Tushare 当前账号无权限**；ETF 估值现用 sw_l2_codes 映射代理（见下） |

**ETF 估值数据过渡方案（2026-06-21，决策C）：**
- 细分主题 ETF 三维评分的"估值维度"暂用 **ETF→申万L2 映射聚合代理**：`data/etf_config.json` 的 `sw_l2_codes` 字段把 ETF 映射到申万 L2 行业，复用 L2 三维评分的估值维度（成员股 `pe_ttm` 历史分位中位数）。
- 这是过渡方案，对细分主题不够精确。后续若升级 Tushare 积分解锁 `sw_daily`（官方行业 PE/PB）+ `fund_portfolio`（真实成分股加权），可替换 `agents/etf_agent.py:_valuation_score` 内部实现，接口不变。
- ETF 元数据/政策配置见 `data/etf_config.json`（可进 git，非个人持仓数据）。

### 已知设计问题

| 位置 | 问题 | 优先级 |
|---|---|---|
| `store.py:stats()` | 每次全量读所有 parquet 文件计算行数，5000+文件时极慢 | 低 |
| `store.py:trading_days_since_ipo` | 用自然日而非交易日，命名有误导性 | 低 |
| `fetcher.py:__del__` | `_bs_logged_in` 永远是 False，析构时不执行 BaoStock logout | 低 |
| `downloader.py:_is_trading_day` | 只排除周末，节假日会触发无效更新 | 中 |
| `downloader_moneyflow.py:fetch_hk_hold` | Tushare 普通积分账户北向持股为季度披露（非日频），streak 只能按披露点计数 | 低 |
