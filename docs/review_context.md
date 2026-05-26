# Alpha量化 — Review & 单元测试上下文文档

> 本文档供独立 Review Session 使用，包含项目结构、模块职责、接口清单、已知问题和测试建议。
> 生成时间：2026-05-26

---

## 一、项目概览

| 项目 | 说明 |
|---|---|
| 根目录 | `/home/wz/alpha量化` |
| 运行环境 | Python 3.12，虚拟环境 `venv/` |
| 激活方式 | `source venv/bin/activate` |
| 主入口 | `python3 main.py <command>` |
| 数据目录 | `data/market/cn/{stock,index,etf}/` |
| 旧数据目录 | `data/kline/*.parquet`（旧结构，暂保留，新代码不读取） |

### 目录结构

```
alpha量化/
├── main.py                        # CLI 主入口
├── config/settings.py             # 全局配置
├── data/
│   ├── store.py                   # 本地存储层（多市场多资产）
│   ├── fetcher.py                 # 网络数据获取（AKShare/BaoStock）
│   ├── downloader.py              # K线批量下载器
│   ├── market/                    # 新数据目录（init 后生成）
│   │   ├── cn/stock/              # A股日线 Parquet
│   │   ├── cn/index/              # A股指数
│   │   ├── cn/etf/                # ETF
│   │   └── ipo_dates.json         # 上市日期索引
│   └── kline/                     # 旧数据目录（暂保留）
├── engine/scanner.py              # 全市场扫描引擎
├── backtest/engine.py             # 历史回测引擎
├── strategies/
│   ├── base.py                    # 策略基类 & Signal
│   └── builtin/
│       ├── ma_cross.py            # 均线金叉策略
│       ├── macd_rsi.py            # MACD+RSI 共振策略
│       └── vol_price_breakout.py  # 量价突破策略
├── notifier/push.py               # 多渠道推送
├── trader/assistant.py            # 半自动交易辅助
├── signals/manager.py             # 信号持久化
└── tools/plot_kline.py            # K线图验证工具
```

---

## 二、模块职责与接口

### 2.1 data/store.py

**职责**：本地行情存储层，Parquet 格式，支持多市场（cn/hk/us）多资产（stock/index/etf）。

```python
# 核心接口（market 默认 "cn"，asset_type 默认 "stock"）
save(code, df, asset_type="stock", market="cn") -> None
load(code, asset_type="stock", market="cn", start=None, end=None) -> Optional[DataFrame]
exists(code, asset_type="stock", market="cn") -> bool
last_date(code, asset_type="stock", market="cn") -> Optional[str]
list_codes(asset_type="stock", market="cn") -> List[str]
stats() -> dict   # 返回 {market: {atype: {count, rows, size_mb}}, "total": {...}}

# 上市日期（key 格式为 "cn:600519"）
load_ipo_dates() -> Dict[str, dict]
save_ipo_dates(ipo: Dict[str, dict]) -> None
get_ipo_date(code, market="cn") -> Optional[str]
trading_days_since_ipo(code, market="cn", as_of=None) -> Optional[int]
```

**A股字段（cn/stock）**：
`open, high, low, close, preclose, volume, amount, turn, trade_status, pct_chg, pe_ttm, pb_mrq, ps_ttm, pcf_ttm, is_st`

**指数/ETF字段**：
`open, high, low, close, preclose, volume, amount, pct_chg`

---

### 2.2 data/fetcher.py

**职责**：网络数据获取，AKShare 为主，BaoStock 为备，自动降级，内置 pickle 缓存。

```python
class DataFetcher:
    def __init__(self, config: dict)
    def get_stock_list() -> DataFrame          # 全市场股票列表
    def get_daily(code, start, end, adjust="qfq") -> DataFrame
    def get_realtime(codes: list) -> DataFrame
    def get_financial(code) -> DataFrame
    # 内部方法（downloader 直接调用）
    def _with_fallback(primary, *fallbacks, **kwargs)
    def _get_daily_baostock(code, start, end, adjust) -> DataFrame
    def _relogin_baostock() -> None
```

---

### 2.3 data/downloader.py

**职责**：基于 BaoStock 的 K 线批量下载，支持断点续传和增量更新。

```python
class KlineDownloader:
    def __init__(self, fetcher: DataFetcher, workers: int = 1)
    def init_db(start="2024-01-02", end=None, force=False,
                types=["stock","index","etf"]) -> dict
    def update_db(end=None, types=["stock","index","etf"]) -> dict
```

