"""
数据获取器 - 多源聚合
支持 AKShare / BaoStock / Tushare Pro，自动降级切换
"""
import contextlib
import hashlib
import logging
import pickle
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class DataFetcher:
    """统一数据获取接口，内置缓存与多源降级"""

    def __init__(self, config: dict):
        self.cfg = config
        self.cache_dir = Path(config.get("cache_dir", "data/cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_days = config.get("cache_days", 1)
        self._bs_logged_in = False
        self._bs_lock = threading.Lock()  # 防止多线程并发登录

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def get_stock_list(self) -> pd.DataFrame:
        """获取A股全市场股票列表，返回 DataFrame: code, name, market
        用 baostock，完成后重置连接供后续 K 线下载使用"""
        df = self._with_fallback("_get_stock_list_baostock",
                                 "_get_stock_list_akshare")
        # 获取完列表后重置 baostock 连接，避免影响后续 K 线查询
        self._relogin_baostock()
        return df

    def get_daily(self, code: str, start: str, end: str,
                  adjust: str = "qfq") -> pd.DataFrame:
        """
        获取日线数据（前复权）
        :param code: 股票代码，如 '000001' 或 '000001.SZ'
        :param start: 开始日期 'YYYY-MM-DD'
        :param end:   结束日期 'YYYY-MM-DD'
        :param adjust: 复权方式 qfq/hfq/none
        :return: DataFrame columns=[date, open, high, low, close, volume, amount, pct_chg]
        """
        code_clean = code.replace(".SZ", "").replace(".SH", "")
        cache_key = f"daily_{code_clean}_{start}_{end}_{adjust}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached

        df = self._with_fallback(
            "_get_daily_akshare", "_get_daily_baostock",
            code=code_clean, start=start, end=end, adjust=adjust
        )
        if df is not None and not df.empty:
            self._save_cache(cache_key, df)
        return df

    def get_realtime(self, codes: list) -> pd.DataFrame:
        """获取实时行情快照"""
        return self._get_realtime_akshare(codes)

    def get_financial(self, code: str) -> pd.DataFrame:
        """获取基本面财务数据（年报/季报）"""
        cache_key = f"fin_{code}_{datetime.now().strftime('%Y%m')}"
        cached = self._load_cache(cache_key)
        if cached is not None:
            return cached
        df = self._get_financial_akshare(code)
        if df is not None and not df.empty:
            self._save_cache(cache_key, df)
        return df

    # ------------------------------------------------------------------
    # AKShare 实现
    # ------------------------------------------------------------------

    def _get_stock_list_akshare(self, **_) -> pd.DataFrame:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        df.columns = ["code", "name"]
        df["market"] = df["code"].apply(
            lambda x: "SH" if x.startswith("6") else "SZ"
        )
        return df

    def _get_daily_akshare(self, code: str, start: str, end: str,
                           adjust: str = "qfq", **_) -> pd.DataFrame:
        import akshare as ak
        adjust_map = {"qfq": "qfq", "hfq": "hfq", "none": ""}
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=adjust_map.get(adjust, "qfq"),
        )
        if df is None or df.empty:
            return pd.DataFrame()
        # 兼容中英文列名（akshare 不同版本可能不同）
        col_map = {
            "日期": "date", "开盘": "open", "最高": "high",
            "最低": "low", "收盘": "close", "成交量": "volume",
            "成交额": "amount", "涨跌幅": "pct_chg",
            # 英文列名（部分版本）
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
            "amount": "amount", "pct_chg": "pct_chg",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
        if "date" not in df.columns:
            logger.warning(f"akshare 返回未知列名: {df.columns.tolist()}")
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"])
        # 只取需要的列，缺失列用 NaN 填充
        result_cols = ["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
        for col in result_cols:
            if col not in df.columns:
                df[col] = float("nan")
        return df[result_cols]

    def _get_realtime_akshare(self, codes: list) -> pd.DataFrame:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if codes:
            df = df[df["代码"].isin(codes)]
        df = df.rename(columns={
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "pct_chg", "成交量": "volume", "成交额": "amount",
        })
        return df

    def _get_financial_akshare(self, code: str, **_) -> pd.DataFrame:
        import akshare as ak
        try:
            df = ak.stock_financial_analysis_indicator(symbol=code)
            return df
        except Exception as e:
            logger.warning(f"AKShare 财务数据获取失败 {code}: {e}")
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # BaoStock 实现（备用）
    # ------------------------------------------------------------------

    def _login_baostock(self):
        """每个线程独立登录，用 thread-local 存储连接状态"""
        import baostock as bs
        local = self._get_thread_local()
        if not getattr(local, "bs_logged_in", False):
            bs.login()
            local.bs_logged_in = True

    def _relogin_baostock(self):
        """强制重新登录 baostock（连接异常时调用）"""
        import baostock as bs
        local = self._get_thread_local()
        with contextlib.suppress(Exception):
            bs.logout()
        bs.login()
        local.bs_logged_in = True

    def _get_thread_local(self):
        """获取当前线程的 thread-local 存储"""
        if not hasattr(self, "_thread_local"):
            import threading
            self._thread_local = threading.local()
        return self._thread_local

    def _get_stock_list_baostock(self, **_) -> pd.DataFrame:
        import baostock as bs
        self._login_baostock()
        rs = bs.query_stock_basic()
        data = []
        while rs.error_code == "0" and rs.next():
            data.append(rs.get_row_data())
        df = pd.DataFrame(data, columns=rs.fields)
        # type=1: 股票，status=1: 上市中
        df = df[(df["type"] == "1") & (df["status"] == "1")]
        df = df.rename(columns={"code": "full_code", "code_name": "name"})
        df["market"] = df["full_code"].str.split(".").str[0].str.upper()
        df["code"] = df["full_code"].str.split(".").str[1]
        return df[["code", "name", "market"]].reset_index(drop=True)

    def _get_daily_baostock(self, code: str, start: str, end: str,
                            adjust: str = "qfq", **_) -> pd.DataFrame:
        import socket

        import baostock as bs
        self._login_baostock()
        market = "sh" if code.startswith("6") else "sz"
        bs_code = f"{market}.{code}"
        adjust_flag = {"qfq": "2", "hfq": "1", "none": "3"}.get(adjust, "2")
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(30)
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume,amount,pctChg",
                start_date=start, end_date=end,
                frequency="d", adjustflag=adjust_flag,
            )
            # baostock 连接异常时 error_code 非 0，强制重连后重试一次
            if rs.error_code != "0":
                logger.warning(f"baostock 查询 {code} 异常({rs.error_code})，重连后重试")
                self._relogin_baostock()
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount,pctChg",
                    start_date=start, end_date=end,
                    frequency="d", adjustflag=adjust_flag,
                )
            data = []
            while rs.error_code == "0" and rs.next():
                data.append(rs.get_row_data())
        finally:
            socket.setdefaulttimeout(old_timeout)
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=["date", "open", "high", "low",
                                          "close", "volume", "amount", "pct_chg"])
        for col in ["open", "high", "low", "close", "volume", "amount", "pct_chg"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        return df

    # ------------------------------------------------------------------
    # 通用降级与缓存
    # ------------------------------------------------------------------

    def _with_fallback(self, primary_method: str, *fallback_methods: str, **kwargs):
        methods = [primary_method, *list(fallback_methods)]
        for method_name in methods:
            method = getattr(self, method_name, None)
            if method is None:
                continue
            try:
                result = method(**kwargs)
                if result is not None and (
                    not isinstance(result, pd.DataFrame) or not result.empty
                ):
                    return result
            except Exception as e:
                logger.warning(f"{method_name} 失败，尝试下一数据源: {e}")
        logger.error("所有数据源均失败")
        return pd.DataFrame()

    def _cache_path(self, key: str) -> Path:
        h = hashlib.md5(key.encode()).hexdigest()[:12]
        return self.cache_dir / f"{h}.pkl"

    def _load_cache(self, key: str) -> pd.DataFrame | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
        if age > timedelta(days=self.cache_days):
            return None
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def _save_cache(self, key: str, df: pd.DataFrame):
        path = self._cache_path(key)
        try:
            with open(path, "wb") as f:
                pickle.dump(df, f)
        except Exception as e:
            logger.warning(f"缓存写入失败: {e}")

    def __del__(self):
        local = getattr(self, "_thread_local", None)
        if local is not None and getattr(local, "bs_logged_in", False):
            try:
                import baostock as bs
                bs.logout()
            except Exception:
                pass
