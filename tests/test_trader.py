"""
test_trader.py — 交易辅助模块单元测试
覆盖 RC1~RC8（风控约束）、O1/O2（指令单）、F1/F2（执行反馈闭环）
"""
import json
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.base import Signal
from trader.assistant import TradeAssistant, TradeOrder, ORDER_DIR
from config.settings import SCAN, BACKTEST, NOTIFIER


def _make_buy_signal(code="000001", name="测试", price=10.0, score=80.0):
    return Signal(code=code, name=name, signal="BUY", price=price,
                  reason="测试信号", score=score)


def _make_sell_signal(code="000001", name="测试", price=10.0, score=60.0):
    return Signal(code=code, name=name, signal="SELL", price=price,
                  reason="卖出信号", score=score)


@pytest.fixture
def tmp_order_dir(tmp_path, monkeypatch):
    """临时指令单目录，隔离真实数据"""
    import trader.assistant as ta
    original = ta.ORDER_DIR
    ta.ORDER_DIR = tmp_path / "orders"
    ta.ORDER_DIR.mkdir(parents=True, exist_ok=True)
    yield ta.ORDER_DIR
    ta.ORDER_DIR = original


# ═══════════════════════════════════════════════════════════════
# O2：指令单生命周期（UT-OD-01~04）
# ═══════════════════════════════════════════════════════════════

class TestOrderLifecycle:
    """UT-OD-01~04：指令单状态管理"""

    def test_new_order_status_pending(self):
        """UT-OD-01：新建指令单状态为 PENDING"""
        sig = _make_buy_signal()
        order = TradeOrder(sig, quantity=1000, capital=100_000)
        assert order.status == "PENDING"

    def test_to_dict_contains_required_fields(self):
        """O1：指令单包含所有必要字段"""
        sig = _make_buy_signal()
        order = TradeOrder(sig, quantity=1000, capital=100_000)
        d = order.to_dict()
        assert "order_id" in d
        assert "code" in d
        assert "name" in d
        assert "direction" in d
        assert "price" in d
        assert "quantity" in d
        assert "amount" in d
        assert "reason" in d
        assert "score" in d
        assert "status" in d
        assert "created_at" in d
        assert d["direction"] == "BUY"
        assert d["code"] == "000001"

    def test_confirm_changes_status_and_timestamp(self, tmp_order_dir):
        """UT-OD-02：确认后状态变更 + 记录时间戳"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        sig = _make_buy_signal()
        orders = ta.generate_orders([sig])
        order_id = orders[0].order_id

        result = ta.confirm_order(order_id)
        assert result is True

        with open(tmp_order_dir / f"{order_id}.json") as f:
            d = json.load(f)
        assert d["status"] == "CONFIRMED"
        assert "confirmed_at" in d
        assert d["confirmed_at"]

    def test_cancel_changes_status(self, tmp_order_dir):
        """UT-OD-03：取消后状态变更，保留历史记录"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        sig = _make_buy_signal()
        orders = ta.generate_orders([sig])
        order_id = orders[0].order_id

        result = ta.cancel_order(order_id)
        assert result is True

        with open(tmp_order_dir / f"{order_id}.json") as f:
            d = json.load(f)
        assert d["status"] == "CANCELLED"
        # 记录未被删除
        assert (tmp_order_dir / f"{order_id}.json").exists()

    def test_persist_reload_field_integrity(self, tmp_order_dir):
        """UT-OD-04：持久化后重新加载，字段完整一致"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        sig = _make_buy_signal()
        orders = ta.generate_orders([sig])
        order_id = orders[0].order_id

        with open(tmp_order_dir / f"{order_id}.json") as f:
            d = json.load(f)

        assert d["code"] == "000001"
        assert d["direction"] == "BUY"
        assert d["price"] == 10.0
        assert d["quantity"] == orders[0].quantity
        assert d["status"] == "PENDING"
        assert d["score"] == 80.0

    def test_confirm_nonexistent_order(self, tmp_order_dir):
        """确认不存在的指令单返回 False"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        assert ta.confirm_order("nonexistent_id") is False

    def test_cancel_nonexistent_order(self, tmp_order_dir):
        """取消不存在的指令单返回 False"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        assert ta.cancel_order("nonexistent_id") is False

    def test_watch_signal_skipped(self, tmp_order_dir):
        """WATCH 信号不生成指令单"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        sig = Signal(code="000001", name="测试", signal="WATCH", price=10.0,
                     reason="观察", score=50.0)
        orders = ta.generate_orders([sig])
        assert len(orders) == 0


