"""
策略基类 - 所有策略必须继承此类
实现 generate_signals() 方法即可接入选股引擎
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any

import pandas as pd


# AC5：市场环境枚举（@deprecated，用 applicable_regimes 替代）
MARKET_CONDITIONS = ("bull", "bear", "range", "volatile")


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
