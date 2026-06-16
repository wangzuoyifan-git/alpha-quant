"""
agents/industry_agent.py — L2 行业轮动层（台阶二：三维评分）

三维评分：景气度① × w1 + 估值② × w2 + 资金流③ × w3
权重 l2_weight_config 由 L1 按 regime 动态产出，从 decision_context 传入。

数据对齐方案（L2-R5b）：
  所有层级（L1/L2/L3）统一走"个股自底向上聚合"路径，按 industry_member 的
  对应层级代码（l1_code/l2_code/l3_code）聚合，代码匹配（非名称匹配）。

三个维度数据源：
  ① 景气度：fundamental growth.YOYNI（净利同比）+ profit.npMargin（净利率），行业中位数
  ② 估值：market pe_ttm 历史时序分位（per-stock→行业中位数，低分位=便宜→高分）
  ③ 资金流：moneyflow/stock net_mf_amount 行业累加
"""
import logging
import json
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# L2 三维评分按 (level, date) 缓存，避免重复读取 5500+ parquet（每日只算一次）
_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache" / "l2_scores"

# 按 risk_mode 决定行业池宽度（Top N 行业）
_POOL_SIZE = {
    "defensive": (5, 7),
    "balanced":  (6, 10),
    "aggressive": (8, 12),
}

# 默认三维权重（L1 未传 weights 时的兜底）
_DEFAULT_WEIGHTS = {"w1": 0.3, "w2": 0.25, "w3": 0.45}

_LEVEL_CODE_COL = {"L1": "l1_code", "L2": "l2_code", "L3": "l3_code"}
_LEVEL_NAME_COL = {"L1": "l1_name", "L2": "l2_name", "L3": "l3_name"}


# ═══════════════════════════════════════════════════════════════
# 通用归一化与三维合并
# ═══════════════════════════════════════════════════════════════

def _normalize_to_100(raw: dict) -> dict:
    """min-max 归一化到 0~100。全部相等时返回 50。空 dict 返回空。"""
    if not raw:
        return {}
    vals = list(raw.values())
    min_v, max_v = min(vals), max(vals)
    if max_v > min_v:
        return {k: round((v - min_v) / (max_v - min_v) * 100, 1) for k, v in raw.items()}
    return {k: 50.0 for k in raw}


def _combine_dims(dims: dict, weights: dict) -> dict:
    """
    三维加权合并，缺失维度权重重归一。

    Args:
        dims: {"prosperity": {ind: 0~100}, "valuation": {...}, "moneyflow": {...}}
              （各维度已归一化到 0~100；某维度为空 dict 表示整体缺失）
        weights: {"w1": ..., "w2": ..., "w3": ...}

    Returns:
        {industry_code: total_score 0~100}

    规则：对每个行业，仅用该行业有值的维度，按权重重归一化后加权。
          w=0 的维度不参与（即使有数据）。
    """
    dim_weight = {
        "prosperity": float(weights.get("w1", 0.0)),
        "valuation": float(weights.get("w2", 0.0)),
        "moneyflow": float(weights.get("w3", 0.0)),
    }
    # 收集所有出现过的行业代码
    all_codes = set()
    for d in dims.values():
        all_codes.update(d.keys())

    result = {}
    for code in all_codes:
        present = []  # (weight, value)
        for dim_name, w in dim_weight.items():
            if w <= 0:
                continue
            dval = dims.get(dim_name, {})
            if code in dval:
                present.append((w, dval[code]))
        if not present:
            continue
        wsum = sum(w for w, _ in present)
        if wsum <= 0:
            continue
        score = sum(w * v for w, v in present) / wsum
        result[code] = round(score, 1)
    return result


def _judge_horizon(prosperity=None, valuation=None, moneyflow=None) -> str:
    """
    三维组合判断 time_horizon（L2-R2a）。

    - 高景气(①≥60) + 低估值(②≥60) → long（配置仓长期价值）
    - 高资金流(③≥60) + 低景气(①<50) → short（交易仓动量，资金驱动非基本面）
    - 其余 → medium
    """
    p = prosperity if prosperity is not None else 50.0
    v = valuation if valuation is not None else 50.0
    m = moneyflow if moneyflow is not None else 50.0

    if prosperity is not None and valuation is not None and p >= 60 and v >= 60:
        return "long"
    if moneyflow is not None and m >= 60 and (prosperity is None or p < 50):
        # 注意：prosperity 缺失时不触发（避免误判），需有景气度数据且偏低
        if prosperity is not None:
            return "short"
    return "medium"


