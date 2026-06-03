# 内置策略注册表
from .ma_cross import MACrossStrategy
from .macd_rsi import MACDRSIStrategy
from .vol_price_breakout import VolPriceBreakoutStrategy
from .etf_momentum import ETFMomentumStrategy
from .fundamental_value import FundamentalValueStrategy
from .money_flow import MoneyFlowStrategy

BUILTIN_STRATEGIES = {
    "ma_cross": MACrossStrategy,
    "macd_rsi": MACDRSIStrategy,
    "vol_price_breakout": VolPriceBreakoutStrategy,
    "etf_momentum": ETFMomentumStrategy,
    "fundamental_value": FundamentalValueStrategy,
    "money_flow": MoneyFlowStrategy,
}

__all__ = [
    "BUILTIN_STRATEGIES",
    "MACrossStrategy",
    "MACDRSIStrategy",
    "VolPriceBreakoutStrategy",
    "ETFMomentumStrategy",
    "FundamentalValueStrategy",
    "MoneyFlowStrategy",
]
