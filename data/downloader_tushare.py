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
from data.tushare_utils import call_nonempty, TushareEmptyError

logger = logging.getLogger(__name__)

DEFAULT_START = "2024-01-02"

# A 股当日 EOD 数据落定时点：早于该时点运行只更新到上一交易日，
# 避免盘中/刚收盘时写入残缺 bar（与港股 _settled_end 逻辑对齐）。
_CN_SETTLE_TIME = "16:00"

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


def _index_ts_code(code: str) -> str:
    """指数代码 → tushare ts_code（0/8/9 开头归上交所，其余深交所）"""
    return f"{code}.{'SH' if str(code)[0] in ('0', '8', '9') else 'SZ'}"


def _etf_ts_code(code: str) -> str:
    """ETF 代码 → tushare ts_code（5 开头上交所，其余深交所）"""
    return f"{code}.{'SH' if str(code).startswith('5') else 'SZ'}"


_PRICE_COLS = ("open", "high", "low", "close", "preclose")

# Tushare index_dailybasic 目前覆盖的主要宽基/规模指数
_INDEX_VALUATION_CODES = {
    "000001", "000005", "000006", "000016", "000300", "000688", "000852",
    "000905", "399001", "399005", "399006", "399016", "399300", "399673",
    "399905",
}


def _apply_fund_adj(df: pd.DataFrame, factors: pd.Series,
                    latest: float) -> pd.DataFrame:
    """按 fund_adj 因子做后复权：price * factor / latest_factor。

    factors 为按日期索引的原始复权因子；latest 为最新因子。
    传入的 df 必须是未复权原始价格（有 date 列或 DatetimeIndex）。
    """
    if df is None or df.empty or factors is None or factors.empty:
        return df
    latest = float(latest)
    if not latest or latest != latest:
        return df
    out = df.copy()
    if "date" in out.columns:
        dates = pd.to_datetime(out["date"])
        f = factors.reindex(dates).ffill().fillna(factors.iloc[0])
        out["adj_factor"] = f.to_numpy()
        ratio = (f / latest).to_numpy()
    else:
        f = factors.reindex(out.index).ffill().fillna(factors.iloc[0])
        out["adj_factor"] = f
        ratio = f / latest
    for col in _PRICE_COLS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce") * ratio
    return out