**下载字段**（baostock → store 列名映射）：
```
tradestatus → trade_status
pctChg      → pct_chg
peTTM       → pe_ttm
pbMRQ       → pb_mrq
psTTM       → ps_ttm
pcfNcfTTM   → pcf_ttm
isST        → is_st
```

---

### 2.4 engine/scanner.py

**职责**：全市场并发扫描，多线程执行策略，返回 Top N 信号。

```python
class ScanEngine:
    def __init__(self, fetcher: DataFetcher, scan_config: dict)
    def register_strategy(key: str, strategy: BaseStrategy)
    def load_strategies(strategy_classes: dict, params: dict = None)
    def scan(date: str = None) -> List[Signal]
```

---

### 2.5 backtest/engine.py

**职责**：事件驱动回测，逐日模拟买卖，输出资金曲线和绩效报告。

```python
@dataclass
class Trade:
    code, name, entry_date, entry_price, exit_date, exit_price
    shares, pnl, pnl_pct, reason_in, reason_out

@dataclass
class BacktestResult:
    strategy_name, start_date, end_date
    initial_capital, final_capital
    total_return, annual_return, max_drawdown
    sharpe_ratio, win_rate, profit_factor, total_trades
    trades: List[Trade]
    equity_curve: pd.Series
    def summary() -> str

class Backtester:
    def __init__(self, fetcher: DataFetcher, config: dict)
    def run(strategy, stock_codes: List[str], start, end) -> BacktestResult
```

---

### 2.6 strategies/base.py

```python
@dataclass
class Signal:
    code: str
    name: str
    signal: str        # "BUY" / "SELL" / "WATCH"
    price: float
    reason: str
    score: float       # 0~100
    extra: dict

class BaseStrategy(ABC):
    name: str
    description: str
    version: str
    def __init__(self, params: dict = None)
    def generate_signals(df: DataFrame, code: str, name: str) -> List[Signal]  # 抽象
    def requires_bars() -> int   # 默认 60
```

---

### 2.7 内置策略

| 策略类 | key | 信号逻辑 | requires_bars |
|---|---|---|---|
| `MACrossStrategy` | `ma_cross` | MA5 上穿/下穿 MA20 + 量能 | `max(slow*2, 60)` |
| `MACDRSIStrategy` | `macd_rsi` | MACD 金叉 + RSI 超卖回升 | 60 |
| `VolPriceBreakoutStrategy` | `vol_price_breakout` | 突破近 N 日高点 + 放量 | `lookback + 10` |

---

### 2.8 其他模块

```python
# notifier/push.py
class Notifier:
    def send_signals(signals: List[Signal], title: str)
    def send_text(title: str, content: str)

# trader/assistant.py
class TradeAssistant:
    def generate_orders(signals, current_capital=None) -> List[TradeOrder]
    def list_pending_orders() -> List[dict]
    def confirm_order(order_id) -> bool
    def cancel_order(order_id) -> bool
    def export_orders_csv(output_path=None) -> str

# signals/manager.py
def save_signals(signals: List[Signal], date: str = None) -> str
def load_signals(date: str) -> List[dict]

# tools/plot_kline.py
# CLI: python3 tools/plot_kline.py 600519 000001 --start 2024-01-01
```

---

## 三、已知 Bug（需优先修复）

### 🔴 高优先级（运行时崩溃）

| 位置 | 问题 | 影响 |
|---|---|---|
| `main.py:cmd_dbstats` | 访问 `s['stocks']`/`s['total_rows']`/`s['kline_dir']`，但 `store.stats()` 返回结构已变更，这些 key 不存在 | `python3 main.py dbstats` 必崩 |
| `backtest/engine.py:108-142` | 对每只股票执行了两次相同的 `min_bars` 判断和 `data[code]` 赋值，第二次在 `continue` 之后仍执行，`miss` 计数偏高 | 回测结果统计不准 |
| `strategies/builtin/ma_cross.py` | `reason` 字符串中 `'，量能放大{:.1f}x'.format(...)` 写在普通字符串里而非 f-string，输出字面量 `{:.1f}x` | 信号原因描述错误 |

### 🟡 中优先级（逻辑错误）

| 位置 | 问题 |
|---|---|
| `strategies/builtin/macd_rsi.py` | RSI 超卖回升只用于加分，不满足时仍发 BUY，与"双重过滤"描述不符 |
| `strategies/builtin/vol_price_breakout.py` | 无 SELL 信号，回测中持仓永不平仓 |
| `engine/scanner.py` | `SCAN` 配置中的 `min_volume`/`min_price`/`exclude_new` 未实现 |
| `signals/manager.py` | `cmd_scan` 未调用 `save_signals`，扫描结果不持久化 |
| `data/fetcher.py` | `__del__` 中 `_bs_logged_in` 永远是初始值 `False`，析构时不执行 logout |
| `data/downloader.py` | `_is_trading_day` 只排除周末，节假日会触发无效更新 |

