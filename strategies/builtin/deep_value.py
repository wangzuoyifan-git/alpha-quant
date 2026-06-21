"""
内置策略：B2 深度价值 — L3-B 基本面策略

正交维度：价值（Value）— 低估值均值回归溢价
升级自 v1.0，改进：
  - 行业排除：金融（银行/保险/证券）和地产行业的低 PB 是商业模式特性而非低估
    通过代码前缀 + 行业代码双重识别（宽松：只排除明确的金融地产）
  - 股息率验证：真正的价值股应有分红能力，PE 低但无分红的可能是价值陷阱
  - 连续盈利：要求近 2 季净利润均为正（非单季侥幸）
"""
from typing import List

import numpy as np
import pandas as pd

from ..base import BaseStrategy, Signal
from data.store_fundamental import load as load_fund

# 申万一级行业代码中的金融/地产行业（这类行业低PB是常态，不代表低估）
_EXCLUDED_INDUSTRIES = {"801780", "801790", "801180"}  # 银行、非银金融、房地产

# 金融股通常以 6003xx（银行）/6006xx（证券）前缀，地产以 000002/600048 等，
# 不做代码前缀排除（太复杂且易误杀），改用行业分类排除（需 meta 域）


class DeepValueStrategy(BaseStrategy):
    """
    B2 深度价值

    筛选条件（默认值，均可通过 params 配置）：
      - pb_mrq <= max_pb（默认 1.5）
      - 0 < pe_ttm <= max_pe（默认 15）
      - 净利润近 2 季均为正（consecutive_profitable=2）
      - liabilityToAsset <= max_debt（默认 0.6）
      - 股息率（div_yield）>= min_div_yield（默认 0，0=不要求；建议设 0.01 即 1%）
      - 排除金融/地产行业（exclude_finance=True）
    """

    name = "深度价值"
    description = "低PB+低PE+连续盈利+行业排除+股息验证，防价值陷阱"
    version = "2.0.0"
    applicable_regimes = ["crisis", "risk_off", "balanced", "risk_on", "volatile"]
    style = "value"
    applicable_scenario = "逆向布局显著低估标的，长期持有；金融/地产自动排除"
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
        min_div_yield = self.params.get("min_div_yield", 0.0)
        consecutive = self.params.get("consecutive_profitable", 2)
        exclude_finance = self.params.get("exclude_finance", True)

        if df is None or df.empty or "close" not in df.columns:
            return []

        # 行业排除：金融/地产（通过 meta 域反查申万一级行业，code→L1）
        if exclude_finance:
            from data.store_meta import get_stock_l1_industry
            ind = get_stock_l1_industry(code)
            if ind in _EXCLUDED_INDUSTRIES:
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
        # 连续 consecutive 季净利润为正
        if "netProfit" not in profit.columns:
            return []
        recent_profits = profit["netProfit"].iloc[-consecutive:]
        if len(recent_profits) < consecutive or not (recent_profits > 0).all():
            return []

        balance = load_fund(code, "balance")
        debt = np.nan
        if balance is not None and not balance.empty:
            debt = float(balance.iloc[-1].get("liabilityToAsset", np.nan))
            if pd.notna(debt) and debt > max_debt:
                return []

        # 股息率验证（来自 K 线 df 的 dv_ratio 字段，若有）
        div_yield = float(last.get("dv_ratio", 0.0) or 0.0) / 100  # dv_ratio 单位为%
        if min_div_yield > 0 and div_yield < min_div_yield:
            return []

        price = float(last["close"])
        if price <= 0:
            return []

        reasons = [f"PB{pb:.2f}", f"PE{pe:.1f}"]
        if pd.notna(debt):
            reasons.append(f"负债率{debt*100:.1f}%")
        if div_yield > 0:
            reasons.append(f"股息率{div_yield*100:.1f}%")

        score = 50.0 + (max_pb - pb) / max_pb * 30 + max(0, (max_pe - pe)) * 1.0
        score = float(min(max(score, 0.0), 100.0))

        return [Signal(
            code=code, name=name, signal="WATCH",
            price=price, reason="，".join(reasons), score=score,
            position_type="config", time_horizon="long",
        )]

    def compute_exit_levels(self, df, entry_price, fundamental_data=None):
        """深度价值出场：PB>2.0或PE历史分位>80%减仓，无价格止损"""
        from strategies.base import ExitLevels
        pe_exit_pct = self.params.get("pe_exit_pct", 0.80)
        exit_desc = (f"PE历史分位>{pe_exit_pct*100:.0f}%或PB>2.0开始减仓；"
                     "连续亏损2季或股息取消触发清仓")
        return ExitLevels(
            stop_loss=0.0,
            take_profit=entry_price * 1.5,
            atr=0.0,
            exit_logic="fundamental",
            pe_exit_pct=pe_exit_pct,
            fundamental_exit=exit_desc,
        )
