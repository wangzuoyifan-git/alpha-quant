"""P0-1/P0-4：成交真实性与未来函数测试。

覆盖：next-open 成交、涨跌停不可买/不可卖、T+1、印花税、整手、停牌。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.engine import Backtester
from backtest.execution import (
    ExecutionModel,
    can_buy,
    can_sell,
    limit_pct_for,
    limit_prices,
    round_lot,
)
from strategies.base import BaseStrategy, ExitLevels, Signal

# ══════════════════════════════════════════════════════════════
# 纯成交规则
# ══════════════════════════════════════════════════════════════

class TestLimitRules:
    def test_limit_pct_by_board(self):
        assert limit_pct_for("000001") == pytest.approx(0.10)
        assert limit_pct_for("600519") == pytest.approx(0.10)
        assert limit_pct_for("300750") == pytest.approx(0.20)
        assert limit_pct_for("301001") == pytest.approx(0.20)
        assert limit_pct_for("688981") == pytest.approx(0.20)
        assert limit_pct_for("830799") == pytest.approx(0.30)
        assert limit_pct_for("430047") == pytest.approx(0.30)
        assert limit_pct_for("510300", asset_type="etf") == pytest.approx(0.10)
        assert limit_pct_for("000001", is_st=True) == pytest.approx(0.05)

    def test_limit_prices_round_to_cent(self):
        up, dn = limit_prices(10.0, "000001")
        assert up == pytest.approx(11.0)
        assert dn == pytest.approx(9.0)

    def test_open_at_limit_up_cannot_buy(self):
        # 主板：前收 10，开盘 11（涨停），普通波动非一字板也不能买
        assert not can_buy(11.0, 11.2, 10.9, 11.0, 10.0, "000001")

    def test_open_below_limit_up_can_buy(self):
        assert can_buy(10.5, 10.8, 10.4, 10.6, 10.0, "000001")

    def test_one_word_board_cannot_buy(self):
        assert not can_buy(10.0, 10.0, 10.0, 10.0, 10.0, "000001")

    def test_gem_limit_is_20pct(self):
        # 创业板前收 10，开盘 11（+10%）仍可买；开盘 12（+20%）不可买
        assert can_buy(11.0, 11.2, 10.9, 11.0, 10.0, "300750")
        assert not can_buy(12.0, 12.1, 11.9, 12.0, 10.0, "300750")

    def test_one_word_limit_down_cannot_sell(self):
        assert not can_sell(9.0, 9.0, 9.0, 9.0, 10.0, "000001")

    def test_limit_down_but_opened_can_sell(self):
        # 跌停价开盘但盘中打开（high>low），可卖
        assert can_sell(9.0, 9.2, 8.9, 9.1, 10.0, "000001")

    def test_suspended_bar_cannot_trade(self):
        assert not can_buy(float("nan"), 10.0, 10.0, 10.0, 9.9, "000001")
        assert not can_sell(float("nan"), 10.0, 10.0, 10.0, 9.9, "000001")
        # A 股停牌残留行：OHLC 齐全但成交量为 0/NaN
        assert not can_buy(10.0, 10.0, 10.0, 10.0, 9.9, "000001", volume=0)
        assert not can_sell(10.0, 10.0, 10.0, 10.0, 9.9, "000001", volume=float("nan"))

    def test_round_lot_and_size_buy(self):
        assert round_lot(199) == 100
        assert round_lot(99) == 0
        em = ExecutionModel(commission=0.0, slippage=0.0)
        # 预算 1050 / 价格 10 = 105 -> 整手 100
        assert em.size_buy(2000, 10.0, 1050) == 100
        # 现金不足时自动回落
        assert em.size_buy(1000, 10.0, 1000) == 100


# ══════════════════════════════════════════════════════════════
# 引擎级成交时点/费用/停牌
# ══════════════════════════════════════════════════════════════

class _FakeStrategy(BaseStrategy):
    name = "fake"
    description = "test"
    time_horizon = "short"

    def __init__(self, buy_dates=(), sell_dates=(), codes=None):
        super().__init__()
        self.buy_dates = {str(d)[:10] for d in buy_dates}
        self.sell_dates = {str(d)[:10] for d in sell_dates}
        self.codes = set(codes) if codes else None
        self.as_of_seen = []

    def requires_bars(self) -> int:
        return 2

    def generate_signals(self, df, code, name, as_of=None):
        last_date = str(pd.Timestamp(df.iloc[-1]["date"]))[:10]
        self.as_of_seen.append(as_of)
        if self.codes is not None and code not in self.codes:
            return []
        price = float(df.iloc[-1]["close"])
        if last_date in self.sell_dates:
            return [Signal(code=code, name=name, signal="SELL",
                           price=price, reason="test_sell")]
        if last_date in self.buy_dates:
            return [Signal(code=code, name=name, signal="BUY",
                           price=price, reason="test_buy")]
        return []

    def compute_exit_levels(self, df, entry_price, fundamental_data=None):
        return ExitLevels(stop_loss=0.0, take_profit=entry_price * 2,
                          atr=0.0, exit_logic="atr", scale_levels=[])


def _frame(dates, opens, highs, lows, closes):
    idx = pd.to_datetime(dates)
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": np.full(len(dates), 1_000_000.0),
    }, index=pd.DatetimeIndex(idx, name="date"))


def _install_store(monkeypatch, frames):
    import data.store as store

    def mock_load(code, asset_type="stock", market="cn", start=None, end=None):
        if asset_type != "stock":
            return None
        df = frames.get(code)
        if df is None:
            return None
        df = df.copy()
        if start:
            df = df[df.index >= pd.Timestamp(start)]
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        return df if not df.empty else None

    monkeypatch.setattr(store, "load", mock_load)
    monkeypatch.setattr(store, "load_ipo_dates", lambda: {})


def _bt(**cfg):
    base = {
        "initial_capital": 100_000,
        "commission_rate": 0.0,
        "slippage": 0.0,
        "stamp_duty": 0.0,
        "position_size": 1.0,
        "max_positions": 1,
    }
    base.update(cfg)
    return Backtester(None, base)


DATES = [str(d.date()) for d in pd.bdate_range("2024-01-02", periods=7)]


class TestEngineExecution:
    def test_signal_t_fills_next_open(self, monkeypatch):
        df = _frame(DATES, [10, 10, 11, 11.5, 11, 11.2, 11.3],
                    [10.2, 10.4, 11.3, 11.8, 11.3, 11.5, 11.6],
                    [9.8, 9.9, 10.8, 11.2, 10.8, 11.0, 11.1],
                    [10, 10.2, 11.2, 11.5, 11.0, 11.2, 11.3])
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[4]])
        res = _bt().run(strat, ["000001"], DATES[0], DATES[-1])

        assert len(res.trades) == 1
        t = res.trades[0]
        # t=day1 收盘信号 → day2 开盘 11.0 成交，而非 day1 收盘 10.2
        assert t.entry_date == DATES[2]
        assert t.entry_price == pytest.approx(11.0)

    def test_limit_up_open_blocks_buy(self, monkeypatch):
        # day1 收盘 10；day2 开盘 11（涨停）→ 买不进
        df = _frame(DATES, [10, 10, 11.0, 11.2, 11.3, 11.4, 11.5],
                    [10.2, 10.2, 11.3, 11.4, 11.5, 11.6, 11.7],
                    [9.8, 9.9, 10.9, 11.0, 11.1, 11.2, 11.3],
                    [10, 10.0, 11.2, 11.3, 11.4, 11.5, 11.6])
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[4]])
        res = _bt().run(strat, ["000001"], DATES[0], DATES[-1])
        assert res.trades == []

    def test_one_word_limit_down_blocks_sell_and_retries(self, monkeypatch):
        # day2 开盘买入；day3 一字跌停（9.0）→ 卖不出；day4 正常开盘卖出
        df = _frame(DATES, [10, 10, 10.0, 9.0, 9.2, 9.3, 9.4],
                    [10.2, 10.1, 10.2, 9.0, 9.5, 9.6, 9.7],
                    [9.8, 9.9, 9.9, 9.0, 9.0, 9.1, 9.2],
                    [10, 10.0, 10.0, 9.0, 9.3, 9.4, 9.5])
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[2]])
        res = _bt().run(strat, ["000001"], DATES[0], DATES[-1])

        assert len(res.trades) == 1
        t = res.trades[0]
        assert t.entry_date == DATES[2]
        assert t.exit_date == DATES[4]  # day3 一字跌停，延后到 day4
        assert t.exit_price == pytest.approx(9.2)

    def test_t_plus_one_no_same_day_exit(self, monkeypatch):
        # day2 开盘买入；day2 收盘出现 SELL 信号，最早 day3 开盘卖出
        df = _frame(DATES, [10, 10, 10.0, 10.5, 10.6, 10.7, 10.8],
                    [10.2, 10.2, 10.3, 10.7, 10.8, 10.9, 11.0],
                    [9.8, 9.9, 9.8, 10.3, 10.4, 10.5, 10.6],
                    [10, 10.0, 10.1, 10.6, 10.7, 10.8, 10.9])
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[2]])
        res = _bt().run(strat, ["000001"], DATES[0], DATES[-1])
        assert len(res.trades) == 1
        assert res.trades[0].entry_date == DATES[2]
        assert res.trades[0].exit_date == DATES[3]

    def test_stamp_duty_charged_on_sell_only(self, monkeypatch):
        df = _frame(DATES, [10, 10, 10.0, 10.5, 10.6, 10.7, 10.8],
                    [10.2, 10.2, 10.3, 10.7, 10.8, 10.9, 11.0],
                    [9.8, 9.9, 9.8, 10.3, 10.4, 10.5, 10.6],
                    [10, 10.0, 10.1, 10.6, 10.7, 10.8, 10.9])
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[3]])
        no_tax = _bt(stamp_duty=0.0).run(strat, ["000001"], DATES[0], DATES[-1])
        with_tax = _bt(stamp_duty=0.001).run(strat, ["000001"], DATES[0], DATES[-1])

        assert with_tax.final_capital < no_tax.final_capital
        shares = no_tax.trades[0].shares
        exit_px = no_tax.trades[0].exit_price
        assert no_tax.final_capital - with_tax.final_capital == pytest.approx(
            shares * exit_px * 0.001
        )

    def test_lot_size_rounding(self, monkeypatch):
        df = _frame(DATES, [10, 10, 10.0, 10.5, 10.6, 10.7, 10.8],
                    [10.2, 10.2, 10.3, 10.7, 10.8, 10.9, 11.0],
                    [9.8, 9.9, 9.8, 10.3, 10.4, 10.5, 10.6],
                    [10, 10.0, 10.1, 10.6, 10.7, 10.8, 10.9])
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[3]])
        res = _bt(initial_capital=1_050).run(strat, ["000001"], DATES[0], DATES[-1])
        assert res.trades[0].shares == 100  # 1050/10=105 → 100

    def test_suspension_skips_execution(self, monkeypatch):
        # day2 停牌（无 K 线），买入信号过期，不成交
        df = _frame([DATES[0], DATES[1], DATES[3], DATES[4], DATES[5], DATES[6]],
                    [10, 10, 10.5, 10.6, 10.7, 10.8],
                    [10.2, 10.2, 10.7, 10.8, 10.9, 11.0],
                    [9.8, 9.9, 10.3, 10.4, 10.5, 10.6],
                    [10, 10.0, 10.6, 10.7, 10.8, 10.9])
        ref = _frame(DATES, [10] * len(DATES), [10.1] * len(DATES),
                     [9.9] * len(DATES), [10] * len(DATES))
        _install_store(monkeypatch, {"000001": df, "000002": ref})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[4]],
                              codes=["000001"])
        res = _bt(position_size=0.5).run(
            strat, ["000001", "000002"], DATES[0], DATES[-1]
        )
        assert [t for t in res.trades if t.code == "000001"] == []

    def test_zero_volume_row_blocks_buy(self, monkeypatch):
        # 停牌日仍有 OHLC 残留，但 volume=NaN → 不可买
        df = _frame(DATES, [10, 10, 10.5, 10.6, 10.7, 10.8, 10.9],
                    [10.2, 10.2, 10.7, 10.8, 10.9, 11.0, 11.1],
                    [9.8, 9.9, 10.3, 10.4, 10.5, 10.6, 10.7],
                    [10, 10.0, 10.6, 10.7, 10.8, 10.9, 11.0])
        df.loc[pd.Timestamp(DATES[2]), "volume"] = np.nan
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[4]])
        res = _bt().run(strat, ["000001"], DATES[0], DATES[-1])
        assert res.trades == []

    def test_zero_volume_row_blocks_sell_and_retries(self, monkeypatch):
        df = _frame(DATES, [10, 10, 10.0, 10.6, 10.8, 10.9, 11.0],
                    [10.2, 10.2, 10.3, 10.8, 11.0, 11.1, 11.2],
                    [9.8, 9.9, 9.8, 10.3, 10.5, 10.6, 10.7],
                    [10, 10.0, 10.1, 10.7, 10.9, 11.0, 11.1])
        df.loc[pd.Timestamp(DATES[3]), "volume"] = 0.0
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[2]])
        res = _bt().run(strat, ["000001"], DATES[0], DATES[-1])
        assert len(res.trades) == 1
        assert res.trades[0].exit_date == DATES[4]

    def test_sell_signal_retries_after_suspension(self, monkeypatch):
        # day2 买入；day3 收盘 SELL；day4 停牌；day5 开盘卖出
        df = _frame([DATES[0], DATES[1], DATES[2], DATES[3], DATES[5], DATES[6]],
                    [10, 10, 10.0, 10.6, 10.8, 10.9],
                    [10.2, 10.2, 10.3, 10.8, 11.0, 11.1],
                    [9.8, 9.9, 9.8, 10.3, 10.5, 10.6],
                    [10, 10.0, 10.1, 10.7, 10.9, 11.0])
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[3]])
        res = _bt().run(strat, ["000001"], DATES[0], DATES[-1])
        assert len(res.trades) == 1
        assert res.trades[0].entry_date == DATES[2]
        assert res.trades[0].exit_date == DATES[5]

    def test_same_close_mode_keeps_legacy_fill(self, monkeypatch):
        df = _frame(DATES, [10, 10, 10.0, 10.5, 10.6, 10.7, 10.8],
                    [10.2, 10.2, 10.3, 10.7, 10.8, 10.9, 11.0],
                    [9.8, 9.9, 9.8, 10.3, 10.4, 10.5, 10.6],
                    [10, 10.0, 10.1, 10.6, 10.7, 10.8, 10.9])
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]], sell_dates=[DATES[4]])
        res = _bt(execution_mode="same_close").run(
            strat, ["000001"], DATES[0], DATES[-1]
        )
        assert len(res.trades) == 1
        assert res.trades[0].entry_date == DATES[1]  # 当日收盘成交（旧行为）

    def test_strategy_receives_as_of(self, monkeypatch):
        df = _frame(DATES, [10, 10, 10.0, 10.5, 10.6, 10.7, 10.8],
                    [10.2, 10.2, 10.3, 10.7, 10.8, 10.9, 11.0],
                    [9.8, 9.9, 9.8, 10.3, 10.4, 10.5, 10.6],
                    [10, 10.0, 10.1, 10.6, 10.7, 10.8, 10.9])
        _install_store(monkeypatch, {"000001": df})
        strat = _FakeStrategy(buy_dates=[DATES[1]])
        _bt().run(strat, ["000001"], DATES[0], DATES[-1])
        seen = {d for d in strat.as_of_seen if d is not None}
        assert DATES[1] in seen
        assert all(d <= DATES[-1] for d in seen)
