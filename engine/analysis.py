"""
engine/analysis.py — A2/A3 辅助分析模块

根据需求 A2 提供每条信号的背景信息：
 P0: 技术面摘要（触发原因/涨跌幅/量比/换手率/20日价格区间）
 P0: 资金流摘要（近5日主力净流入额/占比）
 P0: 基本面摘要（ROE/净利润同比/毛利率/资产负债率/PE/PB）
 P1: 行业背景（申万行业/近5日涨跌/行业资金流排名）
 A3: 数据缺失时标注"数据不可用"
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict

import pandas as pd
import numpy as np

import data.store as store
from data.store_meta import load as load_meta
from data.store_fundamental import load as load_fund, last_date as fund_last_date
from data.store_moneyflow import load as load_mf, list_codes as list_mf_codes

logger = logging.getLogger(__name__)


def analyze_signal(signal: Dict, date: str = None) -> Dict:
    """
    对一个信号做辅助分析，返回完整辅助信息 dict。

    参数:
      signal: {"code", "name", "signal", "price", "score", "reason"} 格式

    返回:
      {"tech_summary": {...}, "fund_flow": {...}, "fundamental": {...},
       "industry": {...}, "data_unavailable": [...]}
    """
    code = signal.get("code", "")
    name = signal.get("name", "")
    analysis = {
        "code": code,
        "name": name,
        "tech_summary": _get_tech_summary(code),
        "fund_flow": _get_fund_flow_summary(code),
        "fundamental": _get_fundamental_summary(code),
        "industry": _get_industry_context(code),
        "data_unavailable": [],
    }
    for section in ("tech_summary", "fund_flow", "fundamental", "industry"):
        if not analysis[section] or analysis[section].get("available") is False:
            analysis["data_unavailable"].append(section)
    return analysis


# ═══════════════════════════════════════════════════════════════
# P0 技术面摘要
# ═══════════════════════════════════════════════════════════════

def _get_tech_summary(code: str) -> Dict:
    from data.store_meta import load as load_calendar
    df = store.load(code, asset_type="stock", market="cn")
    if df is None or df.empty:
        return {"available": False, "note": "无本地行情数据"}

    try:
        last = df.iloc[-1]
        close = float(last["close"])
        volume = float(last.get("volume", 0))
        turnover = float(last.get("turn", np.nan))
        pct_chg = float(last.get("pct_chg", np.nan))

        if len(df) >= 20:
            recent = df.iloc[-20:]
            high_20 = float(recent["high"].max())
            low_20 = float(recent["low"].min())
            avg_vol_5 = float(df["volume"].iloc[-6:-1].mean()) if len(df) >= 6 else volume
            vol_ratio = volume / avg_vol_5 if avg_vol_5 > 0 else 1.0
        else:
            high_20, low_20, vol_ratio = close, close, 1.0

        return {
            "available": True,
            "price": round(close, 2),
            "pct_chg": round(pct_chg, 2) if pd.notna(pct_chg) else None,
            "turnover": round(turnover, 2) if pd.notna(turnover) else None,
            "vol_ratio": round(vol_ratio, 2),
            "price_high_20d": round(high_20, 2),
            "price_low_20d": round(low_20, 2),
            "pe_ttm": round(float(last.get("pe_ttm", np.nan)), 2) if pd.notna(last.get("pe_ttm")) else None,
            "pb_mrq": round(float(last.get("pb_mrq", np.nan)), 2) if pd.notna(last.get("pb_mrq")) else None,
        }
    except Exception as e:
        logger.debug(f"技术面摘要 {code}: {e}")
        return {"available": False, "note": str(e)}


# ═══════════════════════════════════════════════════════════════
# P0 资金流摘要
# ═══════════════════════════════════════════════════════════════

def _get_fund_flow_summary(code: str) -> Dict:
    try:
        mf_codes = list_mf_codes("stock")
        if code not in mf_codes:
            return {"available": False, "note": "无个股资金流数据"}

        df = load_mf("stock", code=code)
        if df is None or df.empty:
            return {"available": False, "note": "无资金流数据"}

        recent = df.iloc[-5:] if len(df) >= 5 else df
        net_main_5d = float(recent.get("net_mf_amount", pd.Series([0])).sum())
        # D7 情绪因子：融资余额近5日变化
        from data.store_moneyflow import load as load_margin
        margin = load_margin("margin", code=code)
        margin_change = None
        if margin is not None and not margin.empty and "rzye" in margin.columns:
            m_recent = margin.iloc[-5:] if len(margin) >= 5 else margin
            if len(m_recent) >= 2:
                margin_change = float(m_recent["rzye"].iloc[-1]) - float(m_recent["rzye"].iloc[0])

        return {
            "available": True,
            "net_main_5d": round(net_main_5d, 0) if pd.notna(net_main_5d) else None,
            "margin_change_5d": round(margin_change, 0) if margin_change else None,
        }
    except Exception as e:
        logger.debug(f"资金流摘要 {code}: {e}")
        return {"available": False, "note": str(e)}


# ═══════════════════════════════════════════════════════════════
# P0 基本面摘要
# ═══════════════════════════════════════════════════════════════

def _get_fundamental_summary(code: str) -> Dict:
    try:
        profit = load_fund(code, "profit")
        if profit is None or profit.empty:
            return {"available": False, "note": "无季频财务数据"}

        latest = profit.iloc[-1]
        return {
            "available": True,
            "reported_date": fund_last_date(code, "profit") or "N/A",
            "roe": round(float(latest.get("roew", np.nan)), 2) if pd.notna(latest.get("roew")) else None,
            "netprofit_yoy": round(float(latest.get("netprofit_yoy", np.nan)), 2) if pd.notna(latest.get("netprofit_yoy")) else None,
            "grossmargin": None,  # 财报中不一定有，标注不可用
            "debt_ratio": _safe_float(latest.get("debt_assets_ratio")),
        }
    except Exception as e:
        logger.debug(f"基本面摘要 {code}: {e}")
        return {"available": False, "note": str(e)}


# ═══════════════════════════════════════════════════════════════
# P1 行业背景
# ═══════════════════════════════════════════════════════════════

def _get_industry_context(code: str) -> Dict:
    try:
        sw = load_meta("industry_sw")
        if sw is None or sw.empty:
            return {"available": False, "note": "无行业分类数据"}

        code_col = next((c for c in sw.columns if "code" in c.lower()), None)
        if code_col is None:
            return {"available": False, "note": "行业数据缺少代码列"}

        match = sw[sw[code_col].astype(str) == str(code)]
        if match.empty:
            return {"available": False, "note": f"代码 {code} 未匹配到行业"}

        # 行业名称列：industry_name 或 industry
        ind_col = "industry_name" if "industry_name" in sw.columns else \
            next((c for c in sw.columns if "industry" in c.lower() and "code" not in c.lower()), None)
        industry = str(match.iloc[0].get(ind_col or sw.columns[-1], ""))
        return {
            "available": True,
            "industry": industry or None,
            "note": "行业涨跌需行业指数日线数据（sw_index）",
        }
    except Exception as e:
        logger.debug(f"行业背景 {code}: {e}")
        return {"available": False, "note": str(e)}


def _safe_float(val) -> Optional[float]:
    """安全转换 float，NaN/None 返回 None"""
    if val is None or not pd.notna(val):
        return None
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return None


def format_analysis_markdown(analysis: Dict) -> str:
    """将辅助分析 dict 格式化为可推送的 markdown 文本"""
    lines = []
    code = analysis.get("code", "")
    name = analysis.get("name", "")

    tech = analysis.get("tech_summary", {})
    if tech.get("available"):
        lines.append(f"**技术面** PE{tech.get('pe_ttm','N/A')} PB{tech.get('pb_mrq','N/A')} "
                     f"换手{tech.get('turnover','N/A')}% 量比{tech.get('vol_ratio','N/A')}x "
                     f"20日区间 ¥{tech.get('price_low_20d','N/A')}-{tech.get('price_high_20d','N/A')}")
    else:
        lines.append("**技术面** 数据不可用")

    flow = analysis.get("fund_flow", {})
    if flow.get("available"):
        ft = flow.get("net_main_5d")
        margin = flow.get("margin_change_5d")
        flow_line = f"**资金流** 近5日主力净流入{_fmt_num(ft)}"
        if margin is not None:
            flow_line += f" 融资余额变化{_fmt_num(margin)}"
        lines.append(flow_line)
    else:
        lines.append("**资金流** 数据不可用")

    fund = analysis.get("fundamental", {})
    if fund.get("available"):
        lines.append(f"**基本面** ROE{fund.get('roe','N/A')}% 净利同比{fund.get('netprofit_yoy','N/A')}% "
                     f"负债率{fund.get('debt_ratio','N/A')}% 报告期{fund.get('reported_date','N/A')}")
    else:
        lines.append("**基本面** 数据不可用")

    ind = analysis.get("industry", {})
    if ind.get("available") and ind.get("industry"):
        lines.append(f"**行业** {ind['industry']}")
    else:
        lines.append("**行业** 数据不可用")

    return "\n".join(lines)


def _fmt_num(val) -> str:
    if val is None:
        return "N/A"
    if abs(val) >= 1e8:
        return f"{val/1e8:.1f}亿"
    if abs(val) >= 1e4:
        return f"{val/1e4:.0f}万"
    return f"{val:.0f}"
