"""
内置策略：B1 质量价值（GARP）— L3-B 基本面策略

高 ROE + 合理 PE/PB + 盈利增长（不亏损），GARP（合理价格成长）核心。
输出配置仓候选：signal=WATCH, position_type=config, time_horizon=medium。

数据量纲：fundamental 域来自 BaoStock，财务比率为小数（ROE 0.15=15%）。
估值 pe_ttm/pb_mrq 来自 K线 df（market 域）。
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal, ttm_sum
from data.store_fundamental import load as load_fund


class FundamentalValueStrategy(BaseStrategy):
    """
    B1 质量价值（GARP）

    筛选条件（默认值，均可通过 params 配置）：
      - roeAvg >= min_roe（默认 0.15，即 15%）— 近4季 TTM 口径
      - 0 < pe_ttm <= max_pe（默认 30，排除亏损股）
      - pb_mrq <= max_pb（默认 5）
      - YOYNI（净利润同比）>= min_growth（默认 0，不亏损）
    """

    name = "质量价值(GARP)"
    description = "高ROE+合理PE/PB+盈利增长，GARP 核心的配置仓候选"
    version = "2.0.0"
    applicable_regimes = ["crisis", "risk_off", "balanced", "risk_on", "volatile"]
    style = "quality"
    applicable_scenario = "任何市场环境下筛选质地优良、估值合理的长期配置标的"
    risk_profile = "defensive"
    time_horizon = "medium"
    data_dependency = ["fundamental", "technical"]
    market_condition = "range"  # @deprecated

    def requires_bars(self) -> int:
        return 5

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        min_roe = self.params.get("min_roe", 0.15)
        max_pe = self.params.get("max_pe", 30.0)
        max_pb = self.params.get("max_pb", 5.0)
        min_growth = self.params.get("min_growth", 0.0)

        if df is None or df.empty or "close" not in df.columns:
            return []

        profit = load_fund(code, "profit")
        if profit is None or profit.empty:
            return []
        # roeAvg 为单季 ROE，累加近4季还原 TTM 年化口径
        roe = ttm_sum(profit.get("roeAvg")) if "roeAvg" in profit.columns else np.nan
        if pd.isna(roe) or roe < min_roe:
            return []

        # 净利润同比（growth 域 YOYNI）
        growth = load_fund(code, "growth")
        yoy_ni = np.nan
        if growth is not None and not growth.empty:
            yoy_ni = float(growth.iloc[-1].get("YOYNI", np.nan))
        if pd.notna(yoy_ni) and yoy_ni < min_growth:
            return []

        last = df.iloc[-1]
        pe = float(last.get("pe_ttm", np.nan))
        pb = float(last.get("pb_mrq", np.nan))
        # 估值过滤：排除亏损（PE<=0）和高估
        if pd.isna(pe) or pe <= 0 or pe > max_pe:
            return []
        if pd.isna(pb) or pb > max_pb:
            return []

        price = float(last["close"])
        if price <= 2 or price > 500:
            return []

        reasons = [f"ROE{roe*100:.1f}%", f"PE{pe:.1f}", f"PB{pb:.1f}"]
        if pd.notna(yoy_ni):
            reasons.append(f"净利同比{yoy_ni*100:+.1f}%")

        # 评分：ROE 主导 + 低估值加分
        score = 40.0 + roe * 100 * 1.5 + max(0, (max_pe - pe)) * 0.5
        score = float(min(max(score, 0.0), 100.0))

        return [Signal(
            code=code, name=name, signal="WATCH",
            price=price, reason="，".join(reasons), score=score,
            position_type="config", time_horizon="medium",
        )]
