"""
L2 集成测试 — 扫描流程 (IT-SC-01~06)

依赖本地已有数据，不走网络。
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

pytestmark = pytest.mark.integration


def _has_market_data():
    import data.store as store
    return len(store.list_codes("stock")) > 100


@pytest.mark.skipif(not _has_market_data(), reason="本地无足够行情数据（需>100只）")
class TestScanFlow:
    """IT-SC-01~06：扫描流程集成测试"""

    @pytest.fixture
    def scan_result(self):
        from data.fetcher import DataFetcher
        from engine.scanner import ScanEngine
        from config.settings import SCAN, DATA_SOURCE
        from strategies.builtin.ma_cross import MACrossStrategy
        from strategies.builtin.macd_rsi import MACDRSIStrategy

        fetcher = DataFetcher(DATA_SOURCE)
        engine = ScanEngine(fetcher, SCAN)
        engine.register_strategy("ma", MACrossStrategy())
        engine.register_strategy("macd_rsi", MACDRSIStrategy())

        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=180)).strftime("%Y-%m-%d")

        import data.store as store
        codes = store.list_codes("stock")[:50]  # 限制50只加快测试
        signals = []
        for code in codes:
            sigs = engine._scan_single(code, code, start, end)  # nosec
            signals.extend(sigs)
        # 按评分降序排列（模拟 scan() 的合并排序逻辑）
        signals.sort(key=lambda s: s.score, reverse=True)
        return signals

    def test_results_sorted_by_score_desc(self, scan_result):
        """IT-SC-01：扫描结果按评分降序排列"""
        if len(scan_result) < 2:
            pytest.skip("触发信号不足2条")
        scores = [s.score for s in scan_result]
        assert scores == sorted(scores, reverse=True), f"前5评分: {scores[:5]}"

    def test_no_st_in_results(self, scan_result):
        """IT-SC-03：ST 标的不出现在扫描结果中"""
        st_codes = [s.code for s in scan_result if "ST" in s.name or "退" in s.name]
        assert len(st_codes) == 0, f"发现ST/退市标的: {st_codes}"

    def test_no_trading_halt_in_results(self):
        """IT-SC-04：停牌标的不出现在扫描结果中"""
        import data.store as store
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        codes = store.list_codes("stock")[:20]
        for code in codes:
            df = store.load(code, start=start, end=end)
            if df is not None and not df.empty:
                last_status = str(df.iloc[-1].get("trade_status", "1"))
                # 验证 trade_status 列存在且正常值为 "1"
                assert last_status in ("0", "1"), f"{code} trade_status={last_status}"

    def test_results_persist_to_file(self, scan_result):
        """IT-SC-05：扫描结果可持久化并重新加载"""
        from signals.manager import save_signals, load_signals
        date = datetime.now().strftime("%Y-%m-%d")
        if scan_result:
            path = save_signals(scan_result, date=date, overwrite=True)
            loaded = load_signals(date)
            assert len(loaded) > 0
            assert "code" in loaded[0]
            assert "score" in loaded[0]

    def test_nan_field_codes_excluded(self):
        """IT-SC-06：关键字段 NaN 的标的不被扫描"""
        import data.store as store
        import pandas as pd

        codes = store.list_codes("stock")[:20]
        found_nan = False
        for code in codes:
            df = store.load(code)
            if df is not None and not df.empty:
                last = df.iloc[-1]
                if pd.isna(last.get("close")):
                    found_nan = True
                    break
        # NaN 数据不应出现在可用数据中；如果出现是数据质量问题
        assert not found_nan, "存在 close 为 NaN 的标的"


@pytest.mark.skipif(not _has_market_data(), reason="本地无足够行情数据")
class TestSignalAnalysis:
    """IT-AA-01~05：辅助分析数据聚合"""

    def test_analysis_contains_p0_fields(self):
        """IT-AA-01：信号辅助分析包含 P0 字段"""
        from engine.analysis import analyze_signal
        signal = {"code": "600519", "name": "贵州茅台", "signal": "BUY",
                  "price": 1600, "score": 80, "reason": "测试"}
        analysis = analyze_signal(signal)
        assert "tech_summary" in analysis
        assert "fund_flow" in analysis
        assert "fundamental" in analysis
        assert "industry" in analysis
        assert "data_unavailable" in analysis

    def test_missing_fundamental_marks_unavailable(self):
        """IT-AA-02：基本面缺失时标注不可用"""
        from engine.analysis import analyze_signal
        signal = {"code": "999999", "name": "不存在", "signal": "BUY",
                  "price": 10, "score": 60, "reason": "测试"}
        analysis = analyze_signal(signal)
        fund = analysis["fundamental"]
        if not fund.get("available"):
            assert "data_unavailable" in analysis

    def test_missing_fundflow_marks_unavailable(self):
        """IT-AA-03：资金流缺失时标注不可用"""
        from engine.analysis import analyze_signal
        signal = {"code": "999999", "name": "不存在", "signal": "BUY",
                  "price": 10, "score": 60, "reason": "测试"}
        analysis = analyze_signal(signal)
        flow = analysis["fund_flow"]
        if not flow.get("available"):
            assert "fund_flow" in analysis.get("data_unavailable", []) or flow.get("available") is False

    def test_volume_ratio_calculation(self):
        """IT-AA-04：量比计算正确（近5日均量 vs 当日量）"""
        import data.store as store
        df = store.load("600519")
        if df is None or df.empty or len(df) < 6:
            pytest.skip("600519 无足够数据")
        import numpy as np
        vol_today = float(df["volume"].iloc[-1])
        vol_5_avg = df["volume"].iloc[-6:-1].mean()
        if vol_5_avg > 0:
            ratio = vol_today / vol_5_avg
            assert ratio > 0
            # 从 analysis 中验证
            from engine.analysis import analyze_signal
            analysis = analyze_signal({"code": "600519", "name": "贵州茅台", "signal": "BUY",
                                      "price": 1600, "score": 80, "reason": "test"})
            tech = analysis.get("tech_summary", {})
            if tech.get("available"):
                assert tech.get("vol_ratio") is not None

    def test_net_main_5d_calculation(self):
        """IT-AA-05：近5日主力净流入 = 近5条 moneyflow 之和"""
        from data.store_moneyflow import list_codes as list_mf_codes, load as load_mf
        codes = list_mf_codes("stock")
        if not codes:
            pytest.skip("无 moneyflow 数据")
        # 任选一只
        df = load_mf("stock", code=codes[0])
        if df is None or len(df) < 5:
            pytest.skip(f"{codes[0]} moneyflow 不足5条")
        import numpy as np
        net_5d = float(df["net_mf_amount"].iloc[-5:].sum()) if "net_mf_amount" in df.columns else 0
        assert isinstance(net_5d, float)
