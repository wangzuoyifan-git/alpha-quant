# 内置策略注册表（正交框架，v2）
from .ma_cross import MACrossStrategy
from .macd_rsi import MACDRSIStrategy
from .vol_price_breakout import VolPriceBreakoutStrategy
from .etf_momentum import ETFMomentumStrategy
from .fundamental_value import FundamentalValueStrategy
from .deep_value import DeepValueStrategy
from .growth_pick import GrowthPickStrategy
from .money_flow import MoneyFlowStrategy  # @deprecated：保留类供回测历史兼容，不注册进策略池

BUILTIN_STRATEGIES = {
    # L3-A 交易仓（4个正交维度）
    "ma_cross":           MACrossStrategy,        # 趋势跟踪（个股主力）
    "macd_rsi":           MACDRSIStrategy,         # 均值回归
    "vol_price_breakout": VolPriceBreakoutStrategy, # 突破
    "etf_momentum":       ETFMomentumStrategy,      # ETF 轮动
    # L3-B 配置仓（3个 Fama-French 正交维度）
    "fundamental_value":  FundamentalValueStrategy, # 质量（Quality）
    "deep_value":         DeepValueStrategy,        # 价值（Value）
    "growth_pick":        GrowthPickStrategy,       # 成长（Growth）
    # money_flow 已降级为趋势策略内置过滤层，不再独立注册
}

__all__ = [
    "BUILTIN_STRATEGIES",
    "MACrossStrategy", "MACDRSIStrategy", "VolPriceBreakoutStrategy",
    "ETFMomentumStrategy", "FundamentalValueStrategy", "DeepValueStrategy",
    "GrowthPickStrategy", "MoneyFlowStrategy",
]
