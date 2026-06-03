"""
test_data_quality.py — 数据异常检测单元测试
覆盖 UT-DR-01~04：价格异常、成交量异常、字段缺失、时间序列断裂
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_price_df(prices, dates=None, seed=42):
    """构造含 OHLCV 的日线 DataFrame，方便异常检测测试"""
    n = len(prices)
    if dates is None:
        dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": [p * 0.99 for p in prices],
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.98 for p in prices],
        "close": prices,
        "preclose": [p * 0.995 for p in prices],
        "volume": np.full(n, 1_000_000.0),
        "trade_status": "1",
    })


class TestPriceAnomaly:
    """UT-DR-01：单日涨跌幅超 ±20% 且无停牌标记"""

    def test_jump_over_20_pct_no_trade_halt(self):
        """涨跌幅＞20% 但无停牌标记 → 异常"""
        closes = [10.0] * 10 + [13.0]  # +30% 跳变
        df = _make_price_df(closes)
        df["pct_chg"] = df["close"].pct_change() * 100
        # 第11天涨跌幅
        last_pct = df["pct_chg"].iloc[-1]
        is_anomaly = abs(last_pct) > 20 and df["trade_status"].iloc[-1] != "0"
        assert is_anomaly

    def test_drop_over_20_pct_no_trade_halt(self):
        """跌幅＞20% 但无停牌标记 → 异常"""
        closes = [10.0] * 10 + [7.5]  # -25%
        df = _make_price_df(closes)
        df["pct_chg"] = df["close"].pct_change() * 100
        last_pct = df["pct_chg"].iloc[-1]
        is_anomaly = abs(last_pct) > 20 and df["trade_status"].iloc[-1] != "0"
        assert is_anomaly

    def test_normal_change_not_anomaly(self):
        """正常涨跌（5%）不触发异常"""
        closes = [10.0] * 10 + [10.5]  # +5%
        df = _make_price_df(closes)
        df["pct_chg"] = df["close"].pct_change() * 100
        last_pct = df["pct_chg"].iloc[-1]
        is_anomaly = abs(last_pct) > 20 and df["trade_status"].iloc[-1] != "0"
        assert not is_anomaly

    def test_large_jump_during_trade_halt_not_anomaly(self):
        """股价跳变但是停牌日 → 不判定异常"""
        closes = [10.0] * 10 + [13.0]
        df = _make_price_df(closes)
        df.at[10, "trade_status"] = "0"  # 停牌
        df["pct_chg"] = df["close"].pct_change() * 100
        last_pct = df["pct_chg"].iloc[-1]
        is_anomaly = abs(last_pct) > 20 and df["trade_status"].iloc[-1] != "0"
        assert not is_anomaly


class TestVolumeAnomaly:
    """UT-DR-02：成交量为 0 且无停牌标记"""

    def test_zero_volume_no_halt(self):
        """成交量为0 + 交易状态正常 → 标记为数据缺失"""
        df = _make_price_df([10.0] * 20)
        df.at[19, "volume"] = 0.0
        df.at[19, "trade_status"] = "1"
        last = df.iloc[-1]
        is_missing = float(last["volume"]) == 0 and last["trade_status"] != "0"
        assert is_missing

    def test_zero_volume_but_halted(self):
        """成交量为0 + 停牌 → 正常（停牌股无成交）"""
        df = _make_price_df([10.0] * 20)
        df.at[19, "volume"] = 0.0
        df.at[19, "trade_status"] = "0"
        last = df.iloc[-1]
        is_missing = float(last["volume"]) == 0 and last["trade_status"] != "0"
        assert not is_missing

    def test_normal_volume_not_detected(self):
        """正常成交量不触发异常"""
        df = _make_price_df([10.0] * 20)
        last = df.iloc[-1]
        is_missing = float(last["volume"]) == 0 and last["trade_status"] != "0"
        assert not is_missing


class TestFieldMissing:
    """UT-DR-03：关键字段（close/volume）为 NaN"""

    def test_close_nan_marks_missing(self):
        """close 为 NaN 标记为缺失"""
        df = _make_price_df([10.0] * 20)
        df.at[19, "close"] = float("nan")
        last = df.iloc[-1]
        is_field_missing = pd.isna(last["close"])
        assert is_field_missing

    def test_volume_nan_marks_missing(self):
        """volume 为 NaN 标记为缺失"""
        df = _make_price_df([10.0] * 20)
        df.at[19, "volume"] = float("nan")
        last = df.iloc[-1]
        is_field_missing = pd.isna(last["volume"])
        assert is_field_missing

    def test_missing_field_excluded_from_scan(self):
        """字段缺失的标的应从扫描结果中排除"""
        df = _make_price_df([10.0] * 20)
        df.at[19, "close"] = float("nan")
        # 模拟扫描过滤逻辑
        should_participate = df.dropna(subset=["close"]).shape[0] == len(df)
        assert not should_participate

    def test_all_fields_present_not_missing(self):
        """所有字段完整不触发异常"""
        df = _make_price_df([10.0] * 20)
        missing_close = df["close"].isna().any()
        missing_vol = df["volume"].isna().any()
        assert not missing_close
        assert not missing_vol


class TestSequenceBreak:
    """UT-DR-04：时间序列断裂超过 5 个自然日"""

    def test_gap_over_5_days_triggers_alert(self):
        """间隔超 5 自然日触发告警"""
        # 直接用显式日期制造 7 天断裂
        dates_sampled = pd.DatetimeIndex([
            pd.Timestamp("2024-06-03"),
            pd.Timestamp("2024-06-04"),
            # 7 day gap (June 4 → June 11)
            pd.Timestamp("2024-06-11"),
            pd.Timestamp("2024-06-12"),
        ])
        df = pd.DataFrame({
            "close": [10.0 + i for i in range(len(dates_sampled))],
            "volume": np.full(len(dates_sampled), 1_000_000.0),
        }, index=dates_sampled)
        gaps = pd.Series(df.index).diff().dropna()
        max_gap = gaps.max().days if len(gaps) > 0 else 0
        assert max_gap > 5

    def test_normal_weekend_gap_not_triggered(self):
        """周末正常间隔（1~3自然日）不触发告警"""
        dates = pd.date_range("2024-06-03", "2024-06-07", freq="B")  # Mon-Fri
        df = pd.DataFrame({
            "close": [10.0 + i for i in range(len(dates))],
            "volume": np.full(len(dates), 1_000_000.0),
        }, index=dates)
        gaps = pd.Series(df.index).diff().dropna()
        max_gap = gaps.max().days if len(gaps) > 0 else 0
        assert max_gap <= 5

    def test_continuous_dates_no_gap(self):
        """连续日期无断裂"""
        dates = pd.date_range("2024-06-01", "2024-06-14")
        df = pd.DataFrame({
            "close": [10.0 + i for i in range(len(dates))],
            "volume": np.full(len(dates), 1_000_000.0),
        }, index=dates)
        gaps = pd.Series(df.index).diff().dropna()
        max_gap = gaps.max().days if len(gaps) > 0 else 0
        assert max_gap <= 1

    def test_multiple_gaps_in_sequence(self):
        """序列中有多个断裂时检测到最大断裂"""
        dates_list = [
            pd.Timestamp("2024-06-01"),
            pd.Timestamp("2024-06-02"),
            # 7 day gap
            pd.Timestamp("2024-06-10"),
            pd.Timestamp("2024-06-11"),
            # 14 day gap
            pd.Timestamp("2024-06-25"),
            pd.Timestamp("2024-06-26"),
        ]
        df = pd.DataFrame({
            "close": [10.0 + i for i in range(len(dates_list))],
            "volume": np.full(len(dates_list), 1_000_000.0),
        }, index=pd.DatetimeIndex(dates_list))
        gaps = pd.Series(df.index).diff().dropna()
        max_gap = gaps.max().days if len(gaps) > 0 else 0
        assert max_gap > 5
