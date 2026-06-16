"""
内置策略：B2 深度价值 — L3-B 基本面策略

低 PB + 低 PE + 盈利为正 + 负债可控，深度低估的长期价值标的。
输出配置仓候选：signal=WATCH, position_type=config, time_horizon=long。

数据量纲：fundamental 域来自 BaoStock，财务比率为小数。
估值 pe_ttm/pb_mrq 来自 K线 df（market 域）。
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal
from data.store_fundamental import load as load_fund


class DeepValueStrategy(BaseStrategy):
    """
    B2 深度价值

    筛选条件（默认值，均可通过 params 配置）：
      - pb_mrq <= max_pb（默认 1.5）
      - 0 < pe_ttm <= max_pe（默认 15，排除亏损股）
      - 净利润为正（profit.netProfit > 0）
      - liabilityToAsset <= max_debt（默认 0.6，即 60%）
    """

    name = "深度价值"
    description = "低PB+低PE+盈利为正+负债可控，深度低估的长期价值标的"
    version = "1.0.0"
    applicable_regimes = ["crisis", "risk_off", "balanced", "risk_on", "volatile"]
    style = "value"
    applicable_scenario = "寻找显著低于内在价值的标的，逆向布局，长期持有"
    risk_profile = "defensive"
    time_horizon = "long"
    data_dependency = ["fundamental", "technical"]
    market_condition = "range"  # @deprecated

    def requires_bars(self) -> int:
        return 5

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        max_pb = self.params.get("max_pb", 1.5)
        max_pe = self.params.get("max_pe", 15.0)
        max_debt = self.params.get("max_debt", 0.6)

        if df is None or df.empty or "close" not in df.columns:
            return []

        last = df.iloc[-1]
        pe = float(last.get("pe_ttm", np.nan))
        pb = float(last.get("pb_mrq", np.nan))
        if pd.isna(pe) or pe <= 0 or pe > max_pe:
            return []
        if pd.isna(pb) or pb > max_pb:
            return []

        profit = load_fund(code, "profit")
        if profit is None or profit.empty:
            return []
        net_profit = float(profit.iloc[-1].get("netProfit", np.nan))
        if pd.isna(net_profit) or net_profit <= 0:
            return []

        # 负债率过滤
        balance = load_fund(code, "balance")
        debt = np.nan
        if balance is not None and not balance.empty:
            debt = float(balance.iloc[-1].get("liabilityToAsset", np.nan))
            if pd.notna(debt) and debt > max_debt:
                return []

        price = float(last["close"])
        if price <= 2 or price > 500:
            return []

        reasons = [f"PB{pb:.2f}", f"PE{pe:.1f}"]
        if pd.notna(debt):
            reasons.append(f"负债率{debt*100:.1f}%")

        # 评分：PB 越低越高分 + PE 折价加分
        score = 50.0 + (max_pb - pb) / max_pb * 30 + max(0, (max_pe - pe)) * 1.0
        score = float(min(max(score, 0.0), 100.0))

        return [Signal(
            code=code, name=name, signal="WATCH",
            price=price, reason="，".join(reasons), score=score,
            position_type="config", time_horizon="long",
        )]
