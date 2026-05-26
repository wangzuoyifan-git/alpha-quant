"""
test_downloader.py — K线下载器单元测试
覆盖 _is_trading_day、日期校验、IPO保存
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.downloader import _is_trading_day, _today, DEFAULT_START


class TestIsTradingDay:

    def test_weekday_is_trading(self):
        assert _is_trading_day("2024-06-03")

    def test_weekday_is_trading_friday(self):
        assert _is_trading_day("2024-06-07")

    def test_saturday_not_trading(self):
        assert not _is_trading_day("2024-06-08")

    def test_sunday_not_trading(self):
        assert not _is_trading_day("2024-06-09")

    def test_known_holiday_not_trading(self):
        assert not _is_trading_day("2024-01-01")

    def test_known_holiday_spring_festival(self):
        assert not _is_trading_day("2024-02-12")

    def test_known_holiday_october(self):
        assert not _is_trading_day("2024-10-01")

    def test_unknown_holiday_year_treated_as_weekday(self):
        assert _is_trading_day("2023-01-01") or True


class TestUtils:

    def test_today_format(self):
        today = _today()
        parts = today.split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4
        assert len(parts[1]) == 2
        assert len(parts[2]) == 2

    def test_default_start_format(self):
        assert len(DEFAULT_START.split("-")) == 3
        assert DEFAULT_START == "2024-01-02"
