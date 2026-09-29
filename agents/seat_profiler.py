"""
agents/seat_profiler.py — 席位字典构建（游资 / 量化识别）

三层设计（对应“字典是会衰减的模型，不是一次性资产”）：
  ① 硬标签：数据侧游资/量化别名（data/smart_money/seat_labels.json，可插拔）
  ② 软标签：无监督聚类。对未标注席位，用四个行为特征聚类打软标签：
       - 上榜频次 freq        （窗口内上榜 episode 数）
       - 分散度 dispersion    （涉及股票数 / 上榜次数，越高越像量化/程序化）
       - 买卖同席率 same_seat_rate（同一席位同一标的当日既买又卖，做T/对冲特征）
       - T+1 兑现率 t1_exit_rate（净买入后紧邻下一次上榜即为净卖出且间隔≤K交易日，快进快出）
  ③ 回测闭环：次日跟投胜率（上榜后 1/3/5/10 交易日收益的胜率）作为校准/衰减度量。

产出：data/smart_money/seat_dictionary.json / .csv / _report.md
字段含：label、label_basis、confidence、effective_date、half_life_days、样本与特征。

口径声明：软标签为聚类推断，非官方认定，使用时应带置信度并定期重估。
"""
import csv
import json
import logging
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from agents.smart_money_agent import (
    LABEL_BRANCH,
    LABEL_HOT,
    LABEL_QUANT,
    PriceCache,
    _safe_float,
    _today,
    _window_start,
    load_seat_episodes,
)

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "smart_money"
DICT_PATH = _BASE_DIR / "seat_dictionary.json"
CSV_PATH = _BASE_DIR / "seat_dictionary.csv"
REPORT_PATH = _BASE_DIR / "seat_dictionary_report.md"
LABELS_PATH = _BASE_DIR / "seat_labels.json"
SNAPSHOT_DIR = _BASE_DIR / "snapshots"
SNAPSHOT_KEEP = 60  # 保留最近 N 份带日期快照

FEATURES = ("freq", "dispersion", "same_seat_rate", "t1_exit_rate")
FOLLOW_HORIZONS = (1, 3, 5, 10)
DEFAULT_T1_DAYS = 2
DEFAULT_HALF_LIFE = 90

# 规则席位列（不参与聚类，由 classify_seat 直接判定）
_RULE_SEAT_KEYS = ("机构专用", "股通专用", "股通", "机构投资者", "自然人", "中小投资者")

# 外部字典 xlsx（reference/A股龙虎榜席位资金画像字典.xlsx）资金类型 → 本项目 label
_XLSX_TYPE_MAP = {
    "游资": LABEL_HOT,
    "量化/算法交易": LABEL_QUANT,
    "量化/算法交易｜外资背景": LABEL_QUANT,
    "疑似量化/高频": LABEL_QUANT,
    "散户集群": "个人投资者(汇总)",
}
_XLSX_SKIP_TYPES = {
    "低频营业部（非画像重点）",
    "低频营业部（非画像重点）（外资通道）",
    "样本不足（需更长区间验证）",
    "聚合通道席位（非单一主体）",
}
DEFAULT_XLSX = (Path(__file__).resolve().parent.parent
                / "reference" / "A股龙虎榜席位资金画像字典.xlsx")


# ═══════════════════════════════════════════════════════════════
# ① 硬标签
# ═══════════════════════════════════════════════════════════════

def load_hard_labels() -> list:
    """
    读取数据侧游资/量化别名（硬标签）。

    文件 data/smart_money/seat_labels.json 结构：
      {"labels": [
          {"match": "华鑫证券上海分公司", "label": "知名游资营业部",
           "source": "eastmoney", "as_of": "2026-09-01", "confidence": 0.9, "tag": "游资(数据源)"}
      ]}
    不存在返回 []。可插拔：任意数据源只要产出该文件即可作为硬标签。
    """
    if not LABELS_PATH.exists():
        return []
    try:
        raw = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
        return [x for x in raw.get("labels", []) if x.get("match")]
    except Exception as e:
        logger.warning(f"seat_labels.json 读取失败: {e}")
        return []


def _match_hard_label(agency: str, labels: list) -> dict | None:
    for x in labels:
        m = str(x.get("match", ""))
        if m and m in agency:
            return x
    return None


