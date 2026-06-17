"""
K 线数据下载器 — Tushare 版
接口与 KlineDownloader 对齐（init_db / update_db），供主链路调用。

数据源：
  stock : pro_bar(adj='hfq') + daily_basic（pe/pb/turn）
  index : index_daily
  etf   : pro_bar(asset='FD', adj=None)

Schema 兼容 baostock 口径（store.py 零改动）：
  volume 单位：股（tushare vol 单位手，×100）
  amount 单位：元（tushare amount 单位千元，×1000）
"""
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta

import pandas as pd

import data.store as store

logger = logging.getLogger(__name__)

DEFAULT_START = "2024-01-02"

# tushare 同一接口并发控制：批间 sleep
_SLEEP = 0.3          # 每只之间最短间隔（秒）
_RETRY_SLEEP = 2.0    # 单只失败重试等待

# 单只整体 wall-clock 硬超时（秒）：
# requests 的 read timeout 在 TCP 半开/慢速分块场景下可能不触发，
# 用独立线程 + future 超时兜底，防止单只死卡拖垮整批下载。
_PER_STOCK_TIMEOUT = 45


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _to_ts_date(d: str) -> str:
    """'YYYY-MM-DD' → 'YYYYMMDD'"""
    return d.replace("-", "")


def _ts_code(code: str) -> str:
    """6位代码 → tushare ts_code（含交易所后缀）"""
    if code.startswith("6"):
        return f"{code}.SH"
    elif code.startswith(("8", "9")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _print_bar(done, total, ok, skip, fail, elapsed, prefix="下载进度", tty=True):
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
        if done % 50 == 0 or done == total:
            _log(line)


class TushareKlineDownloader:
    """
    Tushare 主链路下载器。
    参数：
      pro     — tushare.pro_api 实例（由外部传入，避免重复初始化）
      workers — 预留（当前串行，tushare 限频不适合高并发）
    """

    def __init__(self, pro, workers: int = 1):
        self._pro = pro
        self.workers = workers
        # 导入 tushare（pro_bar 需要 ts 模块级别调用）
        import tushare as ts
        self._ts = ts

    # ------------------------------------------------------------------
    # 公共接口（与 KlineDownloader 对齐）
    # ------------------------------------------------------------------

    def init_db(self,
                start: str = DEFAULT_START,
                end: str | None = None,
                force: bool = False,
                types: list[str] | None = None) -> dict:
        """全量初始化，支持断点续传"""
        end = end or _today()
        types = types or ["stock", "index", "etf"]

        _log("=" * 55)
        _log("TushareKlineDownloader — 全量初始化")
        _log(f"时间范围: {start} ~ {end}")
        _log(f"资产类型: {', '.join(types)}")
        _log("=" * 55)

        all_secs = self._get_security_list(types)
        for atype, secs in all_secs.items():
            _log(f"  {atype}: {len(secs)} 只")

        total_result = {"ok": 0, "skip": 0, "fail": 0, "total": 0}

        for atype in types:
            secs = all_secs.get(atype, [])
            if not secs:
                continue
            if not force:
                pending = [(c, n) for c, n in secs
                           if not store.exists(c, atype, market="cn")]
                skipped = len(secs) - len(pending)
                if skipped:
                    _log(f"[{atype}] 断点续传: 已有 {skipped} 只，待下载 {len(pending)} 只")
            else:
                pending = secs
                skipped = 0

            if not pending:
                _log(f"[{atype}] 已全部完成，跳过")
                continue

            result = self._batch_download(pending, atype, start=start, end=end,
                                          force=force)
            result["skip"] += skipped
            for k in ("ok", "skip", "fail", "total"):
                total_result[k] += result[k]

        _log("保存上市日期索引...")
        self._save_ipo_dates()
        return self._print_stats(total_result)

    def update_db(self,
                  end: str | None = None,
                  types: list[str] | None = None) -> dict:
        """增量更新：各证券从本地最新日期+1天补到 end"""
        end = end or _today()
        types = types or ["stock", "index", "etf"]

        _log("=" * 55)
        _log(f"TushareKlineDownloader — 增量更新  目标日期: {end}")
        _log("=" * 55)

        all_secs = self._get_security_list(types)
        total_result = {"ok": 0, "skip": 0, "fail": 0, "total": 0}

        for atype in types:
            secs = all_secs.get(atype, [])
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
            for k in ("ok", "skip", "fail", "total"):
                total_result[k] += result[k]

        _log("更新完成！")
        self._save_ipo_dates()
        return self._print_stats(total_result)

    # ------------------------------------------------------------------
    # 证券列表（tushare stock_basic）
    # ------------------------------------------------------------------

    def _get_security_list(self, types: list[str]) -> dict:
        result = {t: [] for t in types}

        if "stock" in types or "index" in types or "etf" in types:
            # 股票
            if "stock" in types:
                df = self._pro.stock_basic(
                    exchange="", list_status="L",
                    fields="ts_code,name,list_date"
                )
                for _, row in df.iterrows():
                    code = row["ts_code"].split(".")[0]
                    result["stock"].append((code, row["name"]))

            # 指数：沪深主流指数（上交所 + 深交所）
            if "index" in types:
                idx_codes = []
                for mkt in ("SSE", "SZSE"):
                    try:
                        df_idx = self._pro.index_basic(market=mkt, fields="ts_code,name")
                        idx_codes.extend(
                            (r["ts_code"].split(".")[0], r["name"])
                            for _, r in df_idx.iterrows()
                        )
                    except Exception as e:
                        logger.warning(f"index_basic {mkt} 失败: {e}")
                result["index"] = idx_codes

            # ETF：fund_basic list_status=L type=ETF
            if "etf" in types:
                try:
                    df_etf = self._pro.fund_basic(
                        status="L", market="E",
                        fields="ts_code,name"
                    )
                    result["etf"] = [
                        (r["ts_code"].split(".")[0], r["name"])
                        for _, r in df_etf.iterrows()
                    ]
                except Exception as e:
                    logger.warning(f"fund_basic 失败: {e}")

        return result

    # ------------------------------------------------------------------
    # 批量下载
    # ------------------------------------------------------------------

    def _batch_download(self, stocks: list[tuple[str, str]],
                        asset_type: str,
                        end: str,
                        start: str | None = None,
                        start_map: dict | None = None,
                        force: bool = False) -> dict:
        total = len(stocks)
        ok = skip = fail = 0
        t0 = time.time()
        is_tty = sys.stdout.isatty()

        _print_bar(0, total, ok, skip, fail, 0, prefix=f"[{asset_type}]", tty=is_tty)

        for code, _name in stocks:
            s = (start_map or {}).get(code, start)
            try:
                status = self._download_one_timed(code, asset_type, s, end, force=force)
            except FutureTimeout:
                logger.warning(f"{code} 下载超过 {_PER_STOCK_TIMEOUT}s，跳过")
                status = "fail"
            except Exception as e:
                logger.warning(f"{code} 异常: {e}")
                status = "fail"

            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1

            done = ok + skip + fail
            _print_bar(done, total, ok, skip, fail,
                       time.time() - t0, prefix=f"[{asset_type}]", tty=is_tty)
            time.sleep(_SLEEP)

        if not is_tty:
            _log(f"[{asset_type}] 完成: 新增={ok} 跳过={skip} 失败={fail}")
        else:
            print()

        return {"ok": ok, "skip": skip, "fail": fail, "total": total}

    # ------------------------------------------------------------------
    # 单只下载
    # ------------------------------------------------------------------

    def _download_one_timed(self, code: str, asset_type: str,
                            start: str, end: str, force: bool = False) -> str:
        """带 wall-clock 硬超时的单只下载。

        在独立单线程 executor 中执行 _download_one，超过 _PER_STOCK_TIMEOUT
        秒抛 FutureTimeout（由调用方捕获判 fail）。每只用独立 executor，
        超时后不阻塞等待残留线程（第三方库内部阻塞无法强杀），后台自行结束。
        """
        ex = ThreadPoolExecutor(max_workers=1)
        future = ex.submit(self._download_one, code, asset_type, start, end, force=force)
        try:
            return future.result(timeout=_PER_STOCK_TIMEOUT)
        finally:
            ex.shutdown(wait=False)

    def _download_one(self, code: str, asset_type: str,
                      start: str, end: str, force: bool = False) -> str:
        if start and end and start > end:
            return "skip"

        sd, ed = _to_ts_date(start), _to_ts_date(end)
        tscode = _ts_code(code)

        if asset_type == "stock":
            df = self._download_stock(tscode, code, sd, ed)
        elif asset_type == "index":
            # 指数市场后缀：0/8/9 开头 → SH；其余 → SZ
            idx_suffix = "SH" if code[0] in ("0", "8", "9") else "SZ"
            df = self._download_index(f"{code}.{idx_suffix}", sd, ed)
        else:  # etf
            # ETF 市场后缀：5 开头 → SH；1 开头 → SZ
            etf_suffix = "SH" if code.startswith("5") else "SZ"
            df = self._download_etf(f"{code}.{etf_suffix}", sd, ed)

        if df is None or df.empty:
            return "skip"

        # force=True 时覆盖已有日期（迁移重下场景，新 tushare 口径替换旧 baostock）
        store.save(code, df, asset_type=asset_type, market="cn", force=force)
        return "ok"

    def _download_stock(self, tscode: str, code: str,
                        sd: str, ed: str) -> pd.DataFrame | None:
        """stock：pro_bar(hfq) + daily_basic merge"""
        for attempt in range(3):
            try:
                df = self._ts.pro_bar(
                    ts_code=tscode, adj="hfq",
                    start_date=sd, end_date=ed,
                    ma=[], factors=[],
                    api=self._pro,   # 复用统一 client（含可控超时）
                )
                break
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"pro_bar {tscode} 失败: {e}")
                    return None
                time.sleep(_RETRY_SLEEP)

        if df is None or df.empty:
            return None

        df = df.rename(columns={
            "trade_date": "date",
            "vol": "volume",
            "pre_close": "preclose",
        })
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100   # 手→股
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1000  # 千元→元
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        df = df.sort_values("date").reset_index(drop=True)

        # 拉取 daily_basic（pe/pb/turn）
        try:
            time.sleep(_SLEEP)
            db = self._pro.daily_basic(
                ts_code=tscode, start_date=sd, end_date=ed,
                fields="trade_date,pe_ttm,pb,ps_ttm,pcf_ncf_ttm,turnover_rate"
            )
            if db is not None and not db.empty:
                db = db.rename(columns={
                    "trade_date": "date",
                    "pb": "pb_mrq",
                    "pcf_ncf_ttm": "pcf_ttm",
                    "turnover_rate": "turn",
                })
                db["date"] = pd.to_datetime(db["date"], format="%Y%m%d")
                # 只 merge 实际返回的估值列（pcf_ncf_ttm 等可能缺失）
                basic_cols = [c for c in ["pe_ttm", "pb_mrq", "ps_ttm",
                                          "pcf_ttm", "turn"] if c in db.columns]
                df = df.merge(db[["date", *basic_cols]], on="date", how="left")
        except Exception as e:
            logger.debug(f"daily_basic {tscode} 失败（忽略）: {e}")

        # trade_status 暂全量置 "1"（停牌用 suspend_d 后续覆写）
        # 注：保持字符串类型，与 baostock 口径及 store.STR_COLS 一致
        df["trade_status"] = "1"

        # is_st：暂全量置 "0"（精确值可后续用 namechange 覆写）
        df["is_st"] = "0"

        return df

    def _download_index(self, tscode: str, sd: str, ed: str) -> pd.DataFrame | None:
        """index：index_daily，preclose 由 close.shift(1) 补"""
        for attempt in range(3):
            try:
                df = self._pro.index_daily(
                    ts_code=tscode, start_date=sd, end_date=ed
                )
                break
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"index_daily {tscode} 失败: {e}")
                    return None
                time.sleep(_RETRY_SLEEP)

        if df is None or df.empty:
            return None

        df = df.rename(columns={
            "trade_date": "date",
            "vol": "volume",
        })
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100
        df["amount"] = pd.to_numeric(df.get("amount", pd.Series(dtype=float)),
                                     errors="coerce") * 1000
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        df = df.sort_values("date").reset_index(drop=True)

        # preclose = 前日 close（index_daily 无此字段时自行 shift）
        if "pre_close" in df.columns:
            df = df.rename(columns={"pre_close": "preclose"})
        else:
            df["preclose"] = df["close"].shift(1)

        return df

    def _download_etf(self, tscode: str, sd: str, ed: str) -> pd.DataFrame | None:
        """ETF：pro_bar(asset='FD', adj=None)，不复权"""
        for attempt in range(3):
            try:
                df = self._ts.pro_bar(
                    ts_code=tscode, asset="FD", adj=None,
                    start_date=sd, end_date=ed,
                    ma=[], factors=[],
                    api=self._pro,   # 复用统一 client（含可控超时）
                )
                break
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"pro_bar ETF {tscode} 失败: {e}")
                    return None
                time.sleep(_RETRY_SLEEP)

        if df is None or df.empty:
            return None

        df = df.rename(columns={
            "trade_date": "date",
            "vol": "volume",
            "pre_close": "preclose",
        })
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1000
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        df = df.sort_values("date").reset_index(drop=True)

        if "preclose" not in df.columns:
            df["preclose"] = df["close"].shift(1)

        return df

    # ------------------------------------------------------------------
    # 上市日期索引（tushare stock_basic）
    # ------------------------------------------------------------------

    def _save_ipo_dates(self) -> None:
        try:
            df = self._pro.stock_basic(
                exchange="", list_status="L",
                fields="ts_code,name,list_date"
            )
            ipo = {}
            for _, row in df.iterrows():
                tscode = row["ts_code"]
                code = tscode.split(".")[0]
                ipo_date = row.get("list_date", "")
                if not ipo_date:
                    continue
                # 统一格式 YYYYMMDD → YYYY-MM-DD
                if len(ipo_date) == 8:
                    ipo_date = f"{ipo_date[:4]}-{ipo_date[4:6]}-{ipo_date[6:]}"
                ipo[f"cn:{code}"] = {
                    "ipo_date": ipo_date,
                    "market": "cn",
                    "type": "stock",
                    "name": row.get("name", ""),
                    "full_code": tscode,
                }
            store.save_ipo_dates(ipo)
            _log(f"上市日期已保存，共 {len(ipo)} 只")
        except Exception as e:
            logger.warning(f"保存上市日期失败: {e}")

    # ------------------------------------------------------------------
    # 统计输出
    # ------------------------------------------------------------------

    def _print_stats(self, result: dict | None = None) -> dict:
        s = store.stats()
        cn = s.get("cn", {})
        _log("本地数据库统计:")
        for atype in ("stock", "index", "etf"):
            info = cn.get(atype, {})
            _log(f"  {atype:6s}: {info.get('count', 0):5d}只  "
                 f"{info.get('rows', 0):>10,}行  {info.get('size_mb', 0):.1f}MB")
        _log(f"  {'合计':6s}: {s['total']['count']:5d}只  "
             f"{s['total']['rows']:>10,}行  {s['total']['size_mb']:.1f}MB")
        if result:
            _log(f"本次: 新增={result.get('ok', 0)} "
                 f"跳过={result.get('skip', 0)} "
                 f"失败={result.get('fail', 0)}")
        return {**s, **(result or {})}
