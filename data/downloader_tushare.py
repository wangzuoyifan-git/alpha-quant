"""
Tushare 数据下载器

负责下载（均需 Tushare Pro Token）：
  - moneyflow     个股历史资金流向（2010起，日频）
  - daily         日线行情（stk_daily，可替代 BaoStock）
  - daily_basic   每日指标（PE/PB/换手率/市值等）
  - top_list      龙虎榜（按日期，含上榜原因/净买入/席位）
  - margin_detail 融资融券明细（按股票代码，日频）

接口限流说明（Tushare 默认）：
  - 每分钟最多 200 次（普通积分）
  - 单次最多返回 5000 行
  - 批量拉取时每次请求后 sleep 0.3~0.5s

用法:
  dl = TushareDownloader()
  dl.init_db()                    # 全量初始化（moneyflow 2010起，其余 2024起）
  dl.init_db(start='2010-01-01')  # 指定起始日期
  dl.update_db()                  # 每日增量更新
  dl.init_moneyflow(start='2010-01-01')   # 单独初始化资金流向
  dl.init_daily(start='2024-01-01')       # 单独初始化日线
  dl.init_top_list(start='2024-01-01')    # 单独初始化龙虎榜
  dl.init_margin(start='2024-01-01')      # 单独初始化融资融券
"""
import logging
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

import data.store_tushare as store

logger = logging.getLogger(__name__)

# 默认起始日期
DEFAULT_START_MONEYFLOW = "2010-01-01"
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
        # 强制覆盖 timeout（Tushare 可能显式传 timeout=None 导致 socket 永久阻塞）
        kwargs["timeout"] = _REQUEST_TIMEOUT
        return _orig_request(self, method, url, **kwargs)

    requests.Session.request = _patched_request
except Exception:
    pass

# 线程池用于实现真正的超时（对任何阻塞调用有效）
_executor = ThreadPoolExecutor(max_workers=1)


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
    """指数退避重试，用线程池实现真正的超时（对任何阻塞调用有效）

    每次调用使用独立的单线程池：若某次请求底层 socket 卡死，超时后
    用 shutdown(wait=False) 丢弃该线程池（让卡死线程自生自灭，最终被
    socket timeout 终止），避免下一个请求排队在死线程后面造成永久死锁。
    """
    for attempt in range(max_retries):
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            future = ex.submit(func)
            result = future.result(timeout=_REQUEST_TIMEOUT)
            ex.shutdown(wait=False)
            return result
        except FuturesTimeoutError:
            ex.shutdown(wait=False)  # 不等待，丢弃卡死线程
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


