"""
K 线数据下载器（多资产全字段版）

支持：A股 / 指数 / ETF
字段：OHLCV + 换手率 + PE/PB/PS/PCF + isST（股票专有）

用法：
    dl = KlineDownloader(fetcher)
    dl.init_db()     # 首次全量，断点续传
    dl.update_db()   # 每日增量
"""
import logging
import sys
import time
from datetime import datetime, timedelta

import data.store as store
from data.fetcher import DataFetcher

logger = logging.getLogger(__name__)

DEFAULT_START = "2024-01-02"

# baostock type -> asset_type 映射
TYPE_MAP = {"1": "stock", "2": "index", "5": "etf"}

# 各类型请求字段
FIELDS = {
    "stock": ("date,open,high,low,close,preclose,volume,amount,"
              "turn,tradestatus,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"),
    "index": "date,open,high,low,close,preclose,volume,amount,pctChg",
    "etf":   "date,open,high,low,close,preclose,volume,amount,pctChg",
}

# store 列名映射（baostock 字段 -> store 列名）
COL_RENAME = {
    "tradestatus": "trade_status",
    "pctChg":      "pct_chg",
    "peTTM":       "pe_ttm",
    "pbMRQ":       "pb_mrq",
    "psTTM":       "ps_ttm",
    "pcfNcfTTM":   "pcf_ttm",
    "isST":        "is_st",
}


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# 已知 A 股节假日（2024-2026，不含周末，需手动补充）
_CHINA_HOLIDAYS: set[str] = set()
def _register_holidays(year: int):
    """登记已知节假日到全局集合（可按需扩展）"""
    holidays: dict[int, list[str]] = {
        2024: [
            "2024-01-01", "2024-02-12", "2024-02-13", "2024-02-14",
            "2024-02-15", "2024-02-16", "2024-04-04", "2024-04-05",
            "2024-04-29", "2024-04-30", "2024-05-01", "2024-05-02",
            "2024-05-03", "2024-06-10", "2024-09-16", "2024-09-17",
            "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04",
            "2024-10-07", "2024-10-08",
        ],
        2025: [
            "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30",
            "2025-01-31", "2025-02-03", "2025-02-04", "2025-04-04",
            "2025-05-01", "2025-05-02", "2025-05-05", "2025-06-02",
            "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06",
            "2025-10-07", "2025-10-08",
        ],
    }
    for d in holidays.get(year, []):
        _CHINA_HOLIDAYS.add(d)


def _is_trading_day(date_str: str) -> bool:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    if d.weekday() >= 5:
        return False
    _register_holidays(d.year)
    return date_str not in _CHINA_HOLIDAYS


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _print_bar(done: int, total: int, ok: int, skip: int, fail: int,
               elapsed: float, prefix: str = "下载进度", tty: bool = True):
    pct = done / max(total, 1)
    bar = "█" * int(28 * pct) + "░" * (28 - int(28 * pct))
    eta = elapsed / done * (total - done) if done > 0 else 0
    line = (f"{prefix} [{bar}] {done}/{total}"
            f"  新增:{ok} 跳过:{skip} 失败:{fail}"
            f"  已用:{elapsed:.0f}s ETA:{eta:.0f}s")
    if tty:
        sys.stdout.write(f"\r{line}   ")
        sys.stdout.flush()
    else:
        if done % 200 == 0 or done == total:
            _log(line)