def import_seat_labels_xlsx(xlsx_path=None, out_path=None) -> dict:
    """
    读取外部席位字典 xlsx → 生成硬标签 data/smart_money/seat_labels.json。

    口径（与用户确认）：
      - 标签以“资金类型”为准；数据侧“游资别名”仅作 tag
      - 只导入高置信类别：游资 / 量化 / 疑似量化 / 散户集群；低频、样本不足、聚合通道不导入
      - 以“营业部名称”为键，席位ID 仅存档（存在跨名称碰撞，不作主键）
      - 同名多行自动合并（保留最高置信，合并 tag）
    """
    import pandas as pd

    xlsx_path = Path(xlsx_path) if xlsx_path else DEFAULT_XLSX
    out_path = Path(out_path) if out_path else LABELS_PATH
    if not xlsx_path.exists():
        raise FileNotFoundError(f"字典 xlsx 不存在: {xlsx_path}")

    xl = pd.ExcelFile(xlsx_path, engine="openpyxl")
    if "席位字典主表" not in xl.sheet_names:
        raise ValueError("xlsx 缺少 sheet『席位字典主表』")
    main = xl.parse("席位字典主表")

    merged: dict = {}
    for _, r in main.iterrows():
        name = str(r.get("营业部名称（全称）") or "").strip()
        ftype = str(r.get("资金类型") or "").strip()
        if not name or ftype in _XLSX_SKIP_TYPES:
            continue
        label = _XLSX_TYPE_MAP.get(ftype)
        if not label:
            continue
        alias = r.get("游资别名（数据侧标签）")
        tags = []
        if isinstance(alias, str) and alias.strip():
            tags = [a.strip() for a in alias.replace(";", "；").split("；") if a.strip()]
        conf = _safe_float(r.get("置信度(0-100)"))
        conf = round(conf / 100.0, 2) if conf is not None else 0.5
        rec = {
            "match": name,
            "label": label,
            "label_basis": "硬标签(数据源)",
            "confidence": conf,
            "source": f"seat_dict_xlsx:{xlsx_path.name}",
            "as_of": str(r.get("生效止（末见日）") or ""),
            "effective_from": str(r.get("生效起（首见日）") or ""),
            "seat_id": None if pd.isna(r.get("席位ID")) else str(r.get("席位ID")),
            "tags": tags,
            "evidence": str(r.get("判定依据") or "")[:200],
        }
        prev = merged.get(name)
        if prev is None:
            merged[name] = rec
        else:
            if rec["confidence"] > prev["confidence"]:
                rec["tags"] = list(dict.fromkeys((prev.get("tags") or []) + rec["tags"]))
                merged[name] = rec
            else:
                prev["tags"] = list(dict.fromkeys((prev.get("tags") or []) + rec["tags"]))

    result = {
        "schema_version": "1.0",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": str(xlsx_path),
        "n": len(merged),
        "labels": list(merged.values()),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out_path) + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)
    logger.info(f"席位硬标签导入完成: {len(merged)} 条 -> {out_path}")
    return result


# ═══════════════════════════════════════════════════════════════
# ② 四特征 + ③ 回测（次日跟投胜率）
# ═══════════════════════════════════════════════════════════════

