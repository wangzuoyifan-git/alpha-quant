# 内置策略注册表
from .ma_cross import MACrossStrategy
from .macd_rsi import MACDRSIStrategy
from .vol_price_breakout import VolPriceBreakoutStrategy

BUILTIN_STRATEGIES = {
    "ma_cross": MACrossStrategy,
    "macd_rsi": MACDRSIStrategy,
    "vol_price_breakout": VolPriceBreakoutStrategy,
}

__all__ = ["BUILTIN_STRATEGIES", "MACrossStrategy", "MACDRSIStrategy", "VolPriceBreakoutStrategy"]
