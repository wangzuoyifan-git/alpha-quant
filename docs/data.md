# Alpha量化 — 数据层文档

> 数据层接口、各域结构、数据源说明、待办缺口。
> 最后更新：2026-05-29
>
> **项目基础信息见 `docs/project.md`**
> **数据需求详细定义（D1~D7 含需求来源、字段要求、时效要求）见 `docs/requirements.md` 第二章**

---

## 一、当前数据库状态

```
data/market/cn/
  stock/        5207只   294万行   348MB   字段：OHLCV+换手率+PE/PB/PS/PCF+isST
  index/         541只    30万行    25MB   字段：OHLCV+pct_chg
  etf/          1535只    65万行    42MB   字段：OHLCV+pct_chg（2024-01-02起）
  ipo_dates.json         8624只上市日期+行业+名称索引

data/fundamental/
  profit/       5207只   季频盈利数据（2024Q1起）
  balance/      5207只   季频资产负债
  cashflow/     5207只   季频现金流
  growth/       5207只   季频成长能力
  dupont/       5207只   季频杜邦分析

data/meta/
  industry_csrc.parquet  证监会行业分类
  industry_sw.parquet    申万行业分类（股票→行业映射）
  sw_index/              申万行业指数日线
  calendar.parquet       交易日历

data/factor/
  northbound.parquet     北向资金（日频，2024起）
  moneyflow/             个股资金流向（⚠️ 待补全，见缺口说明）
  moneyflow_sector/      行业资金流向（⚠️ 待补全，Tushare moneyflow_ind）

data/macro/
  国内宏观（D5a）：
  bond_yield.parquet     中国国债收益率2Y/10Y（日频）
  cny_usd.parquet        人民币汇率CNY/USD（日频）       ⚠️ 待下载
  m2.parquet             M2货币供应同比（月频）
  shrongji.parquet       社融规模增量/存量同比（月频）   ⚠️ 待下载
  cpi_ppi.parquet        CPI/PPI同比（月频）
  pmi.parquet            PMI制造业/非制造业（月频）
  境外宏观（D5b）：
  fed_rate.parquet       美联储基准利率（事件触发）       ⚠️ 待下载
  us_bond_yield.parquet  美债收益率2Y/10Y（日频）        ⚠️ 待下载
  vix.parquet            VIX恐慌指数（日频）             ⚠️ 待下载
  dxy.parquet            美元指数DXY（日频）             ⚠️ 待下载
  oil.parquet            原油价格WTI/布伦特（日频）      ⚠️ 待下载
  copper.parquet         LME铜价（日频）                 ⚠️ 待下载
  sp500.parquet          标普500指数（日频）             ⚠️ 待下载
  hsi.parquet            恒生指数（日频）                ⚠️ 待下载

data/tushare/
  moneyflow/             个股资金流向（2010起，按code分片）
  moneyflow_ind/         行业资金流向
  moneyflow_mkt/         大盘资金流向
  daily/                 ⚠️ 待下载
  top_list/              ⚠️ 待下载
  margin/                ⚠️ 待下载

data/events/                                              ⚠️ 待建设（D8）
  calendar.json          E1 定期事件日历（宏观/政策/财报季窗口期）
  corporate/             E2 企业事件（按code分片，结构化接口优先）
    {code}.parquet       单股所有企业事件记录
  market_shock/          E3 突发事件归档（事后人工记录，Phase 2+ LLM 驱动）
    {date}.json          按日期归档的突发事件记录
```

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

注意：`trading_days_since_ipo` 当前用自然日计算，命名有误导性，待修复。

### 2.2 meta 域（data/store_meta.py）

```python
import data.store_meta as store_meta

store_meta.save(data_type, df, code=None) -> None
store_meta.load(data_type, code=None, start=None, end=None) -> Optional[DataFrame]
store_meta.exists(data_type, code=None) -> bool
store_meta.last_date(data_type) -> Optional[str]
```

`data_type` 取值：`industry_csrc` / `industry_sw` / `sw_index` / `calendar`

### 2.3 fundamental 域（data/store_fundamental.py）

```python
import data.store_fundamental as store_fund

store_fund.save(code, df, data_type) -> None
store_fund.load(code, data_type, start=None, end=None) -> Optional[DataFrame]
store_fund.exists(code, data_type) -> bool
store_fund.last_date(code, data_type) -> Optional[str]
```

