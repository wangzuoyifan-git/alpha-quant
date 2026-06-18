"""
策略基类 - 所有策略必须继承此类
实现 generate_signals() 方法即可接入选股引擎
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

import pandas as pd


# AC5：市场环境枚举（@deprecated，用 applicable_regimes 替代）
MARKET_CONDITIONS = ("bull", "bear", "range", "volatile")


@dataclass
class ScaleLevel:
    """分级操作表单条记录"""
    price: float
    level_type: str   # hard_stop / soft_stop / take_1 / take_2 / trail
    action: str       # stop_loss / reduce / take_profit / trail_stop
    size_pct: float   # 仓位变化比例，负数=减仓（-1.0=清仓）
    reason: str
    cancels_group: str = ""   # "stop" 或 ""；触发时取消同组其他档位


@dataclass
class ExitLevels:
    """
    策略出场价位框架，由 compute_exit_levels() 返回，序列化写入 Signal.extra["exit_levels"]。

    必填字段（任何策略都必须提供）：
      stop_loss   : 硬止损价；配置仓无价格止损时填 0.0
      take_profit : 主要止盈目标价
      atr         : 当日 ATR(14)，供 position_manager 计算仓位

    配置仓专用字段（技术仓留默认值）：
      pe_exit_pct       : PE历史分位超过此值开始减仓（0.0=不使用）
      fundamental_exit  : 基本面出场条件描述（供展示）
    """
    stop_loss: float
    take_profit: float
    atr: float
    support: float = 0.0
    resistance: float = 0.0
    exit_logic: str = "atr"
    scale_levels: List[ScaleLevel] = field(default_factory=list)
    pe_exit_pct: float = 0.0
    fundamental_exit: str = ""

    def to_dict(self) -> dict:
        return {
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "atr": self.atr,
            "support": self.support,
            "resistance": self.resistance,
            "exit_logic": self.exit_logic,
            "scale_levels": [
                {"price": l.price, "level_type": l.level_type, "action": l.action,
                 "size_pct": l.size_pct, "reason": l.reason, "cancels_group": l.cancels_group}
                for l in self.scale_levels
            ],
            "pe_exit_pct": self.pe_exit_pct,
            "fundamental_exit": self.fundamental_exit,
        }


def ttm_sum(series: pd.Series, periods: int = 4) -> float:
    """
    将单季财务比率（如 BaoStock roeAvg）累加为 TTM（近 N 季）值。

    BaoStock 的 roeAvg 是单季 ROE，需累加近 4 季还原年化口径。
    数据不足 periods 季时，按已有季数等比例放大估算。
    返回 float（NaN 表示无有效数据）。
    """
    import numpy as np
    if series is None:
        return float("nan")
    v = series.dropna()
    if len(v) == 0:
        return float("nan")
    if len(v) >= periods:
        return float(v.iloc[-periods:].sum())
    # 不足 N 季：用已有季均值 × periods 估算年化
    return float(v.mean() * periods)


@dataclass
class Signal:
    """单个交易信号"""
    code: str
    name: str
    signal: str          # BUY / SELL / WATCH
    price: float
    reason: str          # 触发原因描述
    score: float = 0.0   # 信号强度评分 0~100
    extra: Dict[str, Any] = field(default_factory=dict)
    # 多周期配置字段（阶段四台阶一，D-5）
    position_type: str = "trading"   # "trading"（交易仓）/ "config"（配置仓候选）
    time_horizon: str = "short"      # "short"(日~周) / "medium"(半年+) / "long"(1年+)
    # @deprecated: 用 applicable_regimes 替代
    market_condition: str = ""

    def __str__(self):
        mc = f" [{self.market_condition}]" if self.market_condition else ""
        return (f"[{self.signal}]{mc} {self.name}({self.code}) "
                f"¥{self.price:.2f} 评分:{self.score:.1f} | {self.reason}")


class BaseStrategy(ABC):
    """策略插件基类"""

    name: str = "未命名策略"
    description: str = ""
    version: str = "1.0.0"

    # ══════ 6 维能力画像（阶段四 D-4）══════

    # ① 适用 regime（主筛）
    applicable_regimes: List[str] = ["balanced"]
    # ② 策略风格
    style: str = ""
    # ③ 适用场景（自然语言，供 LLM 推理）
    applicable_scenario: str = ""
    # ④ 风险风格（声明+一致性校验，不独立筛选）
    risk_profile: str = "balanced"
    # ⑤ 时间周期（占位，台阶一全填 short）
    time_horizon: str = "short"
    # ⑥ 数据依赖（占位）
    data_dependency: List[str] = ["technical"]
    # ⑦ 适用资产类型（空列表=不限制，["etf"]=只跑ETF）
    applicable_asset_types: List[str] = []

    # @deprecated: 用 applicable_regimes 替代
    market_condition: str = "range"

    def __init__(self, params: dict = None):
        self.params = params or {}

    def profile(self) -> dict:
        """返回策略 6 维能力画像，供 select_strategies / LLM 消费。"""
        return {
            "name": self.name,
            "style": self.style,
            "applicable_regimes": self.applicable_regimes,
            "risk_profile": self.risk_profile,
            "time_horizon": self.time_horizon,
            "position_type": "config" if self.time_horizon in ("medium", "long") else "trading",
            "data_dependency": self.data_dependency,
            "applicable_scenario": self.applicable_scenario,
            "description": self.description,
        }

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame, code: str, name: str) -> List[Signal]:
        """
        核心选股逻辑
        :param df: 日线数据 DataFrame（已含技术指标）
        :param code: 股票代码
        :param name: 股票名称
        :return: 信号列表（空表示无信号）
        """
        ...

    def requires_bars(self) -> int:
        """策略需要的最少K线数量"""
        return 60

    def compute_exit_levels(
        self,
        df: pd.DataFrame,
        entry_price: float,
        fundamental_data: Optional[dict] = None,
    ) -> "ExitLevels":
        """
        计算持仓出场价位框架。默认：ATR 动态止损 + R=2 止盈。
        各策略可重写以提供更精确的出场逻辑。

        :param df: 同 generate_signals 的 df（date 为普通列，已 reset_index）
        :param entry_price: 进场价（不复权口径）
        :param fundamental_data: 配置仓策略可用，由调用方从 store 传入（避免重复IO）
        """
        from strategies.position_manager import (
            calc_atr, calc_swing_low, calc_swing_high, build_scale_levels,
        )
        from config.settings import RISK_CONTROL

        atr = calc_atr(df)
        mult = self.params.get(
            "atr_stop_multiplier", RISK_CONTROL.get("atr_stop_multiplier", 2.5)
        )
        stop_loss   = round(entry_price - atr * mult, 2)
        take_profit = round(entry_price + atr * mult * 2, 2)
        support     = calc_swing_low(df, n=20)
        resistance  = calc_swing_high(df, n=20)

        scale_cfg = RISK_CONTROL.get("scale_out", {})
        levels_raw = build_scale_levels(
            entry_price, stop_loss, take_profit, support, resistance, atr, scale_cfg
        )
        levels = [
            ScaleLevel(
                price=l["price"], level_type=l["level_type"], action=l["action"],
                size_pct=l["size_pct"], reason=l["reason"], cancels_group=l["cancels_group"],
            )
            for l in levels_raw
        ]
        return ExitLevels(
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr,
            support=support,
            resistance=resistance,
            exit_logic="atr",
            scale_levels=levels,
        )
