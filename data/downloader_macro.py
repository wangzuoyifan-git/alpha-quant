"""
macro 域下载器

负责下载（11 项，init_db/update_db 均已覆盖）：
  国内:
  - bond_yield  国债收益率（AKShare bond_zh_us_rate，日频，含美债字段）
  - m2          M2货币供应（AKShare macro_china_money_supply，月频）
  - cpi_ppi     CPI/PPI（AKShare macro_china_cpi/ppi 东财，月频）
  - pmi         PMI（AKShare macro_china_pmi，月频）
  - shrongji    社融规模（Tushare sf_month 主 / AKShare 备，月频）
  - cny_usd     人民币汇率（AKShare currency_boc_safe 外管局中间价，日频）
  境外:
  - vix / dxy / oil_wti / sp500 / hsi（日频）
  - dxy 主源为东财 push2his 直连（100.UDI），AKShare 为备
  - oil_wti 主源新浪外盘 CL（WTI），备源 OIL（布伦特）

用法:
  dl = MacroDownloader()
  dl.init_db()     # 全量初始化
  dl.update_db()   # 增量更新（全量重取 + 去重覆盖）
"""
import logging
import time
from datetime import datetime
from typing import Optional

import pandas as pd

import data.store_macro as store

logger = logging.getLogger(__name__)

DEFAULT_START = "2024-01-01"