class TushareDownloader:

    def __init__(self):
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

    # ==================================================================
    # 公共入口
    # ==================================================================

    def init_db(self,
                start: str = DEFAULT_START,
                moneyflow_start: str = DEFAULT_START_MONEYFLOW) -> None:
        """
        全量初始化 tushare 域。

        moneyflow 从 moneyflow_start（默认2010）开始，
        其余数据从 start（默认2024）开始。
        """
        _log("=" * 60)
        _log(f"Tushare 域初始化  日线/龙虎榜/融资融券起始: {start}")
        _log(f"                  资金流向起始: {moneyflow_start}")
        _log("=" * 60)

        _log("[1/6] 初始化个股资金流向（moneyflow）...")
        self.init_moneyflow(start=moneyflow_start)

        _log("[2/6] 初始化日线行情（daily + daily_basic）...")
        self.init_daily(start=start)

        _log("[3/6] 初始化龙虎榜（top_list）...")
        self.init_top_list(start=start)

        _log("[4/6] 初始化融资融券（margin_detail）...")
        self.init_margin(start=start)

        _log("[5/6] 初始化行业资金流（moneyflow_ind）...")
        self.init_moneyflow_ind(start=start)

        _log("[6/6] 初始化大盘资金流（moneyflow_mkt）...")
        self.init_moneyflow_mkt(start=start)

        _log("Tushare 域初始化完成！")

    def update_db(self) -> None:
        """每日增量更新所有 tushare 数据"""
        today = datetime.now().strftime(_FMT_DASH)
        _log(f"Tushare 域增量更新  目标日期: {today}")

        _log("[1/6] 更新个股资金流向...")
        self._update_moneyflow()

        _log("[2/6] 更新日线行情...")
        self._update_daily()

        _log("[3/6] 更新龙虎榜...")
        self._update_top_list()

        _log("[4/6] 更新融资融券...")
        self._update_margin()

        _log("[5/6] 更新行业资金流...")
        self._update_moneyflow_ind()

        _log("[6/6] 更新大盘资金流...")
        self._update_moneyflow_mkt()

        _log("Tushare 域更新完成！")

    # ==================================================================
    # moneyflow — 个股资金流向
    # ==================================================================

    def init_moneyflow(self, start: str = DEFAULT_START_MONEYFLOW) -> None:
        """
        全量下载个股历史资金流向（2010起）。
        按股票代码逐只拉取，每只一次性拉全量历史。
        结合 ipo_dates.json 确定每只股票的实际起始日期，避免无效请求。
        """
        import json
        from pathlib import Path

        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(self._pro)
        total = len(codes)
        _log(f"  moneyflow: 共 {total} 只，起始 {start} ~ {end}")

        # 加载 ipo_dates，用于确定每只股票的实际上市日期
        ipo_path = Path(__file__).parent / "market" / "ipo_dates.json"
        ipo_dates: dict = {}
        if ipo_path.exists():
            with open(ipo_path) as f:
                raw = json.load(f)
            for k, v in raw.items():
                code6 = k.split(":")[-1]
                ipo_dates[code6] = v.get("ipo_date", "")

        # 预加载所有已有文件的 last_date，避免循环里反复读 parquet
        _log("  预加载已有数据索引...")
        existing_last: dict = {}
        for p in (Path(__file__).parent / "tushare" / "moneyflow").glob("*.parquet"):
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

            # 断点续传：用内存缓存判断，不读文件
            ld = existing_last.get(code6)
            if ld and ld >= end:
                skip += 1
                continue

            if ld:
                fetch_start = (
                    datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)
                ).strftime(_FMT_DASH)
            else:
                # 用 ipo_date 和 start 取较晚的那个，避免拉上市前的空数据
                ipo = ipo_dates.get(code6, "")
                if ipo and ipo > start:
                    fetch_start = ipo
                else:
                    fetch_start = start

            try:
                df = self._fetch_moneyflow(ts_code, fetch_start, end)
                if df is None or df.empty:
                    skip += 1
                    continue
                df["code"] = code6
                store.save("moneyflow", df, code=code6)
                # 更新内存缓存
                existing_last[code6] = end
                ok += 1
                time.sleep(0.35)
            except Exception as e:
                logger.debug(f"moneyflow {ts_code} 失败: {e}")
                fail += 1
                time.sleep(1.0)

            if (i + 1) % 200 == 0:
                elapsed = time.time() - t0
                _log(f"  进度: {i+1}/{total}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  moneyflow 完成: 新增={ok} 跳过={skip} 失败={fail}  {elapsed:.0f}s")

    def _update_moneyflow(self) -> None:
        """增量更新：只补全有缺口的股票"""
        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(self._pro)
        ok = skip = fail = 0
        t0 = time.time()

        for i, ts_code in enumerate(codes):
            code6 = ts_code.split(".")[0]
            ld = store.last_date("moneyflow", code=code6)
            if ld and ld >= end:
                skip += 1
                continue
            fetch_start = (
                (datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)).strftime(_FMT_DASH)
                if ld else DEFAULT_START_MONEYFLOW
            )
            try:
                df = self._fetch_moneyflow(ts_code, fetch_start, end)
                if df is None or df.empty:
                    skip += 1
                    continue
                df["code"] = code6
                store.save("moneyflow", df, code=code6)
                ok += 1
                time.sleep(0.35)
            except Exception as e:
                logger.debug(f"moneyflow update {ts_code} 失败: {e}")
                fail += 1
                time.sleep(1.0)

            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                _log(f"  moneyflow 进度: {i+1}/{len(codes)}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  moneyflow 更新完成: 新增={ok} 跳过={skip} 失败={fail}  {elapsed:.0f}s")

    def _fetch_moneyflow(self, ts_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """
        拉取单只股票资金流向，自动分段（Tushare 单次最多 5000 行）。
        字段：trade_date, buy_sm_vol/amount, sell_sm_vol/amount,
              buy_md/lg/elg_vol/amount, net_mf_vol/amount
        """
        all_dfs = []
        cur_start = datetime.strptime(start, _FMT_DASH)
        cur_end = datetime.strptime(end, _FMT_DASH)
        # 每次拉 1 年（约 250 个交易日），减少单次数据量降低超时风险
        chunk_days = 365

        while cur_start <= cur_end:
            seg_end = min(cur_start + timedelta(days=chunk_days), cur_end)

            def _fetch(s=cur_start, e=seg_end):
                return self._pro.moneyflow(
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
    # daily + daily_basic — 日线行情 & 每日指标
    # ==================================================================

    def init_daily(self, start: str = DEFAULT_START) -> None:
        """
        全量下载日线行情和每日指标。
        按股票代码逐只拉取，daily 和 daily_basic 同步下载。
        """
        from pathlib import Path

        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(self._pro)
        total = len(codes)
        _log(f"  daily/daily_basic: 共 {total} 只，{start} ~ {end}")

        # 预加载 daily 的 last_date 缓存
        _log("  预加载已有数据索引...")
        existing_last: dict = {}
        for p in (Path(__file__).parent / "tushare" / "daily").glob("*.parquet"):
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
                df_d, df_b = self._fetch_daily(ts_code, fetch_start, end)
                if df_d is not None and not df_d.empty:
                    store.save("daily", df_d, code=code6)
                if df_b is not None and not df_b.empty:
                    store.save("daily_basic", df_b, code=code6)
                existing_last[code6] = end
                ok += 1
                time.sleep(0.35)
            except Exception as e:
                logger.debug(f"daily {ts_code} 失败: {e}")
                fail += 1
                time.sleep(1.0)

            if (i + 1) % 200 == 0:
                elapsed = time.time() - t0
                _log(f"  进度: {i+1}/{total}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  daily 完成: 新增={ok} 跳过={skip} 失败={fail}  {elapsed:.0f}s")

    def _update_daily(self) -> None:
        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(self._pro)
        ok = skip = fail = 0
        t0 = time.time()

        for i, ts_code in enumerate(codes):
            code6 = ts_code.split(".")[0]
            ld = store.last_date("daily", code=code6)
            if ld and ld >= end:
                skip += 1
                continue
            fetch_start = (
                (datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)).strftime(_FMT_DASH)
                if ld else DEFAULT_START
            )
            try:
                df_d, df_b = self._fetch_daily(ts_code, fetch_start, end)
                if df_d is not None and not df_d.empty:
                    store.save("daily", df_d, code=code6)
                if df_b is not None and not df_b.empty:
                    store.save("daily_basic", df_b, code=code6)
                ok += 1
                time.sleep(0.35)
            except Exception as e:
                logger.debug(f"daily update {ts_code} 失败: {e}")
                fail += 1
                time.sleep(1.0)

            if (i + 1) % 500 == 0:
                elapsed = time.time() - t0
                _log(f"  daily 进度: {i+1}/{len(codes)}  新增:{ok} 跳过:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  daily 更新完成: 新增={ok} 跳过={skip} 失败={fail}  {elapsed:.0f}s")

    def _fetch_daily(self, ts_code: str, start: str, end: str):
        """返回 (df_daily, df_daily_basic) 元组"""
        def _fd():
            return self._pro.daily(
                ts_code=ts_code,
                start_date=_to_ts_date(start),
                end_date=_to_ts_date(end),
            )

        def _fb():
            return self._pro.daily_basic(
                ts_code=ts_code,
                start_date=_to_ts_date(start),
                end_date=_to_ts_date(end),
            )

        df_d = _retry(_fd)
        time.sleep(0.15)
        df_b = _retry(_fb)
        return df_d, df_b

    # ==================================================================
    # top_list — 龙虎榜
    # ==================================================================

    def init_top_list(self, start: str = DEFAULT_START) -> None:
        """
        全量下载龙虎榜，按交易日逐日拉取。
        字段：trade_date, ts_code, name, close, pct_change,
              turnover_rate, amount, l_sell, l_buy, l_amount,
              net_amount, net_rate, amount_rate, float_values, reason
        """
        end = datetime.now().strftime(_FMT_DASH)
        _log(f"  top_list: {start} ~ {end}")

        trade_dates = _retry(lambda: _get_trade_dates(self._pro, start, end))
        # 跳过已下载的日期
        existing = set(d.replace("-", "") for d in store.list_dates("top_list"))
        dates_todo = [d for d in trade_dates if d not in existing]
        _log(f"  top_list: 共 {len(trade_dates)} 个交易日，待下载 {len(dates_todo)} 个")

        ok = skip = fail = 0
        t0 = time.time()

        for i, td in enumerate(dates_todo):
            date_dash = _to_dash_date(td)
            try:
                def _fetch(d=td):
                    return self._pro.top_list(trade_date=d)

                df = _retry(_fetch)
                if df is None or df.empty:
                    # 非龙虎榜触发日，记录空文件占位（避免重复请求）
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
        end = datetime.now().strftime(_FMT_DASH)
        existing = set(d.replace("-", "") for d in store.list_dates("top_list"))

        # 找最新已有日期，从次日开始
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

        trade_dates = _retry(lambda: _get_trade_dates(self._pro, fetch_start, end))
        dates_todo = [d for d in trade_dates if d not in existing]
        if not dates_todo:
            _log("  top_list：已是最新")
            return

        ok = skip = fail = 0
        for td in dates_todo:
            date_dash = _to_dash_date(td)
            try:
                def _fetch(d=td):
                    return self._pro.top_list(trade_date=d)

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
    # margin_detail — 融资融券
    # ==================================================================

    def init_margin(self, start: str = DEFAULT_START) -> None:
        """
        全量下载融资融券明细，按股票代码逐只拉取。
        字段：trade_date, ts_code, rzye, rqye, rzmre, rqyl,
              rzche, rqchl, rqmcl, rzrqye
        """
        from pathlib import Path

        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(self._pro)
        total = len(codes)
        _log(f"  margin: 共 {total} 只，{start} ~ {end}")

        # 预加载 margin 的 last_date 缓存
        _log("  预加载已有数据索引...")
        existing_last: dict = {}
        for p in (Path(__file__).parent / "tushare" / "margin").glob("*.parquet"):
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
        end = datetime.now().strftime(_FMT_DASH)
        codes = _get_all_stock_codes(self._pro)
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
        all_dfs = []
        cur_start = datetime.strptime(start, _FMT_DASH)
        cur_end = datetime.strptime(end, _FMT_DASH)
        chunk_days = 365  # 每次拉 1 年

        while cur_start <= cur_end:
            seg_end = min(cur_start + timedelta(days=chunk_days), cur_end)

            def _fetch(s=cur_start, e=seg_end):
                return self._pro.margin_detail(
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
    # moneyflow_ind — 行业资金流（东财，按日期分片）
    # ==================================================================

    def init_moneyflow_ind(self, start: str = DEFAULT_START) -> None:
        """
        全量下载行业资金流（东财 moneyflow_ind_dc），按交易日逐日拉取。
        content_type 包含：行业 / 概念 / 地域，全部保存。
        字段：trade_date, content_type, ts_code, name, pct_change, close,
              net_amount, net_amount_rate, buy_elg/lg/md/sm_amount(_rate), rank
        """
        end = datetime.now().strftime(_FMT_DASH)
        _log(f"  moneyflow_ind: {start} ~ {end}")

        trade_dates = _retry(lambda: _get_trade_dates(self._pro, start, end))
        existing = set(d.replace("-", "") for d in store.list_dates("moneyflow_ind"))
        dates_todo = [d for d in trade_dates if d not in existing]
        _log(f"  moneyflow_ind: 共 {len(trade_dates)} 个交易日，待下载 {len(dates_todo)} 个")

        ok = skip = fail = 0
        t0 = time.time()

        for i, td in enumerate(dates_todo):
            date_dash = _to_dash_date(td)
            try:
                def _fetch(d=td):
                    return self._pro.moneyflow_ind_dc(trade_date=d)

                df = _retry(_fetch)
                if df is None or df.empty:
                    store.save("moneyflow_ind",
                               pd.DataFrame(columns=["trade_date"]),
                               date=date_dash)
                    skip += 1
                else:
                    store.save("moneyflow_ind", df, date=date_dash)
                    ok += 1
                time.sleep(0.4)
            except Exception as e:
                logger.debug(f"moneyflow_ind {td} 失败: {e}")
                fail += 1
                time.sleep(1.5)

            if (i + 1) % 100 == 0:
                elapsed = time.time() - t0
                _log(f"  进度: {i+1}/{len(dates_todo)}  有数据:{ok} 空日:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  moneyflow_ind 完成: 有数据={ok} 空日={skip} 失败={fail}  {elapsed:.0f}s")

    def _update_moneyflow_ind(self) -> None:
        """增量更新行业资金流"""
        end = datetime.now().strftime(_FMT_DASH)
        existing = set(d.replace("-", "") for d in store.list_dates("moneyflow_ind"))

        if existing:
            last = max(existing)
            fetch_start = (
                datetime.strptime(_to_dash_date(last), _FMT_DASH) + timedelta(days=1)
            ).strftime(_FMT_DASH)
        else:
            fetch_start = DEFAULT_START

        if fetch_start > end:
            _log("  moneyflow_ind：已是最新")
            return

        trade_dates = _retry(lambda: _get_trade_dates(self._pro, fetch_start, end))
        dates_todo = [d for d in trade_dates if d not in existing]
        if not dates_todo:
            _log("  moneyflow_ind：已是最新")
            return

        ok = skip = fail = 0
        for td in dates_todo:
            date_dash = _to_dash_date(td)
            try:
                def _fetch(d=td):
                    return self._pro.moneyflow_ind_dc(trade_date=d)

                df = _retry(_fetch)
                if df is None or df.empty:
                    store.save("moneyflow_ind",
                               pd.DataFrame(columns=["trade_date"]),
                               date=date_dash)
                    skip += 1
                else:
                    store.save("moneyflow_ind", df, date=date_dash)
                    ok += 1
                time.sleep(0.4)
            except Exception as e:
                logger.debug(f"moneyflow_ind update {td} 失败: {e}")
                fail += 1
                time.sleep(1.5)

        _log(f"  moneyflow_ind 更新完成: 有数据={ok} 空日={skip} 失败={fail}")

    # ==================================================================
    # moneyflow_mkt — 大盘资金流（东财，单文件）
    # ==================================================================

    def init_moneyflow_mkt(self, start: str = DEFAULT_START) -> None:
        """
        全量下载大盘资金流（东财 moneyflow_mkt_dc），按年分段批量拉取。
        字段：trade_date, close_sh/sz, pct_change_sh/sz,
              net_amount, net_amount_rate, buy_elg/lg/md/sm_amount(_rate)
        """
        end = datetime.now().strftime(_FMT_DASH)
        _log(f"  moneyflow_mkt: {start} ~ {end}")

        ld = store.last_date("moneyflow_mkt")
        fetch_start = (
            (datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)).strftime(_FMT_DASH)
            if ld else start
        )
        if fetch_start > end:
            _log("  moneyflow_mkt：已是最新")
            return

        all_dfs = []
        cur = datetime.strptime(fetch_start, _FMT_DASH)
        cur_end = datetime.strptime(end, _FMT_DASH)

        while cur <= cur_end:
            seg_end = min(cur + timedelta(days=365), cur_end)
            try:
                def _fetch(s=cur, e=seg_end):
                    return self._pro.moneyflow_mkt_dc(
                        start_date=s.strftime(_FMT),
                        end_date=e.strftime(_FMT),
                    )
                df = _retry(_fetch)
                if df is not None and not df.empty:
                    all_dfs.append(df)
                time.sleep(0.4)
            except Exception as e:
                logger.warning(f"moneyflow_mkt {cur.strftime(_FMT_DASH)} 失败: {e}")
            cur = seg_end + timedelta(days=1)

        if not all_dfs:
            _log("  moneyflow_mkt：无数据")
            return

        result = pd.concat(all_dfs, ignore_index=True)
        result = result.drop_duplicates(subset=["trade_date"])
        store.save("moneyflow_mkt", result)
        _log(f"  moneyflow_mkt 完成: {len(result)} 行")

    def _update_moneyflow_mkt(self) -> None:
        """增量更新大盘资金流"""
        end = datetime.now().strftime(_FMT_DASH)
        ld = store.last_date("moneyflow_mkt")
        fetch_start = (
            (datetime.strptime(ld, _FMT_DASH) + timedelta(days=1)).strftime(_FMT_DASH)
            if ld else DEFAULT_START
        )
        if fetch_start > end:
            _log("  moneyflow_mkt：已是最新")
            return
        try:
            def _fetch():
                return self._pro.moneyflow_mkt_dc(
                    start_date=_to_ts_date(fetch_start),
                    end_date=_to_ts_date(end),
                )
            df = _retry(_fetch)
            if df is not None and not df.empty:
                store.save("moneyflow_mkt", df)
                _log(f"  moneyflow_mkt 更新: +{len(df)} 行")
            else:
                _log("  moneyflow_mkt：无新数据")
        except Exception as e:
            logger.warning(f"moneyflow_mkt 更新失败: {e}")
