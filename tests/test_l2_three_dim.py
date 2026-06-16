"""
test_l2_three_dim.py — L2 三维评分单元测试（台阶二 4.2）
景气度① × w1 + 估值② × w2 + 资金流③ × w3

覆盖：
- _normalize_to_100 归一化
- _combine_dims 三维加权 + 缺失维度权重重归一
- _judge_horizon horizon 升级规则
- evaluate_industry_rotation 端到端（mock loaders）含 prosperity/valuation 非 pending
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.industry_agent as ia


# ════════════════════════════════════════════════════════════
# _normalize_to_100
# ════════════════════════════════════════════════════════════

class TestNormalize:

    def test_minmax_maps_to_0_100(self):
        out = ia._normalize_to_100({"a": 0.0, "b": 50.0, "c": 100.0})
        assert out["a"] == 0.0
        assert out["c"] == 100.0
        assert 49 <= out["b"] <= 51

    def test_all_equal_returns_50(self):
        out = ia._normalize_to_100({"a": 5.0, "b": 5.0})
        assert out["a"] == 50.0 and out["b"] == 50.0

    def test_empty_returns_empty(self):
        assert ia._normalize_to_100({}) == {}

    def test_higher_value_higher_score(self):
        out = ia._normalize_to_100({"a": 1.0, "b": 9.0})
        assert out["b"] > out["a"]


# ════════════════════════════════════════════════════════════
# _combine_dims — 三维加权 + 权重重归一
# ════════════════════════════════════════════════════════════

class TestCombineDims:

    def test_full_three_dims_weighted(self):
        dims = {
            "prosperity": {"x": 100.0},
            "valuation": {"x": 0.0},
            "moneyflow": {"x": 50.0},
        }
        weights = {"w1": 0.5, "w2": 0.3, "w3": 0.2}
        out = ia._combine_dims(dims, weights)
        # 100*0.5 + 0*0.3 + 50*0.2 = 60
        assert abs(out["x"] - 60.0) < 0.01

    def test_missing_dim_reweights(self):
        """景气度维度缺失（空 dict），w2/w3 重归一为 100%"""
        dims = {
            "prosperity": {},          # 缺失
            "valuation": {"x": 80.0},
            "moneyflow": {"x": 40.0},
        }
        weights = {"w1": 0.5, "w2": 0.3, "w3": 0.2}
        out = ia._combine_dims(dims, weights)
        # 重归一：w2=0.3/0.5=0.6, w3=0.2/0.5=0.4 → 80*0.6 + 40*0.4 = 64
        assert abs(out["x"] - 64.0) < 0.01

    def test_w1_zero_excludes_prosperity(self):
        """w1=0 时景气度不参与（即使有数据）"""
        dims = {
            "prosperity": {"x": 100.0},
            "valuation": {"x": 0.0},
            "moneyflow": {"x": 50.0},
        }
        weights = {"w1": 0.0, "w2": 0.5, "w3": 0.5}
        out = ia._combine_dims(dims, weights)
        # 0*0.5 + 50*0.5 = 25
        assert abs(out["x"] - 25.0) < 0.01

    def test_only_moneyflow_present(self):
        dims = {"prosperity": {}, "valuation": {}, "moneyflow": {"x": 70.0}}
        weights = {"w1": 0.3, "w2": 0.3, "w3": 0.4}
        out = ia._combine_dims(dims, weights)
        assert abs(out["x"] - 70.0) < 0.01  # 重归一为 100%

    def test_industry_only_in_some_dims(self):
        """某行业只在部分维度有值，按该行业present维度重归一"""
        dims = {
            "prosperity": {"x": 100.0, "y": 60.0},
            "valuation": {"x": 50.0},  # y 缺
            "moneyflow": {"x": 0.0, "y": 80.0},
        }
        weights = {"w1": 0.4, "w2": 0.3, "w3": 0.3}
        out = ia._combine_dims(dims, weights)
        # y: 只有 prosperity(0.4) + moneyflow(0.3) → 重归一 0.4/0.7, 0.3/0.7
        expected_y = 60.0 * (0.4/0.7) + 80.0 * (0.3/0.7)
        assert abs(out["y"] - expected_y) < 0.1


# ════════════════════════════════════════════════════════════
# _judge_horizon
# ════════════════════════════════════════════════════════════

class TestJudgeHorizon:

    def test_high_prosperity_low_valuation_long(self):
        # 高景气(≥60) + 低估值(估值分≥60) → long
        assert ia._judge_horizon(prosperity=70, valuation=70, moneyflow=30) == "long"

    def test_high_moneyflow_low_prosperity_short(self):
        # 高资金流(≥60) + 低景气(<50) → short
        assert ia._judge_horizon(prosperity=40, valuation=30, moneyflow=75) == "short"

    def test_otherwise_medium(self):
        assert ia._judge_horizon(prosperity=55, valuation=55, moneyflow=55) == "medium"

    def test_missing_dims_default_medium(self):
        # 维度为 None（缺失）时不触发极端规则
        assert ia._judge_horizon(prosperity=None, valuation=None, moneyflow=80) == "medium"


# ════════════════════════════════════════════════════════════
# evaluate_industry_rotation 端到端（mock 三个聚合维度）
# ════════════════════════════════════════════════════════════

class TestEvaluateThreeDim:

    def _patch_dims(self, monkeypatch):
        """mock 三个原始维度聚合，返回 {ind_code: raw_value}"""
        mf = {"801080": 1e9, "801150": 5e8, "801770": 2e8}
        pros = {"801080": 0.05, "801150": 0.25, "801770": 0.10}
        val = {"801080": 30.0, "801150": 80.0, "801770": 55.0}  # 估值分（已倒数，高=低估）
        names = {"801080": "电子", "801150": "医药生物", "801770": "通信"}

        monkeypatch.setattr(ia, "_aggregate_raw_dims_cached",
                            lambda c, level, date=None: (mf, pros, val))
        monkeypatch.setattr(ia, "_load_member_map",
                            lambda level: (names, {"x": "y"}))
        return mf, pros, val, names

    def test_three_dim_scores_not_pending(self, monkeypatch):
        self._patch_dims(monkeypatch)
        # w1>0 → 景气度参与
        result = ia.evaluate_industry_rotation(
            risk_mode="balanced", level="L2",
            weights={"w1": 0.3, "w2": 0.25, "w3": 0.45})
        assert result["success"]
        detail = result["industry_detail"]
        # 至少一个行业的 prosperity/valuation 非 "pending"
        any_code = next(iter(detail))
        assert detail[any_code]["prosperity"] != "pending"
        assert detail[any_code]["valuation"] != "pending"

    def test_weights_affect_ranking(self, monkeypatch):
        self._patch_dims(monkeypatch)
        # 资金流主导 → 801080 (最高 mf) 居前
        mf_heavy = ia.evaluate_industry_rotation(
            risk_mode="aggressive", level="L2",
            weights={"w1": 0.0, "w2": 0.0, "w3": 1.0})
        top_mf = mf_heavy["industry_scores"][0]["industry_code"]
        assert top_mf == "801080"
        # 景气度主导 → 801150 (最高 prosperity) 居前
        pros_heavy = ia.evaluate_industry_rotation(
            risk_mode="defensive", level="L2",
            weights={"w1": 1.0, "w2": 0.0, "w3": 0.0})
        top_pros = pros_heavy["industry_scores"][0]["industry_code"]
        assert top_pros == "801150"

    def test_horizon_in_detail(self, monkeypatch):
        self._patch_dims(monkeypatch)
        result = ia.evaluate_industry_rotation(
            risk_mode="balanced", level="L2",
            weights={"w1": 0.3, "w2": 0.25, "w3": 0.45})
        for code, d in result["industry_detail"].items():
            assert d["time_horizon"] in ("short", "medium", "long")