def compute_seat_features(episodes: pd.DataFrame,
                          prices: PriceCache | None = None,
                          t1_days: int = DEFAULT_T1_DAYS,
                          follow_horizons=FOLLOW_HORIZONS) -> pd.DataFrame:
    """
    对每个席位计算四特征 + 次日跟投胜率。返回以 agency 为索引的 DataFrame。
    """
    prices = prices or PriceCache()
    if episodes is None or episodes.empty:
        return pd.DataFrame(columns=["agency", *FEATURES])

    eps = episodes.copy()
    eps["both"] = (eps["buy"] > 0) & (eps["sell"] > 0)

    agg = eps.groupby("agency").agg(
        freq=("net", "size"),
        active_days=("date", "nunique"),
        stocks=("code", "nunique"),
        same_seat_rate=("both", "mean"),
        cum_net=("net", "sum"),
    )
    agg["dispersion"] = agg["stocks"] / agg["freq"].clip(lower=1)

    # T+1 兑现率：净买入 episode 后，紧邻的下一次上榜（同席位同标的）为净卖出且间隔≤t1_days
    e = eps.sort_values(["agency", "code", "date"]).copy()
    grp = e.groupby(["agency", "code"], sort=False)
    e["next_net"] = grp["net"].shift(-1)
    e["next_date"] = grp["date"].shift(-1)
    hit = defaultdict(int)
    tot = defaultdict(int)
    for a, c, d, nd, net, nnet in zip(
            e["agency"], e["code"], e["date"], e["next_date"], e["net"], e["next_net"],
            strict=False):
        net = net or 0
        if net <= 0:
            continue
        tot[a] += 1
        if nnet is not None and nnet < 0 and nd is not None:
            gap = prices.trading_days_between(c, d, nd)
            if gap is not None and 0 < gap <= t1_days:
                hit[a] += 1
    agg["t1_exit_rate"] = [
        (hit[a] / tot[a]) if tot.get(a) else 0.0 for a in agg.index
    ]

    # 次日跟投胜率：净买入 episode，上榜后 N 交易日收益 > 0 的比例
    wins = defaultdict(lambda: defaultdict(int))
    cnts = defaultdict(lambda: defaultdict(int))
    for a, c, d, net in zip(eps["agency"], eps["code"], eps["date"], eps["net"], strict=False):
        if (net or 0) <= 0:
            continue
        for h in follow_horizons:
            r = prices.forward_return(c, d, h)
            if r is not None:
                cnts[a][h] += 1
                if r > 0:
                    wins[a][h] += 1
    for h in follow_horizons:
        agg[f"win_d{h}"] = [
            round(wins[a][h] / cnts[a][h], 3) if cnts[a].get(h) else None for a in agg.index
        ]
        agg[f"n_d{h}"] = [cnts[a].get(h, 0) for a in agg.index]

    agg = agg.reset_index()
    return agg


# ═══════════════════════════════════════════════════════════════
# 聚类（scipy kmeans2，无需 sklearn）
# ═══════════════════════════════════════════════════════════════

def _standardize(mat: np.ndarray) -> tuple:
    mu = mat.mean(axis=0)
    sd = mat.std(axis=0)
    sd[sd == 0] = 1.0
    return (mat - mu) / sd, mu, sd


def _softmax_margin(x: np.ndarray, centroids: np.ndarray) -> float:
    d = np.linalg.norm(centroids - x, axis=1)
    p = np.exp(-d)
    p = p / p.sum() if p.sum() > 0 else np.ones_like(p) / len(p)
    p = np.sort(p)[::-1]
    return float(p[0] - p[1]) if len(p) > 1 else 1.0


def cluster_seats(feat: pd.DataFrame, k: int = 3, seed: int = 42) -> pd.DataFrame:
    """对席位做 KMeans 聚类，返回新增 cluster/posterior_margin 两列。输入需含 FEATURES 列。"""
    from scipy.cluster.vq import kmeans2

    out = feat.copy()
    x = out[list(FEATURES)].fillna(0.0).to_numpy(dtype=float)
    if len(x) < k:
        out["cluster"] = 0
        out["posterior_margin"] = 1.0
        return out
    z, _, _ = _standardize(x)
    try:
        centroids, labels = kmeans2(z, k, seed=seed, minit="++")
    except Exception as e:  # 空簇等极端情况
        logger.warning(f"kmeans2 失败({e})，回落为单簇")
        out["cluster"] = 0
        out["posterior_margin"] = 1.0
        return out
    out["cluster"] = labels
    zc = np.array([z[labels == c].mean(axis=0) if (labels == c).any() else centroids[c]
                   for c in range(k)])
    out["posterior_margin"] = [_softmax_margin(row, zc) for row in z]
    return out


