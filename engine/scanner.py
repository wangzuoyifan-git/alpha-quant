"""
选股引擎 - 全市场并发扫描，多策略并行执行
"""
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Dict, Type, Callable, Optional

import pandas as pd

from data.fetcher import DataFetcher
import data.store as store
from strategies.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


def _print_progress(done: int, total: int, signals: int,
                    prefix: str = "扫描进度", width: int = 30):
    """在同一行打印进度条，不产生多余换行"""
    pct = done / max(total, 1)
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    line = f"\r{prefix} [{bar}] {done}/{total}  触发信号: {signals}"
    sys.stdout.write(line)
    sys.stdout.flush()


class ScanEngine:
    """
    全市场扫描引擎
    - 支持多策略同时运行
    - 多线程并发，加速扫描
    - 内置基础过滤（ST/新股/流动性）
    - 实时终端进度条
    """

    def __init__(self, fetcher: DataFetcher, scan_config: dict):
        self.fetcher = fetcher
        self.cfg = scan_config
        self.strategies: Dict[str, BaseStrategy] = {}
        self._progress_cb: Optional[Callable] = None  # 外部进度回调

    def register_strategy(self, key: str, strategy: BaseStrategy):
        """注册策略"""
        self.strategies[key] = strategy
        logger.info(f"策略已注册: {strategy.name} (key={key})")

    def load_strategies(self, strategy_classes: Dict[str, Type[BaseStrategy]],
                        params: Dict[str, dict] = None):
        """批量加载策略"""
        params = params or {}
        for key, cls in strategy_classes.items():
            self.register_strategy(key, cls(params.get(key, {})))

    def scan(self, date: str = None) -> List[Signal]:
        """
        执行全市场扫描
        :param date: 扫描日期，默认今日
        :return: 所有策略触发的信号列表（按评分降序）
        """
        if not self.strategies:
            raise ValueError("未注册任何策略，请先调用 register_strategy()")

        end = date or datetime.now().strftime("%Y-%m-%d")
        # 获取足够长的历史数据
        max_bars = max(s.requires_bars() for s in self.strategies.values())
        start = (datetime.strptime(end, "%Y-%m-%d") -
                 timedelta(days=int(max_bars * 1.5))).strftime("%Y-%m-%d")

        t0 = time.time()
        logger.info(f"开始全市场扫描，日期范围: {start} ~ {end}")
        print(f"\n[1/3] 获取股票列表...", flush=True)
        stock_list = self._get_filtered_stock_list()
        total = len(stock_list)
        print(f"[2/3] 待扫描股票数: {total}，开始并发拉取数据与计算信号...", flush=True)

        all_signals: List[Signal] = []
        workers = self.cfg.get("workers", 20)

        done = 0
        errors = 0
        _print_progress(0, total, 0)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self._scan_single, row["code"], row["name"], start, end
                ): row["code"]
                for _, row in stock_list.iterrows()
            }
            for future in as_completed(futures):
                done += 1
                try:
                    signals = future.result(timeout=30)
                    all_signals.extend(signals)
                except Exception as e:
                    errors += 1
                    logger.debug(f"扫描 {futures[future]} 出错: {e}")
                _print_progress(done, total, len(all_signals))

        elapsed = time.time() - t0
        print()  # 进度条换行
        print(f"[3/3] 扫描完成  耗时: {elapsed:.0f}s  错误: {errors}  触发信号: {len(all_signals)}", flush=True)
        logger.info(f"扫描完成 {elapsed:.0f}s，触发信号 {len(all_signals)}")

        # 同一只股票同方向信号合并（取最高评分，原因拼接）
        merged: dict = {}  # key: (code, signal)
        for s in all_signals:
            key = (s.code, s.signal)
            if key not in merged:
                merged[key] = s
            else:
                existing = merged[key]
                if s.score > existing.score:
                    # 保留高分信号，附加原因
                    existing.reason = f"{s.reason} / {existing.reason}"
                    existing.score = s.score
                else:
                    existing.reason = f"{existing.reason} / {s.reason}"

        deduped = list(merged.values())
        deduped.sort(key=lambda s: s.score, reverse=True)
        top_n = self.cfg.get("top_n", 20)
        result = deduped[:top_n]
        return result

    def _scan_single(self, code: str, name: str, start: str, end: str) -> List[Signal]:
        """扫描单只股票：优先读本地库，本地无数据才走网络"""
        # 优先读本地 Parquet
        df = store.load(code, asset_type="stock", market="cn", start=start, end=end)
        if df is None or len(df) < 30:
            # 本地没有，回退到网络拉取（不写库，由 downloader 负责持久化）
            df = self.fetcher.get_daily(code, start, end)
            if df is not None and not df.empty and "date" in df.columns:
                df = df.set_index("date")
        if df is None or len(df) < 30:
            return []

        # 基于最新一日行情做价格/成交量过滤
        last = df.iloc[-1]
        min_price  = self.cfg.get("min_price", 0)
        max_price  = self.cfg.get("max_price", float("inf"))
        min_volume = self.cfg.get("min_volume", 0)
        if not (min_price <= float(last["close"]) <= max_price):
            return []
        if float(last.get("volume", 0)) < min_volume:
            return []

        # 还原为带 date 列的格式供策略使用
        df_reset = df.reset_index()

        signals = []
        for strategy in self.strategies.values():
            try:
                sigs = strategy.generate_signals(df_reset, code, name)
                signals.extend(sigs)
            except Exception as e:
                logger.debug(f"策略 {strategy.name} 处理 {code} 出错: {e}")
        return signals

    def _get_filtered_stock_list(self) -> pd.DataFrame:
        """从本地库获取股票列表并过滤，不走网络"""
        ipo_dates = store.load_ipo_dates()

        # 直接读本地已有的股票代码
        codes = store.list_codes("stock", market="cn")
        if not codes:
            print(" 本地无数据，请先运行 init", flush=True)
            return pd.DataFrame(columns=["code", "name"])

        # 从 ipo_dates 补充名称
        rows = []
        for code in codes:
            info = ipo_dates.get(f"cn:{code}", {})
            name = info.get("name", code)
            rows.append({"code": code, "name": name})
        stock_list = pd.DataFrame(rows)
        print(f"  本地股票数: {len(stock_list)} 只", flush=True)

        # 排除 ST
        if self.cfg.get("exclude_st", True):
            before = len(stock_list)
            stock_list = stock_list[~stock_list["name"].str.contains("ST|退", na=False)]
            print(f"  排除ST/退市: -{before - len(stock_list)} 只", flush=True)

        # 排除北交所（代码以 8/4 开头）
        before = len(stock_list)
        stock_list = stock_list[
            ~stock_list["code"].str.startswith(("8", "4"), na=False)
        ]
        print(f"  排除北交所: -{before - len(stock_list)} 只", flush=True)

        # 排除上市不足60天新股
        if self.cfg.get("exclude_new", True):
            before = len(stock_list)
            keep_mask = []
            for _, row in stock_list.iterrows():
                ipo = ipo_dates.get(f"cn:{row['code']}", {}).get("ipo_date")
                if not ipo:
                    keep_mask.append(True)
                    continue
                try:
                    days_listed = (datetime.now() -
                                   datetime.strptime(ipo, "%Y-%m-%d")).days
                    keep_mask.append(days_listed >= 60)
                except Exception:
                    keep_mask.append(True)
            stock_list = stock_list[keep_mask]
            print(f"  排除上市不足60天: -{before - len(stock_list)} 只", flush=True)

        logger.info(f"有效股票数: {len(stock_list)}")
        return stock_list.reset_index(drop=True)