# ═══════════════════════════════════════════════════════════════
# 成分股映射
# ═══════════════════════════════════════════════════════════════

def _load_member_map(level: str):
    """
    加载 industry_member，返回 (name_map, code_to_ind)。

    name_map: {industry_code: industry_name}
    code_to_ind: {code6: industry_code}
    数据缺失时返回 ({}, {})。
    """
    import data.store_meta as sm

    member = sm.load("industry_member")
    if member is None or member.empty:
        return {}, {}
    code_col = _LEVEL_CODE_COL.get(level)
    name_col = _LEVEL_NAME_COL.get(level)
    if code_col is None or code_col not in member.columns:
        return {}, {}

    member = member.copy()
    member["code6"] = member["ts_code"].astype(str).str.split(".").str[0]
    code_to_ind = dict(zip(member["code6"], member[code_col].astype(str)))
    name_map = dict(zip(member[code_col].astype(str), member[name_col].astype(str)))
    return name_map, code_to_ind


# ═══════════════════════════════════════════════════════════════
# ③ 资金流维度
# ═══════════════════════════════════════════════════════════════

def _parallel_read(code_to_ind: dict, reader):
    """
    对每个 (code6, ind) 调用 reader(code6) → 单只股票的指标值（或 None）。

    注：实测 parquet 解码持有 GIL，线程并发反而更慢，故串行读取。
    返回 [(ind, value), ...]（跳过 None）。
    """
    out = []
    for code6, ind in code_to_ind.items():
        try:
            val = reader(code6)
        except Exception:
            continue
        if val is not None:
            out.append((ind, val))
    return out


def _raw_moneyflow(code_to_ind: dict, level: str, date: str = None) -> dict:
    """个股 net_mf_amount 按行业累加。返回 {industry_code: net_amount_sum}。"""
    base = Path(__file__).parent.parent / "data" / "moneyflow" / "stock"
    if not base.exists():
        return {}
    target_ts = pd.Timestamp(date) if date else None

    def _read(code6):
        f = base / f"{code6}.parquet"
        if not f.exists():
            return None
        df = pd.read_parquet(f, columns=["net_mf_amount"])
        if df.empty:
            return None
        if target_ts is not None and isinstance(df.index, pd.DatetimeIndex):
            df = df[df.index <= target_ts]
            if df.empty:
                return None
        return float(df.iloc[-1]["net_mf_amount"])

    agg: dict = {}
    for ind, val in _parallel_read(code_to_ind, _read):
        agg[ind] = agg.get(ind, 0.0) + val
    return agg


# ═══════════════════════════════════════════════════════════════
# ① 景气度维度
# ═══════════════════════════════════════════════════════════════

def _raw_prosperity(code_to_ind: dict, level: str) -> dict:
    """
    景气度：行业成分股 YOYNI（净利同比）中位数 + npMargin（净利率）中位数。
    两指标各按行业归一化后取均值，返回 {industry_code: 0~100 combined}。

    性能：直接按单列读 parquet（避免 sf.load 读全列）。
    """
    growth_dir = Path(__file__).parent.parent / "data" / "fundamental" / "growth"
    profit_dir = Path(__file__).parent.parent / "data" / "fundamental" / "profit"

    def _read_yoy(code6):
        f = growth_dir / f"{code6}.parquet"
        if not f.exists():
            return None
        v = pd.read_parquet(f, columns=["YOYNI"])["YOYNI"].dropna()
        return float(v.iloc[-1]) if len(v) else None

    def _read_npm(code6):
        f = profit_dir / f"{code6}.parquet"
        if not f.exists():
            return None
        v = pd.read_parquet(f, columns=["npMargin"])["npMargin"].dropna()
        return float(v.iloc[-1]) if len(v) else None

    by_ind_yoy: dict = {}
    by_ind_npm: dict = {}
    for ind, val in _parallel_read(code_to_ind, _read_yoy):
        by_ind_yoy.setdefault(ind, []).append(val)
    for ind, val in _parallel_read(code_to_ind, _read_npm):
        by_ind_npm.setdefault(ind, []).append(val)

    if not by_ind_yoy and not by_ind_npm:
        return {}

    med_yoy = {ind: float(np.median(vals)) for ind, vals in by_ind_yoy.items()}
    med_npm = {ind: float(np.median(vals)) for ind, vals in by_ind_npm.items()}

    norm_yoy = _normalize_to_100(med_yoy)
    norm_npm = _normalize_to_100(med_npm)

    combined: dict = {}
    all_inds = set(norm_yoy) | set(norm_npm)
    for ind in all_inds:
        parts = [x for x in (norm_yoy.get(ind), norm_npm.get(ind)) if x is not None]
        if parts:
            combined[ind] = float(np.mean(parts))
    return combined


