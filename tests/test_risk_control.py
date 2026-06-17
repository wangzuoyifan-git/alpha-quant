"""
tests/test_risk_control.py — 风控规则测试（台阶一）

- GAP-01: RC5 总账户回撤超限暂停新开仓
- GAP-35: RC7 减持窗口期内 BUY 信号被拦截
- GAP-36: RC8 VIX 超阈值熔断
- GAP-05: F1 指令单含 expected_price
- GAP-06: F1 record_execution 实际成交录入
- GAP-07: F2 price_deviation 计算
"""
import pytest
from strategies.base import Signal


def _make_signal(code="000001", sig_type="BUY", price=10.0):
    return Signal(code=code, name="测试股", signal=sig_type, price=price, reason="test")


def _make_ta():
    from trader.assistant import TradeAssistant
    trade_cfg = {"position_size": 0.1, "initial_capital": 1_000_000,
                 "commission_rate": 0.0003, "slippage": 0.001, "benchmark": "000300.SH"}
    risk_cfg = {
        "max_single_position": 0.10, "max_positions": 10,
        "max_industry_concentration": 0.30, "stop_loss_ratio": 0.07,
        "max_total_drawdown": 0.15, "quality_filter": {},
        "reduce_window_block": True,
        "circuit_breaker": {"vix_threshold": 30, "index_drop_threshold": 0.03,
                            "pause_new_orders": True},
        "take_profit_ratio": 0.20, "enforce": True,
    }
    return TradeAssistant(trade_cfg, risk_cfg)


class TestRC5TotalDrawdown:
    """GAP-01: RC5 总账户回撤超限时拦截所有新开仓"""

    def test_rc5_blocks_when_drawdown_exceeded(self):
        ta = _make_ta()
        # 账户净值从 100万 跌到 82万（回撤 18% > 15% 阈值）
        signals = [_make_signal("000001"), _make_signal("000002")]
        result = ta.filter_signals_by_risk(signals, current_equity=820_000)
        assert result == [], "RC5 触发时应返回空列表"

    def test_rc5_passes_when_drawdown_ok(self):
        ta = _make_ta()
        signals = [_make_signal("000001")]
        # 回撤 5%，未超限
        result = ta.filter_signals_by_risk(signals, current_equity=950_000)
        assert len(result) == 1


class TestRC7ReduceWindow:
    """GAP-35: RC7 减持窗口期内 BUY 信号被拦截"""

    def test_rc7_blocks_buy_in_reduce_window(self, monkeypatch):
        from data import store_events
        monkeypatch.setattr(store_events, "has_reduce_window",
                            lambda code, date=None: True)
        ta = _make_ta()
        signals = [_make_signal("000001", "BUY")]
        result = ta.filter_signals_by_risk(signals, current_equity=1_000_000)
        assert result == [], "RC7 减持窗口期内 BUY 应被拦截"

    def test_rc7_allows_sell_in_reduce_window(self, monkeypatch):
        from data import store_events
        monkeypatch.setattr(store_events, "has_reduce_window",
                            lambda code, date=None: True)
        ta = _make_ta()
        # SELL 信号不受 RC7 约束
        signals = [_make_signal("000001", "SELL")]
        result = ta.filter_signals_by_risk(signals, current_equity=1_000_000)
        assert len(result) == 1

    def test_rc7_passes_when_no_reduce_window(self, monkeypatch):
        from data import store_events
        monkeypatch.setattr(store_events, "has_reduce_window",
                            lambda code, date=None: False)
        ta = _make_ta()
        signals = [_make_signal("000001", "BUY")]
        result = ta.filter_signals_by_risk(signals, current_equity=1_000_000)
        assert len(result) == 1

    def test_rc7_silent_when_events_unavailable(self, monkeypatch):
        """事件数据缺失时不阻断（静默降级）"""
        from data import store_events
        monkeypatch.setattr(store_events, "has_reduce_window",
                            lambda code, date=None: (_ for _ in ()).throw(Exception("no data")))
        ta = _make_ta()
        signals = [_make_signal("000001", "BUY")]
        # 不应抛异常
        result = ta.filter_signals_by_risk(signals, current_equity=1_000_000)
        assert isinstance(result, list)


class TestRC8CircuitBreaker:
    """GAP-36: RC8 VIX 超阈值或指数跌幅超阈值时熔断"""

    def test_rc8_blocks_on_high_vix(self):
        ta = _make_ta()
        signals = [_make_signal("000001")]
        result = ta.filter_signals_by_risk(signals, current_equity=1_000_000, vix=35.0)
        assert result == [], "RC8 VIX=35>30 应熔断"

    def test_rc8_blocks_on_index_drop(self):
        ta = _make_ta()
        signals = [_make_signal("000001")]
        result = ta.filter_signals_by_risk(signals, current_equity=1_000_000,
                                           index_drop=-0.04)
        assert result == [], "RC8 指数跌幅 4%>3% 应熔断"

    def test_rc8_passes_normal(self):
        ta = _make_ta()
        signals = [_make_signal("000001")]
        result = ta.filter_signals_by_risk(signals, current_equity=1_000_000,
                                           vix=18.0, index_drop=-0.01)
        assert len(result) == 1


