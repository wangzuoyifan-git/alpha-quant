"""
港股（market="hk"）K 线下载器

设计目标：
  - store.py 已内置 hk 市场结构（data/market/hk/stock），本模块补齐数据通路。
  - **优先支持港股通（南向）标的**：默认 connect_only=True，
    从港股通成分列表下载；列表获取失败时降级为全市场，并保留已有缓存。
  - 数据源（重要：Tushare `hk_daily` 当前账号限频 1次/小时，不适合批量，故）
      主 : AKShare `stock_hk_daily`（Sina 源，返回全历史、不复权、无显著限频）
      备 : Tushare `hk_daily`（按日期范围增量拉取，但因每小时限频仅作兜底）
  - 港股通成分列表来源：AKShare `stock_hk_ggt_components_em`（东财），
    成功获取后缓存到 data/meta/hk_connect.json 供复用/权威信息表。

单位说明（AKShare / Tushare hk_daily 口径已为股、元，直接存储）：
  volume : 股（直接存储，无需 ×100）
  amount : 元 HKD（直接存储，非千元）
  close/preclose : 不复权（原始价；港股 pro_bar 复权接口/账号暂不可用）
"""
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import data.store as store
from config.settings import DATA_SOURCE

logger = logging.getLogger(__name__)

DEFAULT_START = "2020-01-02"
_SLEEP = 0.3
_RETRY_SLEEP = 2.0
_PER_STOCK_TIMEOUT = 45

# 港股通成分列表本地缓存（data/meta/ 域，gitignore）
_CONNECT_FILE = Path(__file__).parent / "meta" / "hk_connect.json"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _to_ts_date(d: str) -> str:
    return d.replace("-", "")


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _ts_code(code: str) -> str:
    """港股 5 位代码 → tushare ts_code（.HK 后缀）"""
    return f"{code}.HK"


def _bars(done, total, ok, fail, t0, prefix="港股"):
    pct = done / max(total, 1)
    bar = "█" * int(28 * pct) + "░" * (28 - int(28 * pct))
    line = (f"{prefix} [{bar}] {done}/{total}  新增:{ok} 失败:{fail}  "
            f"已用:{time.time()-t0:.0f}s")
    import sys
    if sys.stdout.isatty():
        sys.stdout.write(f"\r{line}   ")
        sys.stdout.flush()
    elif done % 50 == 0 or done == total:
        _log(line)


