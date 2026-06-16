"""
moneyflow 域下载器

负责下载资金流与情绪类数据：
  - stock      个股历史资金流向（Tushare moneyflow，2010起，日频）
  - industry   行业资金流（Tushare moneyflow_ind_dc，东财，按日期分片）
  - market     大盘资金流（Tushare moneyflow_mkt_dc，东财，单文件）
  - margin     融资融券明细（Tushare margin_detail，按股票代码，日频）
  - top_list   龙虎榜（Tushare top_list，按日期）
  - northbound 北向资金（AKShare stock_hsgt_hist_em，无需 Tushare token）

接口限流说明（Tushare 默认）：
  - 每分钟最多 200 次（普通积分）
  - 单次最多返回 5000 行
  - 批量拉取时每次请求后 sleep 0.3~0.5s

用法:
  dl = MoneyflowDownloader()
  dl.init_db()                    # 全量初始化（stock 2010起，其余 2024起）
  dl.update_db()                  # 每日增量更新
  dl.init_stock(start='2010-01-01')      # 单独初始化个股资金流
  dl.init_northbound()                   # 单独初始化北向资金（AKShare）

注意：northbound 用 AKShare，不需要 TUSHARE_TOKEN；其余 5 类需要 token，
      token 在首次调用 Tushare 接口时惰性初始化。
"""
import logging
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

import data.store_moneyflow as store

logger = logging.getLogger(__name__)

# 默认起始日期
DEFAULT_START_STOCK = "2010-01-01"
DEFAULT_START = "2024-01-01"

# Tushare 日期格式
_FMT = "%Y%m%d"
_FMT_DASH = "%Y-%m-%d"

# 全局请求超时（秒）
_REQUEST_TIMEOUT = 30

# socket 超时（对底层 TCP 连接生效）
socket.setdefaulttimeout(_REQUEST_TIMEOUT)

# monkey-patch requests.Session，给所有 HTTP 请求加超时
try:
    import requests
    _orig_request = requests.Session.request

    def _patched_request(self, method, url, **kwargs):
        kwargs["timeout"] = _REQUEST_TIMEOUT
        return _orig_request(self, method, url, **kwargs)

    requests.Session.request = _patched_request
except Exception:
    pass


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _to_ts_date(d: str) -> str:
    """'YYYY-MM-DD' -> 'YYYYMMDD'"""
    return d.replace("-", "")


def _to_dash_date(d: str) -> str:
    """'YYYYMMDD' -> 'YYYY-MM-DD'"""
    if len(d) == 8 and "-" not in d:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d


def _retry(func, max_retries: int = 3, wait_base: float = 2.0):
    """指数退避重试，用线程池实现真正的超时（对任何阻塞调用有效）"""
    for attempt in range(max_retries):
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            future = ex.submit(func)
            result = future.result(timeout=_REQUEST_TIMEOUT)
            ex.shutdown(wait=False)
            return result
        except FuturesTimeoutError:
            ex.shutdown(wait=False)
            e = TimeoutError(f"请求超时（>{_REQUEST_TIMEOUT}s）")
            if attempt < max_retries - 1:
                wait = wait_base ** attempt
                logger.debug(f"第{attempt+1}次超时，{wait:.0f}s 后重试")
                time.sleep(wait)
            else:
                raise e
        except Exception as e:
            ex.shutdown(wait=False)
            if attempt < max_retries - 1:
                wait = wait_base ** attempt
                logger.debug(f"第{attempt+1}次失败，{wait:.0f}s 后重试: {e}")
                time.sleep(wait)
            else:
                raise


def _get_trade_dates(pro, start: str, end: str) -> list:
    """获取指定范围内的交易日列表（YYYYMMDD 格式）"""
    df = pro.trade_cal(
        exchange="SSE",
        start_date=_to_ts_date(start),
        end_date=_to_ts_date(end),
        is_open="1",
        fields="cal_date",
    )
    if df is None or df.empty:
        return []
    return sorted(df["cal_date"].tolist())


def _get_all_stock_codes(pro) -> list:
    """获取全部 A 股代码列表（ts_code 格式，如 000001.SZ）"""
    df = pro.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,list_status",
    )
    if df is None or df.empty:
        return []
    return df["ts_code"].tolist()


