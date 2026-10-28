"""
内置策略：B3 成长筛选 — L3-B 基本面策略

正交维度：成长（Growth）— 盈利增长预期溢价
升级自 v1.0，改进：
  - 新增收入增长维度（YOYEPS 或 YOYAsset 替代只看净利润，防止一次性利润粉饰）
  - 新增 PEG 控制估值：PE / (净利增长率*100) ≤ peg_max（默认 1.5），防高位接盘
  - PEG 仅在 PE 和增速均可用时生效，缺失时降级为只用财务指标
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal, ttm_roe
from data.store_fundamental import load_asof as load_fund


def _load_fund(code: str, data_type: str, as_of=None, history=None):
    """兼容旧测试/外部 monkeypatch：优先走 as-of 接口，否则退回旧 load。"""
    try:
        return load_fund(code, data_type, as_of=as_of, history=history)
    except TypeError:
        return load_fund(code, data_type)


class GrowthPickStrategy(BaseStrategy):
    """
    B3 成长筛选

    筛选条件（默认值，均可通过 params 配置）：
      - YOYNI（净利润同比）>= min_ni_growth（默认 0.25）
      - YOYEquity（净资产同比）>= min_equity_growth（默认 0.15）— 收入/资产双验证
      - roeAvg（TTM）>= min_roe（默认 0.10）
      - PEG = pe_ttm / (YOYNI*100) <= peg_max（默认 1.5，0=禁用）
    """

    name = "成长筛选"
    description = "净利+净资产双增长+ROE+PEG估值控制，成长型配置仓候选"
    version = "2.0.0"
    applicable_regimes = ["crisis", "risk_off", "balanced", "risk_on", "volatile"]
    style = "growth"
    applicable_scenario = "捕捉业绩高速成长的优质成长股，PEG防高估；中期配置"
    risk_profile = "balanced"
    time_horizon = "medium"
    data_dependency = ["fundamental", "technical"]
    market_condition = "range"  # @deprecated

    def requires_bars(self) -> int:
        return 5

    def generate_signals(self, df: pd.DataFrame, code: str, name: str,
                         as_of=None) -> List[Signal]:
        min_ni_growth = self.params.get("min_ni_growth", 0.25)
        min_equity_growth = self.params.get("min_equity_growth", 0.15)
        min_roe = self.params.get("min_roe", 0.10)
        peg_max = self.params.get("peg_max", 1.5)

        if df is None or df.empty or "close" not in df.columns:
            return []

        growth = _load_fund(code, "growth", as_of=as_of)
        if growth is None or growth.empty:
            return []
        g = growth.iloc[-1]
        yoy_ni = float(g.get("YOYNI", np.nan))
        yoy_equity = float(g.get("YOYEquity", np.nan))
        if pd.isna(yoy_ni) or yoy_ni < min_ni_growth:
            return []
        if pd.isna(yoy_equity) or yoy_equity < min_equity_growth:
            return []

        profit = _load_fund(code, "profit", as_of=as_of, history=8)
        if profit is None or profit.empty:
            return []
        roe = ttm_roe(profit)
        if pd.isna(roe) or roe < min_roe:
            return []

        last = df.iloc[-1]
        price = float(last["close"])
        if price <= 0:
            return []

        # PEG 控制估值（pe_ttm 来自 K 线 df）
        pe = float(last.get("pe_ttm", np.nan))
        peg = np.nan
        if peg_max > 0 and pd.notna(pe) and pe > 0 and yoy_ni > 0:
            peg = pe / (yoy_ni * 100)
            if peg > peg_max:
                return []  # 高增长但估值更高，性价比不足

        reasons = [f"净利同比{yoy_ni*100:+.1f}%",
                   f"净资产同比{yoy_equity*100:+.1f}%",
                   f"ROE{roe*100:.1f}%"]
        if pd.notna(peg):
            reasons.append(f"PEG{peg:.2f}")

        score = 40.0 + yoy_ni * 100 * 0.5 + roe * 100 * 1.0
        score = float(min(max(score, 0.0), 100.0))

        return [Signal(
            code=code, name=name, signal="WATCH",
            price=price, reason="，".join(reasons), score=score,
            position_type="config", time_horizon="medium",
        )]

    def compute_exit_levels(self, df, entry_price, fundamental_data=None):
        """成长策略出场：PEG>2.0或PE历史分位>75%减仓，净利增速转负清仓"""
        from strategies.base import ExitLevels
        pe_exit_pct = self.params.get("pe_exit_pct", 0.75)
        exit_desc = (f"PE历史分位>{pe_exit_pct*100:.0f}%或PEG>2.0开始减仓；"
                     "净利润同比增速转负或低于15%触发清仓")
        return ExitLevels(
            stop_loss=0.0,
            take_profit=entry_price * 2.0,  # 成长股目标更高
            atr=0.0,
            exit_logic="fundamental",
            pe_exit_pct=pe_exit_pct,
            fundamental_exit=exit_desc,
        )