class HKDownloader:
    """港股日线下载器（与 TushareKlineDownloader 接口对齐：init_db / update_db）。

    参数：
      pro         — tushare.pro_api 实例（None 时用配置 token 惰性初始化）
      connect_only— 默认 True：只下载港股通标的；False：下载全部港股
    """

    def __init__(self, pro=None, connect_only: bool = True):
        self._pro = pro
        self.connect_only = connect_only
        if pro is None:
            self._init_pro_from_config()

    # ------------------------------------------------------------------
    # pro_api 初始化
    # ------------------------------------------------------------------

    def _init_pro_from_config(self):
        token = DATA_SOURCE.get("tushare_token", "")
        if not token:
            _log("未配置 TUSHARE_TOKEN，港股下载将使用 AKShare 备用源")
            self._pro = None
            return
        try:
            import tushare as ts
            self._pro = ts.pro_api(token, timeout=20)
        except Exception as e:
            logger.warning(f"初始化 tushare pro_api 失败: {e}")
            self._pro = None

    # ------------------------------------------------------------------
    # 公共接口（与 KlineDownloader 对齐）
    # ------------------------------------------------------------------

    def init_db(self, start: str = DEFAULT_START, end: str | None = None,
                force: bool = False, connect_only: bool | None = None) -> dict:
        end = end or _today()
        connect_only = self.connect_only if connect_only is None else connect_only

        mode = "港股通（南向）" if connect_only else "全市场"
        _log("=" * 55)
        _log(f"HKDownloader — 全量初始化   范围: {mode}")
        _log(f"时间范围: {start} ~ {end}")
        _log("=" * 55)

        secs = self._get_security_list(connect_only=connect_only)
        _log(f"待下载标的: {len(secs)} 只")

        if not force:
            pending = [(c, n) for c, n in secs
                       if not store.exists(c, "stock", market="hk")]
            skipped = len(secs) - len(pending)
            if skipped:
                _log(f"断点续传: 已有 {skipped} 只，待下载 {len(pending)} 只")
        else:
            pending = secs
            skipped = 0

        if pending:
            result = self._batch_download(pending, start, end, force=force)
        else:
            result = {"ok": 0, "skip": 0, "fail": 0, "total": 0}
        result["skip"] += skipped

        _log("保存上市日期索引...")
        self._save_ipo_dates(connect_only=connect_only)
        return self._print_stats(result)

    def update_db(self, end: str | None = None,
                  connect_only: bool | None = None) -> dict:
        end = end or _today()
        connect_only = self.connect_only if connect_only is None else connect_only

        _log("=" * 55)
        _log(f"HKDownloader — 增量更新  目标日期: {end}")
        _log("=" * 55)

        secs = self._get_security_list(connect_only=connect_only)
        tasks = []
        already_latest = 0
        for code, name in secs:
            ld = store.last_date(code, "stock", market="hk")
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

        _log(f"已最新: {already_latest}  需更新: {len(tasks)}")
        result = {"ok": 0, "skip": 0, "fail": 0, "total": 0}
        if tasks:
            result = self._batch_download(
                [(c, n) for c, n, _ in tasks], end=end,
                start_map={c: s for c, _, s in tasks})
            result["skip"] += already_latest

        _log("更新完成！")
        self._save_ipo_dates(connect_only=connect_only)
        return self._print_stats(result)

    # ------------------------------------------------------------------
    # 证券列表（港股通标的优先）
    # ------------------------------------------------------------------

    def _get_security_list(self, connect_only: bool) -> list[tuple[str, str]]:
        connect_codes = self._get_connect_codes() if connect_only else {}
        if connect_only:
            if connect_codes:
                _log(f"使用港股通成分列表（{len(connect_codes)} 只）")
                full = self._get_full_list()
                return self._apply_connect(full, connect_codes)
            _log("[警告] 港股通成分列表获取失败，降级为全市场下载。"
                 "可稍后重跑 update-hk 以补齐标的状态。")
        return self._get_full_list()

    def _get_full_list(self) -> list[tuple[str, str]]:
        """从 Tushare hk_basic 取全部上市港股列表。"""
        if self._pro is None:
            _log("[警告] 无 Tushare 配置，无法获取港股列表，跳过")
            return []
        try:
            df = self._pro.hk_basic(list_status="L", fields="ts_code,name")
            out = []
            for _, row in df.iterrows():
                tscode = row["ts_code"]
                if not tscode.endswith(".HK"):
                    continue
                out.append((tscode.split(".")[0], row.get("name", "")))
            return out
        except Exception as e:
            logger.warning(f"hk_basic 获取失败: {e}")
            return []

    @staticmethod
    def _apply_connect(full, connect_codes):
        """把 connect_codes（{code:name}，可带 .HK 后缀）映射到 full 列表并保留命中项。"""
        norm = {}
        for k, v in connect_codes.items():
            code = str(k).split(".")[0] if "." in str(k) else str(k)
            norm[code] = v
        seen = set()
        result = []
        for code, name in full:
            if code in norm and code not in seen:
                result.append((code, name or norm[code]))
                seen.add(code)
        return result

    def _get_connect_codes(self) -> dict:
        """港股通成分列表：先读本地缓存，未命中再请求 AKShare，成功后写回缓存。"""
        # 本地缓存当日有效优先
        cached = self._load_connect_cache()
        if cached:
            return cached

        try:
            import akshare as ak
            df = ak.stock_hk_ggt_components_em()
            if df is not None and not df.empty:
                codes = self._parse_connect_df(df)
                if codes:
                    self._save_connect_cache(codes)
                    return codes
        except Exception as e:
            logger.warning(f"港股通成分列表获取失败: {repr(e)[:100]}")
        return {}

    @staticmethod
    def _parse_connect_df(df: pd.DataFrame) -> dict:
        """解析 AKShare 港股通成分表 → {code: name}。兼容中英文列名。"""
        col_candidates = ["代码", "code", "证券代码", "股份代号"]
        name_candidates = ["名称", "name", "证券简称"]
        code_col = next((c for c in col_candidates if c in df.columns), None)
        name_col = next((c for c in name_candidates if c in df.columns), None)
        if code_col is None:
            return {}
        codes = {}
        for _, row in df.iterrows():
            raw = str(row[code_col])
            code = raw.split(".")[0] if "." in raw else raw
            code = code.zfill(5)
            if code.isdigit():
                codes[code] = str(row[name_col]) if name_col else ""
        return codes

    def _load_connect_cache(self) -> dict:
        try:
            if not _CONNECT_FILE.exists():
                return {}
            data = json.loads(_CONNECT_FILE.read_text(encoding="utf-8"))
            # 仅当日有效：成分列表每日收市后更新
            if data.get("date") == _today():
                return data.get("codes", {})
        except Exception:
            pass
        return {}

    def _save_connect_cache(self, codes: dict):
        try:
            _CONNECT_FILE.parent.mkdir(parents=True, exist_ok=True)
            _CONNECT_FILE.write_text(json.dumps(
                {"date": _today(), "codes": codes},
                ensure_ascii=False, indent=2), encoding="utf-8")
            _log(f"港股通成分列表已缓存: {len(codes)} 只")
        except Exception as e:
            logger.warning(f"缓存港股通列表失败: {e}")

    # ------------------------------------------------------------------
    # 批量下载
    # ------------------------------------------------------------------

    def _batch_download(self, stocks, start: str, end: str,
                        start_map: dict | None = None,
                        force: bool = False) -> dict:
        total = len(stocks)
        ok = fail = 0
        t0 = time.time()
        _bars(0, total, ok, fail, t0)
        for code, _name in stocks:
            s = (start_map or {}).get(code, start)
            try:
                status = self._download_one_timed(code, s, end, force=force)
            except FutureTimeout:
                logger.warning(f"{code} 下载超过 {_PER_STOCK_TIMEOUT}s，跳过")
                status = "fail"
            except Exception as e:
                logger.warning(f"{code} 异常: {e}")
                status = "fail"
            if status == "ok":
                ok += 1
            else:
                fail += 1
            done = ok + fail
            _bars(done, total, ok, fail, t0)
            time.sleep(_SLEEP)
        print()
        _log(f"完成: 新增={ok} 失败={fail}")
        return {"ok": ok, "skip": 0, "fail": fail, "total": total}

    def _download_one_timed(self, code: str, start: str, end: str,
                            force: bool = False) -> str:
        ex = ThreadPoolExecutor(max_workers=1)
        future = ex.submit(self._download_one, code, start, end, force=force)
        try:
            return future.result(timeout=_PER_STOCK_TIMEOUT)
        finally:
            ex.shutdown(wait=False)

    def _download_one(self, code: str, start: str, end: str,
                      force: bool = False) -> str:
        if start and end and start > end:
            return "skip"
        # 主源：AKShare（Sina，全历史，无显著限频）。
        # store.save 按 date 去重（保留已有 first），全历史重取是安全的增量方式。
        df = self._download_stock_ak(code, start, end)
        # 兜底：Tushare hk_daily（账号 1次/小时，非批量场景；失败不影响主流程）
        if df is None or df.empty and self._pro is not None:
            df = self._download_stock_ts(code, start, end)
        if df is None or df.empty:
            return "fail"
        store.save(code, df, asset_type="stock", market="hk", force=force)
        return "ok"

    # ------------------------------------------------------------------
    # Tushare hk_daily（主源，不复权）
    # ------------------------------------------------------------------

    def _download_stock_ts(self, code: str, start: str, end: str) -> pd.DataFrame | None:
        tscode = _ts_code(code)
        sd, ed = _to_ts_date(start), _to_ts_date(end)
        for attempt in range(3):
            try:
                df = self._pro.hk_daily(
                    ts_code=tscode, start_date=sd, end_date=ed)
                break
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"hk_daily {tscode} 失败: {e}")
                    return None
                time.sleep(_RETRY_SLEEP)
        if df is None or df.empty:
            return None
        df = df.rename(columns={
            "trade_date": "date",
            "pre_close": "preclose",
        })
        # 单位：hk_daily 的 vol 为「股」、amount 为「元 HKD」，直接存储
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        df = df.sort_values("date").reset_index(drop=True)
        keep = ["date", "open", "high", "low", "close", "preclose",
                "volume", "amount", "pct_chg"]
        df["volume"] = pd.to_numeric(df.get("vol", pd.Series(dtype=float)),
                                     errors="coerce")
        for col in keep:
            if col not in df.columns:
                df[col] = float("nan")
        return df[keep]

    # ------------------------------------------------------------------
    # AKShare 备用（Sina 源，全历史，按 [start,end] 截取）
    # ------------------------------------------------------------------

    def _download_stock_ak(self, code: str, start: str, end: str) -> pd.DataFrame | None:
        try:
            import akshare as ak
            df = ak.stock_hk_daily(symbol=code, adjust="")
            df["date"] = pd.to_datetime(df["date"])
            df = df[(df["date"] >= pd.Timestamp(start)) &
                    (df["date"] <= pd.Timestamp(end))]
            if df.empty:
                return None
            df = df.sort_values("date").reset_index(drop=True)
            df["preclose"] = df["close"].shift(1)
            df["pct_chg"] = df["close"].pct_change() * 100
            keep = ["date", "open", "high", "low", "close", "preclose",
                    "volume", "amount", "pct_chg"]
            for col in keep:
                if col not in df.columns:
                    df[col] = float("nan")
            return df[keep]
        except Exception as e:
            logger.warning(f"AKShare hk_daily {code} 失败: {e}")
            return None

    # ------------------------------------------------------------------
    # 上市日期索引
    # ------------------------------------------------------------------

    def _save_ipo_dates(self, connect_only: bool):
        try:
            if self._pro is None:
                return
            df = self._pro.hk_basic(list_status="L", fields="ts_code,name,list_date")
            connect = self._get_connect_codes()
            ipo = {}
            for _, row in df.iterrows():
                tscode = row["ts_code"]
                code = tscode.split(".")[0]
                list_date = str(row.get("list_date") or "")
                if list_date and len(list_date) == 8:
                    list_date = f"{list_date[:4]}-{list_date[4:6]}-{list_date[6:]}"
                ipo[f"hk:{code}"] = {
                    "ipo_date": list_date,
                    "market": "hk",
                    "type": "stock",
                    "name": row.get("name", ""),
                    "full_code": tscode,
                    "hk_connect": code in connect,
                }
            store.save_ipo_dates(ipo)
            _log(f"港股上市日期已保存，共 {len(ipo)} 只")
        except Exception as e:
            logger.warning(f"保存港股上市日期失败: {e}")

    # ------------------------------------------------------------------
    # 统计输出
    # ------------------------------------------------------------------

    def _print_stats(self, result: dict) -> dict:
        s = store.stats()
        hk = s.get("hk", {})
        _log("本地港股库存统计:")
        for atype in ("stock", "index"):
            info = hk.get(atype, {})
            _log(f"  hk/{atype:6s}: {info.get('count', 0):5d}只  "
                 f"{info.get('rows', 0):>10,}行  {info.get('size_mb', 0):.1f}MB")
        _log(f"  合计: {s['total']['count']:5d}只")
        if result:
            _log(f"本次: 新增={result.get('ok', 0)} "
                 f"跳过={result.get('skip', 0)} "
                 f"失败={result.get('fail', 0)}")
        return {**s, **(result or {})}