class MoneyflowDownloader:

    def __init__(self):
        # token 惰性初始化：northbound（AKShare）不需要 token
        self._pro = None

    def _ensure_pro(self):
        """惰性初始化 Tushare pro_api（仅 Tushare 接口需要）"""
        if self._pro is not None:
            return self._pro
        token = os.getenv("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError(
                "TUSHARE_TOKEN 未配置，请在 .env 中设置 TUSHARE_TOKEN=your_token"
            )
        try:
            import tushare as ts
            ts.set_token(token)
            self._pro = ts.pro_api()
        except ImportError:
            raise RuntimeError("请先安装 tushare: pip install tushare")
        return self._pro

    # ==================================================================
    # 公共入口
    # ==================================================================

    def init_db(self,
                start: str = DEFAULT_START,
                stock_start: str = DEFAULT_START_STOCK) -> None:
        """
        全量初始化 moneyflow 域。

        stock 从 stock_start（默认2010）开始，其余从 start（默认2024）开始。
        northbound 用 AKShare，不依赖 token。
        """
        _log("=" * 60)
        _log(f"Moneyflow 域初始化  行业/大盘/龙虎榜/融资融券起始: {start}")
        _log(f"                    个股资金流起始: {stock_start}")
        _log("=" * 60)

        _log("[1/6] 初始化个股资金流（stock）...")
        self.init_stock(start=stock_start)

        _log("[2/6] 初始化龙虎榜（top_list）...")
        self.init_top_list(start=start)

        _log("[3/6] 初始化融资融券（margin）...")
        self.init_margin(start=start)

        _log("[4/6] 初始化行业资金流（industry）...")
        self.init_industry(start=start)

        _log("[5/6] 初始化大盘资金流（market）...")
        self.init_market(start=start)

        _log("[6/6] 初始化北向资金（northbound，AKShare）...")
        self.init_northbound()

        _log("Moneyflow 域初始化完成！")

    def update_db(self) -> None:
        """每日增量更新所有 moneyflow 数据"""
        today = datetime.now().strftime(_FMT_DASH)
        _log(f"Moneyflow 域增量更新  目标日期: {today}")

        _log("[1/6] 更新个股资金流...")
        self._update_stock()

        _log("[2/6] 更新龙虎榜...")
        self._update_top_list()

        _log("[3/6] 更新融资融券...")
        self._update_margin()

        _log("[4/6] 更新行业资金流...")
        self._update_industry()

        _log("[5/6] 更新大盘资金流...")
        self._update_market()

        _log("[6/6] 更新北向资金（AKShare）...")
        self._update_northbound()

        _log("Moneyflow 域更新完成！")

    # ==================================================================
    # stock — 个股资金流向（Tushare moneyflow）
    # ==================================================================

    def init_stock(self, start: str = DEFAULT_START_STOCK) -> None:
        """全量下载个股历史资金流向（2010起），按股票代码逐只拉取。"""
        import json
        from pathlib import Path

        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(pro)
        total = len(codes)
        _log(f"  stock: 共 {total} 只，起始 {start} ~ {end}")

        ipo_path = Path(__file__).parent / "market" / "ipo_dates.json"
        ipo_dates: dict = {}
        if ipo_path.exists():
            with open(ipo_path) as f:
                raw = json.load(f)
            for k, v in raw.items():
                code6 = k.split(":")[-1]
                ipo_dates[code6] = v.get("ipo_date", "")

        _log("  预加载已有数据索引...")
        existing_last: dict = {}
        for p in (store.BASE_DIR / "stock").glob("*.parquet"):
            try:
                df_tmp = pd.read_parquet(p, engine="pyarrow", columns=["date"])
                if len(df_tmp.index) > 0:
                    existing_last[p.stem] = df_tmp.index.max().strftime(_FMT_DASH)
            except Exception:
                pass
        _log(f"  已有 {len(existing_last)} 只，开始下载...")

        ok = skip = fail = 0
        t0 = time.time()

        for i, ts_code in enumerate(codes):
            code6 = ts_code.split(".")[0]
            ld = existing_last.get(code6)
            if ld and ld >= end:
                skip += 1
                continue

            if ld:
                fetch_start = (
                    datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)
                ).strftime(_FMT_DASH)
            else:
                ipo = ipo_dates.get(code6, "")
                fetch_start = ipo if (ipo and ipo > start) else start

            try:
                df = self._fetch_stock(ts_code, fetch_start, end)
                if df is None or df.empty:
                    skip += 1
                    continue
                df["code"] = code6
                store.save("stock", df, code=code6)
                existing_last[code6] = end
                ok += 1
                time.sleep(0.35)
            except Exception as e:
                logger.debug(f"stock {ts_code} 失败: {e}")
                fail += 1
                time.sleep(1.0)

            if (i + 1) % 200 == 0:
                elapsed = time.time() - t0
                _log(f"  进度: {i+1}/{total}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  stock 完成: 新增={ok} 跳过={skip} 失败={fail}  {elapsed:.0f}s")

    def _update_stock(self) -> None:
        """增量更新：只补全有缺口的股票"""
        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(pro)
        ok = skip = fail = 0
        t0 = time.time()

        for i, ts_code in enumerate(codes):
            code6 = ts_code.split(".")[0]
            ld = store.last_date("stock", code=code6)
            if ld and ld >= end:
                skip += 1
                continue
            fetch_start = (
                (datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)).strftime(_FMT_DASH)
                if ld else DEFAULT_START_STOCK
            )
            try:
                df = self._fetch_stock(ts_code, fetch_start, end)
                if df is None or df.empty:
                    skip += 1
                    continue
                df["code"] = code6
                store.save("stock", df, code=code6)
                ok += 1
                time.sleep(0.35)
            except Exception as e:
                logger.debug(f"stock update {ts_code} 失败: {e}")
                fail += 1
                time.sleep(1.0)

            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                _log(f"  stock 进度: {i+1}/{len(codes)}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  stock 更新完成: 新增={ok} 跳过={skip} 失败={fail}  {elapsed:.0f}s")

    def _fetch_stock(self, ts_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """拉取单只股票资金流向，自动分段（Tushare 单次最多 5000 行）。"""
        pro = self._ensure_pro()
        all_dfs = []
        cur_start = datetime.strptime(start, _FMT_DASH)
        cur_end = datetime.strptime(end, _FMT_DASH)
        chunk_days = 365

        while cur_start <= cur_end:
            seg_end = min(cur_start + timedelta(days=chunk_days), cur_end)

            def _fetch(s=cur_start, e=seg_end):
                return pro.moneyflow(
                    ts_code=ts_code,
                    start_date=s.strftime(_FMT),
                    end_date=e.strftime(_FMT),
                )

            df = _retry(_fetch)
            if df is not None and not df.empty:
                all_dfs.append(df)
            cur_start = seg_end + timedelta(days=1)
            time.sleep(0.2)

        if not all_dfs:
            return None
        result = pd.concat(all_dfs, ignore_index=True)
        result = result.drop_duplicates(subset=["trade_date"])
        return result

    # ==================================================================
    # top_list — 龙虎榜
    # ==================================================================

    def init_top_list(self, start: str = DEFAULT_START) -> None:
        """全量下载龙虎榜，按交易日逐日拉取。"""
        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        _log(f"  top_list: {start} ~ {end}")

        trade_dates = _retry(lambda: _get_trade_dates(pro, start, end))
        existing = set(d.replace("-", "") for d in store.list_dates("top_list"))
        dates_todo = [d for d in trade_dates if d not in existing]
        _log(f"  top_list: 共 {len(trade_dates)} 个交易日，待下载 {len(dates_todo)} 个")

        ok = skip = fail = 0
        t0 = time.time()

        for i, td in enumerate(dates_todo):
            date_dash = _to_dash_date(td)
            try:
                def _fetch(d=td):
                    return pro.top_list(trade_date=d)

                df = _retry(_fetch)
                if df is None or df.empty:
                    store.save("top_list",
                               pd.DataFrame(columns=["trade_date"]),
                               date=date_dash)
                    skip += 1
                else:
                    store.save("top_list", df, date=date_dash)
                    ok += 1
                time.sleep(0.4)
            except Exception as e:
                logger.debug(f"top_list {td} 失败: {e}")
                fail += 1
                time.sleep(1.5)

            if (i + 1) % 100 == 0:
                elapsed = time.time() - t0
                _log(f"  进度: {i+1}/{len(dates_todo)}  有数据:{ok} 空日:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  top_list 完成: 有数据={ok} 空日={skip} 失败={fail}  {elapsed:.0f}s")

    def _update_top_list(self) -> None:
        """增量更新龙虎榜：补全缺失的交易日"""
        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        existing = set(d.replace("-", "") for d in store.list_dates("top_list"))

        if existing:
            last = max(existing)
            fetch_start = (
                datetime.strptime(_to_dash_date(last), _FMT_DASH) + timedelta(days=1)
            ).strftime(_FMT_DASH)
        else:
            fetch_start = DEFAULT_START

        if fetch_start > end:
            _log("  top_list：已是最新")
            return

        trade_dates = _retry(lambda: _get_trade_dates(pro, fetch_start, end))
        dates_todo = [d for d in trade_dates if d not in existing]
        if not dates_todo:
            _log("  top_list：已是最新")
            return

        ok = skip = fail = 0
        for td in dates_todo:
            date_dash = _to_dash_date(td)
            try:
                def _fetch(d=td):
                    return pro.top_list(trade_date=d)

                df = _retry(_fetch)
                if df is None or df.empty:
                    store.save("top_list",
                               pd.DataFrame(columns=["trade_date"]),
                               date=date_dash)
                    skip += 1
                else:
                    store.save("top_list", df, date=date_dash)
                    ok += 1
                time.sleep(0.4)
            except Exception as e:
                logger.debug(f"top_list update {td} 失败: {e}")
                fail += 1
                time.sleep(1.5)

        _log(f"  top_list 更新完成: 有数据={ok} 空日={skip} 失败={fail}")

    # ==================================================================
    # margin — 融资融券
    # ==================================================================

    def init_margin(self, start: str = DEFAULT_START) -> None:
        """全量下载融资融券明细，按股票代码逐只拉取。"""
        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(pro)
        total = len(codes)
        _log(f"  margin: 共 {total} 只，{start} ~ {end}")

        _log("  预加载已有数据索引...")
        existing_last: dict = {}
        for p in (store.BASE_DIR / "margin").glob("*.parquet"):
            try:
                df_tmp = pd.read_parquet(p, engine="pyarrow", columns=["date"])
                if len(df_tmp.index) > 0:
                    existing_last[p.stem] = df_tmp.index.max().strftime(_FMT_DASH)
            except Exception:
                pass
        _log(f"  已有 {len(existing_last)} 只，开始下载...")

        ok = skip = fail = 0
        t0 = time.time()

        for i, ts_code in enumerate(codes):
            code6 = ts_code.split(".")[0]
            ld = existing_last.get(code6)
            if ld and ld >= end:
                skip += 1
                continue
            fetch_start = (
                (datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)).strftime(_FMT_DASH)
                if ld else start
            )

            try:
                df = self._fetch_margin(ts_code, fetch_start, end)
                if df is None or df.empty:
                    skip += 1
                    continue
                store.save("margin", df, code=code6)
                existing_last[code6] = end
                ok += 1
                time.sleep(0.35)
            except Exception as e:
                logger.debug(f"margin {ts_code} 失败: {e}")
                fail += 1
                time.sleep(1.0)

            if (i + 1) % 200 == 0:
                elapsed = time.time() - t0
                _log(f"  进度: {i+1}/{total}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  margin 完成: 新增={ok} 跳过={skip} 失败={fail}  {elapsed:.0f}s")

    def _update_margin(self) -> None:
        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(pro)
        ok = skip = fail = 0
        t0 = time.time()

        for i, ts_code in enumerate(codes):
            code6 = ts_code.split(".")[0]
            ld = store.last_date("margin", code=code6)
            if ld and ld >= end:
                skip += 1
                continue
            fetch_start = (
                (datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)).strftime(_FMT_DASH)
                if ld else DEFAULT_START
            )
            try:
                df = self._fetch_margin(ts_code, fetch_start, end)
                if df is None or df.empty:
                    skip += 1
                    continue
                store.save("margin", df, code=code6)
                ok += 1
                time.sleep(0.35)
            except Exception as e:
                logger.debug(f"margin update {ts_code} 失败: {e}")
                fail += 1
                time.sleep(1.0)

            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                _log(f"  margin 进度: {i+1}/{len(codes)}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  margin 更新完成: 新增={ok} 跳过={skip} 失败={fail}  {elapsed:.0f}s")

    def _fetch_margin(self, ts_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """拉取单只股票融资融券历史，自动分段"""
        pro = self._ensure_pro()
        all_dfs = []
        cur_start = datetime.strptime(start, _FMT_DASH)
        cur_end = datetime.strptime(end, _FMT_DASH)
        chunk_days = 365

        while cur_start <= cur_end:
            seg_end = min(cur_start + timedelta(days=chunk_days), cur_end)

            def _fetch(s=cur_start, e=seg_end):
                return pro.margin_detail(
                    ts_code=ts_code,
                    start_date=s.strftime(_FMT),
                    end_date=e.strftime(_FMT),
                )

            df = _retry(_fetch)
            if df is not None and not df.empty:
                all_dfs.append(df)
            cur_start = seg_end + timedelta(days=1)
            time.sleep(0.2)

        if not all_dfs:
            return None
        result = pd.concat(all_dfs, ignore_index=True)
        result = result.drop_duplicates(subset=["trade_date"])
        return result

    # ==================================================================
    # industry — 行业资金流（东财，按日期分片）
    # ==================================================================

    def init_industry(self, start: str = DEFAULT_START) -> None:
        """全量下载行业资金流（东财 moneyflow_ind_dc），按交易日逐日拉取。"""
        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        _log(f"  industry: {start} ~ {end}")

        trade_dates = _retry(lambda: _get_trade_dates(pro, start, end))
        existing = set(d.replace("-", "") for d in store.list_dates("industry"))
        dates_todo = [d for d in trade_dates if d not in existing]
        _log(f"  industry: 共 {len(trade_dates)} 个交易日，待下载 {len(dates_todo)} 个")

        ok = skip = fail = 0
        t0 = time.time()

        for i, td in enumerate(dates_todo):
            date_dash = _to_dash_date(td)
            try:
                def _fetch(d=td):
                    return pro.moneyflow_ind_dc(trade_date=d)

                df = _retry(_fetch)
                if df is None or df.empty:
                    store.save("industry",
                               pd.DataFrame(columns=["trade_date"]),
                               date=date_dash)
                    skip += 1
                else:
                    store.save("industry", df, date=date_dash)
                    ok += 1
                time.sleep(0.4)
            except Exception as e:
                logger.debug(f"industry {td} 失败: {e}")
                fail += 1
                time.sleep(1.5)

            if (i + 1) % 100 == 0:
                elapsed = time.time() - t0
                _log(f"  进度: {i+1}/{len(dates_todo)}  有数据:{ok} 空日:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  industry 完成: 有数据={ok} 空日={skip} 失败={fail}  {elapsed:.0f}s")

    def _update_industry(self) -> None:
        """增量更新行业资金流"""
        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        existing = set(d.replace("-", "") for d in store.list_dates("industry"))

        if existing:
            last = max(existing)
            fetch_start = (
                datetime.strptime(_to_dash_date(last), _FMT_DASH) + timedelta(days=1)
            ).strftime(_FMT_DASH)
        else:
            fetch_start = DEFAULT_START

        if fetch_start > end:
            _log("  industry：已是最新")
            return

        trade_dates = _retry(lambda: _get_trade_dates(pro, fetch_start, end))
        dates_todo = [d for d in trade_dates if d not in existing]
        if not dates_todo:
            _log("  industry：已是最新")
            return

        ok = skip = fail = 0
        for td in dates_todo:
            date_dash = _to_dash_date(td)
            try:
                def _fetch(d=td):
                    return pro.moneyflow_ind_dc(trade_date=d)

                df = _retry(_fetch)
                if df is None or df.empty:
                    store.save("industry",
                               pd.DataFrame(columns=["trade_date"]),
                               date=date_dash)
                    skip += 1
                else:
                    store.save("industry", df, date=date_dash)
                    ok += 1
                time.sleep(0.4)
            except Exception as e:
                logger.debug(f"industry update {td} 失败: {e}")
                fail += 1
                time.sleep(1.5)

        _log(f"  industry 更新完成: 有数据={ok} 空日={skip} 失败={fail}")

    # ==================================================================
    # market — 大盘资金流（东财，单文件）
    # ==================================================================

    def init_market(self, start: str = DEFAULT_START) -> None:
        """全量下载大盘资金流（东财 moneyflow_mkt_dc），按年分段批量拉取。"""
        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        _log(f"  market: {start} ~ {end}")

        ld = store.last_date("market")
        fetch_start = (
            (datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)).strftime(_FMT_DASH)
            if ld else start
        )
        if fetch_start > end:
            _log("  market：已是最新")
            return

        all_dfs = []
        cur = datetime.strptime(fetch_start, _FMT_DASH)
        cur_end = datetime.strptime(end, _FMT_DASH)

        while cur <= cur_end:
            seg_end = min(cur + timedelta(days=365), cur_end)
            try:
                def _fetch(s=cur, e=seg_end):
                    return pro.moneyflow_mkt_dc(
                        start_date=s.strftime(_FMT),
                        end_date=e.strftime(_FMT),
                    )
                df = _retry(_fetch)
                if df is not None and not df.empty:
                    all_dfs.append(df)
                time.sleep(0.4)
            except Exception as e:
                logger.warning(f"market {cur.strftime(_FMT_DASH)} 失败: {e}")
            cur = seg_end + timedelta(days=1)

        if not all_dfs:
            _log("  market：无数据")
            return

        result = pd.concat(all_dfs, ignore_index=True)
        result = result.drop_duplicates(subset=["trade_date"])
        store.save("market", result)
        _log(f"  market 完成: {len(result)} 行")

    def _update_market(self) -> None:
        """增量更新大盘资金流"""
        pro = self._ensure_pro()
        end = datetime.now().strftime(_FMT_DASH)
        ld = store.last_date("market")
        fetch_start = (
            (datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)).strftime(_FMT_DASH)
            if ld else DEFAULT_START
        )
        if fetch_start > end:
            _log("  market：已是最新")
            return
        try:
            def _fetch():
                return pro.moneyflow_mkt_dc(
                    start_date=_to_ts_date(fetch_start),
                    end_date=_to_ts_date(end),
                )
            df = _retry(_fetch)
            if df is not None and not df.empty:
                store.save("market", df)
                _log(f"  market 更新: +{len(df)} 行")
            else:
                _log("  market：无新数据")
        except Exception as e:
            logger.warning(f"market 更新失败: {e}")

    # ==================================================================
    # northbound — 北向资金（AKShare，无需 token）
    # ==================================================================

    def init_northbound(self) -> None:
        """全量下载北向资金历史（AKShare stock_hsgt_hist_em）。"""
        self._download_northbound(incremental=False)

    def _update_northbound(self) -> None:
        self._download_northbound(incremental=True)

    def _download_northbound(self, incremental: bool = False) -> None:
        import akshare as ak

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

                df["symbol"] = symbol
                all_dfs.append(df)
                time.sleep(0.8)
            except Exception as e:
                logger.warning(f"北向资金 {symbol} 下载失败: {e}")
                time.sleep(1)

        if not all_dfs:
            _log("  northbound：无数据")
            return

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
            _log("  northbound：合并失败")
            return

        if incremental:
            ld = store.last_date("northbound")
            if ld:
                next_day = (datetime.strptime(ld, "%Y-%m-%d") +
                            timedelta(days=1)).strftime("%Y-%m-%d")
                merged = merged[merged.index >= pd.Timestamp(next_day)]
                if merged.empty:
                    _log("  northbound：已是最新")
                    return

        store.save("northbound", merged.reset_index())
        _log(f"  northbound：{len(merged)} 条记录")
