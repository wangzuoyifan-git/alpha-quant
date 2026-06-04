"""
macro 域下载器

负责下载：
  - bond_yield  国债收益率（AKShare bond_zh_us_rate，日频）
  - m2          M2货币供应（AKShare macro_china_m2，月频）
  - cpi_ppi     CPI/PPI（AKShare macro_china_cpi_monthly，月频）
  - pmi         PMI（AKShare macro_china_pmi_yearly，月频）

用法:
  dl = MacroDownloader()
  dl.init_db()     # 全量初始化
  dl.update_db()   # 每月增量更新
"""
import logging
import time
from datetime import datetime
from typing import Optional

import pandas as pd

import data.store_macro as store

logger = logging.getLogger(__name__)

DEFAULT_START = "2024-01-01"


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _parse_month_col(df: pd.DataFrame) -> pd.DataFrame:
    """将月份/日期列统一转为 date 列（datetime）"""
    df = df.copy()
    # 优先找 "月份"/"时间" 列（需要格式转换），其次找 "日期" 列
    month_cols = [c for c in df.columns if "月份" in c or "时间" in c]
    date_cols  = [c for c in df.columns if c == "日期" or c == "date"]
    target_col = (month_cols + date_cols)[0] if (month_cols or date_cols) else None

    if target_col is None:
        return df

    if target_col != "date":
        df = df.rename(columns={target_col: "date"})

    col = df["date"]
    if not pd.api.types.is_datetime64_any_dtype(col):
        # 兼容 "2026年04月份" 和 "2026-04-01" 两种格式
        df["date"] = (col
                      .astype(str)
                      .str.replace("年", "-", regex=False)
                      .str.replace("月份", "-01", regex=False)
                      .str.replace("月", "-01", regex=False))
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
    return df


def _retry(func, max_retries: int = 3, wait_base: float = 3.0):
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