class TestF1ExpectedPrice:
    """GAP-05: F1 指令单含 expected_price"""

    def test_order_has_expected_price(self, tmp_path, monkeypatch):
        from trader.assistant import TradeAssistant, ORDER_DIR
        monkeypatch.setattr("trader.assistant.ORDER_DIR", tmp_path)

        ta = _make_ta()
        signals = [_make_signal("000001", price=12.5)]
        orders = ta.generate_orders(signals, current_capital=1_000_000)
        assert orders, "应生成指令单"
        assert orders[0].expected_price == 12.5

    def test_confirm_records_expected_price(self, tmp_path, monkeypatch):
        import json
        from trader.assistant import TradeAssistant, ORDER_DIR
        monkeypatch.setattr("trader.assistant.ORDER_DIR", tmp_path)

        ta = _make_ta()
        signals = [_make_signal("000001", price=10.0)]
        orders = ta.generate_orders(signals, current_capital=1_000_000)
        order_id = orders[0].order_id

        ok = ta.confirm_order(order_id)
        assert ok

        data = json.loads((tmp_path / f"{order_id}.json").read_text())
        assert data["expected_price"] == 10.0
        assert data["confirmed_at"]


class TestF1F2Execution:
    """GAP-06/07: F1 实际成交录入 + F2 price_deviation 计算"""

    def test_record_execution_stores_actual_price(self, tmp_path, monkeypatch):
        import json
        from trader.assistant import TradeAssistant
        monkeypatch.setattr("trader.assistant.ORDER_DIR", tmp_path)

        ta = _make_ta()
        signals = [_make_signal("000001", price=10.0)]
        orders = ta.generate_orders(signals, current_capital=1_000_000)
        order_id = orders[0].order_id
        ta.confirm_order(order_id)

        ok = ta.record_execution(order_id, actual_price=10.5, actual_quantity=1000)
        assert ok

        data = json.loads((tmp_path / f"{order_id}.json").read_text())
        assert data["actual_price"] == 10.5
        assert data["actual_quantity"] == 1000

    def test_price_deviation_calculated(self, tmp_path, monkeypatch):
        import json
        from trader.assistant import TradeAssistant
        monkeypatch.setattr("trader.assistant.ORDER_DIR", tmp_path)

        ta = _make_ta()
        signals = [_make_signal("000001", price=10.0)]
        orders = ta.generate_orders(signals, current_capital=1_000_000)
        order_id = orders[0].order_id
        ta.confirm_order(order_id)
        ta.record_execution(order_id, actual_price=10.5, actual_quantity=1000)

        data = json.loads((tmp_path / f"{order_id}.json").read_text())
        assert "price_deviation" in data
        assert abs(data["price_deviation"] - 5.0) < 0.1  # (10.5/10.0-1)*100 = 5%


class TestRC7PledgeAndWatchWarning:
    """RC7 扩展：股权质押过滤 + WATCH 信号事件标注"""

    def test_buy_blocked_by_pledge(self, monkeypatch):
        """BUY 信号在高比例质押时被 RC7_pledge 拦截"""
        from data import store_events
        monkeypatch.setattr(store_events, "has_reduce_window", lambda code, date=None: False)
        monkeypatch.setattr(store_events, "has_pledge_risk", lambda code, threshold=50.0: True)

        ta = _make_ta()
        sigs = ta.filter_signals_by_risk([_make_signal("000001", "BUY")])
        assert sigs == []  # enforce=True 时被过滤

    def test_buy_not_blocked_when_pledge_low(self, monkeypatch):
        """低质押比例 BUY 信号不被拦截"""
        from data import store_events
        monkeypatch.setattr(store_events, "has_reduce_window", lambda code, date=None: False)
        monkeypatch.setattr(store_events, "has_pledge_risk", lambda code, threshold=50.0: False)

        ta = _make_ta()
        sigs = ta.filter_signals_by_risk([_make_signal("000001", "BUY")])
        assert len(sigs) == 1

    def test_watch_gets_event_warning_not_blocked(self, monkeypatch):
        """WATCH 信号有减持/质押风险时打标注而非拦截"""
        from data import store_events
        monkeypatch.setattr(store_events, "has_reduce_window", lambda code, date=None: True)
        monkeypatch.setattr(store_events, "has_pledge_risk", lambda code, threshold=50.0: True)

        ta = _make_ta()
        watch_sig = _make_signal("000001", "WATCH")
        # WATCH 信号不经 filtered 列表（filter_signals_by_risk 只处理 BUY/SELL）
        # 但事件标注发生在迭代中，需要直接调用
        ta.filter_signals_by_risk([watch_sig])
        # WATCH 不被拦截，但 extra 中有 event_warnings
        assert "event_warnings" in watch_sig.extra
        warnings = watch_sig.extra["event_warnings"]
        assert "WARN_reduce" in warnings
        assert "WARN_pledge" in warnings

    def test_watch_no_warning_when_clean(self, monkeypatch):
        """无事件风险的 WATCH 信号不被打标"""
        from data import store_events
        monkeypatch.setattr(store_events, "has_reduce_window", lambda code, date=None: False)
        monkeypatch.setattr(store_events, "has_pledge_risk", lambda code, threshold=50.0: False)

        ta = _make_ta()
        watch_sig = _make_signal("000001", "WATCH")
        ta.filter_signals_by_risk([watch_sig])
        assert watch_sig.extra.get("event_warnings") is None

    def test_has_pledge_risk_threshold(self, tmp_path, monkeypatch):
        """has_pledge_risk 按阈值正确判断"""
        import json
        from data.store_events import save_corp_events, has_pledge_risk
        monkeypatch.setattr("data.store_events.E2_CORP_DIR", tmp_path)

        save_corp_events("000999", [{
            "event_id": "pledge_test_1", "code": "000999",
            "event_type": "pledge", "date": "2026-01-01",
            "end_date": None,
            "detail": {"pledge_ratio": 55.0},
            "source": "test", "phase": 1,
        }])
        assert has_pledge_risk("000999", threshold=50.0) is True
        assert has_pledge_risk("000999", threshold=60.0) is False
