# Alpha量化 — 测试与验证

> 测试分层定义、当前测试文件、关键测试用例模板、运行规范。
> 最后更新：2026-06-11（新增：分层决策测试覆盖）
>
> **项目基础信息见 `docs/project.md`**
> **需求与测试追溯矩阵（含完整 GAP 编号覆盖缺口）见 `docs/traceability.md`**
> **测试级别权威定义及场景对应关系见 `docs/requirements.md` 第六章**

---

## 一、测试分层策略

四个测试级别，详细定义和触发时机见 `docs/requirements.md` 第六章 6.0 节：

```
┌─────────────────────────────────────────────────────┐
│  L4 验收测试                                         │
│  跨真实交易日观察，Phase 1 上线前执行一次             │
│  不可用自动化替代                                     │
├─────────────────────────────────────────────────────┤
│  L3 系统测试（< 30 分钟）                            │
│  允许访问真实数据源（有超时保护）                     │
│  重大变更后、发布前手动触发                           │
├─────────────────────────────────────────────────────┤
│  L2 集成测试（< 10 分钟）                            │
│  依赖本地已有数据，不走网络                           │
│  每次 git commit 前自动运行                           │
├─────────────────────────────────────────────────────┤
│  L1 单元测试（< 1 分钟）                             │
│  无外部依赖，全部 mock，可完全离线运行                │
│  每次 git commit 前自动运行                           │
└─────────────────────────────────────────────────────┘
```

**原则：**
- 网络依赖（DataFetcher、downloader 网络逻辑）一律用 mock，不做真实网络请求
- 数据文件依赖（store.load）用 `tmp_path` fixture 构造临时 Parquet，不依赖本地数据库
- Agent Tool Layer 的测试重点是输入输出契约，不测 LLM 本身

---

## 二、当前测试文件

| 文件 | 覆盖内容 | 状态 |
|---|---|---|
| `tests/test_store.py` | save/load/exists/last_date/stats/IPO 接口 | 已有，覆盖较全 |
| `tests/test_strategies.py` | MACross/MACD_RSI/VolBreakout 信号生成、边界条件、score 范围 | 已有，ETFMomentum 缺失 |
| `tests/test_backtest.py` | BacktestResult 指标计算、数据加载逻辑 | 已有 |
| `tests/test_signals.py` | save_signals/load_signals 往返一致性 | 已有 |
| `tests/test_downloader.py` | `_is_trading_day`、日期格式、DEFAULT_START | 已有，仅覆盖工具函数 |
| `tests/test_agent_tools.py` | agent_tools 18 个单元测试（data/strategy/trade） | 已有，全通过 |
| **`tests/test_macro_regime.py`** | **L1 宏观层分层决策测试（阶段一新增）** | **✅ 10 passed, 1 skipped** |

### 2.1 分层决策测试覆盖（2026-06-11 新增）

`tests/test_macro_regime.py` 对应 `docs/requirements.md` 第八章测试需求（NT-L1-01~04）：

| 测试类 | 测试用例 | 覆盖需求 | 状态 |
|---|---|---|---|
| `TestMacroRegimeOutput` | `test_evaluate_market_condition_structure` | L1-R1 结构化输出 | ✅ 通过 |
| | `test_regime_defensive_scenario` | L1-R2 防御模式映射 | ✅ 通过 |
| | `test_regime_aggressive_scenario` | L1-R2 进攻模式映射 | ✅ 通过 |
| `TestStrategySelection` | `test_select_strategies_defensive` | L3-R1 策略选择 | ✅ 通过 |
| | `test_select_strategies_aggressive` | L3-R1 策略选择 | ✅ 通过 |
| | `test_route_after_macro_logic` | L1-R4 条件边路由 | ✅ 通过 |
| `TestDecisionContext` | `test_decision_context_structure` | L1-R3 数据契约 | ✅ 通过 |
| | `test_workflow_state_extension` | AC10 向后兼容 | ✅ 通过 |
| `TestDecisionLogPersistence` | `test_log_result_structure` | OB-R1 日志结构 | ✅ 通过 |
| | `test_log_file_naming_convention` | 日志命名规范 | ✅ 通过 |
| `TestWorkflowIntegration` | `test_full_workflow_with_macro_regime` | L1-L3 贯通 | ⏭️ Skipped（需真实数据） |

---

## 三、覆盖缺口

以需求编号为主的完整覆盖缺口分析见 `docs/traceability.md` 第四节，包含：
- **GAP-01~18**：Phase 1 验收前必须补充（4.1 节）
- **GAP-19~30**：建议在 Phase 1 稳定后补充（4.2 节）
- 不需要自动化测试的需求列表（4.3 节）

以下按模块维度补充开发视角的缺口描述，与 traceability.md 互为补充：

### 3.1 策略层（对应 GAP-02/03/04 及 traceability.md S2/S4 相关）

**strategies/builtin/etf_momentum.py**
- ETFMomentumStrategy 信号生成（WATCH/SELL）
- 动量计算正确性，数据不足时返回空列表

**engine/scanner.py**
- 信号合并逻辑（同标的同方向取最高分、原因拼接）→ 对应 UT-MG-01~03
- `_get_filtered_stock_list` 北交所/新股过滤 → 对应 GAP-02/GAP-03
- 成交额低于阈值过滤 → 对应 GAP-04

**signals/manager.py**
- 同一天多次 `save_signals` 的行为需明确（覆盖 vs 追加）

### 3.2 数据层（对应 GAP-11~18 及 traceability.md DS/DR 相关）

