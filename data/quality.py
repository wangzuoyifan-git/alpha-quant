"""
数据质量检测模块
DR1 多源交叉验证 + DR2 单源异常检测
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def check_price_anomaly(df: pd.DataFrame, code: str) -> List[Dict]:
    """
    DR2-01：单日涨跌幅超 ±20% 且无停牌标记 → 异常告警。

    返回异常记录列表 [{code, date, pct_chg, is_halt}]
    """
    anomalies = []
    if df is None or df.empty or len(df) < 2:
        return anomalies
    df = df.copy()
    if "pct_chg" not in df.columns and "close" in df.columns:
        if "preclose" in df.columns:
            df["pct_chg"] = (df["close"] / df["preclose"] - 1) * 100
        else:
            df["pct_chg"] = df["close"].pct_change() * 100
    for idx, row in df.iterrows():
        pct = float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else 0
        trade_status = str(row.get("trade_status", "1"))
        if abs(pct) > 20 and trade_status != "0":
            date_str = str(idx) if isinstance(idx, pd.Timestamp) else str(row.get("date", idx))
            anomalies.append({
                "code": code,
                "date": date_str[:10],
                "pct_chg": round(pct, 2),
                "is_halt": trade_status == "0",
            })
    return anomalies


def check_volume_anomaly(df: pd.DataFrame, code: str) -> List[Dict]:
    """
    DR2-02：成交量为 0 且无停牌标记 → 数据缺失告警。
    """
    anomalies = []
    if df is None or df.empty:
        return anomalies
    for idx, row in df.iterrows():
        volume = float(row.get("volume", 0))
        trade_status = str(row.get("trade_status", "1"))
        if volume == 0 and trade_status != "0":
            date_str = str(idx) if isinstance(idx, pd.Timestamp) else str(row.get("date", idx))
            anomalies.append({
                "code": code,
                "date": date_str[:10],
                "type": "volume_zero",
            })
    return anomalies


def check_field_missing(df: pd.DataFrame, code: str) -> List[Dict]:
    """
    DR2-03：关键字段（close/volume）为 NaN → 标记缺失。
    """
    missing = []
    if df is None or df.empty:
        return missing
    for idx, row in df.iterrows():
        date_str = str(idx) if isinstance(idx, pd.Timestamp) else str(row.get("date", idx))
        for field in ("close", "volume"):
            if pd.isna(row.get(field)):
                missing.append({
                    "code": code,
                    "date": date_str[:10],
                    "field": field,
                    "type": "field_nan",
                })
    return missing


def check_sequence_gap(df: pd.DataFrame, code: str,
                        max_gap_days: int = 5) -> List[Dict]:
    """
    DR2-04：时间序列断裂超过 max_gap_days 个自然日 → 告警。
    """
    gaps = []
    if df is None or df.empty or len(df) < 2:
        return gaps
    dates = df.index if isinstance(df.index, pd.DatetimeIndex) else \
        pd.to_datetime(df.index)
    for i in range(1, len(dates)):
        gap = (dates[i] - dates[i - 1]).days
        if gap > max_gap_days:
            gaps.append({
                "code": code,
                "from": str(dates[i - 1])[:10],
                "to": str(dates[i])[:10],
                "gap_days": gap,
            })
    return gaps


def check_cross_source(data_primary: Optional[pd.DataFrame],
                        data_secondary: Optional[pd.DataFrame],
                        code: str,
                        price_tolerance: float = 0.001,
                        volume_tolerance: float = 0.01) -> Dict:
    """
    DR1：多源交叉验证 — 两源收盘价偏差 ≤ 0.1%、成交量偏差 ≤ 1%。

    返回 {"ok": bool, "price_deviation": float|null, "volume_deviation": float|null}
    """
    result = {"ok": True, "price_deviation": None, "volume_deviation": None}

    if data_primary is None or data_primary.empty:
        return result
    if data_secondary is None or data_secondary.empty:
        result["ok"] = True
        logger.debug(f"交叉验证跳过 {code}：无次源数据")
        return result

    last_pri = data_primary.iloc[-1]
    last_sec = data_secondary.iloc[-1]

    if "close" in data_primary.columns and "close" in data_secondary.columns:
        close_pri = float(last_pri["close"])
        close_sec = float(last_sec["close"])
        if close_pri > 0 and close_sec > 0:
            deviation = abs(close_pri - close_sec) / close_pri
            result["price_deviation"] = round(deviation, 6)
            if deviation > price_tolerance:
                result["ok"] = False
                logger.warning(
                    f"DR1 交叉验证失败 {code}：收盘价偏差 {deviation * 100:.2f}% > {price_tolerance * 100}%"
                )

    vol_cols = ("volume", "vol")
    vol_col_pri = next((c for c in vol_cols if c in data_primary.columns), None)
    vol_col_sec = next((c for c in vol_cols if c in data_secondary.columns), None)
    if vol_col_pri and vol_col_sec:
        vol_pri = float(last_pri[vol_col_pri])
        vol_sec = float(last_sec[vol_col_sec])
        if vol_pri > 0 and vol_sec > 0:
            deviation = abs(vol_pri - vol_sec) / vol_pri
            result["volume_deviation"] = round(deviation, 6)
            if deviation > volume_tolerance:
                result["ok"] = False
                logger.warning(
                    f"DR1 交叉验证失败 {code}：成交量偏差 {deviation * 100:.2f}% > {volume_tolerance * 100}%"
                )

    return result


def run_quality_check(code: str, df: pd.DataFrame,
                       secondary_df: pd.DataFrame = None) -> Dict:
    """
    综合质量检查入口：对单只标的数据执行 DR1 + DR2 全部检查。

    返回 {"code": str, "anomalies": list, "cross_check": dict}
    """
    anomalies = []
    anomalies.extend(check_price_anomaly(df, code))
    anomalies.extend(check_volume_anomaly(df, code))
    anomalies.extend(check_field_missing(df, code))
    anomalies.extend(check_sequence_gap(df, code))

    cross = check_cross_source(df, secondary_df, code)

    return {
        "code": code,
        "anomalies": anomalies,
        "cross_check": cross,
        "passed": cross["ok"] and len(anomalies) == 0,
    }
