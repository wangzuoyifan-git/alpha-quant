"""
fund 域下载器（Phase 2：ETF/基金）

数据来源（Tushare）：
  - fund_basic(market='E')   ETF 列表（含 benchmark 文本）
  - fund_share(ts_code)      基金份额（日频）→ ETF 申赎
  - fund_nav(ts_code)        基金净值（日频，含 adj_nav）
  - index_weight(index_code) 指数成分权重（月度）→ ETF 成分权重代理
  - index_basic(market)      指数列表（用于 ETF→指数映射）

用法:
  dl = FundDownloader()
  dl.init_basic()
  dl.build_etf_index_map()
  dl.update_etf_fund(['510300', '159915'])          # 份额 + 净值 + 指数权重
  dl.fetch_index_weight('000300.SH')                # 单个指数权重
"""
import logging
import os
import time
from datetime import datetime, timedelta
from difflib import get_close_matches
from typing import Optional

import pandas as pd

import data.store_fund as store

logger = logging.getLogger(__name__)
_FMT_DASH = "%Y-%m-%d"


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _to_ts_code(code: str) -> str:
    code = str(code)
    if "." in code:
        return code
    if code.startswith(("15", "16", "18")):
        return f"{code}.SZ"
    return f"{code}.SH"


def _norm_index_name(s: str) -> str:
    """归一化指数/基准名称，便于 ETF benchmark ↔ index_basic 名称匹配。"""
    s = str(s)
    for t in ("收益率", "×100%", "*100%", "100%", "%", " ", "（", "）", "(", ")", "×", "*"):
        s = s.replace(t, "")
    return s.strip()


def _get_pro():
    import tushare as ts
    token = os.getenv("TUSHARE_TOKEN", "")
    if not token:
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("TUSHARE_TOKEN="):
                        token = line.strip().split("=", 1)[1]
                        break
    if not token:
        raise RuntimeError("TUSHARE_TOKEN 未配置")
    ts.set_token(token)
    return ts.pro_api()


class FundDownloader:

    def __init__(self):
        self._pro = None

    def pro(self):
        if self._pro is None:
            self._pro = _get_pro()
        return self._pro

    # ── ETF 列表 ──────────────────────────────────────────────
    def init_basic(self) -> int:
        df = self.pro().fund_basic(market="E")
        if df is None or df.empty:
            _log("fund_basic 无数据")
            return 0
        keep = [c for c in ("ts_code", "name", "management", "fund_type", "benchmark",
                            "list_date", "delist_date", "invest_type", "market")
                if c in df.columns]
        store.save("basic", df[keep])
        _log(f"ETF 列表已保存: {len(df)} 只")
        return len(df)

    # ── ETF→跟踪指数映射 ──────────────────────────────────────
    def build_etf_index_map(self, save: bool = True) -> dict:
        basic = store.load("basic")
        if basic is None or basic.empty or "benchmark" not in basic.columns:
            self.init_basic()
            basic = store.load("basic")
        if basic is None or basic.empty:
            return {}

        # 指数名（归一化）→ 代码 + 首4字分桶（加速模糊匹配）
        name_map = {}
        buckets = {}
        for mkt in ("CSI", "SSE", "SZSE", "CNI"):
            try:
                ib = self.pro().index_basic(market=mkt)
                if ib is not None and not ib.empty:
                    for nm, ts_code in zip(ib["name"], ib["ts_code"], strict=False):
                        n = _norm_index_name(nm)
                        name_map[n] = str(ts_code)
                        buckets.setdefault(n[:4], []).append(n)
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"index_basic {mkt} 失败: {e}")

        out = store.load_etf_index_map() if not save else {}
        for _, r in basic.iterrows():
            code = str(r["ts_code"]).split(".")[0]
            if code in out:
                continue
            bench = str(r.get("benchmark") or "")
            if not bench or bench.lower() == "nan":
                continue
            clean = _norm_index_name(bench)
            match = clean if clean in name_map else None
            if match is None:
                cand = get_close_matches(clean, buckets.get(clean[:4], []), n=1, cutoff=0.8)
                match = cand[0] if cand else None
            if match:
                out[code] = {"index_code": name_map[match], "index_name": match,
                             "benchmark": bench}
        if save:
            store.save_etf_index_map(out)
        _log(f"ETF→指数映射: {len(out)} 条")
        return out

    # ── 份额 / 净值 ───────────────────────────────────────────
    def fetch_share(self, code: str) -> Optional[pd.DataFrame]:
        ts_code = _to_ts_code(code)
        df = self.pro().fund_share(ts_code=ts_code)
        if df is None or df.empty:
            return None
        store.save("share", df, code=ts_code.split(".")[0])
        return df

    def fetch_nav(self, code: str, start: str | None = None) -> Optional[pd.DataFrame]:
        ts_code = _to_ts_code(code)
        start = start or (datetime.now() - timedelta(days=365 * 2)).strftime("%Y-%m-%d")
        df = self.pro().fund_nav(ts_code=ts_code, start_date=start.replace("-", ""),
                                 end_date=datetime.now().strftime("%Y%m%d"))
        if df is None or df.empty:
            return None
        store.save("nav", df, code=ts_code.split(".")[0])
        return df

    # ── 指数权重 ──────────────────────────────────────────────
    def fetch_index_weight(self, index_code: str, start: str | None = None,
                           end: str | None = None) -> Optional[pd.DataFrame]:
        start = start or (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
        end = end or datetime.now().strftime("%Y-%m-%d")
        df = self.pro().index_weight(index_code=index_code,
                                     start_date=start.replace("-", ""),
                                     end_date=end.replace("-", ""))
        if df is None or df.empty:
            return None
        store.save("index_weight", df, code=index_code.split(".")[0])
        return df

    # ── 批量 ──────────────────────────────────────────────────
    def update_etf_fund(self, codes: list, with_weight: bool = True) -> dict:
        """更新指定 ETF 的份额 + 净值（+ 其跟踪指数权重）。"""
        if not store.exists("basic"):
            self.init_basic()
        emap = store.load_etf_index_map()
        ok = fail = 0
        done_idx = set()
        for code in codes:
            code6 = str(code).split(".")[0]
            try:
                self.fetch_share(code6)
                time.sleep(0.3)
                self.fetch_nav(code6)
                time.sleep(0.3)
                ok += 1
            except Exception as e:
                logger.debug(f"etf fund {code6} 失败: {e}")
                fail += 1
                time.sleep(0.8)
            if with_weight and code6 in emap:
                idx = emap[code6]["index_code"]
                if idx not in done_idx:
                    try:
                        self.fetch_index_weight(idx)
                        done_idx.add(idx)
                        time.sleep(0.3)
                    except Exception as e:
                        logger.debug(f"index_weight {idx} 失败: {e}")
        result = {"ok": ok, "fail": fail, "index_weight": len(done_idx)}
        _log(f"ETF 基金数据更新: {result}")
        return result
