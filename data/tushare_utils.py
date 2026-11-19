"""Tushare 调用加固：空结果重试 + 明确失败。

背景：tushare 的 DataApi.query 在 HTTP 非 2xx（5xx/429 等）时直接返回空
DataFrame，不抛异常（见 tushare/pro/client.py）。单次网络抖动会被上层误判为
"无数据/无需更新"，导致每日更新静默空跑。本模块对关键的"基础列表"类接口
做非空校验与指数退避重试，重试耗尽仍为空则抛 TushareEmptyError。
"""
import logging
import time

logger = logging.getLogger(__name__)


class TushareEmptyError(RuntimeError):
    """Tushare 关键接口连续返回空结果（通常是服务端异常/限流被静默吞掉）。"""


def call_nonempty(fn, *, label: str, retries: int = 3,
                  wait_base: float = 2.0, min_rows: int = 1):
    """调用 Tushare 接口，空结果或异常时重试，耗尽仍失败则抛错。

    Args:
        fn: 无参可调用，返回 DataFrame。
        label: 日志/异常中的接口描述。
        retries: 总尝试次数。
        wait_base: 退避基数，第 n 次失败后等待 wait_base ** n 秒。
        min_rows: 视为有效结果的最小行数。
    """
    last_err = "未知错误"
    for attempt in range(retries):
        try:
            df = fn()
            rows = 0 if df is None else len(df)
            if rows >= min_rows:
                return df
            last_err = f"返回空结果（rows={rows}）"
        except Exception as e:
            last_err = str(e)

        if attempt < retries - 1:
            wait = wait_base ** attempt
            logger.warning("%s 第%d次失败（%s），%.0fs 后重试",
                           label, attempt + 1, last_err, wait)
            time.sleep(wait)

    raise TushareEmptyError(f"{label} 连续 {retries} 次失败：{last_err}")
