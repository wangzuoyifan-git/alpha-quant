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
from pathlib import Path

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
    import data.store_moneyflow as st

    dates = st.list_dates("industry")
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

    return st.load("industry", date=use_date)


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


def _score_industries_by_aggregation(level: str = "L2",
                                     date: str = None) -> pd.DataFrame:
    """
    L2-R5b：个股资金流按申万成分股自底向上聚合到指定层级评分。

    用 tushare moneyflow（net_mf_amount，个股级）按 industry_member 的
    对应层级代码 groupby 求和，归一化为 0~100 评分。代码匹配，无名称匹配。

    返回 DataFrame，列: industry_code, industry_name, score, net_amount_sum
    """
    import glob
    import data.store_meta as sm

    member = sm.load("industry_member")
    if member is None or member.empty:
        return pd.DataFrame()

    code_col = {"L1": "l1_code", "L2": "l2_code", "L3": "l3_code"}.get(level)
    name_col = {"L1": "l1_name", "L2": "l2_name", "L3": "l3_name"}.get(level)
    if code_col is None or code_col not in member.columns:
        return pd.DataFrame()

    # ts_code（000001.SZ）→ 6位 code 映射到行业
    member = member.copy()
    member["code6"] = member["ts_code"].astype(str).str.split(".").str[0]
    code_to_ind = dict(zip(member["code6"], member[code_col]))
    ind_to_name = dict(zip(member[code_col].astype(str), member[name_col].astype(str)))

    # 读全市场个股资金流的目标日截面（net_mf_amount）
    base = Path(__file__).parent.parent / "data" / "moneyflow" / "stock"
    files = glob.glob(str(base / "*.parquet"))
    if not files:
        return pd.DataFrame()

    rows = []
    target_ts = pd.Timestamp(date) if date else None
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["net_mf_amount"])
            if df.empty:
                continue
            if target_ts is not None and isinstance(df.index, pd.DatetimeIndex):
                df = df[df.index <= target_ts]
                if df.empty:
                    continue
            code6 = Path(f).stem.split(".")[0]
            ind = code_to_ind.get(code6)
            if ind is None:
                continue
            rows.append({"industry_code": ind,
                         "net_mf_amount": float(df.iloc[-1]["net_mf_amount"])})
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    flow = pd.DataFrame(rows)
    agg = flow.groupby("industry_code")["net_mf_amount"].sum().reset_index()
    agg.columns = ["industry_code", "net_amount_sum"]

    min_v, max_v = agg["net_amount_sum"].min(), agg["net_amount_sum"].max()
    if max_v > min_v:
        agg["score"] = ((agg["net_amount_sum"] - min_v) / (max_v - min_v) * 100).round(1)
    else:
        agg["score"] = 50.0

    agg["industry_name"] = agg["industry_code"].astype(str).map(ind_to_name)
    agg = agg.dropna(subset=["industry_name"])
    agg = agg.sort_values("score", ascending=False).reset_index(drop=True)
    return agg[["industry_code", "industry_name", "score", "net_amount_sum"]]


def evaluate_industry_rotation(risk_mode: str = "balanced",
                                date: str = None,
                                level: str = "L1") -> dict:
    """
    L2 行业轮动层：评估行业资金流，输出行业池。

    Args:
        risk_mode: L1 宏观层传入的 risk_mode（defensive/balanced/aggressive）
        date: 评估日期，默认最新
        level: 申万行业层级 L1/L2/L3（2026-06 新增，L2-R5）
               L1 用东财板块名匹配（原逻辑，快）；L2/L3 用个股资金流聚合（代码匹配）

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
        if level == "L1":
            scored = _score_industries(risk_mode=risk_mode, date=date)
        else:
            scored = _score_industries_by_aggregation(level=level, date=date)

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

        # 台阶一：industry_detail 和 industry_horizon_map（简单规则占位，台阶二升级）
        # 规则：score >= 60 → short（交易仓动量操作），其余 → medium（配置仓候选）
        industry_detail = {}
        industry_horizon_map = {}
        for row in scores:
            code = row["industry_code"]
            horizon = "short" if float(row.get("score", 0)) >= 60 else "medium"
            industry_detail[code] = {
                "industry_name": row["industry_name"],
                "score": row["score"],
                "moneyflow": row.get("net_amount_sum"),
                "prosperity": "pending",   # 台阶二接入景气度
                "valuation": "pending",    # 台阶二接入估值
                "time_horizon": horizon,
            }
            industry_horizon_map[code] = horizon

        logger.info(f"L2 行业池: risk_mode={risk_mode}, 选出 {len(allowed)} 个行业: "
                    f"{selected['industry_name'].tolist()[:5]}...")

        return {
            "success": True,
            "allowed_industries": allowed,
            "industry_scores": scores,
            "industry_detail": industry_detail,
            "industry_horizon_map": industry_horizon_map,
            "allowed_styles": [],        # 台阶一直通，台阶二由行业特性收窄
            "fallback": False,
            "risk_mode": risk_mode,
            "level": level,
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