`data_type` 取值：`profit` / `balance` / `cashflow` / `growth` / `dupont`

### 2.4 factor 域（data/store_factor.py）

```python
import data.store_factor as store_factor

store_factor.save(data_type, df, code=None) -> None
store_factor.load(data_type, code=None, start=None, end=None) -> Optional[DataFrame]
store_factor.exists(data_type, code=None) -> bool
store_factor.last_date(data_type) -> Optional[str]
```

`data_type` 取值：`northbound` / `moneyflow` / `moneyflow_sector`

### 2.5 macro 域（data/store_macro.py）

```python
import data.store_macro as store_macro

store_macro.save(data_type, df) -> None
store_macro.load(data_type, start=None, end=None) -> Optional[DataFrame]
store_macro.exists(data_type) -> bool
store_macro.last_date(data_type) -> Optional[str]
```

`data_type` 取值（已有数据）：`bond_yield` / `m2` / `cpi_ppi` / `pmi`

待下载数据使用同一接口，完整 `data_type` 列表见本文档第一节数据库状态中 `data/macro/` 目录清单。

### 2.6 tushare 域（data/store_tushare.py）

```python
import data.store_tushare as store_ts

# 支持 code分片 / date分片 / 单文件 三种存储模式
store_ts.save(data_type, df, code=None, date=None) -> None
store_ts.load(data_type, code=None, date=None, start=None, end=None) -> Optional[DataFrame]
store_ts.list_codes(data_type) -> List[str]
store_ts.list_dates(data_type) -> List[str]
store_ts.last_date(data_type, code=None) -> Optional[str]
```

`data_type` 取值：`moneyflow` / `moneyflow_ind` / `moneyflow_mkt` / `daily` / `top_list` / `margin`

**margin（D7 情绪因子）字段说明**（字段需求来源见 `docs/requirements.md` D7 节）：

| 字段 | 说明 | 级别 |
|---|---|---|
| ts_code | 股票代码 | 个股 |
| trade_date | 交易日期 | 个股 |
| rzye | 融资余额（元） | 个股 |
| rzmre | 融资买入额（元） | 个股 |
| rzche | 融资偿还额（元） | 个股 |
| rqye | 融券余额（元） | 个股 |
| rqmcl | 融券卖出量（股） | 个股 |
| rzrqye | 融资融券余额合计（元） | 个股 |
| rzye_sum | 全市场融资余额合计 | 市场 |
| rqye_sum | 全市场融券余额合计 | 市场 |
| rzrqye_sum | 全市场两融余额合计 | 市场 |

### 2.7 events 域（data/store_events.py）⚠️ 待建设

> 事件域需求定义见 `docs/requirements.md` D8 节

```python
import data.store_events as store_events

# E1 定期事件日历
store_events.save_calendar(events: list[dict]) -> None
store_events.load_calendar(start: str = None, end: str = None,
                           event_type: str = None) -> list[dict]
store_events.get_active_windows(date: str) -> list[dict]
    # 返回指定日期处于窗口期内的所有事件（pre_window/post_window范围内）

# E2 企业事件
store_events.save_corporate(code: str, events: list[dict]) -> None
store_events.load_corporate(code: str, start: str = None, end: str = None,
                            event_type: str = None) -> list[dict]
store_events.has_reduce_window(code: str, date: str) -> bool
    # 判断指定股票在指定日期是否处于大股东减持窗口期（对应 RC7）

# E3 突发事件
store_events.save_shock(event: dict) -> None
store_events.load_shocks(start: str = None, end: str = None) -> list[dict]
```

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
| event_type | str | reduce / increase / pledge / dividend / restructure |
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

### 3.1 market 域（data/downloader.py）

```python
from data.downloader import KlineDownloader

dl = KlineDownloader(fetcher, workers=1)
dl.init_db(start="2024-01-02", end=None, force=False,
           types=["stock", "index", "etf"]) -> dict
dl.update_db(end=None, types=["stock", "index", "etf"]) -> dict
```

BaoStock → store 列名映射：`tradestatus→trade_status, pctChg→pct_chg, peTTM→pe_ttm, pbMRQ→pb_mrq, psTTM→ps_ttm, pcfNcfTTM→pcf_ttm, isST→is_st`

### 3.2 其他域下载器

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
MacroDownloader().update_db()                   # 每月更新