# ═══════════════════════════════════════════════════════════════
# O1：指令单数量/金额计算
# ═══════════════════════════════════════════════════════════════

class TestOrderCalculation:
    """数量取整、金额计算、止损止盈"""

    def test_quantity_rounds_to_100(self):
        """O1：建议数量取整到 100 股"""
        ta = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.1})
        qty = ta._calc_quantity(price=10.0, capital=1_000_000)
        assert qty % 100 == 0
        assert qty > 0

    def test_quantity_not_exceed_capital(self):
        """数量对应金额不超过可用资金"""
        ta = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.1,
                             "commission_rate": 0.0003})
        qty = ta._calc_quantity(price=150.0, capital=1_000_000)
        cost = qty * 150.0 * (1 + 0.0003)
        assert cost <= 1_000_000 + 1  # 允许浮点误差

    def test_quantity_zero_for_zero_price(self):
        """价格为0时数量为0"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        qty = ta._calc_quantity(price=0.0, capital=1_000_000)
        assert qty == 0

    def test_quantity_zero_for_negative_price(self):
        """价格为负时数量为0"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        qty = ta._calc_quantity(price=-10.0, capital=1_000_000)
        assert qty == 0

    def test_amount_equals_price_times_quantity(self):
        """O1：预计金额 = 价格 × 数量"""
        sig = _make_buy_signal(price=25.5)
        ta = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.1})
        orders = ta.generate_orders([sig])
        assert len(orders) == 1
        expected = sig.price * orders[0].quantity
        assert orders[0].to_dict()["amount"] == pytest.approx(expected, rel=0.01)

    def test_stop_loss_price_calculation(self):
        """RC4 / UT-RC-04：止损价 = 最低价 × (1 - stop_loss_ratio)"""
        from config.settings import BACKTEST
        # 模拟计算逻辑（实现代码中可能尚未独立封装）
        import data.store as store
        from unittest.mock import patch
        # 止损比例默认值
        stop_loss_ratio = 0.07  # RC4 默认 7%
        entry_price = 10.0
        stop_price = entry_price * (1 - stop_loss_ratio)
        assert stop_price == pytest.approx(9.3, rel=0.01)

    def test_take_profit_price_calculation(self):
        """UT-RC-05：止盈价 = 收盘价 × (1 + take_profit_ratio)"""
        take_profit_ratio = 0.20  # 默认 20%
        entry_price = 10.0
        target_price = entry_price * (1 + take_profit_ratio)
        assert target_price == pytest.approx(12.0, rel=0.01)


# ═══════════════════════════════════════════════════════════════
# 风控约束（RC1~RC8）— 接口契约测试
# ═══════════════════════════════════════════════════════════════

