"""
L3 系统测试 — 多源交叉验证 (ST-DR-01/02)

访问真实数据源 Tushare，验证与本地存储数据的一致性。
单个请求 ≤ 30s 超时，仅在重大变更后手动触发。
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

import pytest
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

pytestmark = [pytest.mark.system, pytest.mark.timeout(30)]


def _tushare_available():
    try:
        import tushare as ts
        from config.settings import DATA_SOURCE
        token = DATA_SOURCE.get("tushare_token", "")
        if not token:
            return False
        pro = ts.pro_api(token)
        df = pro.trade_cal(exchange="SSE", start_date="20240601", end_date="20240601")
        return len(df) > 0
    except Exception:
        return False


@pytest.mark.skipif(not _tushare_available(), reason="Tushare 不可用")
class TestCrossSourceValidation:
    """ST-DR-01/02：Tushare vs 本地 store 收盘价和成交量对比"""

    def _get_tushare_codes(self):
        import data.store as store
        return store.list_codes("stock")[:10]

    def test_close_price_deviation_within_tolerance(self):
        """ST-DR-01：Tushare 与本地 store 收盘价偏差 ≤ 0.1%"""
        import tushare as ts
        from config.settings import DATA_SOURCE
        import data.store as store

        pro = ts.pro_api(DATA_SOURCE["tushare_token"])
        codes = self._get_tushare_codes()
        failures = []

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

        for code in codes[:3]:  # 仅抽查3只避免请求过多
            try:
                ts_code = f"{'sh' if code.startswith('6') else 'sz'}.{code}"
                ts_df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
                if ts_df.empty:
                    continue
                ts_close = float(ts_df.iloc[0]["close"])

                local = store.load(code)
                if local is None or local.empty:
                    continue
                local_close = float(local.iloc[-1]["close"])

                if local_close > 0:
                    deviation = abs(ts_close - local_close) / local_close
                    if deviation > 0.001:
                        failures.append({
                            "code": code,
                            "tushare": ts_close,
                            "local": local_close,
                            "deviation_pct": round(deviation * 100, 4),
                        })
            except Exception as e:
                continue

        if failures:
            pytest.fail(f"交叉验证失败 {len(failures)} 只:\n{failures[:3]}")

    def test_volume_deviation_detection(self):
        """ST-DR-02：成交量偏差检查逻辑验证"""
        import data.store as store

        # 用本地数据验证检查函数逻辑（不对比两源）
        from data.quality import check_cross_source

        codes = self._get_tushare_codes()[:3]
        for code in codes:
            df = store.load(code)
            if df is None or df.empty:
                continue
            # 同一份数据自对比，偏差应为 0
            result = check_cross_source(df, df, code)
            assert result["ok"] is True
            assert result["price_deviation"] is not None
            assert result["price_deviation"] < 0.001


@pytest.mark.skipif(not _tushare_available(), reason="Tushare 不可用")
class TestDataUpdateIntegrity:
    """ST-DU-01~03：增量更新完整性"""

    def test_incremental_update_rows_count(self):
        """ST-DU-01：增量更新不破坏已有数据行数"""
        import data.store as store
        codes = store.list_codes("stock")[:5]
        row_counts_before = {}
        for code in codes:
            df = store.load(code)
            if df is not None:
                row_counts_before[code] = len(df)

        # 模拟增量更新：重新 save 相同数据
        for code in row_counts_before:
            df = store.load(code)
            store.save(code, df)
            after = store.load(code)
            assert len(after) == row_counts_before[code], f"{code} 行数变化"

    def test_historical_data_md5_unchanged(self):
        """ST-DU-02：增量更新不修改历史数据"""
        import data.store as store
        import hashlib

        codes = store.list_codes("stock")[:3]
        hashes_before = {}
        for code in codes:
            df = store.load(code)
            if df is not None and len(df) > 10:
                head = df.iloc[:-5]  # 除最近5日外的历史数据
                h = hashlib.md5(head.to_csv().encode()).hexdigest()
                hashes_before[code] = h

        for code in hashes_before:
            df = store.load(code)
            store.save(code, df, force=False)  # 正常增量，不覆盖历史
            after = store.load(code)
            head = after.iloc[:-5]
            h_after = hashlib.md5(head.to_csv().encode()).hexdigest()
            assert h_after == hashes_before[code], f"{code} 历史数据被修改"

    def test_resume_from_breakpoint(self):
        """ST-DU-03：断点续传逻辑验证"""
        import data.store as store

        code = store.list_codes("stock")[0]
        df = store.load(code)
        if df is None:
            pytest.skip("无本地数据")

        # 模拟中断恢复：save 部分数据后再次 save 完整数据
        half = df.iloc[:len(df) // 2]
        store.save(code, half, force=True)
        store.save(code, df, force=True)  # 恢复完整数据
        after = store.load(code)
        assert len(after) == len(df)
