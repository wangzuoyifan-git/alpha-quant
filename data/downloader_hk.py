"""
港股（market="hk"）K 线下载器

设计目标：
  - store.py 已内置 hk 市场结构（data/market/hk/stock），本模块补齐数据通路。
  - **优先支持港股通（南向）标的**：默认 connect_only=True，
    从港股通成分列表下载；列表来源 东财（AKShare，重试）→ Tushare hk_hold 近似
    → 全市场降级，并保留已有缓存。
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
import random
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
_HK_SETTLE_TIME = "16:15"   # 港股收盘（16:00）+ 收市竞价/数据落定的大致时点


def _settled_end(end: str, now: datetime | None = None) -> str:
    """港股当日数据需收盘（约 16:15）后才可用；未收盘时把目标日回退到昨日"""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    if end < today:
        return end
    if now.strftime("%H:%M") < _HK_SETTLE_TIME:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return min(end, today)

# 港股通成分列表本地缓存（data/meta/ 域，gitignore）
_CONNECT_FILE = Path(__file__).parent / "meta" / "hk_connect.json"

# 港股估值来源：百度股市通（需带 UA/Referer，akshare 封装未带头会失败）
_BAIDU_URL = "https://gushitong.baidu.com/opendata"
_BAIDU_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"),
    "Referer": "https://gushitong.baidu.com/",
}
_VALUATION_COLS = ("pe_ttm", "pb_mrq", "pcf_ttm")

# 东财资金流接口（港股 secid=116.xxxxx；主域名 TLS 常被重置，备用域名/HTTP）
_EM_FFLOW_HOSTS = ("https://91.push2his.eastmoney.com",
                   "http://push2his.eastmoney.com")
_EM_FFLOW_PATH = "/api/qt/stock/fflow/daykline/get"
_FFLOW_RETRIES = 2          # 单只重试轮数（每轮遍历全部域名），抵御偶发 RemoteDisconnected
_FFLOW_MAX_STREAK = 8       # 连续失败熔断阈值：超过则判定数据源不可用（限流/封禁），提前中止本轮
_FFLOW_MIN_INTERVAL = 1.5   # 相邻请求最小间隔（秒）：东财 push2 为令牌桶限流，过快会耗尽突发额度触发封禁
_FFLOW_JITTER = 0.4         # 间隔抖动上限（秒），避免固定节奏被识别

# 港股财务分析指标 → growth / dupont 分表列
_HK_GROWTH_COLS = ("OPERATE_INCOME_YOY", "GROSS_PROFIT_YOY", "HOLDER_PROFIT_YOY",
                   "OPERATE_INCOME_QOQ", "GROSS_PROFIT_QOQ", "HOLDER_PROFIT_QOQ")
_HK_DUPONT_COLS = ("BASIC_EPS", "DILUTED_EPS", "EPS_TTM", "BPS", "PER_OI",
                   "PER_NETCASH_OPERATE", "OPERATE_INCOME", "GROSS_PROFIT",
                   "HOLDER_PROFIT", "GROSS_PROFIT_RATIO", "NET_PROFIT_RATIO",
                   "ROE_AVG", "ROE_YEARLY", "ROA", "ROIC_YEARLY", "TAX_EBT",
                   "OCF_SALES", "DEBT_ASSET_RATIO", "CURRENT_RATIO",
                   "CURRENTDEBT_DEBT")


def _norm_date(v) -> str | None:
    """规范化日期值 → 'YYYY-MM-DD'，无效返回 None"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan" or s in ("-", "--"):
        return None
    return s.replace("/", "-")[:10]


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
    if sys.stdout.isatty():
        sys.stdout.write(f"\r{line}   ")
        sys.stdout.flush()
    elif done % 50 == 0 or done == total:
        _log(line)


