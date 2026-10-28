# 内置策略注册表（正交框架，v2）
from .chan_theory import ChanTheoryStrategy
from .deep_value import DeepValueStrategy
from .etf_momentum import ETFMomentumStrategy
from .fundamental_value import FundamentalValueStrategy
from .growth_pick import GrowthPickStrategy
from .ma_cross import MACrossStrategy
from .macd_rsi import MACDRSIStrategy
from .money_flow import MoneyFlowStrategy  # @deprecated：保留类供回测历史兼容，不注册进策略池
from .quality_trend import QualityTrendStrategy
from .vol_price_breakout import VolPriceBreakoutStrategy

BUILTIN_STRATEGIES = {
    # L3-A 交易仓（4个正交维度）
    "ma_cross":           MACrossStrategy,        # 趋势跟踪（个股主力）
    "macd_rsi":           MACDRSIStrategy,         # 均值回归
    "vol_price_breakout": VolPriceBreakoutStrategy, # 突破
    "etf_momentum":       ETFMomentumStrategy,      # ETF 轮动
    "chan_theory":        ChanTheoryStrategy,        # 缠论三买/三卖（结构突破）
    "quality_trend":      QualityTrendStrategy,      # 低波动质量趋势（趋势跟踪增强）
    # L3-B 配置仓（3个 Fama-French 正交维度）
    "fundamental_value":  FundamentalValueStrategy, # 质量（Quality）
    "deep_value":         DeepValueStrategy,        # 价值（Value）
    "growth_pick":        GrowthPickStrategy,       # 成长（Growth）
    # money_flow 已降级为趋势策略内置过滤层，不再独立注册
}

__all__ = [
    "BUILTIN_STRATEGIES",
    "ChanTheoryStrategy",
    "DeepValueStrategy",
    "ETFMomentumStrategy",
    "FundamentalValueStrategy",
    "GrowthPickStrategy",
    "MACDRSIStrategy",
    "MACrossStrategy",
    "MoneyFlowStrategy",
    "QualityTrendStrategy",
    "VolPriceBreakoutStrategy",
]