class KlineDownloader:

    def __init__(self, fetcher: DataFetcher, workers: int = 1):
        self.fetcher = fetcher
        self.workers = workers

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def init_db(self,
                start: str = DEFAULT_START,
                end: str | None = None,
                force: bool = False,
                types: list[str] | None = None) -> dict:
        """
        全量初始化下载，支持断点续传。
        types: 下载类型列表，默认 ['stock','index','etf']
        """
        end = end or _today()
        types = types or ["stock", "index", "etf"]

        _log("=" * 55)
        _log("Alpha量化 K线数据库初始化（全资产版）")
        _log(f"时间范围: {start} ~ {end}")
        _log(f"资产类型: {', '.join(types)}")
        _log(f"存储目录: {store.KLINE_DIR}")
        _log("=" * 55)

        _log("[1/3] 获取全市场证券列表（baostock）...")
        all_securities = self._get_security_list(types)
        for atype, secs in all_securities.items():
            _log(f"  {atype}: {len(secs)} 只")

        total_result = {"ok": 0, "skip": 0, "fail": 0, "total": 0}

        for atype in types:
            secs = all_securities.get(atype, [])
            if not secs:
                continue

            if not force:
                pending = [(c, n) for c, n in secs if not store.exists(c, atype, market="cn")]
                skipped = len(secs) - len(pending)
                if skipped:
                    _log(f"[{atype}] 断点续传: 已有 {skipped} 只，待下载 {len(pending)} 只")
            else:
                pending = secs
                skipped = 0

            if not pending:
                _log(f"[{atype}] 已全部完成，跳过")
                continue

            _log(f"[2/3] 开始下载 {atype}，共 {len(pending)} 只...")
            result = self._batch_download(pending, atype, end=end, start=start)
            result["skip"] += skipped
            total_result["ok"]    += result["ok"]
            total_result["skip"]  += result["skip"]
            total_result["fail"]  += result["fail"]
            total_result["total"] += result["total"]

        _log("[3/3] 下载完成！保存上市日期索引...")
        self._save_ipo_dates()
        return self._print_stats(total_result)

    def update_db(self,
                  end: str | None = None,
                  types: list[str] | None = None) -> dict:
        """增量更新：每只证券从本地最新日期+1天补到今天"""
        end = end or _today()
        types = types or ["stock", "index", "etf"]

        if not _is_trading_day(end):
            _log(f"{end} 非交易日，跳过更新。")
            return {}

        _log("=" * 55)
        _log(f"Alpha量化 K线增量更新  目标日期: {end}")
        _log("=" * 55)

        _log("[1/3] 获取全市场证券列表...")
        all_securities = self._get_security_list(types)

        total_result = {"ok": 0, "skip": 0, "fail": 0, "total": 0}

        for atype in types:
            secs = all_securities.get(atype, [])
            tasks = []
            already_latest = 0
            for code, name in secs:
                ld = store.last_date(code, atype, market="cn")
                if ld is None:
                    fetch_start = DEFAULT_START
                else:
                    next_day = (datetime.strptime(ld, "%Y-%m-%d") +
                                timedelta(days=1)).strftime("%Y-%m-%d")
                    if next_day > end:
                        already_latest += 1
                        continue
                    fetch_start = next_day
                # 再次确认 fetch_start 不晚于 end
                if fetch_start > end:
                    already_latest += 1
                    continue
                tasks.append((code, name, fetch_start))

            _log(f"[{atype}] 已最新: {already_latest}  需更新: {len(tasks)}")
            if not tasks:
                continue

            result = self._batch_download(
                [(c, n) for c, n, _ in tasks], atype, end=end,
                start_map={c: s for c, _, s in tasks},
            )
            total_result["ok"]    += result["ok"]
            total_result["skip"]  += result["skip"]
            total_result["fail"]  += result["fail"]
            total_result["total"] += result["total"]

        _log("[3/3] 更新完成！")
        self._save_ipo_dates()
        return self._print_stats(total_result)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _set_bs_timeout(self, seconds: float) -> None:
        """给 baostock 已连接的 socket 设置超时"""
        try:
            import baostock.common.context as bs_ctx
            sock = getattr(bs_ctx, "default_socket", None)
            if sock:
                sock.settimeout(seconds)
        except Exception:
            pass

    def _get_security_list(self, types: list[str]) -> dict:
        """从 baostock 获取各类型证券列表，返回 {atype: [(code, name), ...]}"""
        import baostock as bs
        import socket
        socket.setdefaulttimeout(60)
        bs.login()
        self._set_bs_timeout(60)
        socket.setdefaulttimeout(None)
        try:
            rs = bs.query_stock_basic()
            result = {t: [] for t in types}
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                full_code, name, _ipo_date, _out_date, typ, status = row
                if status != "1":
                    continue
                atype = TYPE_MAP.get(typ)
                if atype not in types:
                    continue
                code = full_code.split(".")[1]
                if atype == "stock" and code[0] not in ("0", "3", "6"):
                    continue
                result[atype].append((code, name))
            return result
        finally:
            bs.logout()
            socket.setdefaulttimeout(None)

    def _batch_download(self, stocks: list[tuple[str, str]],
                        asset_type: str,
                        end: str,
                        start: str | None = None,
                        start_map: dict | None = None) -> dict:
        """login 一次，串行下载整批，不重连"""
        import baostock as bs
        import socket

        total = len(stocks)
        ok = skip = fail = 0
        t0 = time.time()
        is_tty = sys.stdout.isatty()

        # 先 login，再给已连接的 socket 设置 timeout
        # 等待 3 秒，避免与上一次 logout 间隔太短触发服务器限流
        time.sleep(3)
        socket.setdefaulttimeout(10)
        bs.login()
        self._set_bs_timeout(10)

        _print_bar(0, total, ok, skip, fail, 0,
                   prefix=f"[{asset_type}]", tty=is_tty)
        consecutive_fails = 0
        try:
            for code, _name in stocks:
                s = (start_map or {}).get(code, start)
                status = self._download_one(bs, code, asset_type, s, end)
                if status == "ok":
                    ok += 1
                    consecutive_fails = 0
                elif status == "skip":
                    skip += 1
                    consecutive_fails = 0
                else:
                    fail += 1
                    consecutive_fails += 1
                    # 连续失败 3 次，重新登录 baostock
                    if consecutive_fails >= 3:
                        logger.warning(f"连续失败 {consecutive_fails} 次，重新登录 baostock")
                        try:
                            bs.logout()
                        except Exception:
                            pass
                        time.sleep(3)  # 等待后再重连，避免限流
                        bs.login()
                        self._set_bs_timeout(10)  # 重连后重新设置 timeout
                        consecutive_fails = 0
                done = ok + skip + fail
                _print_bar(done, total, ok, skip, fail,
                           time.time() - t0,
                           prefix=f"[{asset_type}]", tty=is_tty)
        finally:
            bs.logout()

        if not is_tty:
            _log(f"[{asset_type}] 完成: 新增={ok} 跳过={skip} 失败={fail}")
        else:
            print()  # 换行

        return {"ok": ok, "skip": skip, "fail": fail, "total": total}

    def _download_one(self, bs, code: str, asset_type: str,
                      start: str, end: str) -> str:
        """下载单只证券并写入本地库"""
        # 日期合法性校验：start 不能晚于 end
        if start and end and start > end:
            logger.debug(f"{code} 跳过：start={start} > end={end}")
            return "skip"

        market = "sh" if code.startswith("6") or code.startswith("5") \
                      or (code.startswith("0") and asset_type == "index") \
                 else "sz"
        # 指数代码前缀修正
        if asset_type == "index":
            market = "sh" if code.startswith("0") or code.startswith("9") else "sz"

        bs_code = f"{market}.{code}"
        fields = FIELDS[asset_type]

        # socket timeout 已在 _batch_download 的 login 前统一设置
        try:
            rs = bs.query_history_k_data_plus(
                bs_code, fields,
                start_date=start, end_date=end,
                frequency="d", adjustflag="1",
            )
            if rs.error_code != "0":
                logger.debug(f"{bs_code} error: {rs.error_code} {rs.error_msg}")
                return "fail"
            data = []
            while rs.next():
                data.append(rs.get_row_data())
        except Exception as e:
            logger.warning(f"{bs_code} 查询异常: {e}")
            return "fail"

        if not data:
            return "skip"

        import pandas as pd
        df = pd.DataFrame(data, columns=rs.fields)
        df = df.rename(columns=COL_RENAME)
        df["date"] = pd.to_datetime(df["date"])

        # 数值转换（跳过字符串类型列）
        str_cols = {"trade_status", "is_st", "code"}
        for col in df.columns:
            if col not in str_cols and col != "date":
                df[col] = pd.to_numeric(df[col], errors="coerce")

        store.save(code, df, asset_type=asset_type, market="cn")
        return "ok"

    def _save_ipo_dates(self) -> None:
        """从 baostock 批量拉取上市日期并保存"""
        import baostock as bs
        import socket
        socket.setdefaulttimeout(60)
        bs.login()
        self._set_bs_timeout(60)
        socket.setdefaulttimeout(None)
        try:
            rs = bs.query_stock_basic()
            ipo = {}
            while rs.error_code == "0" and rs.next():
                full_code, name, ipo_date, _, typ, _status = rs.get_row_data()
                if not ipo_date:
                    continue
                atype = TYPE_MAP.get(typ, "other")
                code = full_code.split(".")[1]
                key = f"cn:{code}"          # 统一用 market:code 格式
                ipo[key] = {
                    "ipo_date": ipo_date,
                    "market": "cn",
                    "type": atype,
                    "name": name,
                    "full_code": full_code,
                }
            store.save_ipo_dates(ipo)
            _log(f"上市日期已保存，共 {len(ipo)} 只")
        except Exception as e:
            logger.warning(f"保存上市日期失败: {e}")
        finally:
            bs.logout()

    def _print_stats(self, result: dict | None = None) -> dict:
        s = store.stats()
        cn = s.get("cn", {})
        _log("本地数据库统计:")
        for atype in ("stock", "index", "etf"):
            info = cn.get(atype, {})
            _log(f"  {atype:6s}: {info.get('count',0):5d}只  "
                 f"{info.get('rows',0):>10,}行  {info.get('size_mb',0):.1f}MB")
        _log(f"  {'合计':6s}: {s['total']['count']:5d}只  "
             f"{s['total']['rows']:>10,}行  {s['total']['size_mb']:.1f}MB")
        if result:
            _log(f"本次: 新增={result.get('ok',0)} "
                 f"跳过={result.get('skip',0)} "
                 f"失败={result.get('fail',0)}")
        return {**s, **(result or {})}
