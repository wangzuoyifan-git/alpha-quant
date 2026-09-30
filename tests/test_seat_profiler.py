"""
test_seat_profiler.py — 席位字典（游资/量化识别）单元测试

覆盖：xlsx 硬标签导入（过滤/映射/合并）、四特征、聚类命名纯度门槛、
build_seat_dictionary 落盘、classify_seat 字典接入。全部离线（合成数据）。
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.seat_profiler as sp
import agents.smart_money_agent as sma


class FakePrices:
    def forward_return(self, code, date, h):
        return {1: 0.02, 3: 0.03, 5: -0.01, 10: 0.05}.get(h)

    def trading_days_between(self, code, d1, d2):
        return 1


def _episodes():
    rows = []
    # 游资甲：高频、集中（3只股票）、快进快出（买后卖）
    for i in range(12):
        rows.append(dict(date=f"2026-{6+i%3:02d}-{10+i%18:02d}",
                         code=f"00000{i%3}", agency="游资甲营业部",
                         buy=1e8, sell=2e7, net=8e7))
        rows.append(dict(date=f"2026-{6+i%3:02d}-{11+i%18:02d}",
                         code=f"00000{i%3}", agency="游资甲营业部",
                         buy=1e7, sell=9e7, net=-8e7))
    # 量化乙：分散（多股票）、买卖同席
    for i in range(12):
        rows.append(dict(date=f"2026-{6+i%3:02d}-{10+i%18:02d}",
                         code=f"6000{i%10:02d}", agency="量化乙营业部",
                         buy=5e7, sell=5e7, net=0.0))
    return pd.DataFrame(rows)


class TestImportXlsx:
    def _write(self, tmp_path):
        main = pd.DataFrame([
            {"席位ID": "A1", "营业部名称（全称）": "游资甲营业部", "资金类型": "游资",
             "游资别名（数据侧标签）": "东北猛男", "置信度(0-100)": 94,
             "量化行为打分": 90, "生效起（首见日）": "2026-08-03",
             "生效止（末见日）": "2026-09-28", "判定依据": "游资"},
            {"席位ID": "A2", "营业部名称（全称）": "量化乙营业部", "资金类型": "量化/算法交易",
             "置信度(0-100)": 80, "生效起（首见日）": "2026-08-03",
             "生效止（末见日）": "2026-09-28", "判定依据": "量化"},
            {"席位ID": "A3", "营业部名称（全称）": "散户丙营业部", "资金类型": "散户集群",
             "游资别名（数据侧标签）": "拉萨天团", "置信度(0-100)": 90,
             "生效止（末见日）": "2026-09-28"},
            {"席位ID": "A2", "营业部名称（全称）": "量化乙营业部", "资金类型": "量化/算法交易",
             "游资别名（数据侧标签）": "别名X", "置信度(0-100)": 85,
             "生效止（末见日）": "2026-09-28"},
            {"席位ID": "A4", "营业部名称（全称）": "低频丁营业部",
             "资金类型": "低频营业部（非画像重点）", "置信度(0-100)": 10},
            {"席位ID": None, "营业部名称（全称）": "机构专用",
             "资金类型": "聚合通道席位（非单一主体）", "置信度(0-100)": 95},
        ])
        p = tmp_path / "d.xlsx"
        with pd.ExcelWriter(p, engine="openpyxl") as w:
            main.to_excel(w, sheet_name="席位字典主表", index=False)
        return p

    def test_filters_mapping_merge(self, tmp_path):
        res = sp.import_seat_labels_xlsx(self._write(tmp_path), out_path=tmp_path / "l.json")
        labels = {x["match"]: x for x in res["labels"]}
        assert labels["游资甲营业部"]["label"] == sma.LABEL_HOT
        assert labels["散户丙营业部"]["label"] == "个人投资者(汇总)"
        assert "拉萨天团" in labels["散户丙营业部"]["tags"]
        assert "低频丁营业部" not in labels and "机构专用" not in labels
        # 同名合并取高置信并合并 tag
        assert labels["量化乙营业部"]["confidence"] == 0.85
        assert "别名X" in labels["量化乙营业部"]["tags"]


class TestFeatures:
    def test_compute(self):
        feat = sp.compute_seat_features(_episodes(), prices=FakePrices())
        d = feat.set_index("agency")
        assert d.loc["游资甲营业部", "freq"] == 24
        assert d.loc["游资甲营业部", "stocks"] <= 3  # 集中
        assert d.loc["量化乙营业部", "dispersion"] > d.loc["游资甲营业部", "dispersion"]
        assert "win_d1" in d.columns


class TestClusterNaming:
    def test_purity_gate(self):
        # 构造：一个簇硬标签全为游资，另一簇全为量化
        feat = pd.DataFrame({
            "agency": ["游资甲营业部", "量化乙营业部"],
            "freq": [100, 10], "dispersion": [0.2, 0.9],
            "same_seat_rate": [0.1, 0.9], "t1_exit_rate": [0.5, 0.1],
        })
        clustered = sp.cluster_seats(feat, k=2)
        hard = [{"match": "游资甲营业部", "label": sma.LABEL_HOT},
                {"match": "量化乙营业部", "label": sma.LABEL_QUANT}]
        naming = sp._name_clusters(clustered, 2, hard)
        assert set(naming.values()) == {sma.LABEL_HOT, sma.LABEL_QUANT}


class TestBuildDictionary:
    def test_build_and_hard_priority(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sp, "LABELS_PATH", tmp_path / "labels.json")
        (tmp_path / "labels.json").write_text(json.dumps({"labels": [
            {"match": "游资甲营业部", "label": sma.LABEL_HOT, "label_basis": "硬标签(数据源)",
             "confidence": 0.9, "source": "xlsx", "as_of": "2026-09-28", "tags": ["东北猛男"]},
        ]}, ensure_ascii=False), encoding="utf-8")
        res = sp.build_seat_dictionary(window_months=6, min_appearances=2,
                                       episodes=_episodes(), prices=FakePrices(),
                                       output_dir=tmp_path)
        seats = {s["agency"]: s for s in res["seats"]}
        assert seats["游资甲营业部"]["label_basis"].startswith("硬")
        assert "东北猛男" in seats["游资甲营业部"]["tags"]
        assert (tmp_path / "seat_dictionary.json").exists()
        assert (tmp_path / "seat_dictionary.csv").exists()
        assert list((tmp_path / "snapshots").glob("seat_dictionary_*.json"))

    def test_snapshot_asof(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sp, "LABELS_PATH", tmp_path / "labels.json")
        monkeypatch.setattr(sp, "SNAPSHOT_DIR", tmp_path / "snapshots")
        (tmp_path / "labels.json").write_text(json.dumps({"labels": []}), encoding="utf-8")
        sp.build_seat_dictionary(window_months=6, min_appearances=2,
                                 episodes=_episodes(), prices=FakePrices(),
                                 output_dir=tmp_path)
        snaps = sp.list_snapshots()
        assert len(snaps) == 1
        assert sp.load_snapshot_asof("2099-01-01")["seats"]
        assert sp.load_snapshot_asof("2000-01-01") is None

    def test_hard_label_without_episode_kept(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sp, "LABELS_PATH", tmp_path / "labels.json")
        (tmp_path / "labels.json").write_text(json.dumps({"labels": [
            {"match": "未上榜营业部", "label": sma.LABEL_HOT, "confidence": 0.9,
             "source": "xlsx", "as_of": "2026-09-28"},
        ]}, ensure_ascii=False), encoding="utf-8")
        res = sp.build_seat_dictionary(window_months=6, min_appearances=2,
                                       episodes=_episodes(), prices=FakePrices(),
                                       output_dir=tmp_path)
        seats = {s["agency"]: s for s in res["seats"]}
        assert seats["未上榜营业部"]["freq"] == 0
        assert seats["未上榜营业部"]["label"] == sma.LABEL_HOT


class TestSeatBehavior:
    def test_behavior(self, monkeypatch):
        import agents.smart_money_agent as sma
        import data.store_moneyflow as smf
        eps = pd.DataFrame({
            "date": ["2026-01-05", "2026-01-06", "2026-02-10", "2026-02-11"],
            "code": ["000001", "000002", "000001", "000003"],
            "agency": ["游资甲营业部"] * 4,
            "buy": [1e8] * 4, "sell": [0.0] * 4, "net": [1e8] * 4,
            "reasons": [["涨停"], ["换手"], ["涨停"], ["涨幅偏离"]],
        })
        monkeypatch.setattr(sma, "load_seat_episodes", lambda start=None, end=None: eps)
        monkeypatch.setattr(sma, "classify_seat",
                            lambda a: {"label": sma.LABEL_HOT, "label_basis": "硬标签(数据源)"})
        monkeypatch.setattr(smf, "list_dates", lambda dt: ["2026-01-05", "2026-02-11"])

        class P:
            def forward_return(self, c, d, h):
                return {5: 0.02}.get(h)

            def trading_days_between(self, c, d1, d2):
                return 1

        r = sp.seat_behavior("游资甲营业部", window_months=0, horizons=(5,), prices=P())
        assert r["available"] is True
        assert r["appearances"]["total"] == 4
        assert r["returns"]["d5"]["n"] == 4
        assert r["reason_dist"].get("打板") == 2
        assert any("触发场景" in x for x in r["summary"])


class TestClassifyIntegration:
    def test_classify_uses_dictionary(self, tmp_path, monkeypatch):
        p = tmp_path / "seat_dictionary.json"
        p.write_text(json.dumps({"seats": [
            {"agency": "游资甲营业部", "label": sma.LABEL_HOT,
             "label_basis": "硬标签(数据源)", "confidence": 0.9, "tags": ["东北猛男"]},
        ]}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(sma, "SEAT_DICT_PATH", p)
        monkeypatch.setattr(sma, "_seat_dict_cache", None)
        r = sma.classify_seat("游资甲营业部")
        assert r["label"] == sma.LABEL_HOT
        assert r["label_basis"].startswith("硬")
        assert any("置信度" in t for t in r["tags"])
