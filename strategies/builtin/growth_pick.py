"""
内置策略：B3 成长筛选 — L3-B 基本面策略

高净利润增长 + 高净资产增长 + ROE 达标，成长型配置标的。
输出配置仓候选：signal=WATCH, position_type=config, time_horizon=medium。

数据量纲：fundamental 域来自 BaoStock，财务比率为小数（YOYNI 0.25=25%）。
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal, ttm_sum
from data.store_fundamental import load as load_fund


class GrowthPickStrategy(BaseStrategy):
    """
    B3 成长筛选

    筛选条件（默认值，均可通过 params 配置）：
      - YOYNI（净利润同比）>= min_ni_growth（默认 0.25，即 25%）
      - YOYEquity（净资产同比）>= min_equity_growth（默认 0.15）
      - roeAvg >= min_roe（默认 0.10，即 10%）
    """

    name = "成长筛选"
    description = "高净利增长+高净资产增长+ROE达标的成长型配置标的"
    version = "1.0.0"
    applicable_regimes = ["crisis", "risk_off", "balanced", "risk_on", "volatile"]
    style = "growth"
    applicable_scenario = "捕捉业绩高速成长的优质成长股，中期配置"
    risk_profile = "balanced"
    time_horizon = "medium"
    data_dependency = ["fundamental", "technical"]
    market_condition = "range"  # @deprecated

    def requires_bars(self) -> int:
        return 5

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        min_ni_growth = self.params.get("min_ni_growth", 0.25)
        min_equity_growth = self.params.get("min_equity_growth", 0.15)
        min_roe = self.params.get("min_roe", 0.10)

        if df is None or df.empty or "close" not in df.columns:
            return []

        growth = load_fund(code, "growth")
        if growth is None or growth.empty:
            return []
        g = growth.iloc[-1]
        yoy_ni = float(g.get("YOYNI", np.nan))
        yoy_equity = float(g.get("YOYEquity", np.nan))
        if pd.isna(yoy_ni) or yoy_ni < min_ni_growth:
            return []
        if pd.isna(yoy_equity) or yoy_equity < min_equity_growth:
            return []

        profit = load_fund(code, "profit")
        if profit is None or profit.empty:
            return []
        # roeAvg 单季累加为 TTM 年化口径
        roe = ttm_sum(profit.get("roeAvg")) if "roeAvg" in profit.columns else np.nan
        if pd.isna(roe) or roe < min_roe:
            return []

        price = float(df.iloc[-1]["close"])
        if price <= 2 or price > 500:
            return []

        reasons = [f"净利同比{yoy_ni*100:+.1f}%",
                   f"净资产同比{yoy_equity*100:+.1f}%",
                   f"ROE{roe*100:.1f}%"]

        # 评分：净利增长主导 + ROE 加分
        score = 40.0 + yoy_ni * 100 * 0.5 + roe * 100 * 1.0
        score = float(min(max(score, 0.0), 100.0))

        return [Signal(
            code=code, name=name, signal="WATCH",
            price=price, reason="，".join(reasons), score=score,
            position_type="config", time_horizon="medium",
        )]