# ═══════════════════════════════════════════════════════════════
# ② 估值维度（历史时序分位）
# ═══════════════════════════════════════════════════════════════

def _pe_history_percentile(pe_series: pd.Series) -> float:
    """
    当前 PE 在自身历史区间的分位（0~1，低=便宜），返回"低估分" = 1 - 分位。
    仅用 PE>0 的有效值。数据不足返回 NaN。
    """
    v = pe_series.dropna()
    v = v[v > 0]
    if len(v) < 20:  # 历史太短不可靠
        return float("nan")
    current = float(v.iloc[-1])
    pct = float((v <= current).mean())  # 当前值的历史分位
    return 1.0 - pct  # 低估分：越便宜越高


def _raw_valuation(code_to_ind: dict, level: str, date: str = None) -> dict:
    """
    估值：每只成分股 PE 历史时序分位的"低估分"，行业取中位数。
    返回 {industry_code: 0~1 低估分中位数}（高=低估）。

    性能：直接按单列 pe_ttm 读 parquet（避免 store.load 读全列）。
    """
    stock_dir = Path(__file__).parent.parent / "data" / "market" / "cn" / "stock"
    target_ts = pd.Timestamp(date) if date else None

    def _read(code6):
        f = stock_dir / f"{code6}.parquet"
        if not f.exists():
            return None
        df = pd.read_parquet(f, columns=["pe_ttm"])
        if df.empty:
            return None
        if target_ts is not None and isinstance(df.index, pd.DatetimeIndex):
            df = df[df.index <= target_ts]
            if df.empty:
                return None
        score = _pe_history_percentile(df["pe_ttm"])
        return None if np.isnan(score) else score

    by_ind: dict = {}
    for ind, val in _parallel_read(code_to_ind, _read):
        by_ind.setdefault(ind, []).append(val)

    return {ind: float(np.median(vals)) for ind, vals in by_ind.items()}


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def _aggregate_raw_dims_cached(code_to_ind: dict, level: str, date: str = None):
    """
    聚合三维原始值（moneyflow/prosperity/valuation），按 (level, date) 落盘缓存。

    原始聚合需读 5500+ parquet（~50s），但只随 date 变化（日级），
    故按 level+date 缓存，当日重复调用直接命中。

    返回 (raw_mf, raw_pros, raw_val) 三个 {industry_code: raw_value} dict。
    """
    cache_key = f"{level}_{date or 'latest'}"
    cache_file = _CACHE_DIR / f"{cache_key}.json"

    if cache_file.exists():
        try:
            with open(cache_file) as f:
                cached = json.load(f)
            return cached["moneyflow"], cached["prosperity"], cached["valuation"]
        except Exception:
            pass

    raw_mf = _raw_moneyflow(code_to_ind, level, date=date)
    raw_pros = _raw_prosperity(code_to_ind, level)
    raw_val = _raw_valuation(code_to_ind, level, date=date)

    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump({"moneyflow": raw_mf, "prosperity": raw_pros,
                       "valuation": raw_val}, f)
    except Exception as e:
        logger.debug(f"L2 缓存写入失败: {e}")

    return raw_mf, raw_pros, raw_val