**data/downloader.py**
- `_is_trading_day` 节假日判断（当前只排除周末）→ 对应 ST-CA-01/02
- 断点续传逻辑：`force=True` 时覆盖 → 对应 UT-DS-04/05

**data/store_*.py（各扩展域）**
- `store_meta / store_fundamental / store_factor / store_macro / store_tushare` 的 save/load 往返一致性 → 对应 UT-DS-01~08
- margin（D7）字段结构验证 → 对应 GAP-12/13

### 3.3 Agent Tool Layer（已建设，`tests/test_agent_tools.py`，对应 GAP-28）

**agent_tools/data_tools.py**
- `check_data_freshness` 返回结构（`domains` / `stale` 字段）
- `update_domain` 非法域校验 + 7 域路由（mock downloader）+ 异常捕获 `success=False`
- `get_active_event_windows` E1 窗口边界 + `days_to_event` 计算
- `check_reduce_window` RC7 减持窗口期 + holder/end_date 回填
- `query_kline` 摘要结构（末3行 sample、NaN→None）+ 无数据分支
- `validate_data` 非 market 域拒绝、空数据标记、干净数据通过

**agent_tools/strategy_tools.py**
- `run_scan` 返回结构与 Signal 字段一致性
- `run_backtest` 参数透传正确性

**agent_tools/trade_tools.py**
- `filter_signals_by_risk` 过滤逻辑
- `generate_orders` 金额计算、预留字段存在性 → 对应 GAP-29
- `get_pending_orders` / `cancel_order` / `confirm_order` 生命周期封装

---

## 四、关键测试用例模板

### 4.1 策略测试 mock 数据构造

```python
import pandas as pd
import numpy as np

def make_df(n=100, trend="up") -> pd.DataFrame:
    """构造标准测试 DataFrame，含股票所有字段"""
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    if trend == "up":
        close = np.linspace(10, 15, n) + np.random.randn(n) * 0.1
    elif trend == "down":
        close = np.linspace(15, 10, n) + np.random.randn(n) * 0.1
    else:
        close = np.ones(n) * 10 + np.random.randn(n) * 0.3

    return pd.DataFrame({
        "date":         dates,
        "open":         close * 0.99,
        "high":         close * 1.01,
        "low":          close * 0.98,
        "close":        close,
        "preclose":     np.roll(close, 1),
        "volume":       np.random.randint(1_000_000, 10_000_000, n).astype(float),
        "amount":       close * np.random.randint(1_000_000, 10_000_000, n),
        "turn":         np.random.uniform(0.5, 5.0, n),
        "trade_status": "1",
        "pct_chg":      np.random.uniform(-5, 5, n),
        "pe_ttm":       np.random.uniform(10, 50, n),
        "pb_mrq":       np.random.uniform(1, 5, n),
        "ps_ttm":       np.random.uniform(1, 10, n),
        "pcf_ttm":      np.random.uniform(5, 30, n),
        "is_st":        "0",
    })
```

### 4.2 store 测试 fixture

```python
import pytest

@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    """将 store 的数据目录重定向到临时目录，避免污染真实数据"""
    import data.store as store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "market")
    return store
```

### 4.3 Tool Layer 测试模式

```python
from unittest.mock import patch

def test_check_data_freshness_stale():
    """数据落后时 stale 列表非空（对应 IT-DF-02）"""
    with patch("agent_tools.data_tools.store") as mock_store:
        mock_store.last_date.return_value = "2026-05-20"  # 模拟落后8天
        result = check_data_freshness()
    assert result["success"] is True
    assert "market/stock" in result["stale"]

def test_update_domain_failure():
    """downloader 抛异常时返回 success=False（对应 GAP-28）"""
    with patch("agent_tools.data_tools.KlineDownloader") as mock_dl:
        mock_dl.return_value.update_db.side_effect = RuntimeError("网络超时")
        result = update_domain("market")
    assert result["success"] is False
    assert "网络超时" in result["error"]
```

---

## 五、运行规范

### 5.1 运行测试

```bash
cd /home/wz/alpha量化
source venv/bin/activate

# L1+L2：每次 commit 前运行
pytest tests/ -v

# 带覆盖率报告
pytest tests/ -v --cov=. --cov-report=term-missing \
    --cov-omit="venv/*,data/market/*,data/fundamental/*"

# 只运行某个模块
pytest tests/test_strategies.py -v

# 只运行某个用例
pytest tests/test_strategies.py::TestMACross::test_buy_signal -v

# L3：重大变更后手动触发（含真实数据源访问，需激活环境变量）
pytest tests/integration/ -v --timeout=30
```

### 5.2 安装测试依赖

```bash
pip install pytest pytest-cov pytest-timeout
```

### 5.3 CI 建议

当前无 CI，建议在 `scripts/` 下新增 `run_tests.sh`，在每次 `git push` 前手动运行：

```bash
#!/bin/bash
cd /home/wz/alpha量化
source venv/bin/activate
pytest tests/ -v --tb=short
```

### 5.4 不测什么

- `DataFetcher` 的真实网络请求（用 mock 替代）
- `Notifier` 的真实推送（用 mock 替代）
- `downloader_*.py` 的真实下载逻辑（用 mock 替代）
- LLM 的推理结果（不可重现，不纳入自动化测试）
- Web 看板的 HTML 渲染（手动验证）
- 辅助分析的"主观判断"部分（设计约定，见 `docs/traceability.md` 4.3 节）