# bond_yield 中的美债字段（派生到独立文件 us_bond_yield.parquet）
_US_BOND_COLS = ("us_2y", "us_5y", "us_10y", "us_30y",
                 "us_spread_10y_2y", "us_gdp_yoy")


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
        col_str = col.astype(str).str.strip()
        # 处理 YYYYMM 格式（如 "201501" → "2015-01-01"）
        yyyymm_mask = col_str.str.match(r"^\d{6}$")
        if yyyymm_mask.any():
            col_str.loc[yyyymm_mask] = col_str.loc[yyyymm_mask].str[:4] + "-" + col_str.loc[yyyymm_mask].str[4:6] + "-01"
        # 处理文本月份格式（"2026年04月份" → "2026-04-01"）
        text_mask = ~col_str.str.match(r"^\d{4}-\d{2}-\d{2}$") & ~pd.api.types.is_datetime64_any_dtype(col)
        if text_mask.any():
            col_str.loc[text_mask] = (col_str.loc[text_mask]
                                      .str.replace("年", "-", regex=False)
                                      .str.replace("月份", "-01", regex=False)
                                      .str.replace("月", "-01", regex=False))
        df["date"] = pd.to_datetime(col_str, errors="coerce")
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

        _log("[1/7] 下载国债收益率...")
        self._download_bond_yield(start=start)

        _log("[2/7] 下载 M2 货币供应...")
        self._download_m2()

        _log("[3/7] 下载 CPI/PPI...")
        self._download_cpi_ppi()

        _log("[4/7] 下载 PMI...")
        self._download_pmi()

        _log("[5/7] 下载 CNY/USD 汇率...")
        self._download_cny_usd()

        _log("[6/7] 下载社会融资规模...")
        self._download_shrongji()

        _log("[7/7] 下载境外宏观指标...")
        self._download_overseas()

        _log("Macro 域初始化完成！")
        self._print_stats()

    def update_db(self) -> None:
        """增量更新（全量重取 + 去重覆盖，含汇率与境外指标）"""
        _log("Macro 域增量更新...")

        _log("[1/7] 更新国债收益率...")
        self._download_bond_yield(incremental=True)

        _log("[2/7] 更新 M2...")
        self._download_m2()

        _log("[3/7] 更新 CPI/PPI...")
        self._download_cpi_ppi()

        _log("[4/7] 更新 PMI...")
        self._download_pmi()

        _log("[5/7] 更新社融规模...")
        self._download_shrongji()

        _log("[6/7] 更新 CNY/USD 汇率...")
        self._download_cny_usd()

        _log("[7/7] 更新境外宏观指标...")
        self._download_overseas()

        _log("Macro 域更新完成！")
        self._print_stats()

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
            self._sync_us_bond_yield()
        except Exception as e:
            logger.warning(f"国债收益率下载失败: {e}")

    @staticmethod
    def _derive_us_bond_yield(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        """从 bond_yield 中截取美债列（纯函数，便于单测）"""
        if df is None or df.empty:
            return None
        cols = [c for c in _US_BOND_COLS if c in df.columns]
        if not cols:
            return None
        us = df[cols].dropna(how="all")
        return us if not us.empty else None

    def _sync_us_bond_yield(self) -> None:
        """派生 us_bond_yield.parquet（美债 2Y/5Y/10Y/30Y），保持 bond_yield 为唯一数据源"""
        us = self._derive_us_bond_yield(store.load("bond_yield"))
        if us is None:
            return
        store.replace("us_bond_yield", us)
        _log(f"  美债收益率（派生自 bond_yield）：{len(us)} 条记录  最新: {us.index.max().date()}")

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

        # CPI：主源东财 macro_china_cpi（更新及时），备源金十 macro_china_cpi_monthly
        cpi_df = None
        try:
            df = _retry(lambda: ak.macro_china_cpi())
            if df is not None and not df.empty:
                df = _parse_month_col(df)
                df = df.rename(columns={c: f"cpi_{c}" for c in df.columns if c != "date"})
                cpi_df = df
                _log(f"  CPI：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"CPI（东财）下载失败: {e}")
        if cpi_df is None:
            try:
                df = _retry(lambda: ak.macro_china_cpi_monthly())
                if df is not None and not df.empty:
                    df = _parse_month_col(df)
                    df = df.rename(columns={c: f"cpi_{c}" for c in df.columns if c != "date"})
                    cpi_df = df
                    _log(f"  CPI（金十备用源）：{len(df)} 条记录")
            except Exception as e:
                logger.warning(f"CPI 备用源也失败: {e}")

        time.sleep(1)

        # PPI：主源东财 macro_china_ppi，备源金十 macro_china_ppi_yearly
        ppi_df = None
        try:
            df = _retry(lambda: ak.macro_china_ppi())
            if df is not None and not df.empty:
                df = _parse_month_col(df)
                df = df.rename(columns={c: f"ppi_{c}" for c in df.columns if c != "date"})
                ppi_df = df
                _log(f"  PPI：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"PPI（东财）下载失败: {e}")
        if ppi_df is None:
            try:
                df = _retry(lambda: ak.macro_china_ppi_yearly())
                if df is not None and not df.empty:
                    df = _parse_month_col(df)
                    df = df.rename(columns={c: f"ppi_{c}" for c in df.columns if c != "date"})
                    ppi_df = df
                    _log(f"  PPI（金十备用源）：{len(df)} 条记录")
            except Exception as e:
                logger.warning(f"PPI 备用源也失败: {e}")

        time.sleep(1)

        # 合并 CPI + PPI（东财源含全历史，整表覆盖避免旧源字段残留）
        if cpi_df is not None and ppi_df is not None:
            try:
                merged = pd.merge(cpi_df, ppi_df, on="date", how="outer")
                store.replace("cpi_ppi", merged)
                _log(f"  CPI/PPI 合并：{len(merged)} 条记录")
            except Exception as e:
                logger.warning(f"CPI/PPI 合并失败: {e}")
                store.replace("cpi_ppi", cpi_df)
        elif cpi_df is not None:
            store.replace("cpi_ppi", cpi_df)
        elif ppi_df is not None:
            store.replace("cpi_ppi", ppi_df)
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
    # 社融规模
    # ------------------------------------------------------------------

    def _download_shrongji(self) -> None:
        """
        社融规模（月频）。主源 Tushare `sf_month`（更新及时）；
        备源 AKShare `macro_china_shrzgm`（商务数据中心，常滞后数月）。
        """
        df = self._fetch_shrongji_ts()
        if df is None or df.empty:
            df = self._fetch_shrongji_ak()
        if df is None or df.empty:
            _log("  社融规模：无数据")
            return
        store.replace("shrongji", df)
        _log(f"  社融规模：{len(df)} 条记录")

    @staticmethod
    def _fetch_shrongji_ts() -> Optional[pd.DataFrame]:
        """Tushare sf_month：inc_month/inc_cumval 单位亿元，stk_endval 单位万亿元"""
        try:
            from config.settings import DATA_SOURCE
            import tushare as ts
            token = DATA_SOURCE.get("tushare_token", "")
            if not token:
                return None
            pro = ts.pro_api(token, timeout=20)
            raw = pro.sf_month()
            if raw is None or raw.empty:
                return None
            df = raw.copy()
            df["date"] = pd.to_datetime(df["month"], format="%Y%m", errors="coerce")
            df = df.dropna(subset=["date"])
            keep = [c for c in ("inc_month", "inc_cumval", "stk_endval") if c in df.columns]
            return df[["date"] + keep].sort_values("date").reset_index(drop=True)
        except Exception as e:
            logger.warning(f"社融规模（Tushare sf_month）下载失败: {e}")
            return None

    @staticmethod
    def _fetch_shrongji_ak() -> Optional[pd.DataFrame]:
        import akshare as ak
        try:
            df = _retry(lambda: ak.macro_china_shrzgm())
            if df is None or df.empty:
                return None
            return _parse_month_col(df)
        except Exception as e:
            logger.warning(f"社融规模（AKShare 备用源）下载失败: {e}")
            return None

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
        """
        人民币汇率（日频）。主源国家外汇管理局中间价 `currency_boc_safe`
        （全历史、更新及时）；备源 Tushare 离岸 CNH。
        存储列：usd_cny（1 美元兑人民币，元）
        """
        import akshare as ak
        try:
            df = _retry(lambda: ak.currency_boc_safe())
            if df is not None and not df.empty and "美元" in df.columns:
                df = df.copy()
                df = df.rename(columns={"日期": "date"})
                df["date"] = pd.to_datetime(df["date"])
                df["usd_cny"] = pd.to_numeric(df["美元"], errors="coerce") / 100.0
                df = df[["date", "usd_cny"]].dropna(subset=["usd_cny"])
                store.replace("cny_usd", df)
                _log(f"  CNY/USD（外管局中间价）：{len(df)} 条记录")
                return
        except Exception as e:
            logger.warning(f"CNY/USD 外管局中间价下载失败: {e}")

        # 备用：Tushare 离岸人民币 USDCNH
        try:
            from config.settings import DATA_SOURCE
            import tushare as ts
            token = DATA_SOURCE.get("tushare_token", "")
            if token:
                pro = ts.pro_api(token, timeout=20)
                raw = pro.fx_daily(ts_code="USDCNH.FXCM", start_date="20150101",
                                   end_date=datetime.now().strftime("%Y%m%d"))
                if raw is not None and not raw.empty:
                    raw = raw.copy()
                    raw["date"] = pd.to_datetime(raw["trade_date"], format="%Y%m%d")
                    raw["usd_cny"] = pd.to_numeric(raw["bid_close"], errors="coerce")
                    df = raw[["date", "usd_cny"]].dropna().sort_values("date")
                    store.replace("cny_usd", df)
                    _log(f"  CNY/USD（Tushare 离岸 CNH 备用源）：{len(df)} 条记录")
                    return
        except Exception as e:
            logger.warning(f"CNY/USD 备用源也失败: {e}")
        _log("  CNY/USD：无可用数据源")

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
            # VIX 恐慌指数：使用外盘期货 VX（CBOE VIX 期货）数据
            df = _retry(lambda: ak.futures_foreign_hist(symbol="VX"))
            if df is None or df.empty:
                _log("  VIX：无数据")
                return
            df = df.copy()
            # futures_foreign_hist 返回英文列名：date, open, high, low, close, volume
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
            store.save("vix", df)
            _log(f"  VIX（VIX期货）：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"VIX 下载失败: {e}")

    def _download_dxy(self) -> None:
        """
        美元指数 DXY（日频）。
        主源：东财 push2his 直连（secid=100.UDI；与 akshare `index_global_hist_em`
        同源，但该封装在当前网络常连接失败）；备源：AKShare `index_global_hist_em`。
        """
        df = self._fetch_dxy_em()
        if df is None or df.empty:
            df = self._fetch_dxy_ak()
        if df is None or df.empty:
            _log("  DXY：无可用数据源")
            return
        store.replace("dxy", df)
        _log(f"  DXY：{len(df)} 条记录")

    @staticmethod
    def _fetch_dxy_em() -> Optional[pd.DataFrame]:
        """东财 K 线接口（100.UDI 美元指数），返回全历史日线。

        注：`push2his.eastmoney.com` 在当前网络下 TLS 常被重置，
        故依次尝试备用域名（91 前缀 / HTTP）。
        """
        import requests

        hosts = (
            "https://91.push2his.eastmoney.com",
            "http://push2his.eastmoney.com",
        )
        params = {
            "secid": "100.UDI",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "klt": "101", "fqt": "1", "beg": "0", "end": "20500101",
        }
        headers = {"User-Agent": "Mozilla/5.0",
                   "Referer": "https://quote.eastmoney.com/"}

        def _fetch():
            last_err = None
            for host in hosts:
                try:
                    r = requests.get(host + "/api/qt/stock/kline/get",
                                     params=params, timeout=20, headers=headers)
                    r.raise_for_status()
                    klines = (r.json().get("data") or {}).get("klines") or []
                    if not klines:
                        continue
                    rows = []
                    for line in klines:
                        p = line.split(",")
                        if len(p) < 6:
                            continue
                        rows.append({
                            "date": p[0], "open": p[1], "close": p[2],
                            "high": p[3], "low": p[4], "volume": p[5],
                            "amount": p[6] if len(p) > 6 else None,
                            "pct_chg": p[7] if len(p) > 7 else None,
                        })
                    return pd.DataFrame(rows)
                except Exception as e:
                    last_err = e
            if last_err is not None:
                raise last_err
            return None

        try:
            return _retry(_fetch)
        except Exception as e:
            logger.warning(f"美元指数（东财直连）下载失败: {e}")
            return None

    @staticmethod
    def _fetch_dxy_ak() -> Optional[pd.DataFrame]:
        import akshare as ak
        try:
            df = _retry(lambda: ak.index_global_hist_em(symbol="美元指数"))
            if df is None or df.empty:
                return None
            df = df.copy()
            if "日期" in df.columns:
                df = df.rename(columns={"日期": "date"})
            for src, dst in [("最新价", "close"), ("今开", "open"),
                             ("最高", "high"), ("最低", "low")]:
                if src in df.columns and dst not in df.columns:
                    df = df.rename(columns={src: dst})
            return df
        except Exception as e:
            logger.warning(f"美元指数（AKShare 备用源）下载失败: {e}")
            return None

    def _download_oil_wti(self) -> None:
        """
        WTI 原油（日频）。主源新浪外盘期货 CL（NYMEX WTI），备源 OIL（布伦特）。
        注：不再调用上海金基准价接口（历史 bug：黄金数据曾被误存为 oil_wti）。
        """
        import akshare as ak
        for symbol, label in (("CL", "WTI"), ("OIL", "Brent")):
            try:
                df = _retry(lambda s=symbol: ak.futures_foreign_hist(symbol=s))
                if df is None or df.empty:
                    continue
                df = df.copy()
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
                df = df[df["close"] > 0]
                if df.empty:
                    continue
                store.replace("oil_wti", df)
                _log(f"  原油（{label}）：{len(df)} 条记录")
                return
            except Exception as e:
                logger.warning(f"原油 {label} 下载失败: {e}")
        _log("  原油：无可用数据源")

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
            df = _retry(lambda: ak.stock_hk_index_daily_sina(symbol="HSI"))
            if df is None or df.empty:
                _log("  恒生指数：无数据")
                return
            df = df.copy()
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            store.save("hsi", df)
            _log(f"  恒生指数：{len(df)} 条记录")
        except Exception as e:
            logger.warning(f"恒生指数下载失败: {e}")
