"""
内置策略：基本面价值策略
基于 ROE/净利润同比/资产负债率 筛选低估成长标的
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal
from data.store_fundamental import load as load_fund


class FundamentalValueStrategy(BaseStrategy):
    """
    基本面价值策略

    检查标的最近季度：
      - ROE >= min_roe（默认 10%）
      - 净利润同比 >= min_growth（默认 0，即不亏损）
      - 资产负债率 <= max_debt_ratio（默认 60%）
      - 价格在 5%~95% 区间（排除极端价格）
    """

    name = "基本面价值"
    description = "基于ROE/盈利增长/负债率筛选财务健康的低估标的"
    version = "1.0.0"
    market_condition = "range"

    def requires_bars(self) -> int:
        return 5  # 只需要读取基本面数据，不需大量K线

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        min_roe = self.params.get("min_roe", 10.0)
        min_growth = self.params.get("min_growth", 0.0)
        max_debt = self.params.get("max_debt_ratio", 60.0)

        # 从 fundamental store 读财务数据
        profit = load_fund(code, "profit")
        balance = load_fund(code, "balance")

        if profit is None or profit.empty:
            return []

        latest = profit.iloc[-1]
        roe = float(latest.get("roew", np.nan))
        growth = float(latest.get("netprofit_yoy", np.nan))

        if pd.isna(roe) or pd.isna(growth):
            return []

        if roe < min_roe or growth < min_growth:
            return []

        debt_ratio = None
        if balance is not None and not balance.empty:
            latest_bal = balance.iloc[-1]
            debt_ratio = float(latest_bal.get("debt_assets_ratio", np.nan))
            if pd.notna(debt_ratio) and debt_ratio > max_debt:
                return []

        # 从 K 线取当前价格
        if df is None or df.empty or "close" not in df.columns:
            return []

        price = float(df.iloc[-1]["close"])
        if price <= 2 or price > 500:
            return []

        reasons = [f"ROE{roe:.1f}%", f"净利同比{growth:+.1f}%"]
        if debt_ratio is not None and pd.notna(debt_ratio):
            reasons.append(f"负债率{debt_ratio:.1f}%")

        score = min(40.0 + roe * 2.0 + growth * 1.5, 100.0)

        return [Signal(
            code=code, name=name, signal="BUY",
            price=price, reason="，".join(reasons), score=score,
        )]
