"""
factor 域下载器

负责下载：
  - northbound    北向资金历史（AKShare stock_hsgt_hist_em）
  - moneyflow_sector  行业资金流向（AKShare stock_sector_fund_flow_rank）
  - moneyflow     个股资金流向（AKShare stock_individual_fund_flow，仅近60天）

注意：
  - 东方财富接口有限流，每次请求后 sleep 0.5~1s
  - 个股资金流向只能获取近60天，建议每日增量更新
  - 北向资金和行业资金流向可全量拉取历史

用法:
  dl = FactorDownloader()
  dl.init_db()     # 全量初始化
  dl.update_db()   # 每日增量更新
"""
import logging
import time
import sys
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

import data.store_factor as store

logger = logging.getLogger(__name__)

DEFAULT_START = "2024-01-02"


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _retry(func, max_retries: int = 3, wait_base: float = 2.0):
    """指数退避重试"""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt < max_retries - 1:
                wait = wait_base ** attempt
                logger.debug(f"第{attempt+1}次失败，{wait:.0f}s后重试: {e}")
                time.sleep(wait)
            else:
                raise


class FactorDownloader:

    def init_db(self, start: str = DEFAULT_START) -> None:
        """全量初始化 factor 域"""
        _log("=" * 55)
        _log(f"Factor 域初始化  起始: {start}")
        _log("=" * 55)

        _log("[1/3] 下载北向资金历史...")
        self._download_northbound()

        _log("[2/3] 下载行业资金流向...")
        self._download_sector_flow()

        _log("[3/3] 下载个股资金流向（近60天）...")
        self._download_individual_flow()

        _log("Factor 域初始化完成！")

    def update_db(self) -> None:
        """每日增量更新"""
        today = datetime.now().strftime("%Y-%m-%d")
        _log(f"Factor 域增量更新  目标日期: {today}")

        _log("[1/3] 更新北向资金...")
        self._download_northbound(incremental=True)

        _log("[2/3] 更新行业资金流向...")
        self._download_sector_flow(incremental=True)

        _log("[3/3] 更新个股资金流向（近60天）...")
        self._download_individual_flow()

        _log("Factor 域更新完成！")

    # ------------------------------------------------------------------
    # 北向资金
    # ------------------------------------------------------------------

    def _download_northbound(self, incremental: bool = False) -> None:
        import akshare as ak

        # 使用 stock_hsgt_hist_em 获取北向资金历史
        # symbol 可选: 沪股通, 深股通, 北向资金, 南向资金
        symbols = ["沪股通", "深股通"]
        all_dfs = []

        for symbol in symbols:
            try:
                def _fetch(s=symbol):
                    return ak.stock_hsgt_hist_em(symbol=s)

                df = _retry(_fetch)
                if df is None or df.empty:
                    continue

                df = df.copy()
                # 统一列名
                rename_map = {}
                for col in df.columns:
                    if "日期" in col or col.lower() == "date":
                        rename_map[col] = "date"
                    elif "当日" in col and "净买" in col:
                        rename_map[col] = f"{symbol}_net_buy"
                    elif "资金" in col and "净流" in col:
                        rename_map[col] = f"{symbol}_net_flow"
                if rename_map:
                    df = df.rename(columns=rename_map)

                # 添加来源标识
                df["symbol"] = symbol
                all_dfs.append(df)
                time.sleep(0.8)
            except Exception as e:
                logger.warning(f"北向资金 {symbol} 下载失败: {e}")
                time.sleep(1)

        if not all_dfs:
            _log("  北向资金：无数据")
            return

        # 合并沪股通+深股通
        # 找公共 date 列合并
        merged = None
        for df in all_dfs:
            if "date" not in df.columns:
                continue
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            if merged is None:
                merged = df
            else:
                merged = merged.join(df, how="outer", rsuffix="_dup")

        if merged is None or merged.empty:
            _log("  北向资金：合并失败")
            return

        # 如果增量，只保留新数据
        if incremental:
            ld = store.last_date("northbound")
            if ld:
                next_day = (datetime.strptime(ld, "%Y-%m-%d") +
                            timedelta(days=1)).strftime("%Y-%m-%d")
                merged = merged[merged.index >= pd.Timestamp(next_day)]
                if merged.empty:
                    _log("  北向资金：已是最新")
                    return

        store.save("northbound", merged.reset_index())
        _log(f"  北向资金：{len(merged)} 条记录")

    # ------------------------------------------------------------------
    # 行业资金流向
    # ------------------------------------------------------------------

    def _download_sector_flow(self, incremental: bool = False) -> None:
        import akshare as ak

        # 行业资金流向只能获取当日数据，历史数据用 stock_sector_fund_flow_hist
        today = datetime.now().strftime("%Y-%m-%d")

        if incremental:
            ld = store.last_date("moneyflow_sector")
            if ld and ld >= today:
                _log("  行业资金流向：已是最新")
                return

        try:
            def _fetch():
                return ak.stock_sector_fund_flow_rank(
                    indicator="今日", sector_type="行业资金流"
                )

            df = _retry(_fetch)
            if df is None or df.empty:
                _log("  行业资金流向：无数据")
                return

            df = df.copy()
            df["date"] = today
            store.save("moneyflow_sector", df)
            _log(f"  行业资金流向：{len(df)} 个行业")
        except Exception as e:
            logger.warning(f"行业资金流向下载失败: {e}")

    # ------------------------------------------------------------------
    # 个股资金流向（近60天，每日更新）
    # ------------------------------------------------------------------

    def _download_individual_flow(self) -> None:
        """
        个股资金流向只能获取近60天数据。
        从 store.py 的 ipo_dates.json 获取股票列表，
        批量下载并追加存储。
        """
        import akshare as ak
        import data.store as market_store

        # 获取 A 股代码列表（从已有 market 数据）
        codes = market_store.list_codes("stock", "cn")
        if not codes:
            _log("  个股资金流向：无股票列表，跳过")
            return

        total = len(codes)
        ok = skip = fail = 0
        t0 = time.time()
        today = datetime.now().strftime("%Y-%m-%d")

        _log(f"  个股资金流向：共 {total} 只，开始下载...")

        for i, code in enumerate(codes):
            # 检查是否已有今日数据
            ld = store.last_date("moneyflow", code=code)
            if ld and ld >= today:
                skip += 1
                if (i + 1) % 500 == 0:
                    elapsed = time.time() - t0
                    _log(f"  进度: {i+1}/{total}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")
                continue

            # 确定市场
            if code.startswith("6") or code.startswith("5"):
                market = "sh"
            else:
                market = "sz"

            try:
                def _fetch(c=code, m=market):
                    return ak.stock_individual_fund_flow(stock=c, market=m)

                df = _retry(_fetch, max_retries=2, wait_base=1.5)
                if df is None or df.empty:
                    skip += 1
                    time.sleep(0.3)
                    continue

                df = df.copy()
                # 统一日期列
                for col in df.columns:
                    if "日期" in col or col.lower() == "date":
                        df = df.rename(columns={col: "date"})
                        break

                store.save("moneyflow", df, code=code)
                ok += 1
                time.sleep(0.5)  # 东财限流
            except Exception as e:
                logger.debug(f"个股资金流向 {code} 失败: {e}")
                fail += 1
                time.sleep(1)

            if (i + 1) % 200 == 0:
                elapsed = time.time() - t0
                _log(f"  进度: {i+1}/{total}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  个股资金流向完成: 新增={ok} 跳过={skip} 失败={fail}  {elapsed:.0f}s")
