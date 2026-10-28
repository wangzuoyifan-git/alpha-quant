"""A 股成交规则与撮合模型。

只做纯计算，不依赖策略/存储，便于单测与研究框架复用。

约定：
- 信号在 t 日收盘确认，默认 t+1 开盘成交（``ExecutionModel.mode`` 由引擎控制）。
- 涨停/一字板买不进，一字跌停卖不出。
- 买入按 100 股整手；卖出部分仓位也按整手，清仓允许卖出全部余额。
- 佣金买卖双向收取；印花税仅卖出收取。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MAIN_LIMIT_PCT = 0.10
GEM_LIMIT_PCT = 0.20       # 创业板 300/301、科创板 688/689
BSE_LIMIT_PCT = 0.30       # 北交所 4/8/92 开头
ST_LIMIT_PCT = 0.05
ETF_LIMIT_PCT = 0.10


def normalize_code(code: str) -> str:
    """去掉 .SH/.SZ/.BJ 等后缀，返回纯数字代码。"""
    return str(code).split(".")[0].strip()


def limit_pct_for(code: str, asset_type: str = "stock", is_st: bool = False) -> float:
    """按代码/资产类型返回涨跌停幅度。"""
    c = normalize_code(code)
    if asset_type == "etf":
        return ETF_LIMIT_PCT
    if is_st:
        return ST_LIMIT_PCT
    if c[:3] in ("300", "301", "688", "689"):
        return GEM_LIMIT_PCT
    if c[:1] in ("4", "8") or c[:2] == "92":
        return BSE_LIMIT_PCT
    return MAIN_LIMIT_PCT


def _round_price(value: float) -> float:
    # A 股报价最小变动单位 0.01 元，四舍五入
    return round(float(value) + 1e-9, 2)


def limit_prices(prev_close: float, code: str, asset_type: str = "stock",
                 is_st: bool = False, limit_pct: float | None = None) -> tuple[float, float]:
    """返回 (涨停价, 跌停价)。"""
    pct = limit_pct_for(code, asset_type, is_st) if limit_pct is None else float(limit_pct)
    return (_round_price(prev_close * (1 + pct)),
            _round_price(prev_close * (1 - pct)))


def _is_finite(*values) -> bool:
    return all(v is not None and not math.isnan(float(v)) for v in values)


def bar_tradable(open_: float, high: float, low: float, close: float,
                 volume: float | None = None) -> bool:
    """停牌/缺数据判定：OHLC 缺失或成交量为 0/NaN 视为不可交易。

    A 股停牌日常以“前收 OHLC + 无量”形式残留，必须用成交量兜底。
    """
    if not _is_finite(open_, high, low, close):
        return False
    return not (volume is not None and
                (not _is_finite(volume) or float(volume) <= 0))


def can_buy(open_: float, high: float, low: float, close: float,
            prev_close: float, code: str, asset_type: str = "stock",
            limit_buffer: float = 0.98, check_limit: bool = True,
            is_st: bool = False, limit_pct: float | None = None,
            volume: float | None = None) -> bool:
    """开盘是否可买。

    - 停牌（OHLC 缺失）不可买；
    - 一字板（high == low）不可买；
    - 开盘价达到涨停价附近（默认 98% 涨停幅度）不可买。
    """
    if not bar_tradable(open_, high, low, close, volume):
        return False
    if not _is_finite(prev_close) or prev_close <= 0:
        return False
    if not check_limit:
        return True
    pct = limit_pct_for(code, asset_type, is_st) if limit_pct is None else float(limit_pct)
    near_limit_up = prev_close * (1 + pct * limit_buffer)
    one_word = abs(float(high) - float(low)) < 1e-12
    if one_word:
        return False
    return float(open_) < near_limit_up


def can_sell(open_: float, high: float, low: float, close: float,
             prev_close: float, code: str, asset_type: str = "stock",
             limit_buffer: float = 0.98, check_limit: bool = True,
             is_st: bool = False, limit_pct: float | None = None,
             volume: float | None = None) -> bool:
    """开盘是否可卖。

    仅拦截“一字跌停且开盘在跌停价附近”的情形；盘中打开跌停的仍可卖出。
    普通跌停但非一字（high > low）可在开盘卖出。
    """
    if not bar_tradable(open_, high, low, close, volume):
        return False
    if not _is_finite(prev_close) or prev_close <= 0:
        return False
    if not check_limit:
        return True
    pct = limit_pct_for(code, asset_type, is_st) if limit_pct is None else float(limit_pct)
    near_limit_dn = prev_close * (1 - pct * limit_buffer)
    one_word = abs(float(high) - float(low)) < 1e-12
    one_word_down = one_word and float(close) < float(prev_close)
    return not (one_word_down and float(open_) <= near_limit_dn)


def round_lot(shares: float, lot: int = 100) -> int:
    """向下取整到整手。"""
    if shares <= 0 or lot <= 0:
        return 0
    return int(shares // lot) * lot


@dataclass
class ExecutionModel:
    """成交模型：价格、费用与仓位取整。"""

    asset_type: str = "stock"
    commission: float = 0.0003
    slippage: float = 0.001
    stamp_duty: float = 0.0005
    min_lot: int = 100
    limit_buffer: float = 0.98
    limit_check: bool = True
    t_plus_one: bool = True

    def can_buy(self, open_: float, high: float, low: float, close: float,
                prev_close: float, code: str, is_st: bool = False,
                limit_pct: float | None = None,
                volume: float | None = None) -> bool:
        return can_buy(
            open_, high, low, close, prev_close, code,
            asset_type=self.asset_type, limit_buffer=self.limit_buffer,
            check_limit=self.limit_check, is_st=is_st, limit_pct=limit_pct,
            volume=volume,
        )

    def can_sell(self, open_: float, high: float, low: float, close: float,
                 prev_close: float, code: str, is_st: bool = False,
                 limit_pct: float | None = None,
                 volume: float | None = None) -> bool:
        return can_sell(
            open_, high, low, close, prev_close, code,
            asset_type=self.asset_type, limit_buffer=self.limit_buffer,
            check_limit=self.limit_check, is_st=is_st, limit_pct=limit_pct,
            volume=volume,
        )

    def buy_price(self, raw_price: float) -> float:
        return float(raw_price) * (1 + self.slippage)

    def sell_price(self, raw_price: float) -> float:
        return float(raw_price) * (1 - self.slippage)

    def buy_cost(self, shares: int, price: float) -> float:
        return shares * price * (1 + self.commission)

    def sell_proceeds(self, shares: int, price: float) -> float:
        return shares * price * (1 - self.commission - self.stamp_duty)

    def size_buy(self, cash: float, price: float, alloc: float,
                 lot: int | None = None) -> int:
        """在现金和单仓预算约束下计算可买整手股数。"""
        lot = self.min_lot if lot is None else int(lot)
        if price <= 0 or cash <= 0 or alloc <= 0:
            return 0
        budget = min(float(cash), float(alloc))
        shares = round_lot(budget / price, lot)
        while shares > 0 and self.buy_cost(shares, price) > cash:
            shares -= lot
        return max(shares, 0)
