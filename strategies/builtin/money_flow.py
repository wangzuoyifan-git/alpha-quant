"""
内置策略：资金流信号策略
基于主力净流入/北向资金持续流入发出信号
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal
from data.store_moneyflow import load as load_factor


class MoneyFlowStrategy(BaseStrategy):
    """
    资金流信号策略

    检查近 N 日：
      - 主力净流入（个股）累计为正且超过阈值
      - 可选：检查北向资金是否净流入（作为辅助确认）
    """

    name = "资金流信号"
    description = "基于主力净流入/北向资金识别机构动向"
    version = "1.0.0"
    applicable_regimes = ["risk_on", "balanced"]
    style = "moneyflow"
    applicable_scenario = "主力资金持续净流入，机构建仓迹象明显时有效"
    risk_profile = "aggressive"
    time_horizon = "short"
    data_dependency = ["technical", "moneyflow"]
    market_condition = "bull"  # @deprecated

    def requires_bars(self) -> int:
        return 10

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        lookback = self.params.get("lookback", 5)
        min_net = self.params.get("min_net_inflow", 0)  # 最低净流入额

        mf = load_factor("stock", code=code)
        if mf is None or mf.empty or len(mf) < lookback:
            return []

        recent = mf.iloc[-lookback:]

        # 主力净流入
        if "net_mf_amount" not in recent.columns:
            return []

        total_inflow = float(recent["net_mf_amount"].sum())
        if total_inflow <= min_net:
            return []

        if df is None or df.empty or "close" not in df.columns:
            return []

        price = float(df.iloc[-1]["close"])
        if price <= 2 or price > 500:
            return []

        flow_yi = total_inflow / 1e8
        score = min(50.0 + flow_yi * 2.0, 95.0)

        return [Signal(
            code=code, name=name, signal="BUY",
            price=price, score=score,
            reason=f"近{lookback}日主力净流入{flow_yi:.1f}亿",
        )]
