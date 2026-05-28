"""
fundamental 域下载器

负责下载 BaoStock 季频财务数据：
  - profit   盈利能力
  - balance  资产负债
  - cashflow 现金流
  - growth   成长能力
  - dupont   杜邦分析

用法:
  dl = FundamentalDownloader()
  dl.init_db(start_year=2024)   # 全量初始化（近N年）
  dl.update_db()                 # 增量更新（每季度）
"""
import logging
import socket
import sys
import time
from datetime import datetime
from typing import Optional

import pandas as pd

import data.store_fundamental as store

logger = logging.getLogger(__name__)

# 财务数据类型 -> BaoStock 查询函数名
QUERY_FUNCS = {
    "profit":   "query_profit_data",
    "balance":  "query_balance_data",
    "cashflow": "query_cash_flow_data",
    "growth":   "query_growth_data",
    "dupont":   "query_dupont_data",
}

DEFAULT_START_YEAR = 2024


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    logger.info(msg)


def _print_bar(done: int, total: int, ok: int, skip: int, fail: int,
               elapsed: float, prefix: str = "下载进度"):
    pct = done / max(total, 1)
    bar = "█" * int(28 * pct) + "░" * (28 - int(28 * pct))
    eta = elapsed / done * (total - done) if done > 0 else 0
    line = (f"{prefix} [{bar}] {done}/{total}"
            f"  新增:{ok} 跳过:{skip} 失败:{fail}"
            f"  已用:{elapsed:.0f}s ETA:{eta:.0f}s")
    if sys.stdout.isatty():
        sys.stdout.write(f"\r{line}   ")
        sys.stdout.flush()
    else:
        if done % 500 == 0 or done == total:
            _log(line)


def _current_quarter() -> tuple[int, int]:
    """返回当前季度 (year, quarter)"""
    now = datetime.now()
    return now.year, (now.month - 1) // 3 + 1


def _quarters_range(start_year: int, start_q: int = 1) -> list[tuple[int, int]]:
    """生成从 (start_year, start_q) 到当前季度的列表"""
    cur_year, cur_q = _current_quarter()
    quarters = []
    y, q = start_year, start_q
    while (y, q) <= (cur_year, cur_q):
        quarters.append((y, q))
        q += 1
        if q > 4:
            q = 1
            y += 1
    return quarters


