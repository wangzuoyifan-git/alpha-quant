# Alpha量化 — 扩展数据初始化 Session 上下文文档

> 供「数据扩展初始化」Session 使用
> 任务：初始化 fundamental / meta / factor / macro 四个数据域
> 更新时间：2026-05-26
>
> **项目基础信息（环境、Git、目录结构、CLI、数据库状态）见 `docs/project.md`**

---

## 二、当前数据库状态（已完成）

```
data/market/cn/
  stock/   5207只   294万行   348MB   字段：OHLCV+换手率+PE/PB/PS/PCF+isST
  index/    541只    30万行    25MB   字段：OHLCV+pct_chg
  etf/     1535只    65万行    42MB   字段：OHLCV+pct_chg（已修复，2024-01-02起）
  ipo_dates.json  8624只上市日期+行业+名称索引
```

---

## 三、本次任务：初始化四个新数据域

### 优先级顺序

```
P0  meta/      行业分类（申万+证监会）   → 行业轮动策略必需
P1  fundamental/ 季频财务数据           → 价值/质量因子
P2  factor/    资金流向+北向资金         → 选股增强信号
P3  macro/     宏观经济数据             → 跨资产择时
```

---

## 四、目录结构设计

```
data/
  market/          ✅ 已完成
  fundamental/     🟡 本次新建
    profit/   {code}.parquet    季频盈利数据
    balance/  {code}.parquet    季频负债数据
    cashflow/ {code}.parquet    季频现金流
    growth/   {code}.parquet    季频成长能力
    dupont/   {code}.parquet    季频杜邦分析
  meta/            🟡 本次新建
    industry_csrc.parquet       证监会行业分类
    industry_sw.parquet         申万行业分类（股票→行业映射）
    sw_index/ {code}.parquet    申万行业指数日线
    calendar.parquet            交易日历
  factor/          🟢 本次新建
    moneyflow/ {code}.parquet   个股资金流向（日频）
    moneyflow_sector.parquet    行业资金流向（日频）
    northbound.parquet          北向资金（日频）
  macro/           🟢 本次新建
    bond_yield.parquet          国债收益率（日频）
    m2.parquet                  M2货币供应（月频）
    cpi_ppi.parquet             CPI/PPI（月频）
    pmi.parquet                 PMI（月频）
```

---

## 五、各域数据接口详情

### 5.1 meta 域 — 行业分类（P0，最优先）

#### 证监会行业（BaoStock，免费稳定）

```python
import baostock as bs
bs.login()
rs = bs.query_stock_industry()
# 返回字段：updateDate, code, code_name, industry, industryClassification
# 示例：2023-12-31, sh.600519, 贵州茅台, 食品制造业, 证监会行业分类
while rs.error_code == '0' and rs.next():
    row = rs.get_row_data()
bs.logout()
```

存储为 `data/meta/industry_csrc.parquet`，列：`code, name, industry, classification, update_date`

#### 申万行业（AKShare，需加重试）

```python
import akshare as ak

# 申万行业列表（一级）
df = ak.sw_index_first_info()  # 返回：行业代码、行业名称

# 申万行业成分股
df = ak.index_stock_cons(symbol="801010")  # 801010=农林牧渔

# 申万行业指数日线（用于行业轮动）
df = ak.sw_index_daily_indicator(symbol="801010", period="day",
                                  start_date="20240102", end_date="20260526")
```

存储为：
- `data/meta/industry_sw.parquet`：股票→申万行业映射
- `data/meta/sw_index/{code}.parquet`：申万行业指数日线

---

### 5.2 fundamental 域 — 季频财务（P1，BaoStock 免费）

```python
import baostock as bs
bs.login()

# 盈利能力（每季度末更新）
rs = bs.query_profit_data(code="sh.600519", year=2024, quarter=4)
# 字段：code,pubDate,statDate,roeAvg,npMargin,gpMargin,
#        netProfit,epsTTM,MBRevenue,totalShare,liqaShare

# 资产负债
rs = bs.query_balance_data(code="sh.600519", year=2024, quarter=4)
# 字段：code,pubDate,statDate,currentRatio,quickRatio,cashRatio,
#        YOYLiability,liabilityToAsset,assetToEquity

# 现金流
rs = bs.query_cash_flow_data(code="sh.600519", year=2024, quarter=4)
# 字段：code,pubDate,statDate,CAToAsset,NCAToAsset,tangibleAssetToAsset,
#        ebitToInterest,CFOToOR,CFOToNP,CFOToGr

# 成长能力
rs = bs.query_growth_data(code="sh.600519", year=2024, quarter=4)
# 字段：code,pubDate,statDate,YOYEquity,YOYAsset,YOYNI,
#        YOYEPSBasic,YOYPNI

# 杜邦分析
rs = bs.query_dupont_data(code="sh.600519", year=2024, quarter=4)
# 字段：code,pubDate,statDate,dupontROE,dupontAssetStoEquity,
#        dupontAssetTurn,dupontPnitoni,dupontNitogr,dupontTaxBurden,dupontIntburden

bs.logout()
```