def _name_clusters(feat: pd.DataFrame, k: int, hard: list, purity: float = 0.6) -> dict:
    """给每个簇命名（游资/量化/普通）。有硬标签则按多数锚定，否则按行为特征打分。"""
    cluster_mean = feat.groupby("cluster")[list(FEATURES)].mean()

    def z(series):
        sd = series.std()
        if not sd or pd.isna(sd):
            sd = 1.0
        return (series - series.mean()) / sd

    def hard_frac(label_keyword):
        counts = {}
        for _, r in feat.iterrows():
            h = _match_hard_label(r["agency"], hard)
            if h and label_keyword in str(h.get("label", "")):
                counts[r["cluster"]] = counts.get(r["cluster"], 0) + 1
        return counts

    if hard:
        hot_counts = hard_frac("游资")
        quant_counts = hard_frac("量化")
        # 每簇硬标签总数（分母），用于纯度
        hard_total = {}
        for _, r in feat.iterrows():
            if _match_hard_label(r["agency"], hard):
                hard_total[r["cluster"]] = hard_total.get(r["cluster"], 0) + 1
        hot_purity = {c: hot_counts.get(c, 0) / (hard_total.get(c) or 1) for c in range(k)}
        quant_purity = {c: quant_counts.get(c, 0) / (hard_total.get(c) or 1) for c in range(k)}
        hot_cluster = max(hot_purity, key=hot_purity.get) if max(hot_purity.values()) >= purity else None
        quant_cluster = (max(quant_purity, key=quant_purity.get)
                         if max(quant_purity.values()) >= purity else None)
        if quant_cluster is not None and quant_cluster == hot_cluster:
            # 同一簇同时最高：归给纯度更高者
            if quant_purity[quant_cluster] > hot_purity.get(hot_cluster, 0):
                hot_cluster = None
            else:
                quant_cluster = None
    else:
        # 行为签名（无硬标签时的启发式）：
        #   量化/程序化 = 高频 + 买卖同席率高（做T/对冲）+ 分散
        #   游资        = T+1兑现率高（快进快出）+ 相对集中 + 非买卖同席
        zf = z(cluster_mean["freq"])
        zd = z(cluster_mean["dispersion"])
        zs = z(cluster_mean["same_seat_rate"])
        zt = z(cluster_mean["t1_exit_rate"])
        quant_score = zf + zs + zd
        hot_score = zt + 0.5 * zf - 0.5 * zs - 0.5 * zd
        quant_cluster = int(quant_score.idxmax()) if len(quant_score) else None
        order_hot = [int(c) for c in hot_score.sort_values(ascending=False).index
                     if int(c) != quant_cluster]
        hot_cluster = order_hot[0] if order_hot else None

    naming = {}
    for c in range(k):
        if c == hot_cluster:
            naming[c] = LABEL_HOT
        elif c == quant_cluster:
            naming[c] = LABEL_QUANT
        else:
            naming[c] = LABEL_BRANCH
    return naming


# ═══════════════════════════════════════════════════════════════
# 构建字典
# ═══════════════════════════════════════════════════════════════

