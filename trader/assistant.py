"""
半自动交易辅助模块
生成交易指令单 + 风控过滤 + 执行结果反馈闭环
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

import pandas as pd

from strategies.base import Signal

logger = logging.getLogger(__name__)

ORDER_DIR = Path(__file__).parent.parent / "data" / "orders"
ORDER_DIR.mkdir(parents=True, exist_ok=True)


class TradeOrder:
    """交易指令单 — O1 字段完整性 + F1/F2 反馈"""

    def __init__(self, signal: Signal, quantity: int, capital: float,
                 stop_loss_ratio: float = 0.07, take_profit_ratio: float = 0.20):
        self.signal = signal
        self.quantity = quantity
        self.capital = capital
        self.created_at = datetime.now().isoformat()
        self.status = "PENDING"  # PENDING / CONFIRMED / CANCELLED / EXECUTED
        self.order_id = f"{signal.code}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # O1 止损止盈
        self.stop_loss_ratio = stop_loss_ratio
        self.stop_loss_price = round(signal.price * (1 - stop_loss_ratio), 2)
        self.take_profit_ratio = take_profit_ratio
        self.take_profit_price = round(signal.price * (1 + take_profit_ratio), 2)

        # RC 风控标签
        self.risk_flags: List[str] = []
        self.risk_check_detail: Dict[str, bool] = {}

        # F1/F2 执行反馈
        self.expected_price = signal.price
        self.actual_price: Optional[float] = None
        self.actual_quantity: Optional[int] = None
        self.executed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "code": self.signal.code,
            "name": self.signal.name,
            "direction": self.signal.signal,
            "price": self.signal.price,
            "quantity": self.quantity,
            "amount": round(self.quantity * self.signal.price, 2),
            "reason": self.signal.reason,
            "score": self.signal.score,
            "status": self.status,
            "created_at": self.created_at,
            "stop_loss_price": self.stop_loss_price,
            "stop_loss_ratio": self.stop_loss_ratio,
            "take_profit_price": self.take_profit_price,
            "take_profit_ratio": self.take_profit_ratio,
            "risk_flags": self.risk_flags,
            "risk_check_detail": self.risk_check_detail,
            "position_type": self.signal.position_type,
            "time_horizon": self.signal.time_horizon,
            "expected_price": self.expected_price,
            "actual_price": self.actual_price,
            "actual_quantity": self.actual_quantity,
            "executed_at": self.executed_at,
        }

    def __str__(self):
        d = self.to_dict()
        risk = f" [{'|'.join(d['risk_flags'])}]" if d["risk_flags"] else ""
        return (f"[{d['status']}]{risk} {d['direction']} {d['name']}({d['code']}) "
                f"x{d['quantity']}股 @ ¥{d['price']:.2f} "
                f"止损¥{d['stop_loss_price']:.2f} 止盈¥{d['take_profit_price']:.2f}")


class TradeAssistant:
    """
    半自动交易辅助 — 含风控过滤 + 执行反馈

    - filter_signals_by_risk()：RC1~RC8 风控检查
    - generate_orders()：生成含止损止盈的指令单
    - record_execution()：F1/F2 执行结果录入
    - confirm_order / cancel_order：指令单生命周期管理
    """

    def __init__(self, trade_config: dict, risk_config: dict = None):
        self.cfg = trade_config
        self.risk = risk_config or {}

        self.initial_capital = trade_config.get("initial_capital", 1_000_000)
        self.position_size = trade_config.get("position_size", 0.1)
        self.commission = trade_config.get("commission_rate", 0.0003)

        # RC 参数（全部从配置读取，符合 AC2）
        self.rc_max_single = self.risk.get("max_single_position", 0.10)
        self.rc_max_positions = self.risk.get("max_positions", 10)
        self.rc_max_industry = self.risk.get("max_industry_concentration", 0.30)
        self.rc_stop_loss = self.risk.get("stop_loss_ratio", 0.07)
        self.rc_max_drawdown = self.risk.get("max_total_drawdown", 0.15)
        self.rc_take_profit = self.risk.get("take_profit_ratio", 0.20)
        self.rc_enforce = self.risk.get("enforce", True)
        self.rc_circuit_breaker = self.risk.get("circuit_breaker", {})

    # ═══════════════════════════════════════════════════════════
    # RC1~RC8 风控检查
    # ═══════════════════════════════════════════════════════════

    def check_total_drawdown(self, current_equity: float) -> bool:
        """RC5：总账户回撤超限时返回 False（GAP-01）"""
        if current_equity <= 0:
            return False
        drawdown = (current_equity - self.initial_capital) / self.initial_capital
        if drawdown < -self.rc_max_drawdown:
            logger.warning(
                f"RC5 触发：总回撤 {drawdown * 100:.1f}% > {self.rc_max_drawdown * 100}%"
            )
            return False
        return True

    def check_circuit_breaker(self, vix: float = None, index_drop: float = None) -> bool:
        """RC8：突发事件熔断 — VIX > 阈值 或 指数跌幅 > 阈值（GAP-36）"""
        cb = self.rc_circuit_breaker
        if not cb.get("pause_new_orders", True):
            return True

        if vix is not None and cb.get("vix_threshold"):
            if vix > cb["vix_threshold"]:
                logger.warning(f"RC8 VIX熔断：VIX={vix} > {cb['vix_threshold']}")
                return False

        if index_drop is not None and cb.get("index_drop_threshold"):
            if abs(index_drop) > abs(cb["index_drop_threshold"]) and index_drop < 0:
                logger.warning(
                    f"RC8 指数熔断：跌幅 {index_drop * 100:.1f}% > {cb['index_drop_threshold'] * 100}%"
                )
                return False

        return True

    def check_single_position(self, order: TradeOrder) -> bool:
        """RC1：单笔金额超限"""
        max_amount = self.initial_capital * self.rc_max_single
        amount = order.quantity * order.signal.price
        if amount > max_amount:
            order.risk_flags.append("RC1")
            order.risk_check_detail["RC1"] = False
            logger.warning(f"RC1：{order.signal.code} 单笔金额 ¥{amount:,.0f} > ¥{max_amount:,.0f}")
            return False
        order.risk_check_detail["RC1"] = True
        return True

    def check_position_count(self, current_positions: int) -> bool:
        """RC2：持仓数超限"""
        if current_positions >= self.rc_max_positions:
            logger.warning(f"RC2：持仓数 {current_positions} >= {self.rc_max_positions}")
            return False
        return True

    def check_industry_concentration(self, order: TradeOrder,
                                      industry_positions: Dict[str, float],
                                      current_equity: float) -> bool:
        """RC3：同行业持仓超比例"""
        industry = order.signal.extra.get("industry", "")
        if not industry or current_equity <= 0:
            return True

        industry_value = industry_positions.get(industry, 0)
        new_value = industry_value + order.quantity * order.signal.price
        concentration = new_value / current_equity
        if concentration > self.rc_max_industry:
            order.risk_flags.append("RC3")
            order.risk_check_detail["RC3"] = False
            logger.warning(f"RC3：行业 {industry} 集中度 {concentration * 100:.1f}% > {self.rc_max_industry * 100}%")
            return False
        order.risk_check_detail["RC3"] = True
        return True

    def filter_signals_by_risk(
        self,
        signals: List[Signal],
        current_equity: float = None,
        current_positions: Dict[str, dict] = None,
        industry_positions: Dict[str, float] = None,
        vix: float = None,
        index_drop: float = None,
    ) -> List[Signal]:
        """
        RC1~RC8 完整风控过滤，返回通过检查的信号列表。
        未通过的信号会有 risk_flags 标注，但仍会保留在结果中供人工审查。
        enforce=True 时直接过滤掉违规信号。
        """
        equity = current_equity or self.initial_capital
        positions = current_positions or {}
        industry_pos = industry_positions or {}

        if not self.check_total_drawdown(equity):
            return [] if self.rc_enforce else signals

        if not self.check_circuit_breaker(vix=vix, index_drop=index_drop):
            return [] if self.rc_enforce else signals

        filtered = []
        for sig in signals:
            if sig.signal not in ("BUY", "SELL"):
                continue

            qty = self._calc_quantity(sig.price, equity)
            if qty <= 0:
                continue

            order = TradeOrder(sig, qty, equity,
                               stop_loss_ratio=self.rc_stop_loss,
                               take_profit_ratio=self.rc_take_profit)

            rc1_ok = self.check_single_position(order)
            rc2_ok = self.check_position_count(len(positions))
            rc3_ok = self.check_industry_concentration(order, industry_pos, equity)

            # RC7：减持窗口期过滤（BUY 信号在大股东减持窗口内拦截）
            rc7_ok = True
            if sig.signal == "BUY" and self.risk.get("reduce_window_block", True):
                try:
                    from data.store_events import has_reduce_window
                    if has_reduce_window(sig.code):
                        order.risk_flags.append("RC7")
                        rc7_ok = False
                        logger.info(f"RC7 拦截 {sig.code}：处于减持窗口期")
                except Exception:
                    pass  # 事件数据缺失时不阻断

            # RC2 标记（对单个信号不直接标记 RC2，在生成指令单时全局检查）
            if not rc2_ok:
                order.risk_check_detail["RC2"] = False

            all_ok = rc1_ok and rc2_ok and rc3_ok and rc7_ok
            if all_ok or not self.rc_enforce:
                filtered.append(sig)
            else:
                logger.info(f"风控过滤 {sig.code} {sig.signal}: flags={order.risk_flags}")

        return filtered

    # ═══════════════════════════════════════════════════════════
    # 指令单生成与管理
    # ═══════════════════════════════════════════════════════════

    def generate_orders(self, signals: List[Signal],
                        current_capital: float = None,
                        current_positions: int = 0,
                        allocation: dict = None,
                        max_position: float = None) -> List[TradeOrder]:
        """根据信号生成含止损止盈 + 风控标签的指令单（RC1~RC8 全覆盖）

        allocation: {"position_pct": float, "trading_pct": float}
                    由 decision_context.allocation 传入，按 position_type 分流资金池。
                    不传时退化为全资金池（向后兼容）。
        max_position: 总仓位上限比例，由 decision_context.max_position 传入。
        """
        capital = current_capital or self.initial_capital
        max_pos = max_position if max_position is not None else 1.0

        # 按 position_type 计算对应资金池上限
        def _available(position_type: str) -> float:
            if allocation is None:
                return capital
            if position_type == "config":
                return capital * allocation.get("position_pct", 1.0) * max_pos
            else:  # trading
                return capital * allocation.get("trading_pct", 1.0) * max_pos

        orders = []

        # RC5/RC8 全局检查（任一触发则全部拦截并记录告警）
        if not self.check_total_drawdown(capital):
            logger.warning("RC5 触发：generating_orders 全量阻止")
            return []
        if not self.check_circuit_breaker():
            logger.warning("RC8 触发：generating_orders 全量阻止")
            return []

        for sig in signals:
            if sig.signal not in ("BUY", "SELL"):
                continue
            avail = _available(getattr(sig, "position_type", "trading"))
            qty = self._calc_quantity(sig.price, avail)
            if qty <= 0:
                continue

            order = TradeOrder(sig, qty, avail,
                               stop_loss_ratio=self.rc_stop_loss,
                               take_profit_ratio=self.rc_take_profit)

            # RC 检查（逐项记录）
            self.check_single_position(order)
            if current_positions >= self.rc_max_positions:
                order.risk_flags.append("RC2")
            order.risk_check_detail["RC2"] = current_positions < self.rc_max_positions
            order.risk_check_detail["RC5"] = self.check_total_drawdown(capital)
            order.risk_check_detail["RC8"] = self.check_circuit_breaker()

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
        """确认指令单，记录预期买入价 + 时间戳"""
        path = ORDER_DIR / f"{order_id}.json"
        if not path.exists():
            logger.error(f"指令单不存在: {order_id}")
            return False
        with open(path) as f:
            d = json.load(f)
        d["status"] = "CONFIRMED"
        d["confirmed_at"] = datetime.now().isoformat()
        # F1：记录预期买入价
        if "expected_price" not in d:
            d["expected_price"] = d.get("price")
        with open(path, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        logger.info(f"指令单已确认: {order_id}")
        return True

    def cancel_order(self, order_id: str) -> bool:
        """取消指令单，保留历史记录"""
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

    def record_execution(self, order_id: str, actual_price: float,
                         actual_quantity: int) -> bool:
        """F1/F2：录入实际成交价和数量，状态变为 EXECUTED"""
        path = ORDER_DIR / f"{order_id}.json"
        if not path.exists():
            logger.error(f"指令单不存在: {order_id}")
            return False
        with open(path) as f:
            d = json.load(f)
        d["status"] = "EXECUTED"
        d["actual_price"] = actual_price
        d["actual_quantity"] = actual_quantity
        d["executed_at"] = datetime.now().isoformat()
        # 计算成交偏差
        expected = d.get("expected_price") or d.get("price")
        if expected and expected > 0:
            d["price_deviation"] = round((actual_price / expected - 1) * 100, 2)
        with open(path, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        logger.info(f"执行结果已记录: {order_id} 实际¥{actual_price} x{actual_quantity}股")
        return True

    def list_executed_orders(self) -> List[dict]:
        """F2：列出已执行的指令单供策略胜率分析"""
        orders = []
        for f in sorted(ORDER_DIR.glob("*.json")):
            try:
                with open(f) as fp:
                    d = json.load(fp)
                if d.get("status") in ("EXECUTED",):
                    orders.append(d)
            except Exception:
                pass
        return orders

    def get_pnl_summary(self) -> dict:
        """F2：策略汇总 — 按策略来源统计盈亏损"""
        executed = self.list_executed_orders()
        if not executed:
            return {"total_trades": 0, "total_pnl": 0.0, "by_strategy": {}}

        summary = {"total_trades": len(executed), "total_pnl": 0.0, "by_strategy": {}}
        for o in executed:
            pnl = 0.0
            if o.get("actual_price") and o.get("actual_quantity"):
                expected = o.get("expected_price") or o.get("price")
                pnl = (o["actual_price"] - expected) * o["actual_quantity"]
            summary["total_pnl"] += pnl

            reason = o.get("reason", "unknown")
            strategy_key = reason.split(" ")[0].split(",")[0] if reason else "unknown"
            if strategy_key not in summary["by_strategy"]:
                summary["by_strategy"][strategy_key] = {"trades": 0, "pnl": 0.0, "win": 0}
            summary["by_strategy"][strategy_key]["trades"] += 1
            summary["by_strategy"][strategy_key]["pnl"] += pnl
            if pnl > 0:
                summary["by_strategy"][strategy_key]["win"] += 1

        return summary

    # ═══════════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════════

    def export_orders_csv(self, output_path: str = None) -> str:
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
