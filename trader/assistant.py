"""
半自动交易辅助模块
生成交易指令单，支持手动确认后执行（不直接下单，降低风险）
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

from strategies.base import Signal

logger = logging.getLogger(__name__)

ORDER_DIR = Path(__file__).parent.parent / "data" / "orders"
ORDER_DIR.mkdir(parents=True, exist_ok=True)


class TradeOrder:
    """交易指令单"""

    def __init__(self, signal: Signal, quantity: int, capital: float):
        self.signal = signal
        self.quantity = quantity
        self.capital = capital
        self.created_at = datetime.now().isoformat()
        self.status = "PENDING"   # PENDING / CONFIRMED / CANCELLED / EXECUTED
        self.order_id = f"{signal.code}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "code": self.signal.code,
            "name": self.signal.name,
            "direction": self.signal.signal,
            "price": self.signal.price,
            "quantity": self.quantity,
            "amount": self.quantity * self.signal.price,
            "reason": self.signal.reason,
            "score": self.signal.score,
            "status": self.status,
            "created_at": self.created_at,
        }

    def __str__(self):
        d = self.to_dict()
        return (f"[{d['status']}] {d['direction']} {d['name']}({d['code']}) "
                f"x{d['quantity']}股 @ ¥{d['price']:.2f} "
                f"= ¥{d['amount']:,.0f}")


class TradeAssistant:
    """
    半自动交易辅助
    - 根据信号生成指令单
    - 支持仓位计算
    - 持久化指令单，等待人工确认
    - 提供指令单管理（查看/确认/取消）
    """

    def __init__(self, config: dict):
        self.cfg = config
        self.initial_capital = config.get("initial_capital", 1_000_000)
        self.position_size = config.get("position_size", 0.1)
        self.commission = config.get("commission_rate", 0.0003)

    def generate_orders(self, signals: List[Signal],
                        current_capital: float = None) -> List[TradeOrder]:
        """根据信号生成待确认指令单"""
        capital = current_capital or self.initial_capital
        orders = []
        for sig in signals:
            if sig.signal not in ("BUY", "SELL"):
                continue
            qty = self._calc_quantity(sig.price, capital)
            if qty <= 0:
                continue
            order = TradeOrder(sig, qty, capital)
            orders.append(order)
            self._save_order(order)
            logger.info(f"生成指令单: {order}")
        return orders

    def list_pending_orders(self) -> List[dict]:
        """列出所有待确认指令单"""
        orders = []
        for f in sorted(ORDER_DIR.glob("*.json")):
            try:
                with open(f) as fp:
                    d = json.load(fp)
                if d.get("status") == "PENDING":
                    orders.append(d)
            except Exception:
                pass
        return orders

    def confirm_order(self, order_id: str) -> bool:
        """确认指令单（标记为 CONFIRMED，等待人工在券商软件执行）"""
        path = ORDER_DIR / f"{order_id}.json"
        if not path.exists():
            logger.error(f"指令单不存在: {order_id}")
            return False
        with open(path) as f:
            d = json.load(f)
        d["status"] = "CONFIRMED"
        d["confirmed_at"] = datetime.now().isoformat()
        with open(path, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        logger.info(f"指令单已确认: {order_id}")
        return True

    def cancel_order(self, order_id: str) -> bool:
        """取消指令单"""
        path = ORDER_DIR / f"{order_id}.json"
        if not path.exists():
            return False
        with open(path) as f:
            d = json.load(f)
        d["status"] = "CANCELLED"
        with open(path, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        logger.info(f"指令单已取消: {order_id}")
        return True

    def export_orders_csv(self, output_path: str = None) -> str:
        """导出指令单为 CSV，方便导入券商软件"""
        orders = []
        for f in sorted(ORDER_DIR.glob("*.json")):
            try:
                with open(f) as fp:
                    orders.append(json.load(fp))
            except Exception:
                pass
        if not orders:
            return ""
        df = pd.DataFrame(orders)
        path = output_path or str(ORDER_DIR / f"orders_{datetime.now().strftime('%Y%m%d')}.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info(f"指令单已导出: {path}")
        return path

    def _calc_quantity(self, price: float, capital: float) -> int:
        """计算买入数量（A股最小单位100股）"""
        if price <= 0:
            return 0
        alloc = capital * self.position_size
        shares = int(alloc / price / 100) * 100
        cost = shares * price * (1 + self.commission)
        if cost > capital:
            shares -= 100
        return max(shares, 0)

    def _save_order(self, order: TradeOrder):
        path = ORDER_DIR / f"{order.order_id}.json"
        with open(path, "w") as f:
            json.dump(order.to_dict(), f, ensure_ascii=False, indent=2)