def _sleep_to_interval(t_req: float, interval: float, jitter: float = 0.0):
    """从 t_req（最近一次请求发起时刻）起，保证至少间隔 interval 秒（含随机抖动）。

    东财 push2 为令牌桶限流：突发额度用完即对源 IP 连接重置，恢复需较长时间。
    固定且偏快的节奏（原 _SLEEP=0.3s）会迅速耗尽额度，故按最小间隔匀速抓取。
    """
    wait = interval - (time.time() - t_req)
    if wait > 0:
        time.sleep(wait)
    if jitter > 0:
        time.sleep(random.uniform(0, jitter))


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
        requested_end = end or _today()
        end = _settled_end(requested_end)
        if end != requested_end:
            _log(f"[提示] 港股当日数据需收盘后可用，目标日期由 {requested_end} 回退至 {end}")
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
        requested_end = end or _today()
        end = _settled_end(requested_end)
        if end != requested_end:
            _log(f"[提示] 港股当日数据需收盘后可用，目标日期由 {requested_end} 回退至 {end}")
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
    # 港股估值（PE/PB，来源：百度股市通）
    # ------------------------------------------------------------------

    def update_valuation(self, connect_only: bool | None = None,
                         period: str = "近三年",
                         codes: list[str] | None = None) -> dict:
        """下载港股 PE(TTM)/PB(MRQ) 并合并进 K 线文件（不覆盖行情列）"""
        connect_only = self.connect_only if connect_only is None else connect_only
        if codes is None:
            codes = [c for c, _ in self._get_security_list(connect_only=connect_only)]
        total = len(codes)
        _log("=" * 55)
        _log(f"HKDownloader — 估值更新（PE/PB）  标的: {total} 只  区间: {period}")
        _log("=" * 55)

        ok = skip = fail = 0
        t0 = time.time()
        _bars(0, total, ok, fail, t0, prefix="港股估值")
        for code in codes:
            try:
                status = self._update_valuation_one(code, period)
            except Exception as e:
                logger.warning(f"{code} 估值更新异常: {e}")
                status = "fail"
            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
            _bars(ok + skip + fail, total, ok, fail, t0, prefix="港股估值")
            time.sleep(_SLEEP)
        print()
        _log(f"估值更新完成: 成功={ok} 跳过={skip} 失败={fail}")
        return {"ok": ok, "skip": skip, "fail": fail, "total": total}

    def _update_valuation_one(self, code: str, period: str) -> str:
        kline = store.load(code, "stock", market="hk")
        if kline is None or kline.empty:
            return "skip"

        pe = pb = pcf = None
        try:
            pe = self._fetch_baidu_valuation(code, "市盈率(TTM)", period)
        except Exception as e:
            logger.debug(f"{code} PE 获取失败: {e}")
        time.sleep(_SLEEP)
        try:
            pb = self._fetch_baidu_valuation(code, "市净率", period)
        except Exception as e:
            logger.debug(f"{code} PB 获取失败: {e}")
        time.sleep(_SLEEP)
        try:
            pcf = self._fetch_baidu_valuation(code, "市现率", period)
        except Exception as e:
            logger.debug(f"{code} PCF 获取失败: {e}")

        val = self._build_valuation_df(pe, pb, pcf)
        if val is None:
            return "fail"
        store.replace(code, self._merge_valuation(kline, val),
                      asset_type="stock", market="hk")
        return "ok"

    @staticmethod
    def _build_valuation_df(pe: pd.DataFrame | None,
                            pb: pd.DataFrame | None,
                            pcf: pd.DataFrame | None = None) -> pd.DataFrame | None:
        frames = {}
        if pe is not None and not pe.empty:
            frames["pe_ttm"] = pe["value"]
        if pb is not None and not pb.empty:
            frames["pb_mrq"] = pb["value"]
        if pcf is not None and not pcf.empty:
            frames["pcf_ttm"] = pcf["value"]
        if not frames:
            return None
        return pd.DataFrame(frames)

    @staticmethod
    def _merge_valuation(kline: pd.DataFrame,
                         val: pd.DataFrame) -> pd.DataFrame:
        """按日期把估值列合并进 K 线（估值覆盖旧值，日期以 K 线为准）"""
        merged = kline.copy()
        for col in _VALUATION_COLS:
            if col not in val.columns:
                continue
            new = val[col].reindex(merged.index)
            if col in merged.columns:
                merged[col] = new.where(new.notna(), merged[col])
            else:
                merged[col] = new
        return merged

    @staticmethod
    def _parse_baidu_valuation(body: list) -> pd.DataFrame | None:
        rows = []
        for item in body or []:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                rows.append({"date": item[0], "value": float(item[1])})
            except (TypeError, ValueError):
                continue
        if not rows:
            return None
        df = pd.DataFrame(rows).drop_duplicates("date").set_index("date")
        df.index = pd.to_datetime(df.index)
        return df

    @staticmethod
    def _fetch_baidu_valuation(code: str, indicator: str,
                               period: str) -> pd.DataFrame | None:
        """百度股市通-港股估值序列（市盈率(TTM)/市净率）；需带 UA/Referer"""
        import requests

        params = {
            "openapi": "1", "dspName": "iphone", "tn": "tangram", "client": "app",
            "query": indicator, "code": code, "word": "",
            "resource_id": "51171", "market": "hk", "tag": indicator,
            "chart_select": period, "industry_select": "",
            "skip_industry": "1", "finClientType": "pc",
        }
        r = requests.get(_BAIDU_URL, params=params,
                         headers=_BAIDU_HEADERS, timeout=20)
        r.raise_for_status()
        body = (r.json()["Result"][0]["DisplayData"]["resultData"]["tplData"]
                ["result"]["chartInfo"][0]["body"])
        return HKDownloader._parse_baidu_valuation(body)

    # ------------------------------------------------------------------
    # 港股资金流（东财，滚动约 100 个交易日，逐日累积）
    # ------------------------------------------------------------------

    def update_moneyflow(self, connect_only: bool | None = None,
                         codes: list[str] | None = None) -> dict:
        """下载港股个股资金流（主力/超大/大/中/小单净额+占比）"""
        import data.store_moneyflow as store_mf

        connect_only = self.connect_only if connect_only is None else connect_only
        if codes is None:
            codes = [c for c, _ in self._get_security_list(connect_only=connect_only)]
        total = len(codes)
        _log("=" * 55)
        _log(f"HKDownloader — 资金流更新  标的: {total} 只")
        _log("=" * 55)

        ok = skip = fail = 0
        aborted = False
        streak = 0
        t0 = time.time()
        _bars(0, total, ok, fail, t0, prefix="港股资金流")
        for code in codes:
            req_t = time.time()
            try:
                df = self._fetch_hk_fflow(code)
                if df is None or df.empty:
                    skip += 1
                else:
                    store_mf.save("stock", df, code=code, market="hk")
                    ok += 1
                streak = 0
            except Exception as e:
                logger.warning(f"{code} 资金流更新失败: {e}")
                fail += 1
                streak += 1
                if _FFLOW_MAX_STREAK and streak >= _FFLOW_MAX_STREAK:
                    logger.error(
                        f"港股资金流连续失败 {streak} 只，东财接口疑似限流/封禁，提前中止本轮"
                        f"（成功={ok} 失败={fail}，剩余 {total - ok - skip - fail} 只未处理）")
                    aborted = True
                    break
            _bars(ok + skip + fail, total, ok, fail, t0, prefix="港股资金流")
            _sleep_to_interval(req_t, _FFLOW_MIN_INTERVAL, _FFLOW_JITTER)
        print()
        _log(f"资金流更新完成: 成功={ok} 跳过={skip} 失败={fail}"
             + ("（已熔断中止）" if aborted else ""))
        return {"ok": ok, "skip": skip, "fail": fail, "total": total, "aborted": aborted}

    @staticmethod
    def _parse_fflow(klines: list) -> pd.DataFrame | None:
        """东财 fflow klines → 宽表（date + 主力/小/中/大/超大单净额+占比 + close/pct_chg）"""
        cols = ["date", "main_net", "small_net", "medium_net", "large_net",
                "xlarge_net", "main_pct", "small_pct", "medium_pct",
                "large_pct", "xlarge_pct", "close", "pct_chg"]
        rows = [line.split(",")[:len(cols)] for line in (klines or [])
                if len(line.split(",")) >= len(cols)]
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=cols)
        for c in cols[1:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    @staticmethod
    def _fetch_hk_fflow(code: str) -> pd.DataFrame | None:
        import requests

        params = {
            "lmt": "0", "klt": "101", "secid": f"116.{code}",
            "fields1": "f1,f2,f3,f7",
            "fields2": ("f51,f52,f53,f54,f55,f56,f57,f58,"
                        "f59,f60,f61,f62,f63,f64,f65"),
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/120 Safari/537.36"),
            "Referer": "https://data.eastmoney.com/",
        }
        last_err = None
        for attempt in range(_FFLOW_RETRIES):
            for host in _EM_FFLOW_HOSTS:
                try:
                    r = requests.get(host + _EM_FFLOW_PATH, params=params,
                                     headers=headers, timeout=20)
                    r.raise_for_status()
                    klines = (r.json().get("data") or {}).get("klines") or []
                    if not klines:
                        return None  # 有效响应但无数据 → 不重试
                    return HKDownloader._parse_fflow(klines)
                except Exception as e:
                    last_err = e
            if attempt < _FFLOW_RETRIES - 1:
                time.sleep(_RETRY_SLEEP * (attempt + 1))
        if last_err is not None:
            raise last_err
        return None

    # ------------------------------------------------------------------
    # 港股财务（东财：三大报表 + 主要指标）
    # ------------------------------------------------------------------

    def update_financials(self, connect_only: bool | None = None,
                          codes: list[str] | None = None) -> dict:
        """下载港股财务：利润表/资产负债表/现金流量表 + 成长/杜邦指标"""
        connect_only = self.connect_only if connect_only is None else connect_only
        if codes is None:
            codes = [c for c, _ in self._get_security_list(connect_only=connect_only)]
        total = len(codes)
        _log("=" * 55)
        _log(f"HKDownloader — 财务更新  标的: {total} 只")
        _log("=" * 55)

        ok = skip = fail = 0
        t0 = time.time()
        _bars(0, total, ok, fail, t0, prefix="港股财务")
        for code in codes:
            try:
                status = self._update_financials_one(code)
            except Exception as e:
                logger.warning(f"{code} 财务更新异常: {e}")
                status = "fail"
            if status == "ok":
                ok += 1
            elif status == "skip":
                skip += 1
            else:
                fail += 1
            _bars(ok + skip + fail, total, ok, fail, t0, prefix="港股财务")
            time.sleep(_SLEEP)
        print()
        _log(f"财务更新完成: 成功={ok} 跳过={skip} 失败={fail}")
        return {"ok": ok, "skip": skip, "fail": fail, "total": total}

    def _update_financials_one(self, code: str) -> str:
        import akshare as ak
        import data.store_fundamental as store_fund

        got = False
        for symbol, dtype in (("利润表", "profit"),
                              ("资产负债表", "balance"),
                              ("现金流量表", "cashflow")):
            try:
                raw = ak.stock_financial_hk_report_em(
                    stock=code, symbol=symbol, indicator="报告期")
                wide = self._pivot_hk_statement(raw)
                if wide is not None and not wide.empty:
                    store_fund.save(code, wide, dtype, market="hk")
                    got = True
            except Exception as e:
                logger.debug(f"{code} {symbol} 失败: {e}")
            time.sleep(_SLEEP)

        try:
            ind = ak.stock_financial_hk_analysis_indicator_em(
                symbol=code, indicator="报告期")
            growth, dupont = self._split_hk_indicator(ind)
            if growth is not None and not growth.empty:
                store_fund.save(code, growth, "growth", market="hk")
                got = True
            if dupont is not None and not dupont.empty:
                store_fund.save(code, dupont, "dupont", market="hk")
                got = True
        except Exception as e:
            logger.debug(f"{code} 主要指标失败: {e}")
        return "ok" if got else "skip"

    @staticmethod
    def _pivot_hk_statement(raw: pd.DataFrame) -> pd.DataFrame | None:
        """东财港股报表长表（STD_ITEM_NAME/AMOUNT）→ 宽表（index=报告期，columns=科目）"""
        if raw is None or raw.empty:
            return None
        if "STD_ITEM_NAME" not in raw.columns or "AMOUNT" not in raw.columns:
            return None
        df = raw.copy()
        wide = df.pivot_table(index="REPORT_DATE", columns="STD_ITEM_NAME",
                              values="AMOUNT", aggfunc="first")
        if wide.empty:
            return None
        wide.index = pd.to_datetime(wide.index)
        wide.index.name = "stat_date"
        wide.columns.name = None
        return wide

    @staticmethod
    def _split_hk_indicator(ind: pd.DataFrame):
        """主要指标 → (growth 表, dupont 表)"""
        if ind is None or ind.empty or "REPORT_DATE" not in ind.columns:
            return None, None
        df = ind.copy()
        df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
        df = df.set_index("REPORT_DATE")
        df.index.name = "stat_date"
        growth_cols = [c for c in _HK_GROWTH_COLS if c in df.columns]
        dupont_cols = [c for c in _HK_DUPONT_COLS if c in df.columns]
        growth = df[growth_cols] if growth_cols else None
        dupont = df[dupont_cols] if dupont_cols else None
        return growth, dupont

    # ------------------------------------------------------------------
    # 港股企业事件（分红派息 → events E2）
    # ------------------------------------------------------------------

    def update_dividend_events(self, connect_only: bool | None = None,
                               codes: list[str] | None = None) -> dict:
        """下载港股分红派息并写入 events 域 E2（event_type=dividend）"""
        import akshare as ak
        from data.store_events import save_corp_events

        connect_only = self.connect_only if connect_only is None else connect_only
        if codes is None:
            codes = [c for c, _ in self._get_security_list(connect_only=connect_only)]
        total = len(codes)
        _log("=" * 55)
        _log(f"HKDownloader — 分红事件更新  标的: {total} 只")
        _log("=" * 55)

        ok = skip = fail = 0
        t0 = time.time()
        _bars(0, total, ok, fail, t0, prefix="港股分红")
        for code in codes:
            try:
                df = ak.stock_hk_dividend_payout_em(symbol=code)
                events = self._build_hk_dividend_events(df, code)
                if events:
                    save_corp_events(code, events)
                    ok += 1
                else:
                    skip += 1
            except Exception as e:
                logger.warning(f"{code} 分红事件更新失败: {e}")
                fail += 1
            _bars(ok + skip + fail, total, ok, fail, t0, prefix="港股分红")
            time.sleep(_SLEEP)
        print()
        _log(f"分红事件更新完成: 成功={ok} 跳过={skip} 失败={fail}")
        return {"ok": ok, "skip": skip, "fail": fail, "total": total}

    @staticmethod
    def _build_hk_dividend_events(df: pd.DataFrame, code: str) -> list:
        """东财港股分红派息 → E2 事件列表"""
        if df is None or df.empty:
            return []
        events = []
        for _, row in df.iterrows():
            ann = _norm_date(row.get("最新公告日期"))
            if not ann:
                continue
            ex = _norm_date(row.get("除净日"))
            events.append({
                "event_id": f"hk_dividend_{code}_{ann.replace('-', '')}",
                "code": code,
                "event_type": "dividend",
                "date": ann,
                "end_date": ex,
                "detail": {
                    "fiscal_year": str(row.get("财政年度") or ""),
                    "plan": str(row.get("分红方案") or ""),
                    "div_type": str(row.get("分配类型") or ""),
                    "ex_date": ex,
                    "record_date": str(row.get("截至过户日") or ""),
                    "pay_date": _norm_date(row.get("发放日")),
                },
                "source": "akshare",
                "phase": 1,
            })
        return events

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
        """港股通成分列表：当日缓存 → AKShare 东财（重试） → 近期缓存 → Tushare hk_hold 近似列表。"""
        fresh = self._load_connect_cache(allow_stale=False)
        if fresh:
            return fresh

        # 主源：AKShare 东财（该接口偶发连接重置，重试 3 次）
        for attempt in range(3):
            try:
                import akshare as ak
                df = ak.stock_hk_ggt_components_em()
                if df is not None and not df.empty:
                    codes = self._parse_connect_df(df)
                    if codes:
                        self._save_connect_cache(codes)
                        return codes
            except Exception as e:
                logger.warning(f"港股通成分列表获取失败(第{attempt+1}次): {repr(e)[:100]}")
                if attempt < 2:
                    time.sleep(_RETRY_SLEEP * (attempt + 1))

        # 备源1：近期缓存（成分股季度调整，短期过期可接受）
        stale = self._load_connect_cache(allow_stale=True)
        if stale:
            return stale

        # 备源2：Tushare hk_hold（南向持股标的，近似列表）
        codes = self._get_connect_codes_ts()
        if codes:
            self._save_connect_cache(codes)
            return codes
        return {}

    def _get_connect_codes_ts(self) -> dict:
        """备源：Tushare hk_hold 最新交易日南向持股标的（近似港股通列表）"""
        try:
            if self._pro is None:
                return {}
            df = self._pro.hk_hold()
            if df is None or df.empty:
                return {}
            codes = {}
            for _, row in df.iterrows():
                tscode = str(row.get("ts_code") or "")
                code = tscode.split(".")[0] if "." in tscode else tscode
                code = code.zfill(5)
                if code.isdigit():
                    codes[code] = str(row.get("name") or "")
            if codes:
                _log(f"[提示] 东财港股通列表不可用，改用 Tushare hk_hold "
                     f"南向持股标的（{len(codes)} 只，近似列表）")
            return codes
        except Exception as e:
            logger.warning(f"Tushare hk_hold 备用列表获取失败: {e}")
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

    def _load_connect_cache(self, allow_stale: bool = True) -> dict:
        """读取缓存：当日直接可用；allow_stale=True 时近 45 天缓存兜底（成分股季度调整）"""
        try:
            if not _CONNECT_FILE.exists():
                return {}
            data = json.loads(_CONNECT_FILE.read_text(encoding="utf-8"))
            codes = data.get("codes", {})
            if not codes:
                return {}
            date_str = data.get("date", "")
            if date_str == _today():
                return codes
            if not allow_stale:
                return {}
            try:
                age = (datetime.strptime(_today(), "%Y-%m-%d") -
                       datetime.strptime(date_str, "%Y-%m-%d")).days
            except Exception:
                age = 999
            if 0 <= age <= 45:
                _log(f"[提示] 使用 {date_str} 的港股通缓存列表"
                     f"（{len(codes)} 只，已 {age} 天）")
                return codes
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

    def _batch_download(self, stocks, start: str | None = None,
                        end: str | None = None,
                        start_map: dict | None = None,
                        force: bool = False) -> dict:
        total = len(stocks)
        end = end or _today()
        ok = skip = fail = 0
        t0 = time.time()
        _bars(0, total, ok, fail, t0)
        for code, _name in stocks:
            s = (start_map or {}).get(code) or start or DEFAULT_START
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
            elif status == "skip":
                skip += 1
            else:
                fail += 1
            done = ok + skip + fail
            _bars(done, total, ok, fail, t0)
            time.sleep(_SLEEP)
        print()
        _log(f"完成: 新增={ok} 跳过={skip} 失败={fail}")
        return {"ok": ok, "skip": skip, "fail": fail, "total": total}

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
        # 仅当主源异常（None）时才回落 Tushare；窗口内无新数据（空表）直接跳过，
        # 避免每只都触发 hk_daily（限频 1 次/分钟）拖垮整轮
        if df is None and self._pro is not None:
            df = self._download_stock_ts(code, start, end)
        if df is None:
            return "fail"
        if df.empty:
            return "skip"
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
        """主源：AKShare Sina 全历史，按 [start,end] 截取。

        返回 None 表示抓取异常（可回落 Tushare）；返回空表表示窗口内无新数据（跳过）。
        """
        try:
            import akshare as ak
            df = ak.stock_hk_daily(symbol=code, adjust="")
            df["date"] = pd.to_datetime(df["date"])
            df = df[(df["date"] >= pd.Timestamp(start)) &
                    (df["date"] <= pd.Timestamp(end))]
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