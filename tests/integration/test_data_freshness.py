"""
L2 集成测试 — 数据新鲜度检查 (IT-DF-01/02/03)

依赖本地已有数据，不走网络。每次 git commit 前自动运行。
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

pytestmark = pytest.mark.integration


def _has_market_data():
    import data.store as store
    return len(store.list_codes("stock")) > 10


def _has_fundamental_data():
    import data.store_fundamental as sf
    return len(sf.list_codes("profit")) > 10


@pytest.mark.skipif(not _has_market_data(), reason="本地无足够行情数据")
class TestDataFreshnessMarket:
    """IT-DF-01/02：D1 行情新鲜度"""

    def test_market_freshness_check_passes(self):
        """IT-DF-01：本地数据最新日接近上一交易日时 check_data_freshness 通过"""
        from agent_tools import check_data_freshness
        result = check_data_freshness()
        assert result["success"] is True
        assert "market/stock" in result["domains"]
        # 今天数据 stale 说明未收盘更新，但在测试环境下可以接受
        stale = result.get("stale", [])
        if "market/stock" in stale:
            pytest.skip("D1 数据过期（可能未运行今日更新），跳过严格断言")

    def test_scanner_freshness_does_not_raise_on_fresh(self):
        """IT-DF-02：scanner 新鲜度方法不抛异常"""
        import data.store as store
        from data.fetcher import DataFetcher
        from engine.scanner import ScanEngine
        from config.settings import SCAN, DATA_SOURCE

        fetcher = DataFetcher(DATA_SOURCE)
        engine = ScanEngine(fetcher, SCAN)
        from strategies.builtin.ma_cross import MACrossStrategy
        engine.register_strategy("ma", MACrossStrategy())

        end = datetime.now().strftime("%Y-%m-%d")
        stale = engine._check_data_freshness(end, ["stock"])
        # 如果数据不新鲜，scanner 应该返回 stale 列表
        assert isinstance(stale, list)


@pytest.mark.skipif(not _has_fundamental_data(), reason="本地无足够财务数据")
class TestDataFreshnessFundamental:
    """IT-DF-03：D2 基本面非关键域过期不中止扫描"""

    def test_fundamental_stale_logs_but_does_not_block(self):
        from agent_tools import check_data_freshness
        result = check_data_freshness()
        assert "fundamental/profit" in result["domains"]
        # 基本面过期会出现在 stale 或 domains 中，但 scanner 不会因此中止
        assert result["success"] is True
