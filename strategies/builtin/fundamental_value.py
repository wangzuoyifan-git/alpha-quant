"""
内置策略：B1 质量价值（GARP）— L3-B 基本面策略

正交维度：质量（Quality）— Fama-French 质量因子
升级自 v2.0，改进：
  - PE 历史分位过滤（替代固定 PE≤30）：当前 PE 在自身历史区间的分位数 ≤ 阈值
    避免在高估市（全市场 PE 抬升时）系统性买贵
  - 固定 PE 阈值作为硬上限兜底（防止历史数据不足时分位失效）
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
      - roeAvg（TTM）>= min_roe（默认 0.15）
      - 0 < pe_ttm <= max_pe（默认 40，硬上限）
      - pe 历史分位 <= pe_pct_max（默认 0.6，即不超过历史 60 分位）
      - pb_mrq <= max_pb（默认 5）
      - YOYNI >= min_growth（默认 0，不亏损）
    """

    name = "质量价值(GARP)"
    description = "高ROE+PE历史分位过滤+盈利增长，GARP配置仓候选"
    version = "3.0.0"
    applicable_regimes = ["crisis", "risk_off", "balanced", "risk_on", "volatile"]
    style = "quality"
    applicable_scenario = "任何市场环境下筛选质地优良、估值合理的长期配置标的"
    risk_profile = "defensive"
    time_horizon = "medium"
    data_dependency = ["fundamental", "technical"]
    market_condition = "range"  # @deprecated

    def requires_bars(self) -> int:
        return 60  # 需要足够历史 PE 数据计算分位

    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        min_roe = self.params.get("min_roe", 0.15)
        max_pe = self.params.get("max_pe", 40.0)        # 硬上限（兜底）
        pe_pct_max = self.params.get("pe_pct_max", 0.6) # PE 历史分位上限
        max_pb = self.params.get("max_pb", 5.0)
        min_growth = self.params.get("min_growth", 0.0)

        if df is None or df.empty or "close" not in df.columns:
            return []

        profit = load_fund(code, "profit")
        if profit is None or profit.empty:
            return []
        roe = ttm_sum(profit.get("roeAvg")) if "roeAvg" in profit.columns else np.nan
        if pd.isna(roe) or roe < min_roe:
            return []

        growth = load_fund(code, "growth")
        yoy_ni = np.nan
        if growth is not None and not growth.empty:
            yoy_ni = float(growth.iloc[-1].get("YOYNI", np.nan))
        if pd.notna(yoy_ni) and yoy_ni < min_growth:
            return []

        last = df.iloc[-1]
        pe = float(last.get("pe_ttm", np.nan))
        pb = float(last.get("pb_mrq", np.nan))
        if pd.isna(pe) or pe <= 0 or pe > max_pe:
            return []
        if pd.isna(pb) or pb > max_pb:
            return []

        # PE 历史分位过滤：当前 PE 在 df 历史区间的分位数
        if "pe_ttm" in df.columns and len(df) >= 20:
            hist_pe = pd.to_numeric(df["pe_ttm"], errors="coerce").dropna()
            hist_pe = hist_pe[hist_pe > 0]
            if len(hist_pe) >= 20:
                pct = float((hist_pe < pe).sum() / len(hist_pe))
                if pct > pe_pct_max:
                    return []  # 当前 PE 处于历史偏贵区间

        price = float(last["close"])
        if price <= 2 or price > 500:
            return []

        reasons = [f"ROE{roe*100:.1f}%", f"PE{pe:.1f}", f"PB{pb:.1f}"]
        if pd.notna(yoy_ni):
            reasons.append(f"净利同比{yoy_ni*100:+.1f}%")

        score = 40.0 + roe * 100 * 1.5 + max(0, (max_pe - pe)) * 0.5
        score = float(min(max(score, 0.0), 100.0))

        return [Signal(
            code=code, name=name, signal="WATCH",
            price=price, reason="，".join(reasons), score=score,
            position_type="config", time_horizon="medium",
        )]
