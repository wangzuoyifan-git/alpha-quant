"""
agents/etf_agent.py — ETF 独立评分体系（细分主题 ETF）

定位：ETF 是对细分行业/主题的市场定价代理。对工业软件/半导体设备/机床/
创新药/军工等细分主题 ETF，单纯动量或申万 L1 行业评分过于粗糙。本模块产出
ETF 综合评分，指导 ETF 持仓管理（portfolio）与 L3 个股选择联动。

三维评分：
  ① 技术动量（technical）：用 ETF 自身价格序列
     - 绝对动量：N 日涨幅（跑赢无风险利率）
     - 相对动量：vs 沪深300（000300）
     - 趋势强度：站上 MA20/MA60
  ② 估值分位（valuation，过渡方案=选项C）：
     ETF→申万L2 映射（etf_config.sw_l2_codes），复用 L2 三维评分的估值维度
     （成员股 pe_ttm 历史分位聚合，高=低估）。后续若升级 Tushare 积分解锁
     sw_daily/fund_portfolio，可替换为官方行业 PE/PB 与真实成分股加权。
  ③ 政策配置（policy）：etf_config.policy_outlook 人工维护，直接消费。

数据来源：
  - 技术面：data/market/cn/etf/ + data/market/cn/index/000300（本地已有）
  - 估值面：agents.industry_agent.get_industry_score（L2 成员股聚合）
  - 政策面：data/etf_config.json（手工维护）

权重默认 {技术 0.45, 估值 0.25, 政策 0.30}，缺失维度按权重重归一。
"""
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "data" / "etf_config.json"

# 三维默认权重（技术/估值/政策）
_DEFAULT_WEIGHTS = {"technical": 0.45, "valuation": 0.25, "policy": 0.30}

# 政策展望 → 分数映射
_POLICY_SCORE = {"positive": 75.0, "neutral": 50.0, "negative": 25.0}

_RISK_FREE_ANNUAL = 0.02
_DEFAULT_BENCHMARK = "000300"


# ═══════════════════════════════════════════════════════════════
# 配置加载
# ═══════════════════════════════════════════════════════════════

_config_cache = None