def evaluate_industry_rotation(risk_mode: str = "balanced",
                                date: str = None,
                                level: str = "L1",
                                weights: dict = None) -> dict:
    """
    L2 行业轮动层：三维评分（景气度/估值/资金流），输出行业池。

    Args:
        risk_mode: L1 传入的 risk_mode（defensive/balanced/aggressive）
        date: 评估日期，默认最新
        level: 申万行业层级 L1/L2/L3（统一走成分股聚合）
        weights: 三维权重 {"w1","w2","w3"}（景气/估值/资金流），
                 由 L1 的 l2_weight_config 传入，缺省用 _DEFAULT_WEIGHTS

    Returns:
        {success, allowed_industries, industry_scores, industry_detail,
         industry_horizon_map, allowed_styles, fallback, risk_mode, level}
    """
    try:
        weights = weights or dict(_DEFAULT_WEIGHTS)
        name_map, code_to_ind = _load_member_map(level)
        if not code_to_ind:
            logger.warning("L2 行业分类数据缺失，返回空池，L3 退化为全市场扫描")
            return _fallback(risk_mode)

        # 三维原始值聚合（按 level+date 缓存，避免每日重复读 5500+ parquet）
        raw_mf, raw_pros, raw_val = _aggregate_raw_dims_cached(
            code_to_ind, level, date)

        # 各维度归一化到 0~100
        dim_mf = _normalize_to_100(raw_mf)
        dim_pros = _normalize_to_100(raw_pros)
        dim_val = _normalize_to_100(raw_val)

        dims = {"prosperity": dim_pros, "valuation": dim_val, "moneyflow": dim_mf}
        combined = _combine_dims(dims, weights)
        if not combined:
            logger.warning("L2 三维评分无有效行业，退化全市场")
            return _fallback(risk_mode)

        # 排序构建 scored
        scored_rows = []
        for code, total in combined.items():
            scored_rows.append({
                "industry_code": code,
                "industry_name": name_map.get(code, code),
                "score": total,
                "prosperity_score": dim_pros.get(code),
                "valuation_score": dim_val.get(code),
                "moneyflow_score": dim_mf.get(code),
                "net_amount_sum": raw_mf.get(code),
            })
        scored_rows.sort(key=lambda r: r["score"], reverse=True)

        # 按 risk_mode 取 Top N
        _, top_n = _POOL_SIZE.get(risk_mode, (6, 10))
        top_n = min(top_n, len(scored_rows))
        selected = scored_rows[:top_n]

        allowed = [r["industry_code"] for r in selected]

        def _build_detail(rows):
            detail, horizon_map = {}, {}
            for r in rows:
                code = r["industry_code"]
                horizon = _judge_horizon(
                    prosperity=r["prosperity_score"],
                    valuation=r["valuation_score"],
                    moneyflow=r["moneyflow_score"],
                )
                detail[code] = {
                    "industry_name": r["industry_name"],
                    "score": r["score"],
                    "moneyflow": r["net_amount_sum"],
                    "prosperity": r["prosperity_score"] if r["prosperity_score"] is not None else "pending",
                    "valuation": r["valuation_score"] if r["valuation_score"] is not None else "pending",
                    "time_horizon": horizon,
                }
                horizon_map[code] = horizon
            return detail, horizon_map

        # top-N 明细（L2 自动行业池展示用，保持原契约）
        industry_detail, industry_horizon_map = _build_detail(selected)
        # 全量明细（供 industry_override 取池外行业的真实评分，避免显示 0.0）
        all_detail, all_horizon_map = _build_detail(scored_rows)

        logger.info(f"L2 行业池: risk_mode={risk_mode}, weights={weights}, "
                    f"选出 {len(allowed)} 个行业: {[r['industry_name'] for r in selected[:5]]}...")

        return {
            "success": True,
            "allowed_industries": allowed,
            "industry_scores": selected,
            "industry_detail": industry_detail,
            "industry_horizon_map": industry_horizon_map,
            "all_industry_scores": scored_rows,
            "all_industry_detail": all_detail,
            "all_industry_horizon_map": all_horizon_map,
            "allowed_styles": [],
            "fallback": False,
            "risk_mode": risk_mode,
            "level": level,
            "weights": weights,
        }

    except Exception as e:
        logger.error(f"evaluate_industry_rotation 失败: {e}")
        return _fallback(risk_mode, error=str(e))


def _fallback(risk_mode: str, error: str = None) -> dict:
    out = {
        "success": False,
        "allowed_industries": [],
        "industry_scores": [],
        "industry_detail": {},
        "industry_horizon_map": {},
        "allowed_styles": [],
        "fallback": True,
        "risk_mode": risk_mode,
    }
    if error:
        out["error"] = error
    return out