class FundamentalDownloader:

    def __init__(self):
        self._bs = None

    def init_db(self,
                start_year: int = DEFAULT_START_YEAR,
                data_types: Optional[list] = None,
                force: bool = False) -> None:
        """
        全量初始化财务数据。
        start_year: 起始年份（默认2024）
        data_types: 下载类型列表，默认全部
        force: 强制重新下载已有数据
        """
        data_types = data_types or list(QUERY_FUNCS.keys())
        quarters = _quarters_range(start_year)

        _log("=" * 55)
        _log(f"Fundamental 域初始化  {start_year}Q1 ~ 今")
        _log(f"类型: {', '.join(data_types)}  季度数: {len(quarters)}")
        _log("=" * 55)

        # 获取股票列表
        codes = self._get_stock_codes()
        _log(f"股票列表: {len(codes)} 只")

        for dtype in data_types:
            _log(f"\n--- {dtype} ---")
            self._download_type(codes, dtype, quarters, force=force)

        _log("\nFundamental 域初始化完成！")
        self._print_stats()

    def update_db(self, data_types: Optional[list] = None) -> None:
        """
        增量更新：补全最新季度数据。
        每季度末（4月/8月/10月/次年1月）运行。
        """
        data_types = data_types or list(QUERY_FUNCS.keys())
        cur_year, cur_q = _current_quarter()

        _log(f"Fundamental 域增量更新  当前季度: {cur_year}Q{cur_q}")

        codes = self._get_stock_codes()

        for dtype in data_types:
            _log(f"\n--- 更新 {dtype} ---")
            # 只下载最近2个季度（防止漏数据）
            prev_q = cur_q - 1 if cur_q > 1 else 4
            prev_year = cur_year if cur_q > 1 else cur_year - 1
            quarters = [(prev_year, prev_q), (cur_year, cur_q)]
            self._download_type(codes, dtype, quarters, force=True)

        _log("Fundamental 域更新完成！")

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _set_bs_timeout(self, seconds: float) -> None:
        """借用 KlineDownloader 的 baostock timeout patch"""
        from data.downloader import KlineDownloader
        _helper = KlineDownloader.__new__(KlineDownloader)
        _helper._set_bs_timeout(seconds)

    def _bs_login_with_timeout(self, timeout_sec: int = 30) -> bool:
        """带超时的 baostock login，防止死循环"""
        import baostock as bs
        import threading

        result = {"success": False, "error": None}

        def _do_login():
            try:
                rs = bs.login()
                result["success"] = (rs.error_code == "0")
                result["error"] = rs.error_msg
            except Exception as e:
                result["error"] = str(e)

        t = threading.Thread(target=_do_login, daemon=True)
        t.start()
        t.join(timeout=timeout_sec)
        if t.is_alive():
            logger.warning(f"baostock login 超时（{timeout_sec}s），强制继续")
            return False
        return result["success"]

    def _get_stock_codes(self) -> list[str]:
        """从 baostock 获取 A 股代码列表"""
        import baostock as bs

        socket.setdefaulttimeout(60)
        ok = self._bs_login_with_timeout(30)
        if not ok:
            # 重试一次
            time.sleep(3)
            ok = self._bs_login_with_timeout(30)
        self._set_bs_timeout(60)
        socket.setdefaulttimeout(None)
        try:
            rs = bs.query_stock_basic()
            codes = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                full_code, _name, _ipo, _out, typ, status = row
                if status != "1" or typ != "1":
                    continue
                code = full_code.split(".")[1]
                if code[0] not in ("0", "3", "6"):
                    continue
                codes.append(code)
            return codes
        finally:
            bs.logout()
            time.sleep(3)

    def _download_type(self, codes: list[str], dtype: str,
                       quarters: list[tuple[int, int]],
                       force: bool = False) -> None:
        """下载指定类型的所有股票所有季度数据"""
        import baostock as bs

        func_name = QUERY_FUNCS[dtype]
        total = len(codes)
        ok = skip = fail = 0
        t0 = time.time()
        consecutive_fails = 0

        time.sleep(2)
        socket.setdefaulttimeout(10)
        self._bs_login_with_timeout(20)
        self._set_bs_timeout(10)

        try:
            for i, code in enumerate(codes):
                # 确定需要下载的季度
                if not force and store.exists(code, dtype):
                    ld = store.last_date(code, dtype)
                    if ld:
                        # 只下载最新报告期之后的季度
                        ld_year = int(ld[:4])
                        ld_month = int(ld[5:7])
                        ld_q = (ld_month - 1) // 3 + 1
                        pending_quarters = [
                            (y, q) for y, q in quarters
                            if (y, q) > (ld_year, ld_q)
                        ]
                        if not pending_quarters:
                            skip += 1
                            _print_bar(i + 1, total, ok, skip, fail,
                                       time.time() - t0, prefix=f"[{dtype}]")
                            continue
                    else:
                        pending_quarters = quarters
                else:
                    pending_quarters = quarters

                # 确定 baostock 市场前缀
                market = "sh" if code.startswith("6") else "sz"
                bs_code = f"{market}.{code}"

                # 按季度下载并合并
                all_rows = []
                all_fields = None
                quarter_fail = False

                for year, quarter in pending_quarters:
                    try:
                        query_func = getattr(bs, func_name)
                        rs = query_func(code=bs_code, year=year, quarter=quarter)
                        if rs.error_code != "0":
                            continue
                        if all_fields is None:
                            all_fields = rs.fields
                        while rs.next():
                            all_rows.append(rs.get_row_data())
                    except Exception as e:
                        logger.debug(f"{bs_code} {dtype} {year}Q{quarter} 失败: {e}")
                        quarter_fail = True
                        consecutive_fails += 1
                        if consecutive_fails >= 5:
                            logger.warning("连续失败5次，重新登录 baostock")
                            try:
                                bs.logout()
                            except Exception:
                                pass
                            time.sleep(3)
                            self._bs_login_with_timeout(20)
                            self._set_bs_timeout(10)
                            consecutive_fails = 0
                        break

                if all_rows and all_fields:
                    df = pd.DataFrame(all_rows, columns=all_fields)
                    store.save(code, df, dtype)
                    ok += 1
                    consecutive_fails = 0
                elif quarter_fail:
                    fail += 1
                else:
                    skip += 1

                _print_bar(i + 1, total, ok, skip, fail,
                           time.time() - t0, prefix=f"[{dtype}]")

        finally:
            bs.logout()

        if sys.stdout.isatty():
            print()
        _log(f"[{dtype}] 完成: 新增={ok} 跳过={skip} 失败={fail}")

    def _print_stats(self) -> None:
        s = store.stats()
        _log("Fundamental 域统计:")
        for dtype, info in s.items():
            _log(f"  {dtype:10s}: {info['count']:5d}只  {info['size_mb']:.1f}MB")
