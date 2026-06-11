"""
agents/industry_agent.py — L2 行业轮动层

评估当前行业资金流，输出行业池（allowed_industries），
供 Orchestrator 写入 decision_context，约束 L3 扫描范围。

数据对齐方案：
  moneyflow_ind（东财板块，1012个）按名称精确匹配申万一级行业（31个），
  取匹配到的板块的 net_amount 作为行业资金流评分。
"""
import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

# 按 risk_mode 决定行业池宽度（Top N 行业）
_POOL_SIZE = {
    "defensive": (5, 7),   # (min, max)
    "balanced":  (6, 10),
    "aggressive":(8, 12),
}

# 按 risk_mode 决定评分计算的回看天数
_LOOKBACK = {
    "defensive": 20,
    "balanced":  10,
    "aggressive": 5,
}


def _load_latest_moneyflow_ind(date: str = None) -> pd.DataFrame | None:
    """
    加载最近一个有数据的交易日的 moneyflow_ind。
    若 date 为 None，取已有最新日期。
    """
    import data.store_tushare as st

    dates = st.list_dates("moneyflow_ind")
    if not dates:
        return None

    if date:
        target = date.replace("-", "")
        available = [d for d in dates if d.replace("-", "") <= target]
        if not available:
            return None
        use_date = available[-1]
    else:
        use_date = dates[-1]

    return st.load("moneyflow_ind", date=use_date)


def _load_moneyflow_window(risk_mode: str, date: str = None) -> pd.DataFrame | None:
    """
    加载用于评分的 moneyflow_ind 数据窗口。

    阶段二第一版：以最新一日的行业资金流为评分基础（单日截面）。
    回看天数（_LOOKBACK）预留给后续累计净流入扩展。

    统一数据加载入口，便于测试拦截。返回合并后的 DataFrame（含 name, net_amount）。
    """
    # 当前实现：以最新一日截面为准（_load_latest_moneyflow_ind 可被测试 mock）
    df = _load_latest_moneyflow_ind(date=date)
    if df is None or df.empty:
        return None
    return df


def _score_industries(risk_mode: str, date: str = None) -> pd.DataFrame:
    """
    从 moneyflow_ind 和 industry_sw 计算行业评分。

    返回 DataFrame，列: industry_code, industry_name, score, net_amount_sum
    按 score 降序排列。
    """
    import data.store_meta as sm

    # 获取申万一级行业分类
    ind_sw = sm.load("industry_sw")
    if ind_sw is None or ind_sw.empty:
        return pd.DataFrame()

    # 申万一级行业名→代码映射（去重取第一个）
    sw_name_to_code = (
        ind_sw[["industry_code", "industry_name"]]
        .drop_duplicates("industry_name")
        .set_index("industry_name")["industry_code"]
        .to_dict()
    )
    sw_names = set(sw_name_to_code.keys())

    # 加载行业资金流数据窗口
    mf = _load_moneyflow_window(risk_mode=risk_mode, date=date)
    if mf is None or mf.empty:
        return pd.DataFrame()

    # 只保留名称在申万一级的板块（精确匹配）
    mf = mf[mf["name"].isin(sw_names)][["name", "net_amount"]].copy()
    if mf.empty:
        return pd.DataFrame()

    # 按行业名累计净流入
    agg = mf.groupby("name")["net_amount"].sum().reset_index()
    agg.columns = ["industry_name", "net_amount_sum"]

    # 归一化评分 0~100
    min_v, max_v = agg["net_amount_sum"].min(), agg["net_amount_sum"].max()
    if max_v > min_v:
        agg["score"] = ((agg["net_amount_sum"] - min_v) / (max_v - min_v) * 100).round(1)
    else:
        agg["score"] = 50.0

    # 反查申万行业代码
    agg["industry_code"] = agg["industry_name"].map(sw_name_to_code)
    agg = agg.dropna(subset=["industry_code"])
    agg = agg.sort_values("score", ascending=False).reset_index(drop=True)

    return agg[["industry_code", "industry_name", "score", "net_amount_sum"]]


def evaluate_industry_rotation(risk_mode: str = "balanced",
                                date: str = None) -> dict:
    """
    L2 行业轮动层：评估行业资金流，输出行业池。

    Args:
        risk_mode: L1 宏观层传入的 risk_mode（defensive/balanced/aggressive）
        date: 评估日期，默认最新

    Returns:
        {
            "success": bool,
            "allowed_industries": list[str],   # 申万行业代码
            "industry_scores": list[dict],      # 详细评分
            "fallback": bool,                   # True 表示数据缺失、退化为全市场
            "risk_mode": str,
        }
    """
    try:
        scored = _score_industries(risk_mode=risk_mode, date=date)

        if scored.empty:
            logger.warning("L2 行业评分数据缺失，返回空池，L3 将退化为全市场扫描")
            return {
                "success": False,
                "allowed_industries": [],
                "industry_scores": [],
                "fallback": True,
                "risk_mode": risk_mode,
            }

        # 按 risk_mode 取 Top N（取上限，行业数不足时取全部）
        _, top_n = _POOL_SIZE.get(risk_mode, (6, 10))
        top_n = min(top_n, len(scored))
        selected = scored.head(top_n)

        allowed = selected["industry_code"].tolist()
        scores = selected.to_dict("records")

        logger.info(f"L2 行业池: risk_mode={risk_mode}, 选出 {len(allowed)} 个行业: "
                    f"{selected['industry_name'].tolist()[:5]}...")

        return {
            "success": True,
            "allowed_industries": allowed,
            "industry_scores": scores,
            "fallback": False,
            "risk_mode": risk_mode,
        }

    except Exception as e:
        logger.error(f"evaluate_industry_rotation 失败: {e}")
        return {
            "success": False,
            "allowed_industries": [],
            "industry_scores": [],
            "fallback": True,
            "risk_mode": risk_mode,
            "error": str(e),
        }