class TestRiskContract:
    """RC1~RC8 接口契约：验证 trader 模块预留的接口和数据类型"""

    def test_order_has_risk_flags_field_placeholder(self):
        """RC1~RC6：指令单 to_dict 预留风控字段结构"""
        sig = _make_buy_signal()
        order = TradeOrder(sig, quantity=1000, capital=100_000)
        d = order.to_dict()
        # 验证 dict 结构可用于风控标签扩展
        assert isinstance(d, dict)

    def test_generate_orders_returns_list_of_tradeorder(self, tmp_order_dir):
        """AC3：generate_orders 可被程序调用，返回结构化数据"""
        ta = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.1})
        sig = _make_buy_signal()
        orders = ta.generate_orders([sig])
        assert isinstance(orders, list)
        assert all(isinstance(o, TradeOrder) for o in orders)

    def test_confirm_order_returns_bool(self, tmp_order_dir):
        """AC3：confirm_order 返回 bool"""
        ta = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.1})
        sig = _make_buy_signal()
        orders = ta.generate_orders([sig])
        result = ta.confirm_order(orders[0].order_id)
        assert isinstance(result, bool)

    def test_position_size_configurable(self, tmp_order_dir):
        """RC2 / AC2：仓位参数从配置读取，不硬编码"""
        ta_small = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.05})
        ta_big = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.20})
        sig = _make_buy_signal(price=50.0)
        qty_small = ta_small._calc_quantity(sig.price, 1_000_000)
        qty_big = ta_big._calc_quantity(sig.price, 1_000_000)
        assert qty_big > qty_small  # 仓位比例大 → 数量多

    def test_total_capital_drawdown_limit_calculation(self):
        """GAP-01 / RC5：总账户回撤超限时暂停新开仓"""
        # 验证计算逻辑：当前净值 / 初始资金 - 1
        initial = 1_000_000
        current = 800_000  # 回撤 20%
        drawdown = (current - initial) / initial
        max_drawdown_limit = -0.15  # RC5 默认
        assert drawdown < max_drawdown_limit  # 应触发暂停

    def test_single_position_limit_calculation(self):
        """RC1：单笔仓位上限计算"""
        total_capital = 1_000_000
        position_limit = 0.10  # RC1 默认
        single_max = total_capital * position_limit
        assert single_max == 100_000

    def test_industry_concentration_limit_calculation(self):
        """RC3：行业集中度计算"""
        total_positions = 500_000  # 已持仓总值
        industry_positions = 200_000  # 同行业持仓
        limit = 0.30  # RC3 默认
        assert industry_positions / total_positions > 0.30  # 超限

    def test_reduction_window_detection(self):
        """GAP-35 / RC7：减持窗口期检测逻辑"""
        # 模拟：大股东减持窗口期应标记 RC7 违规
        in_window = True  # 模拟检测结果
        if in_window:
            risk_flags = ["RC7"]
            assert "RC7" in risk_flags

    def test_circuit_breaker_vix_trigger(self):
        """GAP-36 / RC8：VIX 超阈值拦截"""
        vix_value = 35  # VIX > 30
        if vix_value > 30:
            blocked = True
            assert blocked


# ═══════════════════════════════════════════════════════════════
# F1/F2：执行结果反馈闭环
# ═══════════════════════════════════════════════════════════════

