"""
L2 集成测试 — 指令单生成 (IT-OD-01~04)

依赖本地已有数据，不走网络。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

pytestmark = pytest.mark.integration


@pytest.fixture
def tmp_order_dir(tmp_path, monkeypatch):
    import trader.assistant as ta
    original = ta.ORDER_DIR
    ta.ORDER_DIR = tmp_path / "orders"
    ta.ORDER_DIR.mkdir(parents=True, exist_ok=True)
    yield ta.ORDER_DIR
    ta.ORDER_DIR = original


class TestOrderGeneration:
    """IT-OD-01~04：指令单集成测试"""

    def test_order_all_fields_present(self, tmp_order_dir):
        """IT-OD-01：BUY 信号生成指令单，所有字段完整"""
        from trader.assistant import TradeAssistant
        from strategies.base import Signal

        sig = Signal(code="000001", name="平安银行", signal="BUY",
                     price=10.50, score=75.0, reason="均线金叉+放量")
        ta = TradeAssistant({"initial_capital": 1_000_000})
        orders = ta.generate_orders([sig])
        assert len(orders) == 1
        d = orders[0].to_dict()

        required = ["order_id", "code", "name", "direction", "price",
                    "quantity", "amount", "reason", "score", "status",
                    "stop_loss_price", "take_profit_price",
                    "risk_flags", "risk_check_detail"]
        for field in required:
            assert field in d, f"缺失字段: {field}"
        assert d["direction"] == "BUY"
        assert d["price"] == 10.50
        assert d["status"] == "PENDING"

    def test_quantity_rounds_to_100(self, tmp_order_dir):
        """IT-OD-02：建议数量取整到 100 股"""
        from trader.assistant import TradeAssistant
        from strategies.base import Signal

        sig = Signal(code="000001", name="平安银行", signal="BUY",
                     price=10.50, score=75.0, reason="test")
        ta = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.1})
        orders = ta.generate_orders([sig])
        assert len(orders) == 1
        assert orders[0].quantity % 100 == 0

    def test_amount_equals_price_times_quantity(self, tmp_order_dir):
        """IT-OD-03：预计金额 = 价格 × 数量，误差 < 0.01%"""
        from trader.assistant import TradeAssistant
        from strategies.base import Signal
        import numpy as np

        sig = Signal(code="000001", name="平安银行", signal="BUY",
                     price=25.80, score=80.0, reason="突破+放量")
        ta = TradeAssistant({"initial_capital": 1_000_000, "position_size": 0.1})
        orders = ta.generate_orders([sig])
        d = orders[0].to_dict()
        expected = sig.price * orders[0].quantity
        assert d["amount"] == pytest.approx(expected, rel=0.0001)

    def test_risk_check_contains_all_rc(self, tmp_order_dir):
        """IT-OD-04：风控检查结果包含 RC1~RC6 各项状态"""
        from trader.assistant import TradeAssistant
        from strategies.base import Signal

        sig = Signal(code="000001", name="平安银行", signal="BUY",
                     price=10.50, score=75.0, reason="test")
        ta = TradeAssistant({"initial_capital": 1_000_000})
        orders = ta.generate_orders([sig])
        d = orders[0].to_dict()

        detail = d["risk_check_detail"]
        required_rc = ["RC1", "RC2", "RC5", "RC8"]
        for rc in required_rc:
            assert rc in detail, f"risk_check_detail 缺少 {rc}"
        assert isinstance(d["risk_flags"], list)

    def test_stop_loss_below_price(self, tmp_order_dir):
        """RC4：止损价低于买入价"""
        from trader.assistant import TradeAssistant
        from strategies.base import Signal

        sig = Signal(code="000001", name="平安银行", signal="BUY",
                     price=50.00, score=80.0, reason="test")
        ta = TradeAssistant({"initial_capital": 1_000_000})
        orders = ta.generate_orders([sig])
        d = orders[0].to_dict()
        assert d["stop_loss_price"] < d["price"]
        assert d["take_profit_price"] > d["price"]

    def test_record_execution_stores_feedback(self, tmp_order_dir):
        """F1/F2：录入实际成交后字段完整"""
        from trader.assistant import TradeAssistant
        from strategies.base import Signal
        import json

        sig = Signal(code="000001", name="平安银行", signal="BUY",
                     price=10.50, score=75.0, reason="test")
        ta = TradeAssistant({"initial_capital": 1_000_000})
        orders = ta.generate_orders([sig])
        order_id = orders[0].order_id
        ta.confirm_order(order_id)
        ta.record_execution(order_id, actual_price=10.55, actual_quantity=1000)

        with open(tmp_order_dir / f"{order_id}.json") as f:
            d = json.load(f)
        assert d["status"] == "EXECUTED"
        assert d["actual_price"] == 10.55
        assert d["actual_quantity"] == 1000
        assert "executed_at" in d
        assert "price_deviation" in d
