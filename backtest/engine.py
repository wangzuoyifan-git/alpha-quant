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

# 基准：沪深300（stock类回测）和中证全债（ETF类回测暂用沪深300替代）
_BENCHMARK_CODE = "000300"
_BENCHMARK_ASSET = "index"
_RISK_FREE_RATE = 0.02  # 年化无风险利率（国债收益率约2%）

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
    sharpe_ratio: float          # 夏普比率（已扣无风险利率）
    win_rate: float              # 胜率
    profit_factor: float         # 盈亏比
    total_trades: int
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)
    coverage_rate: float = 1.0   # B3：数据覆盖率（0~1）
    reliability: str = "normal"  # B3：normal / limited
    # 基准对比字段
    benchmark_return: float = 0.0    # 基准（沪深300）同期总收益率
    alpha: float = 0.0               # 超额年化收益（策略年化 - 基准年化）
    beta: float = 0.0                # 策略相对基准的 beta

    def summary(self) -> str:
        coverage_note = ""
        if self.reliability == "limited":
            coverage_note = (
                f"\n[!] 数据覆盖率 {self.coverage_rate*100:.1f}% (< 80%)，回测结果参考价值有限"
            )
        bm_line = (
            f"基准收益: {self.benchmark_return*100:.2f}%  "
            f"超额收益(alpha): {self.alpha*100:.2f}%  "
            f"beta: {self.beta:.2f}\n"
        ) if self.benchmark_return != 0.0 else ""
        return (
            f"\n{'='*50}\n"
            f"策略: {self.strategy_name}\n"
            f"回测区间: {self.start_date} ~ {self.end_date}\n"
            f"初始资金: ¥{self.initial_capital:,.0f}\n"
            f"最终资金: ¥{self.final_capital:,.0f}\n"
            f"总收益率: {self.total_return*100:.2f}%\n"
            f"年化收益: {self.annual_return*100:.2f}%\n"
            f"{bm_line}"
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
        self.max_positions = config.get("max_positions", 10)

    def run(self, strategy: BaseStrategy, stock_codes: list[str],
            start: str, end: str, asset_type: str = "stock") -> BacktestResult:
        """
        运行单策略回测
        :param strategy: 策略实例
        :param stock_codes: 代码列表（空列表表示全市场）
        :param start: 开始日期
        :param end: 结束日期
        :param asset_type: "stock"（默认）或 "etf"
        """
        if not stock_codes:
            stock_codes = store.list_codes(asset_type, market="cn")
            logger.info(f"未指定标的池，使用本地全市场 {len(stock_codes)} 只 {asset_type}")

        logger.info(f"开始回测: {strategy.name}  {start}~{end}  {len(stock_codes)}只{asset_type}")
        print(f"加载数据中（共 {len(stock_codes)} 只）...", flush=True)

        # 只读本地 Parquet；stock 用 IPO 日期动态调整最少 K 线，ETF 不查 IPO
        ipo_dates = store.load_ipo_dates() if asset_type == "stock" else {}
        data: dict[str, pd.DataFrame] = {}
        miss = 0
        for code in stock_codes:
            df = store.load(code, asset_type=asset_type, market="cn", start=start, end=end)
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
        positions: dict[str, dict] = {}   # code -> {shares, entry_price, entry_date, cost, exit_levels}
        trades: list[Trade] = []
        equity = []

        from strategies.position_manager import check_exit_triggered, update_trail_stop

        for date in all_dates:
            date_str = str(date)[:10]

            # 检查持仓是否需要卖出
            for code in list(positions.keys()):
                if code not in data or date not in data[code].index:
                    continue
                pos = positions[code]
                price = float(data[code].loc[date, "close"])
                df_slice = data[code].loc[:date]
                el_dict = pos.get("exit_levels")
                sold = False

                # 优先用策略出场框架检查触发
                if el_dict and el_dict.get("scale_levels"):
                    trig = check_exit_triggered(price, el_dict, pos.get("triggered_groups", set()))
                    if trig:
                        size_pct = abs(trig.get("size_pct", 1.0))
                        shares_out = max(int(pos["shares"] * size_pct / 100) * 100, 100)
                        shares_out = min(shares_out, pos["shares"])
                        sell_price = price * (1 - self.slippage)
                        proceeds = shares_out * sell_price * (1 - self.commission)
                        capital += proceeds
                        entry_alloc = pos["cost"] * (shares_out / pos["shares"])
                        trade = Trade(
                            code=code, name=code,
                            entry_date=pos["entry_date"], entry_price=pos["entry_price"],
                            exit_date=date_str, exit_price=sell_price,
                            shares=shares_out,
                            pnl=proceeds - entry_alloc,
                            pnl_pct=(sell_price / pos["entry_price"] - 1) * 100,
                            reason_out=trig.get("reason", trig.get("action", "")),
                        )
                        trades.append(trade)
                        grp = trig.get("cancels_group", "")
                        if grp:
                            pos.setdefault("triggered_groups", set()).add(grp)
                        pos["shares"] -= shares_out
                        pos["cost"] -= entry_alloc
                        if pos["shares"] <= 0:
                            del positions[code]
                            sold = True
                        continue

                # 降级：SELL 信号出场（无 exit_levels 或未触发时）
                if not sold:
                    sigs = strategy.generate_signals(df_slice.reset_index(), code, code)
                    sell_sigs = [s for s in sigs if s.signal == "SELL"]
                    if sell_sigs:
                        sell_price = price * (1 - self.slippage)
                        shares = pos["shares"]
                        proceeds = shares * sell_price * (1 - self.commission)
                        capital += proceeds
                        trade = Trade(
                            code=code, name=code,
                            entry_date=pos["entry_date"], entry_price=pos["entry_price"],
                            exit_date=date_str, exit_price=sell_price,
                            shares=shares,
                            pnl=proceeds - pos["cost"],
                            pnl_pct=(sell_price / pos["entry_price"] - 1) * 100,
                            reason_out=sell_sigs[0].reason,
                        )
                        trades.append(trade)
                        del positions[code]
                        sold = True

                # 移动止损更新（trail_* 逻辑，每日收盘后更新）
                if not sold and el_dict and el_dict.get("exit_logic", "").startswith("trail_"):
                    positions[code]["exit_levels"] = update_trail_stop(el_dict, df_slice.reset_index())

            # 扫描买入信号（最多同时持仓 max_positions 只）
            if len(positions) < self.max_positions:
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
                    if buy_sigs and len(positions) < self.max_positions:
                        price = float(df.loc[date, "close"]) * (1 + self.slippage)
                        alloc = capital * self.position_size
                        shares = int(alloc / price / 100) * 100
                        if shares <= 0:
                            continue
                        cost = shares * price * (1 + self.commission)
                        if cost > capital:
                            continue
                        capital -= cost
                        # 建仓时计算出场框架
                        try:
                            el = strategy.compute_exit_levels(df_slice.reset_index(), entry_price=price)
                            el_dict = el.to_dict()
                        except Exception:
                            el_dict = None
                        positions[code] = {
                            "shares": shares,
                            "entry_price": price,
                            "entry_date": date_str,
                            "cost": cost,
                            "exit_levels": el_dict,
                            "triggered_groups": set(),
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

    def _load_benchmark(self, start: str, end: str) -> pd.Series:
        """加载沪深300同期日收益率序列，用于 alpha/beta 计算。失败时返回空 Series。"""
        try:
            df = store.load(_BENCHMARK_CODE, asset_type=_BENCHMARK_ASSET,
                            market="cn", start=start, end=end)
            if df is None or df.empty or "close" not in df.columns:
                return pd.Series(dtype=float)
            return df["close"].pct_change().dropna()
        except Exception:
            return pd.Series(dtype=float)

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

        # 夏普比率（已扣无风险利率）
        daily_ret = equity.pct_change().dropna()
        daily_rf = _RISK_FREE_RATE / 252
        excess_daily = daily_ret - daily_rf
        sharpe = (excess_daily.mean() / (excess_daily.std() + 1e-9)) * np.sqrt(252)

        # 胜率 & 盈亏比
        closed = [t for t in trades if t.exit_price is not None]
        win_trades = [t for t in closed if t.pnl > 0]
        lose_trades = [t for t in closed if t.pnl <= 0]
        win_rate = len(win_trades) / max(len(closed), 1)
        avg_win = np.mean([t.pnl for t in win_trades]) if win_trades else 0
        avg_loss = abs(np.mean([t.pnl for t in lose_trades])) if lose_trades else 1
        profit_factor = avg_win / max(avg_loss, 1e-9)

        # 基准对比（沪深300）
        benchmark_return = 0.0
        alpha = 0.0
        beta = 0.0
        bm_daily = self._load_benchmark(start, end)
        if not bm_daily.empty and len(daily_ret) > 5:
            bm_days = max((bm_daily.index[-1] - bm_daily.index[0]).days, 1)
            bm_total = (1 + bm_daily).prod() - 1
            benchmark_return = float(bm_total)
            bm_annual = (1 + bm_total) ** (365 / bm_days) - 1
            alpha = float(annual_return - bm_annual)
            # beta：策略日收益率 vs 基准日收益率的斜率
            aligned = daily_ret.align(bm_daily, join="inner")
            strat_a, bm_a = aligned
            if len(strat_a) > 5:
                cov = float(np.cov(strat_a, bm_a)[0, 1])
                var_bm = float(bm_a.var())
                beta = cov / var_bm if var_bm > 1e-10 else 0.0

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
            benchmark_return=benchmark_return, alpha=alpha, beta=beta,
        )

    def save_result(self, result: BacktestResult) -> str:
        """B3：回测结果持久化，供策略历史胜率展示"""
        import json
        from pathlib import Path
        out_dir = Path(__file__).parent.parent / "data" / "processed"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"backtest_{result.strategy_name}_{ts}.json"
        data = {
            "strategy": result.strategy_name,
            "start": result.start_date,
            "end": result.end_date,
            "initial_capital": result.initial_capital,
            "final_capital": result.final_capital,
            "total_return": round(result.total_return, 6),
            "annual_return": round(result.annual_return, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "sharpe_ratio": round(result.sharpe_ratio, 4),
            "win_rate": round(result.win_rate, 4),
            "profit_factor": round(result.profit_factor, 4),
            "total_trades": result.total_trades,
            "coverage_rate": round(result.coverage_rate, 4),
            "reliability": result.reliability,
            "benchmark_return": round(result.benchmark_return, 6),
            "alpha": round(result.alpha, 6),
            "beta": round(result.beta, 4),
            "generated_at": datetime.now().isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"回测结果已持久化: {path}")
        return str(path)