def build_seat_dictionary(window_months: int = 6,
                          min_appearances: int = 10,
                          k: int = 3,
                          t1_days: int = DEFAULT_T1_DAYS,
                          follow_horizons=FOLLOW_HORIZONS,
                          purity: float = 0.6,
                          soft_min_conf: float = 0.7,
                          episodes: pd.DataFrame | None = None,
                          prices: PriceCache | None = None,
                          output_dir: Path | None = None) -> dict:
    """
    构建席位字典并落盘（json/csv/md）。返回字典 dict。

    :param window_months: 回溯窗口（月）
    :param min_appearances: 参与聚类/入库的最小上榜次数
    :param k: 聚类簇数（默认 3）
    :param t1_days: T+1 兑现的交易日间隔阈值
    """
    prices = prices or PriceCache()
    output_dir = Path(output_dir) if output_dir else _BASE_DIR
    dict_path = output_dir / "seat_dictionary.json"
    csv_path = output_dir / "seat_dictionary.csv"
    report_path = output_dir / "seat_dictionary_report.md"

    end = _today()
    # 用本地 top_inst 最新日期作为窗口终点更稳妥
    try:
        import data.store_moneyflow as store_mf
        dates = store_mf.list_dates("top_inst")
        if dates:
            end = max(dates)
    except Exception:
        pass
    start = _window_start(window_months, end)

    if episodes is None:
        episodes = load_seat_episodes(start=start, end=end)
    if episodes is None or episodes.empty:
        result = {"schema_version": "1.0", "generated_at": _today(),
                  "window": {"start": start, "end": end}, "seats": [],
                  "notes": ["窗口内无 top_inst 数据"]}
        _write_outputs(result, dict_path, csv_path, report_path)
        return result

    # 规则席位（机构/股通/汇总行）不参与聚类
    eps = episodes[~episodes["agency"].astype(str).str.contains("|".join(_RULE_SEAT_KEYS))]
    feat = compute_seat_features(eps, prices=prices, t1_days=t1_days,
                                 follow_horizons=follow_horizons)
    if feat.empty:
        result = {"schema_version": "1.0", "generated_at": _today(),
                  "window": {"start": start, "end": end}, "seats": [],
                  "notes": ["无可聚类席位"]}
        _write_outputs(result, dict_path, csv_path, report_path)
        return result

    hard = load_hard_labels()
    eligible = feat[feat["freq"] >= min_appearances].copy()
    if eligible.empty:
        eligible = feat.copy()

    clustered = cluster_seats(eligible, k=k)
    naming = _name_clusters(clustered, k, hard, purity=purity)
    clustered["soft_label"] = clustered["cluster"].map(naming)

    # 汇总：每个簇的特征均值（用于报告）
    cluster_profile = clustered.groupby("cluster")[list(FEATURES)].mean().round(3).to_dict("index")

    seats = []
    matched_matches = set()
    for _, r in feat.iterrows():
        agency = r["agency"]
        h = _match_hard_label(agency, hard)
        c = clustered[clustered["agency"] == agency]
        tags, seat_id, evidence = [], None, ""
        if h is not None:
            matched_matches.add(h.get("match"))
            label = h.get("label", LABEL_HOT)
            basis = h.get("label_basis", "硬标签(数据源)")
            conf = round(float(h.get("confidence", 0.9)), 2)
            source = h.get("source", "")
            as_of = h.get("as_of", "") or end
            tags = list(h.get("tags") or [])
            seat_id = h.get("seat_id")
            evidence = h.get("evidence", "")
        elif not c.empty:
            label = c.iloc[0]["soft_label"]
            basis = "软标签(聚类)"
            margin = float(c.iloc[0]["posterior_margin"])
            sample = 1.0 - math.exp(-r["freq"] / max(min_appearances, 1))
            conf = round(min(0.95, max(0.30, 0.35 + 0.40 * sample + 0.25 * margin)), 2)
            if conf < soft_min_conf:
                continue  # 软标签置信不足：不入库（回落为普通营业部）
            source = "cluster"
            as_of = end
        else:
            continue  # 样本过少且无硬标签：不入库
        rec = {
            "agency": agency,
            "label": label,
            "label_basis": basis,
            "confidence": conf,
            "effective_date": end,
            "half_life_days": DEFAULT_HALF_LIFE,
            "source": source,
            "as_of": as_of,
            "seat_id": seat_id,
            "tags": tags,
            "evidence": evidence,
            "freq": int(r["freq"]),
            "stocks": int(r["stocks"]),
            "dispersion": round(float(r["dispersion"]), 4),
            "same_seat_rate": round(float(r["same_seat_rate"]), 4),
            "t1_exit_rate": round(float(r["t1_exit_rate"]), 4),
        }
        for hz in follow_horizons:
            rec[f"win_d{hz}"] = _safe_float(r.get(f"win_d{hz}"))
            rec[f"n_d{hz}"] = int(r.get(f"n_d{hz}") or 0)
        seats.append(rec)

    # 硬标签中窗口内无上榜的席位也纳入字典（freq=0），保证已知游资/量化席位不丢失
    for h in hard:
        m = h.get("match")
        if m and m not in matched_matches:
            seats.append({
                "agency": m, "label": h.get("label", LABEL_HOT),
                "label_basis": h.get("label_basis", "硬标签(数据源)"),
                "confidence": round(float(h.get("confidence", 0.9)), 2),
                "effective_date": end, "half_life_days": DEFAULT_HALF_LIFE,
                "source": h.get("source", ""), "as_of": h.get("as_of", "") or end,
                "seat_id": h.get("seat_id"), "tags": list(h.get("tags") or []),
                "evidence": h.get("evidence", ""),
                "freq": 0, "stocks": 0, "dispersion": 0.0,
                "same_seat_rate": 0.0, "t1_exit_rate": 0.0,
            })

    seats.sort(key=lambda x: (x["label"], -x["confidence"], -x["freq"]))
    result = {
        "schema_version": "1.0",
        "model_version": f"seatdict-{end}",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window": {"start": start, "end": end, "months": window_months},
        "params": {"min_appearances": min_appearances, "k": k, "t1_days": t1_days,
                   "purity": purity, "soft_min_conf": soft_min_conf,
                   "follow_horizons": list(follow_horizons)},
        "hard_label_count": len(hard),
        "cluster_profile": {str(c): {kk: round(float(vv), 3) for kk, vv in prof.items()}
                            for c, prof in cluster_profile.items()},
        "cluster_naming": {str(c): naming[c] for c in naming},
        "seats": seats,
        "notes": [
            "游资/量化标签为数据源硬标签或行为聚类软标签，非官方认定",
            "字典会衰减，建议每季度重估（half_life_days）",
        ],
    }
    _write_outputs(result, dict_path, csv_path, report_path)
    _write_snapshot(result, output_dir / "snapshots")
    logger.info(f"席位字典构建完成: {len(seats)} 席位 -> {dict_path}")
    return result