TushareDownloader().init_moneyflow(start="2010-01-01")
TushareDownloader().init_daily(start="2024-01-01")
```

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
| BaoStock | market/fundamental/meta(证监会) | 免费，稳定，串行，不支持线程并发 |
| AKShare | ETF行情/meta(申万)/factor/macro | 免费，东财接口有限流，需 sleep(0.5~1s) |
| Tushare Pro | moneyflow/daily/top_list/margin/分钟线等 | 已付费，积分充足；需配置 `TUSHARE_TOKEN` |

---

## 六、数据缺口与待办

### 待下载（有接口，未执行）

| 数据 | 域 | 接口 | 优先级 | 说明 |
|---|---|---|---|---|
| 个股资金流向历史 | factor/moneyflow | Tushare `moneyflow`（2010起） | P1 | 已付费，优先用 Tushare；AKShare 仅近60天备用 |
| 行业资金流向 | factor/moneyflow_sector | Tushare `moneyflow_ind` | P1 | 已有部分数据，补全历史 |
| 融资融券（D7） | tushare/margin | Tushare `margin` | P1 | 情绪因子，个股+市场级别 |
| Tushare daily | tushare/daily | Tushare `daily` | P1 | 日线行情补充，含复权因子 |
| 龙虎榜 | tushare/top_list | Tushare `top_list` | P1 | 游资跟踪策略基础数据 |
| 人民币汇率 CNY/USD（D5a） | macro/cny_usd | AKShare `forex_pair_hist` | P1 | 影响北向资金流向，市场环境判断 |
| 社融规模增量/存量（D5a） | macro/shrongji | AKShare `macro_china_shrhbzs` 等 | P1 | 信用周期核心指标 |
| 美债收益率 2Y/10Y（D5b） | macro/us_bond_yield | AKShare `bond_zh_us_rate` | P1 | 中美利差，已有接口，扩展字段 |
| VIX 恐慌指数（D5b） | macro/vix | AKShare `index_vix` | P1 | 全球风险偏好指标 |
| 美元指数 DXY（D5b） | macro/dxy | AKShare `index_investing_global` | P1 | 强美元压制新兴市场 |
| 原油价格 WTI/布伦特（D5b） | macro/oil | AKShare `oil_hist_em` | P1 | 能源板块直接驱动因素 |
| 铜价 LME（D5b） | macro/copper | AKShare `futures_hist_em` | P2 | 工业景气度领先指标 |
| 标普500指数（D5b） | macro/sp500 | AKShare `index_investing_global` | P1 | 全球情绪传导 |
| 恒生指数（D5b） | macro/hsi | AKShare `stock_hk_index_daily_em` | P1 | 港股联动，AH溢价参考 |
| 美联储基准利率（D5b） | macro/fed_rate | AKShare `macro_bank_usa_interest_rate` | P2 | 事件触发，全球流动性锚 |
| 分钟K线 | tushare/mins | Tushare `stk_mins` | P2 | 日内策略基础；日线体系稳定后再接入 |
| 十大流通股东 | tushare/holders | Tushare `top10_floatholders` | P2 | 季频，机构持仓变化 |
| 股东人数 | tushare/holders | Tushare `stk_holdernumber` | P2 | 筹码集中度判断 |
| E1 定期事件日历（D8a） | events/calendar | 人工维护 + AKShare 财报日期接口 | P1 | Phase 1 先建结构，手动维护宏观和财报窗口期 |
| E2 企业事件-减持（D8b） | events/corporate | Tushare `stk_holdertrade` | P1 | RC7 大股东减持窗口期风控依赖此数据 |
| E2 企业事件-质押（D8b） | events/corporate | Tushare `pledge_stat` | P2 | 股权质押风险预警 |
| E3 突发事件归档（D8c） | events/market_shock | Phase 1 人工记录；Phase 2 新闻 API + LLM | P2 | Phase 1 只需建好存储结构，内容事后补录 |

### 已知设计问题

| 位置 | 问题 | 优先级 |
|---|---|---|
| `store.py:stats()` | 每次全量读所有 parquet 文件计算行数，5000+文件时极慢 | 低 |
| `store.py:trading_days_since_ipo` | 用自然日而非交易日，命名有误导性 | 低 |
| `fetcher.py:__del__` | `_bs_logged_in` 永远是 False，析构时不执行 BaoStock logout | 低 |
| `downloader.py:_is_trading_day` | 只排除周末，节假日会触发无效更新 | 中 |