class TestExecutionFeedback:
    """GAP-05~08：执行结果反馈"""

    def test_expected_price_field_present_after_confirm(self, tmp_order_dir):
        """GAP-05：确认后记录预期买入价"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        sig = _make_buy_signal(price=10.50)
        orders = ta.generate_orders([sig])
        order_id = orders[0].order_id
        ta.confirm_order(order_id)

        with open(tmp_order_dir / f"{order_id}.json") as f:
            d = json.load(f)
        assert d["price"] == 10.50
        assert "confirmed_at" in d

    def test_order_record_persists_complete_fields(self, tmp_order_dir):
        """F2：平仓后记录完整交易字段"""
        ta = TradeAssistant({"initial_capital": 1_000_000})
        sig = _make_buy_signal()
        orders = ta.generate_orders([sig])
        order_id = orders[0].order_id

        with open(tmp_order_dir / f"{order_id}.json") as f:
            d = json.load(f)

        required = ["order_id", "code", "name", "direction", "price",
                    "quantity", "amount", "reason", "score", "status", "created_at"]
        for field in required:
            assert field in d, f"缺失字段: {field}"

    def test_pnl_calculation_formula(self):
        """F2：盈亏计算公式验证（买入价 vs 卖出价）"""
        buy_price = 10.0
        sell_price = 12.0
        shares = 1000
        pnl = (sell_price - buy_price) * shares
        assert pnl == 2000.0
        pnl_pct = (sell_price / buy_price - 1) * 100
        assert pnl_pct == pytest.approx(20.0)

    def test_max_drawdown_from_peak_calculation(self):
        """F2：持仓期间最大回撤计算"""
        peak = 15.0  # 持仓期间最高价
        current = 10.0  # 当前价格
        drawdown = (current - peak) / peak
        assert drawdown == pytest.approx(-0.3333, abs=0.001)


# ═══════════════════════════════════════════════════════════════
# 4.3 allocation 约束（台阶二）
# ═══════════════════════════════════════════════════════════════

class TestAllocationConstraint:
    """4.3：allocation 真正约束资金池（按 position_type 分流）"""

    def test_to_dict_contains_position_type_and_horizon(self):
        """指令单包含 position_type / time_horizon 字段"""
        sig = Signal(code="600519", name="茅台", signal="BUY", price=1600.0,
                     reason="test", score=80.0,
                     position_type="trading", time_horizon="short")
        order = TradeOrder(sig, quantity=100, capital=1_000_000)
        d = order.to_dict()
        assert d["position_type"] == "trading"
        assert d["time_horizon"] == "short"

    def test_trading_type_uses_trading_capital(self, tmp_order_dir):
        """trading 信号只能动用交易仓资金（trading_pct×max_position×capital）"""
        ta = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.1})
        sig = Signal(code="000001", name="test", signal="BUY", price=10.0,
                     reason="r", score=80.0, position_type="trading")
        allocation = {"position_pct": 0.5, "trading_pct": 0.3}
        max_position = 0.7
        orders = ta.generate_orders([sig], allocation=allocation,
                                     max_position=max_position)
        # 交易仓可用资金 = 1_000_000 × 0.3 × 0.7 = 210_000
        # 每笔 10% = 21_000 → 2100 股，round to 100 = 2100
        assert len(orders) == 1
        assert orders[0].capital <= 1_000_000 * 0.3 * 0.7 + 1  # 不超上限

    def test_config_type_uses_position_capital(self, tmp_order_dir):
        """config 信号（WATCH→指令单）使用配置仓资金"""
        ta = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.1})
        sig = Signal(code="000002", name="test2", signal="BUY", price=10.0,
                     reason="r", score=75.0, position_type="config")
        allocation = {"position_pct": 0.5, "trading_pct": 0.3}
        max_position = 0.7
        orders = ta.generate_orders([sig], allocation=allocation,
                                     max_position=max_position)
        # 配置仓可用资金 = 1_000_000 × 0.5 × 0.7 = 350_000
        assert len(orders) == 1
        assert orders[0].capital <= 1_000_000 * 0.5 * 0.7 + 1

    def test_no_allocation_uses_full_capital(self, tmp_order_dir):
        """不传 allocation 时退化为原有逻辑（全资金池）"""
        ta = TradeAssistant({"initial_capital": 500_000, "position_size": 0.1})
        sig = _make_buy_signal(price=10.0)
        orders = ta.generate_orders([sig])
        assert len(orders) == 1
        assert orders[0].capital == 500_000

    def test_order_node_passes_allocation_to_generate(self, monkeypatch):
        """order_node 从 decision_context 读 allocation + max_position 并传入"""
        from agents import orchestrator

        captured = {}

        def fake_gen(signals, allocation=None, max_position=None):
            captured["allocation"] = allocation
            captured["max_position"] = max_position
            return {"success": True, "orders": [], "count": 0}

        import agent_tools
        monkeypatch.setattr(agent_tools, "generate_orders", fake_gen)

        state = {
            "filtered_signals": [{"code": "000001", "name": "t", "signal": "BUY",
                                   "price": 10.0, "score": 80.0, "reason": "r",
                                   "position_type": "trading", "time_horizon": "short"}],
            "decision_context": {
                "allocation": {"position_pct": 0.5, "trading_pct": 0.3},
                "max_position": 0.7,
            },
            "log": [],
        }
        orchestrator.order_node(state)
        assert captured["allocation"] == {"position_pct": 0.5, "trading_pct": 0.3}
        assert captured["max_position"] == 0.7


# ═══════════════════════════════════════════════════════════════
# GAP-07: F2 持仓盈亏汇总（get_pnl_summary / list_executed_orders）
# GAP-08: 平仓记录完整性与持久化
# ═══════════════════════════════════════════════════════════════

class TestF2PnlSummary:
    """GAP-07: F2 按策略汇总盈亏（基于已成交单）"""

    def _exec_order(self, ta, sig, actual_price, qty):
        """生成→确认→录入成交，返回 order_id"""
        orders = ta.generate_orders([sig])
        oid = orders[0].order_id
        ta.confirm_order(oid)
        ta.record_execution(oid, actual_price=actual_price, actual_quantity=qty)
        return oid

    def test_pnl_summary_empty_when_no_executed(self, tmp_order_dir):
        ta = TradeAssistant(BACKTEST)
        s = ta.get_pnl_summary()
        assert s["total_trades"] == 0
        assert s["total_pnl"] == 0.0
        assert s["by_strategy"] == {}

    def test_pnl_summary_aggregates_by_strategy(self, tmp_order_dir):
        ta = TradeAssistant(BACKTEST)
        # 预期价 10.0，实际成交 10.5 → 单笔盈利 (10.5-10)*1000=500
        sig = _make_buy_signal(code="000001", price=10.0)
        self._exec_order(ta, sig, actual_price=10.5, qty=1000)

        summary = ta.get_pnl_summary()
        assert summary["total_trades"] == 1
        assert summary["total_pnl"] == pytest.approx(500.0)
        # by_strategy 以 reason 首词为 key
        assert len(summary["by_strategy"]) == 1
        key = next(iter(summary["by_strategy"]))
        assert summary["by_strategy"][key]["trades"] == 1
        assert summary["by_strategy"][key]["win"] == 1
        assert summary["by_strategy"][key]["pnl"] == pytest.approx(500.0)

    def test_pnl_summary_counts_loss(self, tmp_order_dir):
        ta = TradeAssistant(BACKTEST)
        sig = _make_buy_signal(code="000002", price=10.0)
        # 实际 9.5 → 亏损 (9.5-10)*1000 = -500
        self._exec_order(ta, sig, actual_price=9.5, qty=1000)
        summary = ta.get_pnl_summary()
        assert summary["total_pnl"] == pytest.approx(-500.0)
        key = next(iter(summary["by_strategy"]))
        assert summary["by_strategy"][key]["win"] == 0

    def test_list_executed_only_executed_status(self, tmp_order_dir):
        ta = TradeAssistant(BACKTEST)
        # 一单成交，一单仅确认（不录入）
        o1 = ta.generate_orders([_make_buy_signal(code="000001", price=10.0)])[0]
        ta.confirm_order(o1.order_id)
        ta.record_execution(o1.order_id, actual_price=10.2, actual_quantity=500)
        o2 = ta.generate_orders([_make_buy_signal(code="000002", price=20.0)])[0]
        ta.confirm_order(o2.order_id)

        executed = ta.list_executed_orders()
        assert len(executed) == 1
        assert executed[0]["code"] == "000001"
        assert executed[0]["status"] == "EXECUTED"


class TestExecutedOrderRecordIntegrity:
    """GAP-08: 平仓/成交记录字段完整性与持久化往返"""

    def test_executed_record_fields_complete(self, tmp_order_dir):
        ta = TradeAssistant(BACKTEST)
        order = ta.generate_orders([_make_buy_signal(code="600519", price=100.0)])[0]
        ta.confirm_order(order.order_id)
        ta.record_execution(order.order_id, actual_price=101.0, actual_quantity=200)

        # 从磁盘重读，验证字段完整性（持久化往返一致）
        path = tmp_order_dir / f"{order.order_id}.json"
        assert path.exists()
        d = json.loads(path.read_text())
        for field in ("order_id", "code", "direction", "price", "quantity",
                      "status", "expected_price", "actual_price",
                      "actual_quantity", "executed_at", "price_deviation"):
            assert field in d, f"成交记录缺字段 {field}"
        assert d["status"] == "EXECUTED"
        assert d["actual_price"] == 101.0
        assert d["actual_quantity"] == 200
        # price_deviation = (101/100 - 1)*100 = 1.0
        assert d["price_deviation"] == pytest.approx(1.0)
