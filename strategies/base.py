"""
策略基类 - 所有策略必须继承此类
实现 generate_signals() 方法即可接入选股引擎
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any

import pandas as pd


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

    def __str__(self):
        return (f"[{self.signal}] {self.name}({self.code}) "
                f"¥{self.price:.2f} 评分:{self.score:.1f} | {self.reason}")


class BaseStrategy(ABC):
    """策略插件基类"""

    name: str = "未命名策略"
    description: str = ""
    version: str = "1.0.0"

    def __init__(self, params: dict = None):
        self.params = params or {}

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
