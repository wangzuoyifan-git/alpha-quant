# 内置策略注册表
from .ma_cross import MACrossStrategy
from .macd_rsi import MACDRSIStrategy
from .vol_price_breakout import VolPriceBreakoutStrategy
from .etf_momentum import ETFMomentumStrategy
from .fundamental_value import FundamentalValueStrategy
from .deep_value import DeepValueStrategy
from .growth_pick import GrowthPickStrategy
from .money_flow import MoneyFlowStrategy

BUILTIN_STRATEGIES = {
    "ma_cross": MACrossStrategy,
    "macd_rsi": MACDRSIStrategy,
    "vol_price_breakout": VolPriceBreakoutStrategy,
    "etf_momentum": ETFMomentumStrategy,
    "fundamental_value": FundamentalValueStrategy,
    "deep_value": DeepValueStrategy,
    "growth_pick": GrowthPickStrategy,
    "money_flow": MoneyFlowStrategy,
}

__all__ = [
    "BUILTIN_STRATEGIES",
    "MACrossStrategy",
    "MACDRSIStrategy",
    "VolPriceBreakoutStrategy",
    "ETFMomentumStrategy",
    "FundamentalValueStrategy",
    "DeepValueStrategy",
    "GrowthPickStrategy",
    "MoneyFlowStrategy",
]
