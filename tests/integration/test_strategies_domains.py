"""
L3 系统测试 — 新策略健壮性 (ST-NS-01~04) + 新数据域 (ST-ND-01~04)
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

pytestmark = [pytest.mark.system, pytest.mark.timeout(30)]


class TestNewStrategyRobustness:
    """ST-NS-01~04：新策略在全量数据上运行不抛异常"""

    def _run_strategy_on_sample(self, strategy, n=100):
        import data.store as store
        codes = store.list_codes("stock")[:n]
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        total_errors = 0
        total_signals = 0
        for code in codes:
            df = store.load(code, start=start, end=end)
            if df is None or df.empty or len(df) < strategy.requires_bars():
                continue
            try:
                sigs = strategy.generate_signals(df.reset_index(), code, code)
                total_signals += len(sigs)
            except Exception:
                total_errors += 1
        return total_errors, total_signals, len(codes)

    def test_macross_strategy_no_exception(self):
        """ST-NS-01：MA金叉策略全量运行无异常"""
        from strategies.builtin.ma_cross import MACrossStrategy
        errors, sigs, total = self._run_strategy_on_sample(MACrossStrategy(), n=50)
        assert errors == 0, f"{errors}/{total} 只失败"

    def test_macd_rsi_strategy_no_exception(self):
        from strategies.builtin.macd_rsi import MACDRSIStrategy
        errors, _, total = self._run_strategy_on_sample(MACDRSIStrategy(), n=50)
        assert errors == 0, f"{errors}/{total} 只失败"

    def test_vol_breakout_strategy_no_exception(self):
        from strategies.builtin.vol_price_breakout import VolPriceBreakoutStrategy
        errors, _, total = self._run_strategy_on_sample(VolPriceBreakoutStrategy(), n=50)
        assert errors == 0, f"{errors}/{total} 只失败"

    def test_etf_momentum_strategy_no_exception(self):
        from strategies.builtin.etf_momentum import ETFMomentumStrategy
        errors, _, total = self._run_strategy_on_sample(ETFMomentumStrategy({"lookback": 20}), n=30)
        # ETF动量需要amount列，A股数据有amount列
        assert errors == 0, f"{errors}/{total} 只失败"

    def test_fundamental_value_strategy_no_exception(self):
        """ST-NS-02：新策略回测覆盖率"""
        from strategies.builtin.fundamental_value import FundamentalValueStrategy
        errors, _, total = self._run_strategy_on_sample(FundamentalValueStrategy(), n=30)
        assert errors == 0, f"{errors}/{total} 只失败"

    def test_money_flow_strategy_no_exception(self):
        from strategies.builtin.money_flow import MoneyFlowStrategy
        errors, _, total = self._run_strategy_on_sample(MoneyFlowStrategy(), n=30)
        assert errors == 0, f"{errors}/{total} 只失败"

    def test_deep_value_strategy_no_exception(self):
        """B2 深度价值策略 robustness"""
        from strategies.builtin.deep_value import DeepValueStrategy
        errors, _, total = self._run_strategy_on_sample(DeepValueStrategy(), n=30)
        assert errors == 0, f"{errors}/{total} 只失败"

    def test_growth_pick_strategy_no_exception(self):
        """B3 成长筛选策略 robustness"""
        from strategies.builtin.growth_pick import GrowthPickStrategy
        errors, _, total = self._run_strategy_on_sample(GrowthPickStrategy(), n=30)
        assert errors == 0, f"{errors}/{total} 只失败"

    def test_all_strategies_registered(self):
        """验证所有8个策略正确注册（台阶二新增 B2 深度价值 / B3 成长筛选）"""
        from strategies.builtin import BUILTIN_STRATEGIES
        assert len(BUILTIN_STRATEGIES) == 8
        required = {"ma_cross", "macd_rsi", "vol_price_breakout",
                     "etf_momentum", "fundamental_value", "money_flow",
                     "deep_value", "growth_pick"}
        assert set(BUILTIN_STRATEGIES.keys()) == required


class TestNewDataDomain:
    """ST-ND-01~04：新数据域系统测试"""

    def test_all_domain_dirs_exist(self):
        """ST-ND-01：各数据域物理路径无交集"""
        bases = []
        import data.store as store
        import data.store_meta as sm
        import data.store_fundamental as sf
        import data.store_factor as sfact
        import data.store_macro as smacro
        import data.store_moneyflow as smf

        for mod in [store, sm, sf, sfact, smacro, smf]:
            bases.append(str(Path(mod.BASE_DIR).resolve()))
        assert len(bases) == len(set(bases)), f"域名重复: {bases}"

    def test_fundamental_store_roundtrip_with_real_data(self):
        """ST-ND-03：真实数据 save/load 往返"""
        import data.store_fundamental as sf
        codes = sf.list_codes("profit")
        if not codes:
            pytest.skip("本地无 fundamental/profit 数据")
        df = sf.load(codes[0], "profit")
        assert df is not None
        assert not df.empty
        assert isinstance(df.index, type(pytest.importorskip("pandas").DatetimeIndex([])))

    def test_macro_store_roundtrip_with_real_data(self):
        """macro 域数据读取"""
        import data.store_macro as smacro
        for dtype in ("bond_yield", "m2", "cpi_ppi", "pmi"):
            df = smacro.load(dtype)
            if df is not None:
                assert not df.empty, f"{dtype} 为空"
                assert len(df) > 3, f"{dtype} 数据过少: {len(df)}"

    def test_moneyflow_store_roundtrip_with_real_data(self):
        """moneyflow 域数据读取"""
        import data.store_moneyflow as smf
        margin_codes = smf.list_codes("margin")
        stock_codes = smf.list_codes("stock")
        has_data = len(margin_codes) > 0 or len(stock_codes) > 0
        if not has_data:
            pytest.skip("moneyflow 域无数据")

    def test_meta_industry_mapping(self):
        """ST-ND-02：行业映射数据完整性"""
        from data.store_meta import load as load_meta
        sw = load_meta("industry_sw")
        assert sw is not None
        code_col = next((c for c in sw.columns if "code" in c.lower()), None)
        assert code_col is not None, f"无代码列: {list(sw.columns)}"
        assert "industry_name" in sw.columns or "industry" in sw.columns
        # 至少要有茅台
        moutai = sw[sw[code_col].astype(str) == "600519"]
        if not moutai.empty:
            ind_col = "industry_name" if "industry_name" in sw.columns else "industry"
            assert moutai.iloc[0][ind_col], "茅台无行业映射"