**批量下载建议**：
- 遍历所有股票（5207只）× 近10个季度（2024Q1~2026Q2）
- 按季度批量下载，每只股票存一个 parquet（追加模式）
- 注意：year/quarter 参数范围 year=2007~今，quarter=1~4

存储格式（每只股票一个文件，date 为 statDate）：
```python
df.to_parquet(f"data/fundamental/profit/{code}.parquet")
```

---

### 5.3 factor 域 — 资金流向（P2，AKShare）

⚠️ AKShare 东方财富接口有限流，需加请求间隔（0.5~1秒）

```python
import akshare as ak
import time

# 个股资金流向（今日）
df = ak.stock_individual_fund_flow(stock="600519", market="sh")
# 字段：日期,主力净流入-净额,主力净流入-净占比,
#        超大单净流入-净额,大单净流入-净额,中单净流入-净额,小单净流入-净额

# 行业资金流向排名
df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流向")
# 字段：行业,今日涨跌幅,主力净流入-净额,主力净流入-净占比,...

# 北向资金（陆股通）
df = ak.stock_hsgt_north_net_flow_in_em(symbol="北向资金")
# 字段：日期,当日成交净买额,5日成交净买额,10日成交净买额
```

**注意**：个股资金流向接口只能获取近期数据（约60天），建议每日增量更新。

---

### 5.4 macro 域 — 宏观数据（P3，AKShare）

```python
import akshare as ak

# 国债收益率（中美对比）
df = ak.bond_zh_us_rate(start_date="20240102")
# 字段：日期,中国国债收益率2年,中国国债收益率5年,中国国债收益率10年,
#        美国国债收益率2年,美国国债收益率10年,中美利差

# M2货币供应
df = ak.macro_china_m2()
# 字段：月份,今值,预测值,前值

# CPI
df = ak.macro_china_cpi_monthly()
# 字段：月份,全国-当月,全国-同比增长,全国-环比增长

# PMI（官方制造业）
df = ak.macro_china_pmi_yearly()
# 字段：月份,制造业-指数,制造业-同比增长,非制造业-指数
```

---

## 六、数据存取接口规范

新域的数据读写建议参考 `data/store.py` 的设计风格，**但不要修改 store.py**，各域独立实现自己的存取模块：

```python
# 推荐的模块结构
data/
  store.py           # 已有，market 域专用，不要修改
  store_fundamental.py  # 新建，fundamental 域
  store_meta.py         # 新建，meta 域
  store_factor.py       # 新建，factor 域
  store_macro.py        # 新建，macro 域
```

每个 store 模块提供统一接口：
```python
def save(code, df, data_type) -> None
def load(code, data_type, start=None, end=None) -> Optional[DataFrame]
def exists(code, data_type) -> bool
def last_date(code, data_type) -> Optional[str]
```

---

## 七、下载器设计建议

参考 `data/downloader.py` 的 `KlineDownloader` 结构，新建对应下载器：

```python
# data/downloader_fundamental.py
class FundamentalDownloader:
    def init_db(self, start_year=2024, end_year=2026): ...
    def update_db(self): ...  # 每季度更新

# data/downloader_meta.py
class MetaDownloader:
    def init_db(self): ...
    def update_db(self): ...  # 每周更新

# data/downloader_factor.py
class FactorDownloader:
    def init_db(self, start="2024-01-02"): ...
    def update_db(self): ...  # 每日更新
```

---

## 八、main.py 扩展建议

在 `main.py` 新增命令：

```bash
# 初始化扩展数据
python3 main.py init-fundamental   # 下载财务数据
python3 main.py init-meta          # 下载行业分类
python3 main.py init-factor        # 下载资金流向
python3 main.py init-macro         # 下载宏观数据

# 更新（每日/每周/每季度）
python3 main.py update-all         # 更新所有域
```

---

## 九、注意事项

### BaoStock 使用注意
- `bs.login()` 之后必须立即 `settimeout`（参考 `downloader.py` 的 `_set_bs_timeout`）
- 全量查询（query_stock_basic 等）用 60s timeout，单只查询用 10s timeout
- login/logout 之间间隔至少 3 秒，避免限流
- baostock 不是线程安全的，必须串行下载

### AKShare 使用注意
- 东方财富接口限流明显，每次请求后 `time.sleep(0.5~1)`
- 失败时指数退避重试（最多3次）
- 股票列表等大请求用 baostock，行情类用 akshare

### 数据完整性
- 每次下载后验证行数和关键列非空
- 财务数据按 `statDate`（报告期）而非 `pubDate`（披露日）存储
- 季度数据：year=2024,quarter=1 对应 2024Q1（截止3月31日）

---

## 十、验证脚本模板

```python
# 验证新域数据是否正常
import sys; sys.path.insert(0, '/home/wz/alpha量化')

# 验证 meta 域
import pandas as pd
df = pd.read_parquet('data/meta/industry_csrc.parquet')
print(f'证监会行业: {len(df)} 只，{df["industry"].nunique()} 个行业')

# 验证 fundamental 域
df = pd.read_parquet('data/fundamental/profit/600519.parquet')
print(f'贵州茅台盈利数据: {len(df)} 季，最新: {df.index.max()}')
print(df[['roeAvg','npMargin','netProfit']].tail(4))
```