### 🟢 低优先级（设计缺陷）

| 位置 | 问题 |
|---|---|
| `data/store.py:stats()` | 每次全量扫描所有 parquet 文件读行数，数据量大时慢 |
| `data/store.py:trading_days_since_ipo` | 用自然日而非交易日，命名有误导性 |
| `backtest/engine.py` | 最多持仓 10 只硬编码，未从 config 读取 |
| `backtest/engine.py` | 夏普比率未扣除无风险利率 |
| `backtest/engine.py` | `fetcher` 参数接收但完全未使用 |
| `config/settings.py` | `BACKTEST["benchmark"]` 配置了基准指数但回测未实现对比 |
| `strategies/base.py` | `signal` 字段无枚举约束，靠字符串约定 |
| `tools/plot_kline.py` | `store.load` 未传 `asset_type`/`market`，无法绘制指数/ETF |

---

## 四、单元测试建议

### 优先测试项

```
tests/
├── test_store.py          # save/load/exists/last_date/stats 的正确性
├── test_downloader.py     # _download_one 返回值、断点续传逻辑
├── test_strategies.py     # 三个策略的信号生成（构造 mock DataFrame）
├── test_backtest.py       # BacktestResult 指标计算正确性
└── test_signals.py        # save_signals/load_signals 往返一致性
```

### 关键测试用例

**test_store.py**
- `save` 后 `load` 数据一致
- 重复 `save` 自动去重
- `start`/`end` 过滤正确
- `stats()` 返回结构验证（`result["cn"]["stock"]["count"]` 等）
- `get_ipo_date` 兼容新旧 key 格式（`cn:600519` 和 `600519`）

**test_strategies.py**
- 构造标准 DataFrame（含 date/open/high/low/close/volume 列）
- 验证金叉时 `MACrossStrategy` 返回 BUY 信号
- 验证数据不足时返回空列表
- 验证 `score` 在 0~100 范围内
- 验证 `ma_cross` 的 reason 字符串不含字面量 `{:.1f}x`（当前 bug）

**test_backtest.py**
- 构造简单价格序列验证 `total_return` 计算
- 验证 `miss` 计数不重复（当前 bug）
- 验证空股票池时使用本地全市场数据

**test_signals.py**
- `save_signals` 后 `load_signals` 返回相同数量
- 同一天多次 `save` 不丢失数据（当前 bug：会覆盖）

### 运行测试

```bash
cd /home/wz/alpha量化
source venv/bin/activate
pip install pytest pytest-cov
pytest tests/ -v --cov=. --cov-report=term-missing
```

---

## 五、CLI 命令速查

```bash
# 数据管理
python3 main.py init                          # 全量下载（stock+index+etf）
python3 main.py init --types stock            # 只下载A股
python3 main.py init --force                  # 强制重新下载
python3 main.py update                        # 每日增量更新
python3 main.py dbstats                       # 查看数据库统计（当前有bug）

# 分析
python3 main.py scan                          # 全市场扫描
python3 main.py scan --date 2025-01-10        # 指定日期
python3 main.py backtest --strategy macd_rsi  # 全市场回测
python3 main.py backtest --strategy ma_cross --start 2024-01-02 --end 2025-12-31
python3 main.py backtest --codes 600519 000001 300750

# 交易辅助
python3 main.py orders                        # 查看待确认指令单
python3 main.py confirm <order_id>
python3 main.py cancel <order_id>

# 工具
python3 tools/plot_kline.py 600519 000001 --start 2024-01-01

# 服务
python3 main.py web                           # Web看板 http://localhost:8888
python3 main.py schedule                      # 定时任务（收盘后自动更新+扫描）
```

---

## 六、依赖清单

```
akshare>=1.12.0
baostock>=0.8.8
pandas>=2.0.0
numpy>=1.24.0
pyarrow>=12.0.0
mplfinance>=0.12.0
matplotlib>=3.7.0
flask>=3.0.0
apscheduler<4
pytz
requests>=2.31.0
python-dotenv>=1.0.0
rich>=13.0.0
```

---

## 七、环境变量（.env）

```bash
TUSHARE_TOKEN=        # Tushare Pro Token（可选）
WECOM_WEBHOOK=        # 企业微信机器人 Webhook
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
EMAIL_USER=
EMAIL_PASS=
EMAIL_TO=
TELEGRAM_TOKEN=       # 可选
TELEGRAM_CHAT_ID=     # 可选
```
