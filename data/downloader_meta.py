"""
meta 域下载器

负责下载：
  - 证监会行业分类（BaoStock，稳定免费）
  - 申万行业分类（AKShare，含成分股映射）
  - 申万行业指数日线（AKShare index_hist_sw）
  - 交易日历（BaoStock query_trade_dates）

用法:
  dl = MetaDownloader()
  dl.init_db()     # 全量初始化
  dl.update_db()   # 增量更新（每周）
"""
import logging
import socket
import time
from datetime import datetime
from typing import Optional

import pandas as pd

import data.store_meta as store

logger = logging.getLogger(__name__)

DEFAULT_START = "2024-01-02"


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _retry(func, max_retries: int = 3, wait_base: float = 2.0):
    """指数退避重试装饰器（函数版）"""
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


class MetaDownloader:

    def init_db(self, start: str = DEFAULT_START) -> None:
        """全量初始化 meta 域"""
        _log("=" * 55)
        _log("Meta 域初始化（行业分类 + 交易日历）")
        _log("=" * 55)

        _log("[1/4] 下载证监会行业分类（BaoStock）...")
        self._download_csrc_industry()

        _log("[2/4] 下载申万行业分类（AKShare）...")
        self._download_sw_industry()

        _log("[3/4] 下载申万行业指数日线（AKShare）...")
        self._download_sw_index(start=start)

        _log("[4/4] 下载交易日历（BaoStock）...")
        self._download_calendar(start=start)

        _log("Meta 域初始化完成！")
        self._print_stats()

    def update_db(self) -> None:
        """增量更新 meta 域（每周运行）"""
        today = datetime.now().strftime("%Y-%m-%d")
        _log(f"Meta 域增量更新  目标日期: {today}")

        _log("[1/4] 更新证监会行业分类...")
        self._download_csrc_industry()

        _log("[2/4] 更新申万行业分类...")
        self._download_sw_industry()

        _log("[3/4] 更新申万行业指数日线...")
        # 从最新日期续传
        self._download_sw_index(incremental=True)

        _log("[4/4] 更新交易日历...")
        self._download_calendar(incremental=True)

        _log("Meta 域更新完成！")

    # ------------------------------------------------------------------
    # 证监会行业分类
    # ------------------------------------------------------------------

    def _download_csrc_industry(self) -> None:
        import baostock as bs

        socket.setdefaulttimeout(60)
        bs.login()
        socket.setdefaulttimeout(None)
        try:
            rs = bs.query_stock_industry()
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                _log("  证监会行业：无数据返回")
                return
            df = pd.DataFrame(rows, columns=rs.fields)
            df = df.rename(columns={
                "updateDate": "update_date",
                "code_name": "name",
                "industryClassification": "classification",
            })
            # 去掉空行业
            df = df[df["industry"].notna() & (df["industry"] != "")]
            store.save("industry_csrc", df)
            _log(f"  证监会行业：{len(df)} 只，{df['industry'].nunique()} 个行业")
        finally:
            bs.logout()
            time.sleep(1)

    # ------------------------------------------------------------------
    # 申万行业分类
    # ------------------------------------------------------------------

    def _download_sw_industry(self) -> None:
        import akshare as ak

        # 获取申万一级行业列表
        _log("  获取申万一级行业列表...")
        def _get_first():
            return ak.sw_index_first_info()

        first_df = _retry(_get_first)
        if first_df is None or first_df.empty:
            _log("  申万行业列表为空，跳过")
            return

        _log(f"  申万一级行业：{len(first_df)} 个")

        # 遍历每个行业，获取成分股
        # sw_index_first_info 返回 "801010.SI" 格式，index_component_sw 需要 "801010"
        all_rows = []
        for _, row in first_df.iterrows():
            industry_code_full = str(row["行业代码"])          # "801010.SI"
            industry_code = industry_code_full.split(".")[0]   # "801010"
            industry_name = str(row["行业名称"])
            try:
                def _get_cons(code=industry_code):
                    return ak.index_component_sw(symbol=code)

                cons_df = _retry(_get_cons)
                if cons_df is not None and not cons_df.empty:
                    for _, stock_row in cons_df.iterrows():
                        all_rows.append({
                            "stock_code": str(stock_row.get("证券代码", "")),
                            "stock_name": str(stock_row.get("证券名称", "")),
                            "industry_code": industry_code,
                            "industry_name": industry_name,
                            "include_date": str(stock_row.get("计入日期", "")),
                            "weight": stock_row.get("最新权重", ""),
                        })
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"  申万行业 {industry_code} 成分股获取失败: {e}")
                time.sleep(1)

        if not all_rows:
            _log("  申万成分股：无数据")
            return

        sw_df = pd.DataFrame(all_rows)
        store.save("industry_sw", sw_df)
        _log(f"  申万行业映射：{len(sw_df)} 条，{sw_df['stock_code'].nunique()} 只股票")

    # ------------------------------------------------------------------
    # 申万行业指数日线
    # ------------------------------------------------------------------

    def _download_sw_index(self, start: str = DEFAULT_START,
                           incremental: bool = False) -> None:
        import akshare as ak

        # 获取申万一级行业列表
        try:
            first_df = _retry(lambda: ak.sw_index_first_info())
        except Exception as e:
            _log(f"  获取申万行业列表失败: {e}")
            return

        industry_codes_full = first_df["行业代码"].astype(str).tolist()
        # sw_index_first_info 返回 "801010.SI"，index_hist_sw 需要 "801010"
        industry_codes = [c.split(".")[0] for c in industry_codes_full]
        ok = skip = fail = 0

        for code in industry_codes:
            if incremental:
                ld = store.last_date("sw_index", code=code)
                if ld:
                    from datetime import timedelta
                    next_day = (datetime.strptime(ld, "%Y-%m-%d") +
                                timedelta(days=1)).strftime("%Y-%m-%d")
                    if next_day > datetime.now().strftime("%Y-%m-%d"):
                        skip += 1
                        continue
                    fetch_start = next_day  # 保持 "YYYY-MM-DD" 格式
                else:
                    fetch_start = start
            else:
                fetch_start = start  # "YYYY-MM-DD" 格式

            try:
                def _get_hist(c=code):
                    return ak.index_hist_sw(symbol=c, period="day")

                df = _retry(_get_hist)
                if df is None or df.empty:
                    skip += 1
                    time.sleep(0.3)
                    continue

                # 统一列名（index_hist_sw 返回中文列名）
                df = df.copy()
                col_map = {
                    "代码": "symbol",
                    "日期": "date",
                    "收盘": "close",
                    "开盘": "open",
                    "最高": "high",
                    "最低": "low",
                    "成交量": "volume",
                    "成交额": "amount",
                }
                df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

                # 过滤日期范围（fetch_start 是 "YYYY-MM-DD" 格式）
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                    df = df[df["date"] >= pd.Timestamp(fetch_start)]

                if df.empty:
                    skip += 1
                    time.sleep(0.3)
                    continue

                store.save("sw_index", df, code=code)
                ok += 1
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"  申万指数 {code} 下载失败: {e}")
                fail += 1
                time.sleep(1)

        _log(f"  申万行业指数：新增={ok} 跳过={skip} 失败={fail}")

    # ------------------------------------------------------------------
    # 交易日历
    # ------------------------------------------------------------------

    def _download_calendar(self, start: str = DEFAULT_START,
                           incremental: bool = False) -> None:
        import baostock as bs

        if incremental:
            ld = store.last_date("calendar")
            if ld:
                from datetime import timedelta
                start = (datetime.strptime(ld, "%Y-%m-%d") +
                         timedelta(days=1)).strftime("%Y-%m-%d")

        today = datetime.now().strftime("%Y-%m-%d")
        if start > today:
            _log("  交易日历已是最新，跳过")
            return

        socket.setdefaulttimeout(60)
        bs.login()
        socket.setdefaulttimeout(None)
        try:
            rs = bs.query_trade_dates(start_date=start, end_date=today)
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                _log("  交易日历：无数据")
                return
            df = pd.DataFrame(rows, columns=rs.fields)
            df = df.rename(columns={"calendar_date": "date", "is_trading_day": "is_trading"})
            df["is_trading"] = df["is_trading"].astype(int)
            store.save("calendar", df)
            trading_days = df[df["is_trading"] == 1]
            _log(f"  交易日历：{len(df)} 天，其中交易日 {len(trading_days)} 天")
        finally:
            bs.logout()
            time.sleep(1)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def _print_stats(self) -> None:
        for dtype in ("industry_csrc", "industry_sw", "calendar"):
            df = store.load(dtype)
            if df is not None:
                _log(f"  {dtype}: {len(df)} 行")
        codes = store.list_sw_index_codes()
        _log(f"  sw_index: {len(codes)} 个行业指数")
