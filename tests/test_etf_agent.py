"""
test_etf_agent.py — ETF 独立评分体系单元测试

覆盖 agents/etf_agent.py 三维评分（技术动量/估值代理/政策配置）+ 合并逻辑。
技术面用合成价格序列，估值/政策面用 monkeypatch 隔离外部数据依赖。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import agents.etf_agent as ea


@pytest.fixture(autouse=True)
def _reset_config_cache():
    """每个测试前清空配置缓存，避免串扰。"""
    ea._config_cache = None
    yield
    ea._config_cache = None


def _make_df(returns_pct: float, n: int = 80, start: float = 1.0):
    """构造 n 日单调价格序列，末值相对 lookback(20) 前涨 returns_pct%。"""
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    # 让最后20日涨幅约 returns_pct%
    base = np.linspace(start, start, n)
    daily = (1 + returns_pct / 100) ** (1 / 20)
    prices = [start]
    for _ in range(n - 1):
        prices.append(prices[-1] * daily)
    df = pd.DataFrame({"close": prices, "amount": 5_000_000.0}, index=dates)
    df.index.name = "date"
    return df


class TestPolicyScore:
    def test_positive(self):
        score, detail = ea._policy_score({"policy_outlook": "positive"})
        assert score == 75.0
        assert detail["outlook"] == "positive"

    def test_negative(self):
        score, _ = ea._policy_score({"policy_outlook": "negative"})
        assert score == 25.0

    def test_missing(self):
        score, detail = ea._policy_score({})
        assert np.isnan(score)
        assert detail == {}


class TestTechnicalScore:
    def test_strong_momentum_high_score(self):
        df = _make_df(20.0)  # +20% over 20d
        score, detail = ea._technical_score(df, bench_ret=3.0, lookback=20)
        assert score > 70
        assert detail["abs_momentum"] == pytest.approx(19.0, abs=1.5)
        assert detail["above_rf"] is True

    def test_negative_momentum_low_score(self):
        df = _make_df(-10.0)
        score, detail = ea._technical_score(df, bench_ret=3.0, lookback=20)
        assert score < 45
        assert detail["above_rf"] is False

    def test_relative_momentum_penalty(self):
        df = _make_df(5.0)
        # 跑输基准时分数低于跑赢基准
        s_win, _ = ea._technical_score(df, bench_ret=0.0, lookback=20)
        s_lose, _ = ea._technical_score(df, bench_ret=20.0, lookback=20)
        assert s_win > s_lose

    def test_insufficient_data(self):
        df = _make_df(5.0, n=10)
        score, detail = ea._technical_score(df, bench_ret=3.0, lookback=20)
        assert np.isnan(score)


class TestCombine:
    def test_all_present(self):
        out = ea._combine({"technical": 80, "valuation": 60, "policy": 50},
                          {"technical": 0.45, "valuation": 0.25, "policy": 0.30})
        # 0.45*80 + 0.25*60 + 0.30*50 = 36+15+15 = 66
        assert out == pytest.approx(66.0, abs=0.1)

    def test_missing_renormalized(self):
        # 估值缺失，权重在 technical/policy 间重归一
        out = ea._combine({"technical": 80, "valuation": float("nan"), "policy": 50},
                          {"technical": 0.45, "valuation": 0.25, "policy": 0.30})
        # (0.45*80 + 0.30*50) / 0.75 = (36+15)/0.75 = 68
        assert out == pytest.approx(68.0, abs=0.1)

    def test_all_missing(self):
        out = ea._combine({"technical": float("nan")}, {"technical": 0.45})
        assert np.isnan(out)


class TestEvaluateETF:
    def test_full_three_dim(self, monkeypatch):
        # 配置：有 sw_l2_codes + 政策
        monkeypatch.setattr(ea, "load_etf_config", lambda: {
            "159516": {"name": "半导体设备ETF", "theme": "半导体设备",
                       "sw_l2_codes": ["801081.SI"], "policy_outlook": "positive",
                       "benchmark": "000300"}
        })
        # 估值代理：返回低估分 30
        monkeypatch.setattr("agents.industry_agent.get_industry_score",
                            lambda code, level="L2", date=None: {"valuation": 30.0})
        monkeypatch.setattr(ea, "_benchmark_return", lambda *a, **k: 3.0)

        df = _make_df(15.0)
        r = ea.evaluate_etf("159516", df=df)
        assert r["available"] is True
        assert r["technical"] is not None
        assert r["valuation"] == 30.0
        assert r["policy"] == 75.0
        assert r["total_score"] is not None
        # 估值偏贵提示（val<25 才触发，30 不触发）
        assert "估值偏贵" not in r["advice"]

    def test_expensive_valuation_warning(self, monkeypatch):
        monkeypatch.setattr(ea, "load_etf_config", lambda: {
            "X": {"name": "X", "sw_l2_codes": ["801081.SI"],
                  "policy_outlook": "positive", "benchmark": "000300"}
        })
        monkeypatch.setattr("agents.industry_agent.get_industry_score",
                            lambda code, level="L2", date=None: {"valuation": 10.0})
        monkeypatch.setattr(ea, "_benchmark_return", lambda *a, **k: 3.0)
        df = _make_df(25.0)
        r = ea.evaluate_etf("X", df=df)
        assert "估值偏贵" in r["advice"]

    def test_no_sw_l2_codes_macro_etf(self, monkeypatch):
        # 黄金 ETF：无 sw_l2_codes，估值维度缺失
        monkeypatch.setattr(ea, "load_etf_config", lambda: {
            "159934": {"name": "黄金ETF", "sw_l2_codes": [],
                       "policy_outlook": "neutral", "benchmark": "000300"}
        })
        monkeypatch.setattr(ea, "_benchmark_return", lambda *a, **k: 3.0)
        df = _make_df(2.0)
        r = ea.evaluate_etf("159934", df=df)
        assert r["valuation"] is None
        assert r["policy"] == 50.0
        assert r["available"] is True

    def test_unknown_etf_no_meta(self, monkeypatch):
        monkeypatch.setattr(ea, "load_etf_config", lambda: {})
        monkeypatch.setattr(ea, "_benchmark_return", lambda *a, **k: 3.0)
        df = _make_df(5.0)
        r = ea.evaluate_etf("999999", df=df)
        # 无政策无估值，但技术面可算
        assert r["technical"] is not None
        assert r["valuation"] is None
        assert r["policy"] is None
        assert r["available"] is True


class TestStrongThemeIndustries:
    """L3 联动：强势主题 ETF → 申万 L2 行业"""

    def _patch(self, monkeypatch, config, scores):
        monkeypatch.setattr(ea, "load_etf_config", lambda: config)

        def _fake_eval(code, weights=None, date=None):
            s = scores.get(code)
            meta = config.get(code, {})
            return {"total_score": s, "name": meta.get("name", code),
                    "theme": meta.get("theme", "")}
        monkeypatch.setattr(ea, "evaluate_etf", _fake_eval)

    def test_strong_above_threshold_included(self, monkeypatch):
        config = {
            "A": {"sw_l2_codes": ["801104.SI"], "theme": "工业软件"},
            "B": {"sw_l2_codes": ["801081.SI"], "theme": "半导体"},
        }
        self._patch(monkeypatch, config, {"A": 65.0, "B": 40.0})
        out = ea.get_strong_theme_industries(threshold=60.0)
        assert "801104.SI" in out
        assert "801081.SI" not in out  # 40 < 60
        assert out["801104.SI"]["theme"] == "工业软件"

    def test_macro_etf_no_l2_skipped(self, monkeypatch):
        config = {"GOLD": {"sw_l2_codes": [], "theme": "黄金"}}
        self._patch(monkeypatch, config, {"GOLD": 90.0})
        out = ea.get_strong_theme_industries(threshold=60.0)
        assert out == {}

    def test_same_l2_keeps_highest(self, monkeypatch):
        # 两只 ETF 映射同一 L2，保留高分
        config = {
            "A": {"sw_l2_codes": ["801081.SI"], "theme": "半导体设备"},
            "B": {"sw_l2_codes": ["801081.SI"], "theme": "半导体设备2"},
        }
        self._patch(monkeypatch, config, {"A": 70.0, "B": 80.0})
        out = ea.get_strong_theme_industries(threshold=60.0)
        assert out["801081.SI"]["score"] == 80.0
        assert out["801081.SI"]["etf"] == "B"

    def test_none_score_skipped(self, monkeypatch):
        config = {"A": {"sw_l2_codes": ["801104.SI"], "theme": "工业软件"}}
        self._patch(monkeypatch, config, {"A": None})
        out = ea.get_strong_theme_industries(threshold=60.0)
        assert out == {}