class MacroDownloader:

    def init_db(self, start: str = DEFAULT_START) -> None:
        """全量初始化宏观数据"""
        _log("=" * 55)
        _log(f"Macro 域初始化  起始: {start}")
        _log("=" * 55)

        _log("[1/6] 下载国债收益率...")
        self._download_bond_yield(start=start)

        _log("[2/6] 下载 M2 货币供应...")
        self._download_m2()

        _log("[3/6] 下载 CPI/PPI...")
        self._download_cpi_ppi()

        _log("[4/6] 下载 PMI...")
        self._download_pmi()

        _log("[5/6] 下载 CNY/USD 汇率...")
        self._download_cny_usd()

        _log("[6/6] 下载境外宏观指标...")
        self._download_overseas()

        _log("Macro 域初始化完成！")
        self._print_stats()

    def update_db(self) -> None:
        """每月增量更新"""
        _log("Macro 域增量更新...")

        _log("[1/4] 更新国债收益率...")
        self._download_bond_yield(incremental=True)

        _log("[2/4] 更新 M2...")
        self._download_m2()

        _log("[3/4] 更新 CPI/PPI...")
        self._download_cpi_ppi()

        _log("[4/4] 更新 PMI...")
        self._download_pmi()

        _log("Macro 域更新完成！")

    # ------------------------------------------------------------------
    # 国债收益率
    # ------------------------------------------------------------------

    def _download_bond_yield(self, start: str = DEFAULT_START,
                             incremental: bool = False) -> None:
        import akshare as ak

        if incremental:
            ld = store.last_date("bond_yield")
            if ld:
                from datetime import timedelta
                start = (datetime.strptime(ld, "%Y-%m-%d") +
                         timedelta(days=1)).strftime("%Y-%m-%d")

        today = datetime.now().strftime("%Y-%m-%d")
        if start > today:
            _log("  国债收益率：已是最新")
            return

        try:
            def _fetch(s=start):
                return ak.bond_zh_us_rate(start_date=s.replace("-", ""))

            df = _retry(_fetch)
            if df is None or df.empty:
                _log("  国债收益率：无数据")
                return

            df = df.copy()
            # bond_zh_us_rate 返回中文列名，直接精确映射
            rename_map = {
                "日期": "date",
                "中国国债收益率2年": "cn_2y",
                "中国国债收益率5年": "cn_5y",
                "中国国债收益率10年": "cn_10y",
                "中国国债收益率30年": "cn_30y",
                "中国国债收益率10年-2年": "cn_spread_10y_2y",
                "中国GDP年增率": "cn_gdp_yoy",
                "美国国债收益率2年": "us_2y",
                "美国国债收益率5年": "us_5y",
                "美国国债收益率10年": "us_10y",
                "美国国债收益率30年": "us_30y",
                "美国国债收益率10年-2年": "us_spread_10y_2y",
                "美国GDP年增率": "us_gdp_yoy",
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

            store.save("bond_yield", df)
            _log(f"  国债收益率：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"国债收益率下载失败: {e}")

    # ------------------------------------------------------------------
    # M2 货币供应
    # ------------------------------------------------------------------

    def _download_m2(self) -> None:
        import akshare as ak

        try:
            # macro_china_money_supply 返回完整货币供应数据
            df = _retry(lambda: ak.macro_china_money_supply())
            if df is None or df.empty:
                _log("  M2：无数据")
                return

            df = _parse_month_col(df)
            store.save("m2", df)
            _log(f"  M2：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"M2 下载失败: {e}")

        time.sleep(1)

    # ------------------------------------------------------------------
    # CPI / PPI
    # ------------------------------------------------------------------

    def _download_cpi_ppi(self) -> None:
        import akshare as ak

        # CPI
        cpi_df = None
        try:
            df = _retry(lambda: ak.macro_china_cpi_monthly())
            if df is not None and not df.empty:
                df = _parse_month_col(df)
                # 给非 date 列加 cpi_ 前缀
                df = df.rename(columns={c: f"cpi_{c}" for c in df.columns if c != "date"})
                cpi_df = df
                _log(f"  CPI：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"CPI 下载失败: {e}")

        time.sleep(1)

        # PPI
        ppi_df = None
        try:
            df = _retry(lambda: ak.macro_china_ppi_yearly())
            if df is not None and not df.empty:
                df = _parse_month_col(df)
                df = df.rename(columns={c: f"ppi_{c}" for c in df.columns if c != "date"})
                ppi_df = df
                _log(f"  PPI：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"PPI 下载失败: {e}")

        time.sleep(1)

        # 合并 CPI + PPI
        if cpi_df is not None and ppi_df is not None:
            try:
                merged = pd.merge(cpi_df, ppi_df, on="date", how="outer")
                store.save("cpi_ppi", merged)
                _log(f"  CPI/PPI 合并：{len(merged)} 条记录")
            except Exception as e:
                logger.warning(f"CPI/PPI 合并失败: {e}")
                store.save("cpi_ppi", cpi_df)
        elif cpi_df is not None:
            store.save("cpi_ppi", cpi_df)
        elif ppi_df is not None:
            store.save("cpi_ppi", ppi_df)
        else:
            _log("  CPI/PPI：无数据")

    # ------------------------------------------------------------------
    # PMI
    # ------------------------------------------------------------------

    def _download_pmi(self) -> None:
        import akshare as ak

        try:
            df = _retry(lambda: ak.macro_china_pmi())
            if df is None or df.empty:
                _log("  PMI：无数据")
                return

            df = _parse_month_col(df)
            store.save("pmi", df)
            _log(f"  PMI：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"PMI 下载失败: {e}")

        time.sleep(1)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def _print_stats(self) -> None:
        _log("Macro 域统计:")
        for dtype in store.VALID_TYPES:
            df = store.load(dtype)
            if df is not None:
                ld = store.last_date(dtype)
                _log(f"  {dtype:16s}: {len(df):5d} 行  最新: {ld}")
            else:
                _log(f"  {dtype:16s}: 无数据")

    # ------------------------------------------------------------------
    # D5a: CNY/USD 汇率
    # ------------------------------------------------------------------

    def _download_cny_usd(self) -> None:
        import akshare as ak
        try:
            df = _retry(lambda: ak.currency_boc_sina(symbol="美元"))
            if df is None or df.empty:
                _log("  CNY/USD：无数据")
                return
            df = df.copy()
            if "日期" in df.columns:
                df = df.rename(columns={"日期": "date"})
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            store.save("cny_usd", df)
            _log(f"  CNY/USD：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"CNY/USD 汇率下载失败: {e}")
            # 尝试备用接口
            try:
                df = _retry(lambda: ak.fx_spot_quote())
                if df is not None and not df.empty:
                    df = df.copy()
                    df = df.rename(columns={"日期": "date"} if "日期" in df.columns else {})
                    if "date" in df.columns:
                        df["date"] = pd.to_datetime(df["date"])
                        df = df.set_index("date")
                    store.save("cny_usd", df)
                    _log(f"  CNY/USD（备用接口）：{len(df)} 条记录")
            except Exception as e2:
                logger.warning(f"CNY/USD 备用接口也失败: {e2}")

    # ------------------------------------------------------------------
    # D5b: 境外宏观指标（VIX/DXY/原油/铜/标普500/恒生/美联储利率）
    # ------------------------------------------------------------------

    def _download_overseas(self) -> None:
        """下载 D5b 全部境外宏观指标"""
        self._download_vix()
        time.sleep(1)
        self._download_dxy()
        time.sleep(1)
        self._download_oil_wti()
        time.sleep(1)
        self._download_sp500()
        time.sleep(1)
        self._download_hsi()

    def _download_vix(self) -> None:
        import akshare as ak
        try:
            df = _retry(lambda: ak.index_vix())
            if df is None or df.empty:
                _log("  VIX：无数据")
                return
            df = df.copy()
            if "日期" in df.columns:
                df = df.rename(columns={"日期": "date"})
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            store.save("vix", df)
            _log(f"  VIX：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"VIX 下载失败: {e}")

    def _download_dxy(self) -> None:
        import akshare as ak
        try:
            df = _retry(lambda: ak.currency_boc_sina(symbol="美元指数"))
            if df is None or df.empty:
                # 试用别的接口
                df = _retry(lambda: ak.fx_spot_quote())
                if df is None or df.empty:
                    _log("  DXY：无数据")
                    return
            df = df.copy()
            if "日期" in df.columns:
                df = df.rename(columns={"日期": "date"})
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            store.save("dxy", df)
            _log(f"  DXY：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"DXY 下载失败: {e}")

    def _download_oil_wti(self) -> None:
        import akshare as ak
        try:
            df = _retry(lambda: ak.spot_golden_benchmark_sge())
            if df is None or df.empty:
                # 尝试原油专用接口
                try:
                    df = _retry(lambda: ak.futures_international_commodity(
                        symbol="WTI", start_date="20200101"))
                    if df is not None and not df.empty:
                        df = df.copy()
                        for col in df.columns:
                            if "日期" in col or "时间" in col:
                                df = df.rename(columns={col: "date"})
                                break
                        if "date" in df.columns:
                            df["date"] = pd.to_datetime(df["date"])
                            df = df.set_index("date")
                        store.save("oil_wti", df)
                        _log(f"  WTI原油：{len(df)} 条记录")
                        return
                except Exception:
                    pass
                _log("  WTI原油：无数据")
                return
            store.save("oil_wti", df)
            _log(f"  WTI原油：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"WTI原油下载失败: {e}")

    def _download_sp500(self) -> None:
        import akshare as ak
        try:
            df = _retry(lambda: ak.index_us_stock_sina(symbol=".INX"))
            if df is None or df.empty:
                _log("  标普500：无数据")
                return
            df = df.copy()
            if "日期" in df.columns:
                df = df.rename(columns={"日期": "date"})
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            store.save("sp500", df)
            _log(f"  标普500：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"标普500下载失败: {e}")

    def _download_hsi(self) -> None:
        import akshare as ak
        try:
            df = _retry(lambda: ak.stock_hk_index_daily_em(symbol="HSI"))
            if df is None or df.empty:
                _log("  恒生指数：无数据")
                return
            df = df.copy()
            if "日期" in df.columns:
                df = df.rename(columns={"日期": "date"})
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            store.save("hsi", df)
            _log(f"  恒生指数：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"恒生指数下载失败: {e}")