def _write_snapshot(result: dict, snapshot_dir: Path) -> None:
    """落地一份带日期的快照（同日重建覆盖为幂等），并裁剪只保留最近 SNAPSHOT_KEEP 份。"""
    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    p = snapshot_dir / f"seat_dictionary_{day}.json"
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    files = sorted(snapshot_dir.glob("seat_dictionary_*.json"))
    for old in files[:-SNAPSHOT_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def list_snapshots() -> list:
    """返回已有快照日期列表（升序 YYYYMMDD）。"""
    if not SNAPSHOT_DIR.exists():
        return []
    return sorted(p.stem.replace("seat_dictionary_", "")
                  for p in SNAPSHOT_DIR.glob("seat_dictionary_*.json"))


def load_snapshot_asof(date: str) -> dict | None:
    """取“截至 date（含）最近一次”的快照（用于按时间回看历史标签）。date: YYYY-MM-DD 或 YYYYMMDD。"""
    day = str(date).replace("-", "")
    dates = [d for d in list_snapshots() if d <= day]
    if not dates:
        return None
    p = SNAPSHOT_DIR / f"seat_dictionary_{dates[-1]}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_outputs(result: dict, dict_path: Path, csv_path: Path, report_path: Path) -> None:
    out_dir = Path(dict_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(dict_path) + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(dict_path)

    cols = ["agency", "label", "label_basis", "confidence", "effective_date",
            "freq", "stocks", "dispersion", "same_seat_rate", "t1_exit_rate",
            "win_d1", "win_d3", "win_d5", "win_d10"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for s in result.get("seats", []):
            w.writerow(s)

    report_path.write_text(_report_markdown(result), encoding="utf-8")


def _report_markdown(result: dict) -> str:
    lines = ["# 席位字典构建报告", ""]
    lines.append(f"- 生成时间: {result.get('generated_at')}")
    w = result.get("window", {})
    lines.append(f"- 窗口: {w.get('start')} ~ {w.get('end')}（{w.get('months')} 个月）")
    lines.append(f"- 参数: {result.get('params')}")
    lines.append(f"- 硬标签数: {result.get('hard_label_count', 0)}")
    seats = result.get("seats", [])
    by_label = {}
    for s in seats:
        by_label.setdefault(s["label"], []).append(s)
    lines.append(f"- 入库席位数: {len(seats)}（" +
                 "，".join(f"{k}:{len(v)}" for k, v in by_label.items()) + "）")
    lines.append("")
    lines.append("## 簇画像（四特征均值）")
    lines.append("| 簇 | 命名 | freq | dispersion | same_seat_rate | t1_exit_rate |")
    lines.append("|---|---|---|---|---|---|")
    naming = result.get("cluster_naming", {})
    for c, prof in result.get("cluster_profile", {}).items():
        lines.append(f"| {c} | {naming.get(str(c), '-')} | {prof.get('freq')} | "
                     f"{prof.get('dispersion')} | {prof.get('same_seat_rate')} | "
                     f"{prof.get('t1_exit_rate')} |")
    lines.append("")
    for label in (LABEL_HOT, LABEL_QUANT, LABEL_BRANCH):
        group = by_label.get(label, [])
        if not group:
            continue
        lines.append(f"## {label}（{len(group)}）")
        lines.append("| 席位 | 置信度 | freq | dispersion | same_seat_rate | t1_exit_rate | 次日跟投胜率(1/3/5/10) |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in group[:40]:
            wr = "/".join(str(s.get(f"win_d{hz}")) for hz in FOLLOW_HORIZONS)
            lines.append(f"| {s['agency']} | {s['confidence']} | {s['freq']} | {s['dispersion']} | "
                         f"{s['same_seat_rate']} | {s['t1_exit_rate']} | {wr} |")
        lines.append("")
    lines.append("> 标签来自数据源硬标签或行为聚类软标签，非官方认定；字典会衰减，建议每季度重估。")
    return "\n".join(lines)


def dictionary_to_rows(result: dict | None = None) -> list:
    """读取字典文件并返回席位记录列表（用于 CLI/画像消费）。"""
    if result is None:
        if not DICT_PATH.exists():
            return []
        result = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    return result.get("seats", [])
