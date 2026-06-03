"""
历史回测引擎
基于向量化计算，支持多策略对比，输出完整绩效报告
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

import data.store as store
from data.fetcher import DataFetcher
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    code: str
    name: str
    entry_date: str
    entry_price: float
    exit_date: str | None = None
    exit_price: float | None = None
    shares: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason_in: str = ""
    reason_out: str = ""


@dataclass
class BacktestResult:
    strategy_name: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float          # 总收益率
    annual_return: float         # 年化收益率
    max_drawdown: float          # 最大回撤
    sharpe_ratio: float          # 夏普比率
    win_rate: float              # 胜率
    profit_factor: float         # 盈亏比
    total_trades: int
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    coverage_rate: float = 1.0   # B3：数据覆盖率（0~1）
    reliability: str = "normal"  # B3：normal / limited

    def summary(self) -> str:
        coverage_note = ""
        if self.reliability == "limited":
            coverage_note = (
                f"\n[!] 数据覆盖率 {self.coverage_rate*100:.1f}% (< 80%)，回测结果参考价值有限"
            )
        return (
            f"\n{'='*50}\n"
            f"策略: {self.strategy_name}\n"
            f"回测区间: {self.start_date} ~ {self.end_date}\n"
            f"初始资金: ¥{self.initial_capital:,.0f}\n"
            f"最终资金: ¥{self.final_capital:,.0f}\n"
            f"总收益率: {self.total_return*100:.2f}%\n"
            f"年化收益: {self.annual_return*100:.2f}%\n"
            f"最大回撤: {self.max_drawdown*100:.2f}%\n"
            f"夏普比率: {self.sharpe_ratio:.3f}\n"
            f"胜率:     {self.win_rate*100:.1f}%\n"
            f"盈亏比:   {self.profit_factor:.2f}\n"
            f"总交易数: {self.total_trades}\n"
            f"数据覆盖: {self.coverage_rate*100:.1f}%"
            f"{coverage_note}\n"
            f"{'='*50}"
        )


class Backtester:
    """
    事件驱动回测引擎
    - 每日遍历全市场，触发策略信号
    - 支持仓位管理、手续费、滑点
    - 输出资金曲线和详细交易记录
    """

    def __init__(self, fetcher: DataFetcher, config: dict):
        self.fetcher = fetcher
        self.cfg = config
        self.initial_capital = config.get("initial_capital", 1_000_000)
        self.commission = config.get("commission_rate", 0.0003)
        self.slippage = config.get("slippage", 0.001)
        self.position_size = config.get("position_size", 0.1)

    def run(self, strategy: BaseStrategy, stock_codes: list[str],
            start: str, end: str) -> BacktestResult:
        """
        运行单策略回测
        :param strategy: 策略实例
        :param stock_codes: 股票代码列表（空列表表示全市场）
        :param start: 开始日期
        :param end: 结束日期
        """
        # 若未指定股票池，使用本地 stock 库中所有股票
        if not stock_codes:
            stock_codes = store.list_codes("stock", market="cn")
            logger.info(f"未指定股票池，使用本地全市场 {len(stock_codes)} 只")

        logger.info(f"开始回测: {strategy.name}  {start}~{end}  {len(stock_codes)}只股票")
        print(f"加载数据中（共 {len(stock_codes)} 只）...", flush=True)

        # 只读本地 Parquet，根据上市时间动态判断最少 K 线要求
        ipo_dates = store.load_ipo_dates()
        data: dict[str, pd.DataFrame] = {}
        miss = 0
        for code in stock_codes:
            df = store.load(code, asset_type="stock", market="cn", start=start, end=end)
            if df is None or df.empty:
                miss += 1
                continue

            ipo = ipo_dates.get(f"cn:{code}", {}).get("ipo_date")
            if ipo:
                days_listed = (datetime.strptime(end, "%Y-%m-%d") -
                               datetime.strptime(ipo, "%Y-%m-%d")).days
                min_bars = 20 if days_listed < 365 else strategy.requires_bars()
            else:
                min_bars = strategy.requires_bars()

            if len(df) >= min_bars:
                data[code] = df.sort_index()
            else:
                miss += 1

        if not data:
            raise ValueError("没有可用的历史数据，请先运行 python3 main.py init")

        print(f"有效数据: {len(data)} 只  数据不足跳过: {miss} 只", flush=True)

        # B3：计算数据覆盖率
        coverage_rate = len(data) / max(len(stock_codes), 1)

        # 获取所有交易日
        all_dates = sorted(set(
            d for df in data.values() for d in df.index
        ))

        capital = float(self.initial_capital)
        positions: dict[str, dict] = {}   # code -> {shares, entry_price, entry_date}
        trades: list[Trade] = []
        equity = []

        for date in all_dates:
            date_str = str(date)[:10]

            # 检查持仓是否需要卖出
            for code in list(positions.keys()):
                if code not in data or date not in data[code].index:
                    continue
                pos = positions[code]
                df_slice = data[code].loc[:date]
                sigs = strategy.generate_signals(
                    df_slice.reset_index(), code, code
                )
                sell_sigs = [s for s in sigs if s.signal == "SELL"]
                if sell_sigs:
                    price = float(data[code].loc[date, "close"]) * (1 - self.slippage)
                    shares = pos["shares"]
                    proceeds = shares * price * (1 - self.commission)
                    capital += proceeds
                    trade = Trade(
                        code=code, name=code,
                        entry_date=pos["entry_date"],
                        entry_price=pos["entry_price"],
                        exit_date=date_str,
                        exit_price=price,
                        shares=shares,
                        pnl=proceeds - pos["cost"],
                        pnl_pct=(price / pos["entry_price"] - 1) * 100,
                        reason_out=sell_sigs[0].reason,
                    )
                    trades.append(trade)
                    del positions[code]

            # 扫描买入信号（最多同时持仓 10 只）
            if len(positions) < 10:
                for code, df in data.items():
                    if code in positions or date not in df.index:
                        continue
                    df_slice = df.loc[:date]
                    if len(df_slice) < strategy.requires_bars():
                        continue
                    sigs = strategy.generate_signals(
                        df_slice.reset_index(), code, code
                    )
                    buy_sigs = [s for s in sigs if s.signal == "BUY"]
                    if buy_sigs and len(positions) < 10:
                        price = float(df.loc[date, "close"]) * (1 + self.slippage)
                        alloc = capital * self.position_size
                        shares = int(alloc / price / 100) * 100
                        if shares <= 0:
                            continue
                        cost = shares * price * (1 + self.commission)
                        if cost > capital:
                            continue
                        capital -= cost
                        positions[code] = {
                            "shares": shares,
                            "entry_price": price,
                            "entry_date": date_str,
                            "cost": cost,
                        }

            # 计算当日总资产
            market_value = sum(
                pos["shares"] * float(data[c].loc[date, "close"])
                for c, pos in positions.items()
                if c in data and date in data[c].index
            )
            equity.append({"date": date, "equity": capital + market_value})

        equity_series = pd.DataFrame(equity).set_index("date")["equity"]
        result = self._calc_metrics(strategy.name, start, end, equity_series, trades,
                                    coverage_rate)
        logger.info(result.summary())
        return result

    def _calc_metrics(self, name: str, start: str, end: str,
                      equity: pd.Series, trades: list[Trade],
                      coverage_rate: float = 1.0) -> BacktestResult:
        initial = float(self.initial_capital)
        final = float(equity.iloc[-1]) if len(equity) > 0 else initial
        total_return = (final - initial) / initial

        # 年化收益
        days = max((equity.index[-1] - equity.index[0]).days, 1) if len(equity) > 1 else 1
        annual_return = (1 + total_return) ** (365 / days) - 1

        # 最大回撤
        rolling_max = equity.cummax()
        drawdown = (equity - rolling_max) / rolling_max
        max_drawdown = float(drawdown.min())

        # 夏普比率（日收益率）
        daily_ret = equity.pct_change().dropna()
        sharpe = (daily_ret.mean() / (daily_ret.std() + 1e-9)) * np.sqrt(252)

        # 胜率 & 盈亏比
        closed = [t for t in trades if t.exit_price is not None]
        win_trades = [t for t in closed if t.pnl > 0]
        lose_trades = [t for t in closed if t.pnl <= 0]
        win_rate = len(win_trades) / max(len(closed), 1)
        avg_win = np.mean([t.pnl for t in win_trades]) if win_trades else 0
        avg_loss = abs(np.mean([t.pnl for t in lose_trades])) if lose_trades else 1
        profit_factor = avg_win / max(avg_loss, 1e-9)

        reliability = "limited" if coverage_rate < 0.80 else "normal"

        return BacktestResult(
            strategy_name=name,
            start_date=start, end_date=end,
            initial_capital=initial, final_capital=final,
            total_return=total_return, annual_return=annual_return,
            max_drawdown=max_drawdown, sharpe_ratio=float(sharpe),
            win_rate=win_rate, profit_factor=profit_factor,
            total_trades=len(closed),
            trades=trades, equity_curve=equity,
            coverage_rate=coverage_rate, reliability=reliability,
        )
