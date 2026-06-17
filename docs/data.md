# Alpha量化 — 数据层文档

> 数据层接口、各域结构、数据源说明、待办缺口。
> 最后更新：2026-06-17（K线主源切换为 Tushare；新增 inspect 数据直查工具）
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

data/market_baostock/cn/              ← BaoStock 历史基线（静态快照，交叉验证用，不再更新）
  stock/        5208只   baostock hfq 口径（2026-06-16 备份）

data/fundamental/
  profit/       5207只   季频盈利数据（2024Q1起）
  balance/      4819只   季频资产负债
  cashflow/     5207只   季频现金流
  growth/       5207只   季频成长能力
  dupont/       5207只   季频杜邦分析

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
  fed_rate.parquet       美联储基准利率                    ⚠️ 待下载
  vix.parquet            VIX恐慌指数（VIX期货代理，日频）
  dxy.parquet            美元指数DXY（日频）
  oil_wti.parquet        WTI原油价格（日频）
  copper_lme.parquet     LME铜价                          ⚠️ 待下载
  sp500.parquet          标普500指数（日频）
  hsi.parquet            恒生指数（日频）

data/moneyflow/
  stock/                 个股资金流（Tushare，2010起，按code分片）
  industry/              行业资金流（东财 BK 体系，按date分片，583个交易日）
  market.parquet         大盘资金流（Tushare 东财，单文件）
  northbound.parquet     北向资金（AKShare，日频，2024起）
  margin/                融资融券明细（Tushare，按code分片）
  top_list/              龙虎榜（Tushare，按date分片）

data/events/
  calendar.json          E1 定期事件日历
  corporate/             E2 企业事件（按code分片）
  market_shock/          E3 突发事件归档
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
              / `industry_classify`（申万三级分类树）/ `industry_member`（股票→三级行业映射）

**申万三级行业分类（2026-06 新增，Tushare SW2021 体系，代码匹配）：**

- `industry_classify`：申万分类层级树（单文件），列 `index_code`（如 801016.SI）/ `industry_name` / `level`（L1/L2/L3）/ `industry_code` / `parent_code`。L1=31、L2=134、L3=346。
- `industry_member`：股票→三级行业映射（单文件，来自 Tushare `index_member_all`），列 `l1_code/l1_name/l2_code/l2_name/l3_code/l3_name/ts_code/name/in_date/out_date/is_new`，一只股票一行直接含三级归属。

> **设计要点**：放弃 AKShare 名称匹配方案，全部用 Tushare 申万代码体系（`index_classify`/`index_member_all`），层级关系靠 `parent_code`，行业归属靠 `l1/l2/l3_code`，无名称匹配的脆弱性。
> 原 `industry_sw`（AKShare 一级映射）保留不删，向后兼容；scanner 行业过滤优先用 `industry_member`，回退 `industry_sw`。


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

store_macro.save(data_type, df) -> None
store_macro.load(data_type, start=None, end=None) -> Optional[DataFrame]
store_macro.exists(data_type) -> bool
store_macro.last_date(data_type) -> Optional[str]
```

`data_type` 取值（已实现下载）：`bond_yield`（含美债）/ `m2` / `cpi_ppi` / `pmi` / `shrongji` / `cny_usd` / `vix` / `dxy` / `oil_wti` / `sp500` / `hsi`

仍未实现下载器（VALID_TYPES 已预留）：`fed_rate` / `copper_lme` / `us_bond_yield`（美债已并入 `bond_yield`）。
注：`update_db()` 仅增量更新 bond_yield/m2/cpi_ppi/pmi/shrongji，境外指标和汇率需手动重跑 `init_db()` 或单独下载方法。

### 2.6 moneyflow 域（data/store_moneyflow.py）

> 台阶二新建：资金流与情绪类原始数据独立成域（拆分自原 tushare + factor 域）。

```python
import data.store_moneyflow as store_mf