def _rescale_price_cols(df: pd.DataFrame, ratio: float) -> pd.DataFrame:
    """把已复权价格从旧 latest_factor 缩放到新 latest_factor。"""
    out = df.copy()
    for col in _PRICE_COLS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce") * ratio
    return out


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
    # 收盘落定保护
    # ------------------------------------------------------------------

    def _settled_end(self, end: str) -> str:
        """把目标日期收敛到"已收盘落定"的最近交易日。

        - end 早于今天：原样返回（补历史/指定日期不受影响）
        - 今天 16:00 后：允许更新到今天
        - 今天 16:00 前：回退到上一交易日（盘中运行不写残缺 bar）
        """
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if end < today:
            return end
        if now.strftime("%H:%M") >= _CN_SETTLE_TIME:
            return min(end, today)

        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            start = (now - timedelta(days=20)).strftime("%Y-%m-%d")
            df = self._pro.trade_cal(
                exchange="SSE", start_date=_to_ts_date(start),
                end_date=_to_ts_date(yesterday), is_open="1",
                fields="cal_date",
            )
            if df is not None and not df.empty:
                return _to_dash_date(str(df["cal_date"].max()))
        except Exception as e:
            logger.warning(f"trade_cal 获取失败，按昨日回退: {e}")
        return yesterday

    # ------------------------------------------------------------------
    # 公共接口（与 KlineDownloader 对齐）
    # ------------------------------------------------------------------

    def init_db(self,
                start: str = DEFAULT_START,
                end: str | None = None,
                force: bool = False,
                types: list[str] | None = None) -> dict:
        """全量初始化，支持断点续传"""
        requested_end = end or _today()
        end = self._settled_end(requested_end)
        if end != requested_end:
            _log(f"[提示] A股当日数据需收盘后落定，目标日期由 {requested_end} 回退至 {end}")
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
        requested_end = end or _today()
        end = self._settled_end(requested_end)
        if end != requested_end:
            _log(f"[提示] A股当日数据需收盘后落定，目标日期由 {requested_end} 回退至 {end}")
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

    def refresh_recent(self, days: int = 5,
                       types: list[str] | None = None,
                       codes: list[str] | None = None) -> dict:
        """强制重下最近 N 个自然日并覆盖已有行（修复残缺/异常 K 线）。

        与 update_db 不同：使用 force=True 覆盖同日期旧数据，适合在怀疑
        盘中残缺 bar、复权口径变更或个别数据异常后做定向修复。
        """
        types = types or ["stock"]
        days = max(int(days), 1)
        end = self._settled_end(_today())
        start = (datetime.strptime(end, "%Y-%m-%d")
                 - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        wanted = {str(c) for c in codes} if codes else None

        _log("=" * 55)
        _log(f"TushareKlineDownloader — 强制刷新最近数据  {start} ~ {end}")
        _log(f"资产类型: {', '.join(types)}"
             + (f"  指定代码: {len(wanted)} 只" if wanted else ""))
        _log("=" * 55)

        all_secs = self._get_security_list(types)
        total_result = {"ok": 0, "skip": 0, "fail": 0, "total": 0}
        for atype in types:
            secs = all_secs.get(atype, [])
            if wanted:
                secs = [(c, n) for c, n in secs if c in wanted]
            _log(f"[{atype}] 待刷新: {len(secs)} 只")
            if not secs:
                continue
            result = self._batch_download(
                secs, atype, start=start, end=end, force=True)
            for k in ("ok", "skip", "fail", "total"):
                total_result[k] += result[k]

        _log("强制刷新完成！")
        self._save_ipo_dates()
        return self._print_stats(total_result)

    # ------------------------------------------------------------------
    # 证券列表（tushare stock_basic）
    # ------------------------------------------------------------------

    def _get_security_list(self, types: list[str]) -> dict:
        """获取待更新证券列表。

        Tushare 在 HTTP 非 2xx 时会静默返回空表，故基础列表一律走
        call_nonempty：重试耗尽仍为空则抛错，避免空跑被当成"无需更新"。
        """
        result = {t: [] for t in types}
        min_rows = {"stock": 1000, "index": 100, "etf": 100}

        if "stock" in types:
            df = call_nonempty(
                lambda: self._pro.stock_basic(
                    exchange="", list_status="L",
                    fields="ts_code,name,list_date"
                ),
                label="stock_basic", min_rows=min_rows["stock"],
            )
            for _, row in df.iterrows():
                code = row["ts_code"].split(".")[0]
                result["stock"].append((code, row["name"]))

        if "index" in types:
            idx_codes = []
            for mkt in ("SSE", "SZSE"):
                df_idx = call_nonempty(
                    lambda m=mkt: self._pro.index_basic(
                        market=m, fields="ts_code,name"),
                    label=f"index_basic({mkt})", min_rows=10,
                )
                idx_codes.extend(
                    (r["ts_code"].split(".")[0], r["name"])
                    for _, r in df_idx.iterrows()
                )
            result["index"] = idx_codes

        if "etf" in types:
            df_etf = call_nonempty(
                lambda: self._pro.fund_basic(
                    status="L", market="E", fields="ts_code,name"),
                label="fund_basic", min_rows=min_rows["etf"],
            )
            result["etf"] = [
                (r["ts_code"].split(".")[0], r["name"])
                for _, r in df_etf.iterrows()
            ]

        for atype in types:
            n = len(result.get(atype, []))
            if n < min_rows.get(atype, 1):
                raise TushareEmptyError(
                    f"{atype} 证券列表过少（{n} 只），疑似 Tushare 异常")
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
        """index：index_daily + index_dailybasic（pe_ttm/pb_mrq）"""
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

        # 指数估值（Tushare index_dailybasic；仅主要宽基指数支持）
        code = tscode.split(".")[0]
        if code in _INDEX_VALUATION_CODES:
            try:
                time.sleep(_SLEEP)
                db = self._pro.index_dailybasic(
                    ts_code=tscode, start_date=sd, end_date=ed,
                    fields="trade_date,pe_ttm,pb",
                )
                if db is not None and not db.empty:
                    db = db.rename(columns={"trade_date": "date", "pb": "pb_mrq"})
                    db["date"] = pd.to_datetime(db["date"], format="%Y%m%d")
                    keep = [c for c in ("pe_ttm", "pb_mrq") if c in db.columns]
                    df = df.merge(db[["date", *keep]], on="date", how="left")
            except Exception as e:
                logger.debug(f"index_dailybasic {tscode} 失败（忽略）: {e}")

        return df

    def _fetch_fund_adj(self, tscode: str) -> pd.Series | None:
        """获取单只 ETF 的完整原始复权因子（trade_date → adj_factor）。"""
        try:
            adj = self._pro.fund_adj(ts_code=tscode)
        except Exception as e:
            logger.warning(f"fund_adj {tscode} 失败: {e}")
            return None
        if adj is None or adj.empty or "adj_factor" not in adj.columns:
            return None
        adj = adj.copy()
        adj["trade_date"] = pd.to_datetime(adj["trade_date"], format="%Y%m%d")
        s = (adj.sort_values("trade_date")
             .set_index("trade_date")["adj_factor"]
             .astype(float))
        return s[~s.index.duplicated(keep="last")]

    def _download_etf(self, tscode: str, sd: str, ed: str) -> pd.DataFrame | None:
        """ETF：pro_bar(asset='FD', adj=None) + fund_adj 后复权"""
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

        # 后复权：份额折算/分红会造成不复权价格断层，统一按最新因子缩放
        code = tscode.split(".")[0]
        factors = self._fetch_fund_adj(tscode)
        if factors is None or factors.empty:
            logger.warning(f"fund_adj {tscode} 无数据，ETF 保持不复权")
            return df

        latest = float(factors.iloc[-1])
        existing = store.load(code, asset_type="etf", market="cn")
        if existing is not None and not existing.empty:
            has_adj = ("adj_factor" in existing.columns
                       and existing["adj_factor"].notna().any())
            if not has_adj:
                # 兼容历史存量：旧文件为不复权，先整体补复权
                existing = _apply_fund_adj(existing, factors, latest)
                store.replace(code, existing, asset_type="etf", market="cn")
            else:
                old_latest = float(existing["adj_factor"].dropna().iloc[-1])
                if old_latest and abs(latest / old_latest - 1.0) > 1e-9:
                    # 期间发生新折算：把已复权价格从旧基准缩放到新基准
                    existing = _rescale_price_cols(existing, old_latest / latest)
                    store.replace(code, existing, asset_type="etf", market="cn")

        return _apply_fund_adj(df, factors, latest)

    # ------------------------------------------------------------------
    # 存量维护：ETF 复权 / 指数估值
    # ------------------------------------------------------------------

    def repair_etf_adjust(self, codes: list[str] | None = None,
                          sleep: float = 0.35) -> dict:
        """回填/刷新本地 ETF 后复权（不重新下载行情）。

        旧文件无 adj_factor 列：视为不复权，按完整 fund_adj 因子缩放。
        已复权文件：若最新因子变化，则把历史价格缩放到新基准。
        """
        codes = codes or store.list_codes("etf", market="cn")
        result = {"ok": 0, "skip": 0, "fail": 0, "total": len(codes)}
        for i, code in enumerate(codes):
            try:
                df = store.load(code, asset_type="etf", market="cn")
                if df is None or df.empty:
                    result["skip"] += 1
                    continue
                factors = self._fetch_fund_adj(_etf_ts_code(code))
                if factors is None or factors.empty:
                    result["skip"] += 1
                    continue
                latest = float(factors.iloc[-1])
                if ("adj_factor" in df.columns
                        and df["adj_factor"].notna().any()):
                    old_latest = float(df["adj_factor"].dropna().iloc[-1])
                    if old_latest and abs(latest / old_latest - 1.0) > 1e-9:
                        df = _rescale_price_cols(df, old_latest / latest)
                else:
                    df = _apply_fund_adj(df, factors, latest)
                store.replace(code, df, asset_type="etf", market="cn")
                result["ok"] += 1
            except Exception as e:
                logger.warning(f"ETF 复权修复 {code} 失败: {e}")
                result["fail"] += 1
            if (i + 1) % 200 == 0 or i + 1 == len(codes):
                _log(f"  ETF 复权修复进度: {i+1}/{len(codes)} "
                     f"成功={result['ok']} 跳过={result['skip']} "
                     f"失败={result['fail']}")
            if sleep:
                time.sleep(sleep)
        return result

    def update_index_valuation(self, codes: list[str] | None = None,
                               start: str | None = None,
                               end: str | None = None,
                               sleep: float = 0.5) -> dict:
        """回填本地指数 PE_TTM/PB（Tushare index_dailybasic）。

        默认只处理 Tushare 支持的主要宽基指数，避免对大量不支持的
        细分/主题指数空跑并触发接口限频；可用 codes 显式指定其他代码。
        """
        if codes is None:
            local = set(store.list_codes("index", market="cn"))
            codes = sorted(local & _INDEX_VALUATION_CODES)
        else:
            codes = list(codes)
        result = {"ok": 0, "skip": 0, "fail": 0, "total": len(codes)}
        for i, code in enumerate(codes):
            try:
                # 仅处理标准 6 位数字指数代码（过滤外汇/自定义指数）
                if not (len(str(code)) == 6 and str(code).isdigit()):
                    result["skip"] += 1
                    continue
                df = store.load(code, asset_type="index", market="cn")
                if df is None or df.empty:
                    result["skip"] += 1
                    continue
                sd = _to_ts_date(start or df.index.min().strftime("%Y-%m-%d"))
                ed = _to_ts_date(end or df.index.max().strftime("%Y-%m-%d"))
                db = None
                for attempt in range(4):
                    try:
                        db = self._pro.index_dailybasic(
                            ts_code=_index_ts_code(code),
                            start_date=sd, end_date=ed,
                            fields="trade_date,pe_ttm,pb",
                        )
                        break
                    except Exception as e:
                        if "频率超限" in str(e) and attempt < 3:
                            _log("  指数估值接口限频，等待 61s 后重试...")
                            time.sleep(61)
                            continue
                        raise
                if db is None or db.empty:
                    result["skip"] += 1
                    continue
                db = db.rename(columns={"trade_date": "date", "pb": "pb_mrq"})
                db["date"] = pd.to_datetime(db["date"], format="%Y%m%d")
                db = db.set_index("date")
                merged = df.copy()
                for col in ("pe_ttm", "pb_mrq"):
                    if col not in merged.columns:
                        merged[col] = float("nan")
                    if col in db.columns:
                        new = db[col].reindex(merged.index)
                        merged[col] = new.combine_first(merged[col])
                store.replace(code, merged, asset_type="index", market="cn")
                result["ok"] += 1
            except Exception as e:
                logger.warning(f"指数估值回填 {code} 失败: {e}")
                result["fail"] += 1
            if (i + 1) % 200 == 0 or i + 1 == len(codes):
                _log(f"  指数估值回填进度: {i+1}/{len(codes)} "
                     f"成功={result['ok']} 跳过={result['skip']} "
                     f"失败={result['fail']}")
            if sleep:
                time.sleep(sleep)
        return result

    # ------------------------------------------------------------------
    # 上市日期索引（tushare stock_basic）
    # ------------------------------------------------------------------

    def _save_ipo_dates(self) -> None:
        try:
            df = call_nonempty(
                lambda: self._pro.stock_basic(
                    exchange="", list_status="L",
                    fields="ts_code,name,list_date"),
                label="stock_basic(ipo)", min_rows=1000,
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
