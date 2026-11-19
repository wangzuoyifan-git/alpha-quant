"""
moneyflow 域下载器

负责下载资金流与情绪类数据：
  - stock       个股历史资金流向（Tushare moneyflow，2010起，日频；增量按交易日批量）
  - industry    行业资金流（Tushare moneyflow_ind_dc，东财，按日期分片）
  - market      大盘资金流（Tushare moneyflow_mkt_dc，东财，单文件）
  - margin      融资融券明细（Tushare margin_detail，日频；增量按交易日批量）
  - top_list    龙虎榜汇总（Tushare top_list，按日期）
  - top_inst    龙虎榜席位明细（Tushare top_inst，按日期；资金主体识别用）
  - block_trade 大宗交易（Tushare block_trade，按日期；大宗买方用）
  - hk_hold     北向个股持股（Tushare hk_hold，按股票代码；北向行为用）
  - northbound  北向资金（AKShare stock_hsgt_hist_em，无需 Tushare token）

接口限流说明（Tushare 默认）：
  - 每分钟最多 200 次（普通积分）
  - 单次最多返回 5000 行
  - 批量拉取时每次请求后 sleep 0.3~0.5s
  - stock/margin 每日增量用 `moneyflow(trade_date=)` / `margin_detail(trade_date=)`
    一次返回全市场（~5500/~2000 行），请求数从逐只 ~5500 降到每个交易日 1 次，
    落盘仍按股票分文件（见 _update_stock_by_date / _update_margin_by_date）。

用法:
  dl = MoneyflowDownloader()
  dl.init_db()                    # 全量初始化（stock 2010起，其余 2024起）
  dl.update_db()                  # 每日增量更新
  dl.init_stock(start='2010-01-01')      # 单独初始化个股资金流
  dl.init_northbound()                   # 单独初始化北向资金（AKShare）
  dl.update_hk_hold(['000001', '600519'])  # 按需下载北向个股持股（季度披露）

注意：northbound 用 AKShare，不需要 TUSHARE_TOKEN；其余 7 类需要 token，
      token 在首次调用 Tushare 接口时惰性初始化。
注意：hk_hold 为按股票代码逐只拉取，非每日全量，未纳入 update_db 默认流程。
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

# A 股当日 EOD 数据落定时点：早于该时点只更新到上一交易日，
# 避免盘中运行把当日未落定的数据写入本地（次日增量又不会回补）。
_CN_SETTLE_TIME = "16:00"

# 东财板块资金流历史（AKShare 同源，直接按 BK 代码拉取，避免逐板块重复取映射）
_EM_FFLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_EM_FFLOW_FIELDS = ("f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,"
                    "f61,f62,f63,f64,f65")
_EM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120 Safari/537.36"),
    "Referer": "https://data.eastmoney.com/",
}
_AK_SLEEP = 0.12


def _parse_em_fflow_klines(klines) -> Optional[pd.DataFrame]:
    """东财板块 fflow klines → 与 Tushare moneyflow_ind_dc 对齐的 DataFrame。"""
    rows = []
    for line in klines or []:
        p = str(line).split(",")
        if len(p) < 12:
            continue
        try:
            rows.append({
                "date": p[0],
                "net_amount": float(p[1]),
                "buy_sm_amount": float(p[2]),
                "buy_md_amount": float(p[3]),
                "buy_lg_amount": float(p[4]),
                "buy_elg_amount": float(p[5]),
                "net_amount_rate": float(p[6]),
                "buy_sm_amount_rate": float(p[7]),
                "buy_md_amount_rate": float(p[8]),
                "buy_lg_amount_rate": float(p[9]),
                "buy_elg_amount_rate": float(p[10]),
                "close": float(p[11]),
                "pct_change": float(p[12]) if len(p) > 12 else float("nan"),
            })
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return (df.dropna(subset=["date"])
              .drop_duplicates("date")
              .set_index("date")
              .sort_index())


def _fetch_em_board_fflow(bk_code: str) -> Optional[pd.DataFrame]:
    """按东财板块代码（BKxxxx）拉取约 120 个交易日的资金流历史。"""
    import requests

    params = {
        "lmt": "0",
        "klt": "101",
        "fields1": "f1,f2,f3,f7",
        "fields2": _EM_FFLOW_FIELDS,
        "secid": f"90.{bk_code}",
    }
    try:
        r = requests.get(_EM_FFLOW_URL, params=params,
                         headers=_EM_HEADERS, timeout=20)
        r.raise_for_status()
        data = (r.json() or {}).get("data") or {}
        return _parse_em_fflow_klines(data.get("klines"))
    except Exception as e:
        logger.debug(f"东财板块资金流 {bk_code} 失败: {e}")
        return None


def _fetch_ak_market_flow() -> Optional[pd.DataFrame]:
    """AKShare 大盘资金流（约 120 个交易日），列名对齐 market 域。"""
    import akshare as ak

    df = ak.stock_market_fund_flow()
    if df is None or df.empty:
        return None
    rename = {
        "日期": "date",
        "上证-收盘价": "close_sh", "上证-涨跌幅": "pct_change_sh",
        "深证-收盘价": "close_sz", "深证-涨跌幅": "pct_change_sz",
        "主力净流入-净额": "net_amount",
        "主力净流入-净占比": "net_amount_rate",
        "超大单净流入-净额": "buy_elg_amount",
        "超大单净流入-净占比": "buy_elg_amount_rate",
        "大单净流入-净额": "buy_lg_amount",
        "大单净流入-净占比": "buy_lg_amount_rate",
        "中单净流入-净额": "buy_md_amount",
        "中单净流入-净占比": "buy_md_amount_rate",
        "小单净流入-净额": "buy_sm_amount",
        "小单净流入-净占比": "buy_sm_amount_rate",
    }
    df = df.rename(columns=rename)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return (df.dropna(subset=["date"])
              .drop_duplicates("date")
              .set_index("date")
              .sort_index())


def _ak_industry_reference() -> Optional[pd.DataFrame]:
    """无本地行业资金流时，用 AKShare 行业/概念板块列表构建参考表。"""
    import akshare as ak

    frames = []
    for fn, ctype in (("stock_board_industry_name_em", "行业"),
                      ("stock_board_concept_name_em", "概念")):
        try:
            df = getattr(ak, fn)()
            if df is None or df.empty:
                continue
            out = pd.DataFrame({
                "ts_code": df["板块代码"].astype(str) + ".DC",
                "name": df["板块名称"].astype(str),
                "content_type": ctype,
            })
            frames.append(out)
        except Exception as e:
            logger.warning(f"{fn} 失败: {e}")
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True).drop_duplicates("ts_code")

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


def _to_ts_code(code: str) -> str:
    """6 位 A 股代码 -> Tushare ts_code（带交易所后缀）。已带后缀则原样返回。"""
    code = str(code)
    if "." in code:
        return code
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


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
    """获取全部 A 股代码列表（ts_code 格式，如 000001.SZ）。

    Tushare 在 HTTP 非 2xx 时会静默返回空表；这里把空结果当作失败重试，
    避免空列表导致整轮更新 0/0 却仍被判定成功。
    """
    def _fetch():
        df = pro.stock_basic(
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,list_status",
        )
        if df is None or len(df) < 1000:
            raise RuntimeError(
                f"stock_basic 返回异常（rows={0 if df is None else len(df)}）")
        return df

    df = _retry(_fetch, max_retries=3, wait_base=3.0)
    return df["ts_code"].tolist()


# 批量（按 trade_date）增量更新时，用于判断"活跃"窗口的最大缺口（天）。
# 超过该缺口的股票视为长期停牌/退市/新库未初始化，不纳入批量窗口，
# 以免个别陈旧文件把起始日拉回很久以前、导致逐日空跑。
_BATCH_ACTIVE_GAP_DAYS = 30


def _latest_trade_date(pro) -> str | None:
    """最近一个已收盘的交易日（YYYY-MM-DD）；取不到返回 None。

    A 股 16:00 前（未落定）不含当日，避免盘中写当日残缺数据。
    """
    now = datetime.now()
    today = now.strftime(_FMT_DASH)
    start = (now - timedelta(days=20)).strftime(_FMT_DASH)
    dates = _get_trade_dates(pro, start, today)
    if not dates:
        return None
    if now.strftime("%H:%M") < _CN_SETTLE_TIME:
        today_ts = _to_ts_date(today)
        dates = [d for d in dates if d < today_ts]
    if not dates:
        return None
    return _to_dash_date(max(dates))


def _settled_end(pro) -> str:
    """本次运行的目标交易日。

    优先使用调用方（如每日脚本）在任务启动时写入的 TARGET_TRADE_DATE，
    保证一次运行内所有步骤使用同一个目标日，不因跨过 16:00 而中途切换；
    未指定时按当前时刻取最近已落定交易日。
    """
    override = os.getenv("TARGET_TRADE_DATE")
    if override:
        return override
    target = _latest_trade_date(pro)
    if target is None:
        raise RuntimeError("trade_cal 返回空或最近交易日不可用，Tushare 可能异常")
    return target


def _preload_last_dates(dir_path) -> dict:
    """预加载某分文件目录下各 code 的最新日期（只读 index，避免整表读入）。

    dir_path: Path，如 data/moneyflow/stock。返回 {code: 'YYYY-MM-DD'}。
    """
    out: dict = {}
    for p in dir_path.glob("*.parquet"):
        try:
            idx = pd.read_parquet(p, columns=[]).index
            if len(idx):
                out[p.stem] = idx.max().strftime(_FMT_DASH)
        except Exception:
            pass
    return out


def _recent_floor(last_dates: dict, target: str,
                  max_gap_days: int = _BATCH_ACTIVE_GAP_DAYS) -> str | None:
    """取"活跃"股票（最新日期在 target 前 max_gap_days 天内）中最早的日期。

    返回该日期作为批量窗口下界（不含）；无活跃股票返回 None。
    """
    thr = (datetime.strptime(target, _FMT_DASH)
           - timedelta(days=max_gap_days)).strftime(_FMT_DASH)
    recent = [d for d in last_dates.values() if d >= thr]
    return min(recent) if recent else None


class MoneyflowDownloader:

    def __init__(self):
        # token 惰性初始化：northbound（AKShare）不需要 token
        self._pro = None
        # 本次运行的目标交易日（首次使用时确定并缓存，避免跨 16:00 中途切换）
        self._target_date = None

    def _target(self, pro) -> str:
        if self._target_date is None:
            self._target_date = _settled_end(pro)
        return self._target_date

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

        _log("[1/8] 初始化个股资金流（stock）...")
        self.init_stock(start=stock_start)

        _log("[2/8] 初始化龙虎榜汇总（top_list）...")
        self.init_top_list(start=start)

        _log("[3/8] 初始化龙虎榜席位明细（top_inst）...")
        self.init_top_inst(start=start)

        _log("[4/8] 初始化大宗交易（block_trade）...")
        self.init_block_trade(start=start)

        _log("[5/8] 初始化融资融券（margin）...")
        self.init_margin(start=start)

        _log("[6/8] 初始化行业资金流（industry）...")
        self.init_industry(start=start)

        _log("[7/8] 初始化大盘资金流（market）...")
        self.init_market(start=start)

        _log("[8/8] 初始化北向资金（northbound，AKShare）...")
        self.init_northbound()

        _log("Moneyflow 域初始化完成！")

    def update_db(self) -> None:
        """每日增量更新所有 moneyflow 数据"""
        target = self._target(self._ensure_pro())
        _log(f"Moneyflow 域增量更新  目标日期: {target}")

        _log("[1/8] 更新个股资金流...")
        self._update_stock_by_date()

        _log("[2/8] 更新龙虎榜汇总...")
        self._update_top_list()

        _log("[3/8] 更新龙虎榜席位明细...")
        self._update_top_inst()

        _log("[4/8] 更新大宗交易...")
        self._update_block_trade()

        _log("[5/8] 更新融资融券...")
        self._update_margin_by_date()

        _log("[6/8] 更新行业资金流...")
        self._update_industry()

        _log("[7/8] 更新大盘资金流...")
        self._update_market()

        _log("[8/8] 更新北向资金（AKShare）...")
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
        end = self._target(pro)
        codes = _get_all_stock_codes(pro)
        total = len(codes)
        _log(f"  stock: 共 {total} 只，起始 {start} ~ {end}")

        ipo_path = Path(__file__).parent / "market" / "ipo_dates.json"
        ipo_dates: dict = {}
        if ipo_path.exists():
            with open(ipo_path, encoding="utf-8") as f:
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
        end = self._target(pro)
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

    def _update_stock_by_date(self) -> None:
        """按交易日批量更新个股资金流：`moneyflow(trade_date=)` 一次返回全市场。

        逐只调用 ~5500 次/天 → 每个缺失交易日 1 次；保存仍按股票分文件写入。
        仅覆盖"活跃"股票的缺口（见 _recent_floor），长期停牌/退市股不纳入。
        """
        pro = self._ensure_pro()
        target = self._target(pro)

        existing_last = _preload_last_dates(store.BASE_DIR / "stock")
        if not existing_last:
            _log("  stock: 本地无数据，转全量初始化 init_stock()")
            self.init_stock()
            return

        floor = _recent_floor(existing_last, target)
        if floor is None or floor >= target:
            _log(f"  stock: 已是最新（目标交易日 {target}）")
            return

        start = (datetime.strptime(floor, _FMT_DASH)
                 + timedelta(days=1)).strftime(_FMT_DASH)
        dates = _retry(lambda: _get_trade_dates(pro, start, target))
        if not dates:
            raise RuntimeError(
                f"trade_cal 返回空（{start} ~ {target}），Tushare 可能异常")

        _log(f"  stock: 按交易日批量更新 {len(dates)} 个交易日（{start} ~ {target}）")
        ok = skip = fail = 0
        n_files = 0
        t0 = time.time()
        for i, td in enumerate(dates):
            try:
                df = _retry(lambda d=td: pro.moneyflow(trade_date=d))
                if df is None or df.empty:
                    skip += 1
                else:
                    n_files += self._save_grouped(df, "stock")
                    ok += 1
            except Exception as e:
                logger.debug(f"stock {td} 批量失败: {e}")
                fail += 1
            time.sleep(0.35)
            if (i + 1) % 20 == 0 or (i + 1) == len(dates):
                _log(f"  stock 进度: {i+1}/{len(dates)} 交易日  文件写入:{n_files}  "
                     f"失败日:{fail}  {time.time()-t0:.0f}s")
        _log(f"  stock 更新完成: 交易日 成功={ok} 空日={skip} 失败={fail}  "
             f"文件写入={n_files}  {time.time()-t0:.0f}s")

    def _save_grouped(self, df: pd.DataFrame, data_type: str) -> int:
        """把按 trade_date 拉取的"全市场"DataFrame 按 ts_code 拆分落盘。

        - stock：额外写入 code 列（与逐只路径一致）；
        - margin：不写 code 列（与逐只路径一致）。
        返回写入的文件数。
        """
        n = 0
        for ts_code, g in df.groupby("ts_code", sort=False):
            code6 = str(ts_code).split(".")[0]
            g = g.copy()
            if data_type == "stock":
                g["code"] = code6
            store.save(data_type, g, code=code6)
            n += 1
        return n

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
        end = self._target(pro)
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
        end = self._target(pro)
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
    # top_inst / block_trade — 按交易日分片（资金主体识别用）
    # ==================================================================

    def _pull_date_shards(self, data_type: str, fetch_fn, start: str,
                          forward_only: bool = False) -> None:
        """按交易日逐日拉取并按日期分片保存。

        跳过已存在日期（含空日占位），避免重复保存与索引去重误伤多行分片。
        fetch_fn(trade_date) 返回该日 DataFrame。

        forward_only=False（init）：从 start 起补齐所有缺失交易日，支持回溯补历史；
        forward_only=True（update）：只从已有最新日期之后向前增量。
        """
        pro = self._ensure_pro()
        end = self._target(pro)
        existing = set(d.replace("-", "") for d in store.list_dates(data_type))
        if forward_only and existing:
            last = max(existing)
            fetch_start = (
                datetime.strptime(_to_dash_date(last), _FMT_DASH) + timedelta(days=1)
            ).strftime(_FMT_DASH)
        else:
            fetch_start = start
        if fetch_start > end:
            _log(f"  {data_type}：已是最新")
            return

        trade_dates = _retry(lambda: _get_trade_dates(pro, fetch_start, end))
        dates_todo = [d for d in trade_dates if d not in existing]
        if not dates_todo:
            _log(f"  {data_type}：已是最新")
            return

        _log(f"  {data_type}: 待下载 {len(dates_todo)} 个交易日")
        ok = skip = fail = 0
        t0 = time.time()
        for i, td in enumerate(dates_todo):
            date_dash = _to_dash_date(td)
            try:
                df = _retry(lambda d=td: fetch_fn(d))
                if df is None or df.empty:
                    store.save(data_type,
                               pd.DataFrame(columns=["trade_date"]),
                               date=date_dash)
                    skip += 1
                else:
                    store.save(data_type, df, date=date_dash)
                    ok += 1
                time.sleep(0.4)
            except Exception as e:
                logger.debug(f"{data_type} {td} 失败: {e}")
                fail += 1
                time.sleep(1.5)

            if (i + 1) % 100 == 0:
                _log(f"  {data_type} 进度: {i+1}/{len(dates_todo)} "
                     f"有数据:{ok} 空日:{skip} 失败:{fail}  {time.time()-t0:.0f}s")

        _log(f"  {data_type} 完成: 有数据={ok} 空日={skip} 失败={fail}  {time.time()-t0:.0f}s")

    def init_top_inst(self, start: str = DEFAULT_START) -> None:
        """初始化/补齐龙虎榜席位明细（从 start 起补齐缺失交易日）。"""
        pro = self._ensure_pro()
        self._pull_date_shards("top_inst",
                               lambda d: pro.top_inst(trade_date=d), start,
                               forward_only=False)

    def _update_top_inst(self) -> None:
        """增量更新龙虎榜席位明细（只向前补最新）。"""
        pro = self._ensure_pro()
        self._pull_date_shards("top_inst",
                               lambda d: pro.top_inst(trade_date=d),
                               DEFAULT_START, forward_only=True)

    def init_block_trade(self, start: str = DEFAULT_START) -> None:
        """初始化/补齐大宗交易（从 start 起补齐缺失交易日）。"""
        pro = self._ensure_pro()
        self._pull_date_shards("block_trade",
                               lambda d: pro.block_trade(trade_date=d), start,
                               forward_only=False)

    def _update_block_trade(self) -> None:
        """增量更新大宗交易（只向前补最新）。"""
        pro = self._ensure_pro()
        self._pull_date_shards("block_trade",
                               lambda d: pro.block_trade(trade_date=d),
                               DEFAULT_START, forward_only=True)

    # ==================================================================
    # margin — 融资融券
    # ==================================================================

    def init_margin(self, start: str = DEFAULT_START) -> None:
        """全量下载融资融券明细，按股票代码逐只拉取。"""
        pro = self._ensure_pro()
        end = self._target(pro)
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
        end = self._target(pro)
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

    def _update_margin_by_date(self) -> None:
        """按交易日批量更新融资融券：`margin_detail(trade_date=)` 一次返回全市场。

        逐只调用 ~5500 次/天 → 每个缺失交易日 1 次；保存仍按股票分文件写入。
        """
        pro = self._ensure_pro()
        target = self._target(pro)

        existing_last = _preload_last_dates(store.BASE_DIR / "margin")
        if not existing_last:
            _log("  margin: 本地无数据，转全量初始化 init_margin()")
            self.init_margin()
            return

        floor = _recent_floor(existing_last, target)
        if floor is None or floor >= target:
            _log(f"  margin: 已是最新（目标交易日 {target}）")
            return

        start = (datetime.strptime(floor, _FMT_DASH)
                 + timedelta(days=1)).strftime(_FMT_DASH)
        dates = _retry(lambda: _get_trade_dates(pro, start, target))
        if not dates:
            raise RuntimeError(
                f"trade_cal 返回空（{start} ~ {target}），Tushare 可能异常")

        _log(f"  margin: 按交易日批量更新 {len(dates)} 个交易日（{start} ~ {target}）")
        ok = skip = fail = 0
        n_files = 0
        t0 = time.time()
        for i, td in enumerate(dates):
            try:
                df = _retry(lambda d=td: pro.margin_detail(trade_date=d))
                if df is None or df.empty:
                    skip += 1
                else:
                    n_files += self._save_grouped(df, "margin")
                    ok += 1
            except Exception as e:
                logger.debug(f"margin {td} 批量失败: {e}")
                fail += 1
            time.sleep(0.35)
            if (i + 1) % 20 == 0 or (i + 1) == len(dates):
                _log(f"  margin 进度: {i+1}/{len(dates)} 交易日  文件写入:{n_files}  "
                     f"失败日:{fail}  {time.time()-t0:.0f}s")
        _log(f"  margin 更新完成: 交易日 成功={ok} 空日={skip} 失败={fail}  "
             f"文件写入={n_files}  {time.time()-t0:.0f}s")

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
        end = self._target(pro)
        _log(f"  industry: {start} ~ {end}")

        trade_dates = _retry(lambda: _get_trade_dates(pro, start, end))
        existing = set(d.replace("-", "") for d in store.list_dates("industry"))
        dates_todo = [d for d in trade_dates if d not in existing]
        _log(f"  industry: 共 {len(trade_dates)} 个交易日，待下载 {len(dates_todo)} 个")

        ok = skip = fail = 0
        permission = False
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
                if "权限" in str(e) or "访问接口" in str(e):
                    permission = True
                    break
                time.sleep(1.5)

            if (i + 1) % 100 == 0:
                elapsed = time.time() - t0
                _log(f"  进度: {i+1}/{len(dates_todo)}  有数据:{ok} 空日:{skip} 失败:{fail}  {elapsed:.0f}s")

        elapsed = time.time() - t0
        _log(f"  industry 完成: 有数据={ok} 空日={skip} 失败={fail}  {elapsed:.0f}s")
        if permission or (ok == 0 and fail > 0):
            _log("  industry Tushare 不可用，尝试 AKShare fallback...")
            self._update_industry_ak()

    def _update_industry(self) -> None:
        """增量更新行业资金流"""
        pro = self._ensure_pro()
        end = self._target(pro)
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
        permission = False
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
                if "权限" in str(e) or "访问接口" in str(e):
                    permission = True
                    break
                time.sleep(1.5)

        _log(f"  industry 更新完成: 有数据={ok} 空日={skip} 失败={fail}")
        if permission or fail > 0:
            _log("  industry Tushare 不可用/不完整，尝试 AKShare fallback...")
            self._update_industry_ak()

    def _update_industry_ak(self) -> None:
        """东财板块资金流 fallback（约 120 个交易日窗口，逐板块历史）。"""
        existing = set(store.list_dates("industry"))
        ref = None
        if existing:
            ref = store.load("industry", date=sorted(existing)[-1])
        if ref is None or ref.empty:
            ref = _ak_industry_reference()
        if ref is None or ref.empty:
            _log("  industry AKShare fallback：无板块参考表")
            return

        rows_by_date: dict = {}
        ok = fail = 0
        t0 = time.time()
        for _, row in ref.iterrows():
            tscode = str(row.get("ts_code", ""))
            if not tscode:
                continue
            bk = tscode.split(".")[0]
            hist = _fetch_em_board_fflow(bk)
            if hist is None or hist.empty:
                fail += 1
                continue
            hist = hist[~hist.index.strftime("%Y-%m-%d").isin(existing)]
            for d, rec in hist.iterrows():
                item = rec.to_dict()
                item.update({
                    "date": d.strftime(_FMT_DASH),
                    "ts_code": tscode,
                    "name": row.get("name", ""),
                    "content_type": row.get("content_type", "行业"),
                })
                rows_by_date.setdefault(d, []).append(item)
            ok += 1
            if ok % 100 == 0:
                _log(f"  industry AKShare fallback: {ok} 板块 "
                     f"{time.time()-t0:.0f}s")
            time.sleep(_AK_SLEEP)

        for d in sorted(rows_by_date):
            df = pd.DataFrame(rows_by_date[d])
            df["rank"] = (df["net_amount"]
                          .rank(ascending=False, method="min")
                          .astype(int))
            store.save("industry", df, date=d.strftime(_FMT_DASH))

        _log(f"  industry AKShare fallback 完成: 板块={ok} 失败={fail} "
             f"补齐交易日={len(rows_by_date)}")

    # ==================================================================
    # market — 大盘资金流（东财，单文件）
    # ==================================================================

    def init_market(self, start: str = DEFAULT_START) -> None:
        """全量下载大盘资金流（东财 moneyflow_mkt_dc），按年分段批量拉取。"""
        pro = self._ensure_pro()
        end = self._target(pro)
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
            _log("  market：Tushare 无数据，尝试 AKShare fallback...")
            self._update_market_ak()
            return

        result = pd.concat(all_dfs, ignore_index=True)
        result = result.drop_duplicates(subset=["trade_date"])
        store.save("market", result)
        _log(f"  market 完成: {len(result)} 行")

    def _update_market(self) -> None:
        """增量更新大盘资金流"""
        pro = self._ensure_pro()
        end = self._target(pro)
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
            logger.warning(f"market Tushare 更新失败，尝试 AKShare fallback: {e}")
            self._update_market_ak()

    def _update_market_ak(self) -> None:
        """AKShare 大盘资金流 fallback（约 120 个交易日窗口）。"""
        try:
            df = _fetch_ak_market_flow()
            if df is None or df.empty:
                _log("  market AKShare fallback：无数据")
                return
            existing = store.load("market")
            if existing is not None and not existing.empty:
                df = df[~df.index.isin(existing.index)]
            if df.empty:
                _log("  market：已是最新（AKShare）")
                return
            store.save("market", df.reset_index())
            _log(f"  market AKShare fallback 更新: +{len(df)} 行")
        except Exception as e:
            logger.warning(f"market AKShare fallback 失败: {e}")

    def repair_akshare(self, types: Optional[list[str]] = None) -> None:
        """手动触发资金流 AKShare fallback（market / industry）。"""
        types = types or ["market", "industry"]
        if "market" in types:
            self._update_market_ak()
        if "industry" in types:
            self._update_industry_ak()

    # ==================================================================
    # hk_hold — 北向个股持股（Tushare hk_hold，按股票代码）
    # ==================================================================

    def fetch_hk_hold(self, code: str,
                      start: Optional[str] = None,
                      end: Optional[str] = None) -> Optional[pd.DataFrame]:
        """拉取单只 A 股的北向持股历史并落盘（store: hk_hold/{code}.parquet）。

        注意：Tushare hk_hold 为沪深港通持股明细，普通积分账户按季度披露
        （2025Q1 起），因此序列粒度可能为季度而非日频；上层分析需据此说明。
        """
        pro = self._ensure_pro()
        code6 = str(code).split(".")[0]
        ts_code = _to_ts_code(code6)
        end = end or datetime.now().strftime(_FMT_DASH)
        start = start or DEFAULT_START

        def _fetch():
            return pro.hk_hold(
                ts_code=ts_code,
                start_date=_to_ts_date(start),
                end_date=_to_ts_date(end),
            )

        df = _retry(_fetch)
        if df is None or df.empty:
            return None
        df = df.copy()
        # 统一用 6 位 A 股代码作为落盘键（字段 code 在 hk_hold 中为港股内部码）
        if "ts_code" in df.columns:
            df["code"] = df["ts_code"].astype(str).str.split(".").str[0]
        store.save("hk_hold", df, code=code6)
        return df

    def update_hk_hold(self, codes: list[str],
                       start: Optional[str] = None,
                       end: Optional[str] = None) -> dict:
        """批量下载北向个股持股（按代码逐只，0.5s 间隔）。返回统计字典。"""
        ok = skip = fail = 0
        t0 = time.time()
        for code in codes or []:
            code6 = str(code).split(".")[0]
            ld = store.last_date("hk_hold", code=code6)
            # 仅在显式指定 end 且已有数据覆盖到该日时跳过（季度披露，默认总是刷新）
            if ld and end and ld >= end:
                skip += 1
                continue
            try:
                df = self.fetch_hk_hold(code6, start=start, end=end)
                if df is None or df.empty:
                    skip += 1
                else:
                    ok += 1
                time.sleep(0.5)
            except Exception as e:
                logger.debug(f"hk_hold {code6} 失败: {e}")
                fail += 1
                time.sleep(1.0)
        result = {"ok": ok, "skip": skip, "fail": fail,
                  "elapsed": round(time.time() - t0, 1)}
        _log(f"  hk_hold 完成: 新增={ok} 跳过={skip} 失败={fail}  {result['elapsed']}s")
        return result

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

        # 只保留目标交易日及以前，避免写入未落定/盘中残缺净流入
        cutoff = os.getenv("TARGET_TRADE_DATE")
        if not cutoff:
            now = datetime.now()
            cutoff = (now.strftime(_FMT_DASH)
                      if now.strftime("%H:%M") >= _CN_SETTLE_TIME
                      else (now - timedelta(days=1)).strftime(_FMT_DASH))
        merged = merged[merged.index <= pd.Timestamp(cutoff)]
        if merged.empty:
            _log("  northbound：无已落定数据")
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
