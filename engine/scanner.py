"""
选股引擎 - 全市场并发扫描，多策略并行执行
支持 A 股（stock）和 ETF 扫描
"""
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import List, Dict, Type, Callable, Optional, Tuple

import pandas as pd

from data.fetcher import DataFetcher
import data.store as store
import data.store_meta as store_meta
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
    - 支持 stock / etf 两种资产类型
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

    def scan(self, date: str = None,
             asset_types: List[str] = None,
             industry_filter: List[str] = None,
             industry_level: str = "L1",
             etf_whitelist: List[str] = None) -> List[Signal]:
        """
        执行全市场扫描
        :param date: 扫描日期，默认今日
        :param asset_types: 资产类型列表，默认 ["stock"]；可传 ["etf"] 或 ["stock","etf"]
        :param industry_filter: L2 行业池约束（申万行业代码列表）。
                                非 None 时，仅扫描属于这些行业的股票（约束前置）。
                                None 时为全市场扫描（向后兼容 AC13）。
        :param etf_whitelist: L2 行业ETF白名单（代码列表）。
                              非 None 时，ETF 扫描仅限白名单内品种（L2 行业择时）。
                              None 时为全 ETF 扫描（向后兼容）。
        :return: 所有策略触发的信号列表（按评分降序）
        """
        if not self.strategies:
            raise ValueError("未注册任何策略，请先调用 register_strategy()")

        asset_types = asset_types or ["stock"]

        end = date or datetime.now().strftime("%Y-%m-%d")
        max_bars = max(s.requires_bars() for s in self.strategies.values())
        start = (datetime.strptime(end, "%Y-%m-%d") -
                 timedelta(days=int(max_bars * 1.5))).strftime("%Y-%m-%d")

        t0 = time.time()
        logger.info(f"开始扫描，资产类型: {asset_types}，日期范围: {start} ~ {end}")
        if industry_filter:
            logger.info(f"L2 行业约束: {len(industry_filter)} 个行业 {industry_filter}")

        # DR3：数据新鲜度检查
        stale = self._check_data_freshness(end, asset_types)
        if stale:
            logger.warning(f"数据域过期: {stale}")
            if "market/stock" in stale:
                raise RuntimeError(
                    f"关键域 market/stock 数据过期，请先运行 `python main.py update`。"
                    f"过期域: {stale}"
                )
            print(f"[!] 非关键域过期（继续扫描）: {stale}", flush=True)

        # 收集所有待扫描标的：(code, name, asset_type)
        scan_targets: List[tuple] = []

        if "stock" in asset_types:
            print(f"\n[1/3] 获取股票列表...", flush=True)
            stock_list = self._get_filtered_stock_list(
                industry_filter=industry_filter, industry_level=industry_level)
            for _, row in stock_list.iterrows():
                scan_targets.append((row["code"], row["name"], "stock"))
            print(f"  股票: {len(stock_list)} 只", flush=True)

        if "etf" in asset_types:
            print(f"\n[1/3] 获取 ETF 列表..." if "stock" not in asset_types
                  else "", end="", flush=True)
            etf_list = self._get_filtered_etf_list(etf_whitelist=etf_whitelist)
            for _, row in etf_list.iterrows():
                scan_targets.append((row["code"], row["name"], "etf"))
            print(f"  ETF: {len(etf_list)} 只" + (" (L2白名单)" if etf_whitelist else ""), flush=True)

        total = len(scan_targets)
        print(f"[2/3] 待扫描标的: {total}，开始并发计算信号...", flush=True)

        all_signals: List[Signal] = []
        workers = self.cfg.get("workers", 20)

        done = 0
        errors = 0
        _print_progress(0, total, 0)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    self._scan_single, code, name, start, end, atype
                ): code
                for code, name, atype in scan_targets
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

        # 同一标的同方向信号合并（取最高评分，原因拼接）
        merged: dict = {}  # key: (code, signal)
        for s in all_signals:
            key = (s.code, s.signal)
            if key not in merged:
                merged[key] = s
            else:
                existing = merged[key]
                if s.score > existing.score:
                    existing.reason = f"{s.reason} / {existing.reason}"
                    existing.score = s.score
                else:
                    existing.reason = f"{existing.reason} / {s.reason}"

        deduped = list(merged.values())
        deduped.sort(key=lambda s: s.score, reverse=True)
        top_n = self.cfg.get("top_n", 20)
        result = deduped[:top_n]
        return result

    def _scan_single(self, code: str, name: str, start: str, end: str,
                     asset_type: str = "stock") -> List[Signal]:
        """扫描单只标的：优先读本地库，本地无数据才走网络（仅 stock）"""
        df = store.load(code, asset_type=asset_type, market="cn", start=start, end=end)

        if asset_type == "stock":
            if df is None or len(df) < 30:
                # 本地没有，回退到网络拉取（不写库）
                # 复权方式对齐本地存储（后复权 hfq），避免量价/价格口径不一致
                df = self.fetcher.get_daily(code, start, end, adjust="hfq")
                if df is not None and not df.empty and "date" in df.columns:
                    df = df.set_index("date")
        # ETF 只读本地库，不走网络回退（数据量大，网络拉取太慢）

        if df is None or len(df) < 30:
            return []

        # S4：停牌过滤（trade_status=0 的不生成信号）
        last = df.iloc[-1]
        if asset_type == "stock" and str(last.get("trade_status", "1")) != "1":
            return []

        # 价格/成交量过滤
        min_price  = self.cfg.get("min_price", 0)
        max_price  = self.cfg.get("max_price", float("inf"))
        min_volume = self.cfg.get("min_volume", 0)
        min_amount = self.cfg.get("min_amount", 0)
        if not (min_price <= float(last["close"]) <= max_price):
            return []
        if float(last.get("volume", 0)) < min_volume:
            return []
        # S4：成交额过滤（流动性不足，按真实成交额/元过滤，可配置）
        if min_amount and float(last.get("amount", 0)) < min_amount:
            return []

        df_reset = df.reset_index()

        signals = []
        for strategy in self.strategies.values():
            # ⑦ 资产类型过滤：策略声明了 applicable_asset_types 且不含当前类型则跳过
            allowed_types = getattr(strategy, "applicable_asset_types", [])
            if allowed_types and asset_type not in allowed_types:
                continue
            try:
                sigs = strategy.generate_signals(df_reset, code, name)
                signals.extend(sigs)
            except Exception as e:
                logger.debug(f"策略 {strategy.name} 处理 {code} 出错: {e}")
        return signals

    def _resolve_industry_members(self, industry_filter: List[str],
                                  industry_level: str = "L1") -> set:
        """
        把行业代码列表解析为股票代码集合（6位，无后缀）。

        优先用 industry_member（Tushare 三级映射，支持 L1/L2/L3）；
        L1 且新表缺失时回退 industry_sw（AKShare 一级，向后兼容）。
        """
        # 优先新表
        try:
            members = store_meta.get_member_stocks(industry_filter, level=industry_level)
            if members:
                return set(members)
        except Exception as e:
            logger.debug(f"industry_member 解析失败: {e}")

        # 回退：仅 L1 支持旧表
        if industry_level == "L1":
            ind_df = store_meta.load("industry_sw")
            if ind_df is not None and not ind_df.empty:
                return set(
                    ind_df[ind_df["industry_code"].astype(str).isin(
                        [str(c).split(".")[0] for c in industry_filter]
                    )]["stock_code"].astype(str)
                )
        return set()

    def _get_filtered_stock_list(self, industry_filter: List[str] = None,
                                 industry_level: str = "L1") -> pd.DataFrame:
        """从本地库获取股票列表并过滤，不走网络。

        :param industry_filter: L2 行业池约束（申万行业代码列表）。
                                非 None 时，仅保留属于这些行业的股票（约束前置）。
        :param industry_level: 行业代码层级 L1/L2/L3（2026-06 新增）。
                               L1 默认走原 industry_sw（向后兼容）；
                               L2/L3 走 industry_member（Tushare 三级映射）。
        """
        ipo_dates = store.load_ipo_dates()

        codes = store.list_codes("stock", market="cn")
        if not codes:
            print(" 本地无数据，请先运行 init", flush=True)
            return pd.DataFrame(columns=["code", "name"])

        rows = []
        for code in codes:
            info = ipo_dates.get(f"cn:{code}", {})
            name = info.get("name", code)
            rows.append({"code": code, "name": name})
        stock_list = pd.DataFrame(rows)
        print(f"  本地股票数: {len(stock_list)} 只", flush=True)

        # L2 行业池约束（约束前置：在并发扫描前就收窄标的池）
        if industry_filter:
            before = len(stock_list)
            allowed_codes = self._resolve_industry_members(
                industry_filter, industry_level)
            if allowed_codes:
                stock_list = stock_list[stock_list["code"].isin(allowed_codes)]
                print(f"  行业池过滤({industry_level}): -{before - len(stock_list)} 只 "
                      f"(保留 {len(industry_filter)} 个行业的 {len(stock_list)} 只)", flush=True)
            else:
                logger.warning(f"行业映射数据缺失（level={industry_level}），跳过行业过滤")

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

    def _get_filtered_etf_list(self, etf_whitelist: List[str] = None) -> pd.DataFrame:
        """
        从本地库获取 ETF 列表，过滤掉本地无数据的品种。
        :param etf_whitelist: L2 行业ETF白名单；非None时仅返回白名单内且本地有数据的品种。
        """
        ipo_dates = store.load_ipo_dates()
        codes = store.list_codes("etf", market="cn")
        if not codes:
            print("  本地无 ETF 数据，请先运行 init --types etf", flush=True)
            return pd.DataFrame(columns=["code", "name"])

        # 应用 L2 行业ETF白名单约束
        if etf_whitelist is not None:
            whiteset = set(etf_whitelist)
            codes = [c for c in codes if c in whiteset]

        rows = []
        for code in codes:
            info = ipo_dates.get(f"cn:{code}", {})
            name = info.get("name", code)
            rows.append({"code": code, "name": name})

        etf_list = pd.DataFrame(rows)
        logger.info(f"有效 ETF 数: {len(etf_list)}")
        return etf_list.reset_index(drop=True)

    def _check_data_freshness(self, scan_date: str,
                               asset_types: List[str]) -> List[str]:
        """DR3：检查数据域新鲜度，返回过期的域列表"""
        stale = []
        today = datetime.strptime(scan_date, "%Y-%m-%d")

        # 读取交易日历判断上一个交易日
        calendar = store_meta.load("calendar")
        if calendar is not None and not calendar.empty:
            trading_dates = calendar.index[
                calendar.index <= today
            ].sort_values()
            if len(trading_dates) > 0:
                last_trading = trading_dates[-1]
            else:
                last_trading = today
                while last_trading.weekday() >= 5:
                    last_trading -= timedelta(days=1)
        else:
            # 无日历则回退：跳过周末判断
            last_trading = today
            while last_trading.weekday() >= 5:
                last_trading -= timedelta(days=1)

        # D1 行情（关键域）
        if "stock" in asset_types:
            codes = store.list_codes("stock", market="cn")
            if codes:
                latest = None
                sample = min(5, len(codes))
                for code in codes[:sample]:
                    ld = store.last_date(code, "stock", "cn")
                    if ld:
                        ld_dt = datetime.strptime(ld, "%Y-%m-%d")
                        if latest is None or ld_dt > latest:
                            latest = ld_dt
                if latest:
                    if latest < last_trading:
                        stale.append("market/stock")

        if "etf" in asset_types:
            codes = store.list_codes("etf", market="cn")
            if codes:
                latest = None
                sample = min(5, len(codes))
                for code in codes[:sample]:
                    ld = store.last_date(code, "etf", "cn")
                    if ld:
                        ld_dt = datetime.strptime(ld, "%Y-%m-%d")
                        if latest is None or ld_dt > latest:
                            latest = ld_dt
                if latest:
                    if latest < last_trading:
                        stale.append("market/etf")

        # D2 基本面（非关键域，过期记录日志不中止）
        from data import store_fundamental as sf
        for dtype in sf.VALID_TYPES:
            codes = sf.list_codes(dtype)
            if not codes:
                continue
            latest = None
            sample = min(3, len(codes))
            for code in codes[:sample]:
                ld = sf.last_date(code, dtype)
                if ld:
                    ld_dt = datetime.strptime(ld, "%Y-%m-%d")
                    if latest is None or ld_dt > latest:
                        latest = ld_dt
            if latest:
                threshold = datetime(today.year, today.month, 1)
                if latest < threshold and today.month not in (1, 4, 8, 10):
                    stale.append(f"fundamental/{dtype}")

        return stale