# 支持 code分片 / date分片 / 单文件 三种存储模式
store_mf.save(data_type, df, code=None, date=None) -> None
store_mf.load(data_type, code=None, date=None, start=None, end=None) -> Optional[DataFrame]
store_mf.exists(data_type, code=None, date=None) -> bool
store_mf.list_codes(data_type) -> List[str]
store_mf.list_dates(data_type) -> List[str]
store_mf.last_date(data_type, code=None) -> Optional[str]
store_mf.stats() -> dict
```

`data_type` 取值：
- code分片：`stock`（个股资金流）/ `margin`（融资融券）
- date分片：`industry`（行业资金流）/ `top_list`（龙虎榜）
- 单文件：`market`（大盘资金流）/ `northbound`（北向资金）

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

TushareDownloader().init_db(start="2024-01-01")     # 全域：moneyflow/daily/top_list/margin/moneyflow_ind/moneyflow_mkt
TushareDownloader().init_moneyflow(start="2010-01-01")
TushareDownloader().init_daily(start="2024-01-01")  # 同步下载 daily + daily_basic
TushareDownloader().init_top_list(start="2024-01-01")
TushareDownloader().init_margin(start="2024-01-01")
TushareDownloader().update_db()                     # 每日增量更新全域
```

events 域下载器为函数式接口（非类）：

```python
from data.downloader_events import init_events, update_events, build_calendar, download_all_corp_events

init_events(year=None)            # E1 日历生成 + E2 企业事件全量下载
update_events()                   # E1 补充 + E2 近3月增量
build_calendar(year=None)         # 仅生成 E1 定期事件日历
download_all_corp_events(codes=None, start="2024-01-01", end=None)  # E2 减持/质押/分红
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
| **Tushare Pro（主）** | K线日线（stock/index/ETF）/ fundamental / moneyflow / meta（申万SW2021）| 已付费，积分充足；单接口超时20s；需配置 `TUSHARE_TOKEN` |
| BaoStock（备） | K线降级备用（无 token 时自动切换）；历史基线保留在 `data/market_baostock/` | 免费，连接偶尔不稳定 |
| AKShare | ETF行情（辅）/ macro（境外指标）/ northbound | 免费，东财接口有限流 |

---

## 六、数据缺口与待办

### 已完成（接口已实现并执行）

factor 域 `moneyflow`（AKShare 近60天）/ `moneyflow_sector`；tushare 域 `daily` / `daily_basic` / `top_list` / `margin` / `moneyflow`(2010起) / `moneyflow_ind` / `moneyflow_mkt`；macro 域 `cny_usd` / `shrongji` / `vix` / `dxy` / `oil_wti` / `sp500` / `hsi`（含 `bond_yield` 内的美债字段）；events 域 E1 日历 + E2 减持/质押/分红 + E3 结构。

### 待下载（仍未实现下载器或未接入）

| 数据 | 域 | 接口 | 优先级 | 说明 |
|---|---|---|---|---|
| 铜价 LME（D5b） | macro/copper_lme | AKShare `futures_hist_em` | P2 | VALID_TYPES 已预留，`_download_overseas` 未调用铜价下载 |
| 美联储基准利率（D5b） | macro/fed_rate | AKShare `macro_bank_usa_interest_rate` | P2 | VALID_TYPES 已预留，无下载方法 |
| 美债独立文件（D5b） | macro/us_bond_yield | — | P2 | 当前美债字段已并入 `bond_yield`，独立文件未单独生成 |
| 分钟K线 | tushare/mins | Tushare `stk_mins` | P2 | 日内策略基础；日线体系稳定后再接入 |
| 十大流通股东 | tushare/holders | Tushare `top10_floatholders` | P2 | 季频，机构持仓变化 |
| 股东人数 | tushare/holders | Tushare `stk_holdernumber` | P2 | 筹码集中度判断 |
| E3 突发事件自动识别（D8c） | events/market_shock | Phase 2 新闻 API + LLM | P2 | Phase 1 结构已建，内容人工补录 |

### 已知设计问题

| 位置 | 问题 | 优先级 |
|---|---|---|
| `store.py:stats()` | 每次全量读所有 parquet 文件计算行数，5000+文件时极慢 | 低 |
| `store.py:trading_days_since_ipo` | 用自然日而非交易日，命名有误导性 | 低 |
| `fetcher.py:__del__` | `_bs_logged_in` 永远是 False，析构时不执行 BaoStock logout | 低 |
| `downloader.py:_is_trading_day` | 只排除周末，节假日会触发无效更新 | 中 |