def load_etf_config() -> dict:
    """加载 data/etf_config.json，去掉 _meta 段。缺失返回 {}。"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not _CONFIG_PATH.exists():
        _config_cache = {}
        return _config_cache
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        _config_cache = {k: v for k, v in raw.items() if not k.startswith("_")}
    except Exception as e:
        logger.warning(f"etf_config.json 加载失败: {e}")
        _config_cache = {}
    return _config_cache


def get_etf_meta(code: str) -> dict:
    """返回单只 ETF 的配置（name/sw_l2_codes/policy_outlook 等），缺失返回 {}。"""
    return load_etf_config().get(str(code), {})


# ═══════════════════════════════════════════════════════════════
# ① 技术动量维度
# ═══════════════════════════════════════════════════════════════

def _benchmark_return(bench_code: str, lookback: int, date: str = None) -> float:
    """基准（沪深300）的 lookback 日涨幅 %，取不到返回 nan。"""
    import data.store as store
    df = store.load(bench_code, asset_type="index", market="cn", end=date)
    if df is None or df.empty or len(df) < lookback + 1:
        return float("nan")
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    if len(close) < lookback + 1:
        return float("nan")
    return (float(close.iloc[-1]) / float(close.iloc[-lookback]) - 1) * 100


def _technical_score(df: pd.DataFrame, bench_ret: float,
                     lookback: int = 20) -> tuple:
    """
    ETF 技术动量评分 0~100。
    返回 (score, detail_dict)。数据不足返回 (nan, {})。
    """
    if df is None or df.empty or len(df) < lookback + 1:
        return float("nan"), {}
    close = pd.to_numeric(df["close"], errors="coerce")
    if close.isna().all():
        return float("nan"), {}

    last = float(close.iloc[-1])
    base = float(close.iloc[-lookback])
    if base <= 0:
        return float("nan"), {}

    abs_mom = (last / base - 1) * 100              # 绝对动量
    rf_period = _RISK_FREE_ANNUAL * lookback / 252 * 100
    rel_mom = abs_mom - bench_ret if not np.isnan(bench_ret) else float("nan")

    # 基础分：绝对动量映射（动量0%→50分，每1%涨幅+2分）
    score = 50.0 + abs_mom * 2.0

    # 绝对动量未跑赢无风险利率：扣分（不直接归零，作为弱信号）
    if abs_mom < rf_period:
        score -= 15.0

    # 相对动量：跑赢基准加分，跑输扣分
    if not np.isnan(rel_mom):
        score += max(-15.0, min(15.0, rel_mom * 1.0))

    # 趋势确认
    if len(close) >= 20 and last > float(close.rolling(20).mean().iloc[-1]):
        score += 5.0
    if len(close) >= 60 and last > float(close.rolling(60).mean().iloc[-1]):
        score += 5.0

    score = max(0.0, min(100.0, score))
    detail = {
        "abs_momentum": round(abs_mom, 1),
        "rel_momentum": round(rel_mom, 1) if not np.isnan(rel_mom) else None,
        "above_rf": abs_mom >= rf_period,
    }
    return round(score, 1), detail


# ═══════════════════════════════════════════════════════════════
# ② 估值分位维度（过渡：L2 成员股聚合代理）
# ═══════════════════════════════════════════════════════════════

def _valuation_score(sw_l2_codes: list, date: str = None) -> tuple:
    """
    ETF 估值分位代理：取所映射申万 L2 行业估值维度（低估分 0~100，高=低估）
    的均值。多个 L2 行业取均值。无映射或数据缺失返回 (nan, {})。

    返回 (score, detail_dict)。
    """
    if not sw_l2_codes:
        return float("nan"), {}
    from agents.industry_agent import get_industry_score

    vals = []
    used = {}
    for code in sw_l2_codes:
        info = get_industry_score(code, level="L2", date=date)
        if info and info.get("valuation") is not None:
            vals.append(info["valuation"])
            used[code] = info["valuation"]
    if not vals:
        return float("nan"), {}
    score = float(np.mean(vals))
    return round(score, 1), {"by_l2": used}


# ═══════════════════════════════════════════════════════════════
# ③ 政策配置维度
# ═══════════════════════════════════════════════════════════════

def _policy_score(meta: dict) -> tuple:
    """政策展望 → 分数。无配置返回 (nan, {})。"""
    outlook = meta.get("policy_outlook")
    if not outlook or outlook not in _POLICY_SCORE:
        return float("nan"), {}
    return _POLICY_SCORE[outlook], {
        "outlook": outlook,
        "tags": meta.get("policy_tags", []),
        "note": meta.get("note", ""),
    }


# ═══════════════════════════════════════════════════════════════
# 三维合并
# ═══════════════════════════════════════════════════════════════

def _combine(dims: dict, weights: dict) -> float:
    """缺失维度按权重重归一加权。全缺返回 nan。"""
    present = [(weights[k], v) for k, v in dims.items()
               if v is not None and not np.isnan(v) and weights.get(k, 0) > 0]
    if not present:
        return float("nan")
    wsum = sum(w for w, _ in present)
    if wsum <= 0:
        return float("nan")
    return round(sum(w * v for w, v in present) / wsum, 1)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def evaluate_etf(code: str, df: pd.DataFrame = None,
                 weights: dict = None, date: str = None,
                 lookback: int = 20) -> dict:
    """
    单只 ETF 三维综合评分。

    Args:
        code: ETF 代码（6位）
        df: ETF 日线 DataFrame（含 close，DatetimeIndex）。None 则自动加载。
        weights: 三维权重 {"technical","valuation","policy"}，缺省用默认。
        date: 评估日期（截止），默认最新。
        lookback: 动量周期，默认 20。

    Returns:
        {
          "code", "name", "theme",
          "total_score": float 0~100 | None,
          "technical": float|None, "valuation": float|None, "policy": float|None,
          "detail": {...各维度明细...},
          "advice": str（基于综合评分的减/持/加建议）,
          "available": bool（是否有任何有效维度）
        }
    """
    weights = weights or dict(_DEFAULT_WEIGHTS)
    meta = get_etf_meta(code)
    name = meta.get("name", code)

    if df is None:
        import data.store as store
        df = store.load(code, asset_type="etf", market="cn", end=date)

    # ① 技术
    bench = meta.get("benchmark", _DEFAULT_BENCHMARK)
    bench_ret = _benchmark_return(bench, lookback, date=date)
    tech, tech_detail = _technical_score(df, bench_ret, lookback=lookback)

    # ② 估值（L2 代理）
    val, val_detail = _valuation_score(meta.get("sw_l2_codes", []), date=date)

    # ③ 政策
    pol, pol_detail = _policy_score(meta)

    dims = {
        "technical": None if np.isnan(tech) else tech,
        "valuation": None if np.isnan(val) else val,
        "policy": None if np.isnan(pol) else pol,
    }
    total = _combine(
        {k: (v if v is not None else float("nan")) for k, v in dims.items()},
        weights)

    available = any(v is not None for v in dims.values())
    advice = _advice(total, dims)

    return {
        "code": code,
        "name": name,
        "theme": meta.get("theme", ""),
        "total_score": None if np.isnan(total) else total,
        "technical": dims["technical"],
        "valuation": dims["valuation"],
        "policy": dims["policy"],
        "detail": {
            "technical": tech_detail,
            "valuation": val_detail,
            "policy": pol_detail,
            "benchmark_return": round(bench_ret, 1) if not np.isnan(bench_ret) else None,
            "weights": weights,
        },
        "advice": advice,
        "available": available,
    }


def _advice(total: float, dims: dict) -> str:
    """基于综合评分 + 维度组合给出持仓建议。"""
    if np.isnan(total):
        return "数据不足，无法评估"
    tech = dims.get("technical")
    val = dims.get("valuation")

    if total >= 65:
        base = "综合强势，可持有/逢回调加配"
    elif total >= 45:
        base = "中性，持有观察"
    else:
        base = "综合偏弱，建议减配"

    # 风险提示：估值过高（低估分<25）即便动量强也提示
    if val is not None and val < 25:
        base += "；估值偏贵（隐含PE历史高分位），追高需谨慎"
    # 技术转弱单独提示
    if tech is not None and tech < 40:
        base += "；技术动量转弱"
    return base


def evaluate_portfolio_etfs(codes: list, weights: dict = None,
                            date: str = None) -> dict:
    """批量评估多只 ETF，返回 {code: evaluate_etf结果}。"""
    return {c: evaluate_etf(c, weights=weights, date=date) for c in codes}
