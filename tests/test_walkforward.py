"""P1-3：walk-forward、参数平台与实验注册表测试（不联网）。"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import research.experiments as exp
import research.platform as platform
import research.walkforward as wf


def _fake_result(ret=0.1, sharpe=1.2, mdd=-0.05, n=10):
    dates = pd.bdate_range("2024-01-02", periods=60)
    eq = pd.Series([1_000_000 * (1 + ret) ** (i / 59) for i in range(60)],
                   index=dates)
    return SimpleNamespace(
        total_return=ret, annual_return=ret * 2, max_drawdown=mdd,
        sharpe_ratio=sharpe, total_trades=n, equity_curve=eq,
    )


class TestFolds:
    def test_three_folds_contiguous_and_ordered(self):
        folds = wf.make_folds("2024-01-02", "2026-10-26", 3, 12, 3, 4)
        assert len(folds) == 3
        for f in folds:
            tr_s, tr_e = f["train"]
            va_s, va_e = f["valid"]
            te_s, te_e = f["test"]
            assert pd.Timestamp(tr_s) < pd.Timestamp(tr_e)
            assert pd.Timestamp(tr_e) == pd.Timestamp(va_s)
            assert pd.Timestamp(va_e) == pd.Timestamp(te_s)
            assert pd.Timestamp(te_s) < pd.Timestamp(te_e)
        # test 段首尾相接
        assert folds[0]["test"][1] == folds[1]["test"][0]
        assert folds[1]["test"][1] == folds[2]["test"][0]

    def test_oos_metrics(self):
        rets = pd.Series([0.001] * 100,
                         index=pd.bdate_range("2024-01-02", periods=100))
        m = wf._metrics_from_returns(rets)
        assert m["total_return"] > 0
        assert m["max_drawdown"] == 0.0
        assert m["sharpe"] > 0


class TestExperimentRegistry:
    def test_record_and_load(self, tmp_path, monkeypatch):
        reg = tmp_path / "experiments.jsonl"
        monkeypatch.setattr(exp, "REGISTRY", reg)
        entry = exp.record_experiment(
            kind="test", name="demo", params={"a": 1},
            periods={"start": "2024-01-01", "end": "2024-12-31"},
            metrics={"sharpe": 1.0}, conclusion="ok",
        )
        assert entry["git_commit"]
        assert "data" in entry and "timestamp" in entry
        rows = exp.load_experiments()
        assert len(rows) == 1
        assert rows[0]["name"] == "demo"
        assert rows[0]["params"] == {"a": 1}


class TestWalkforward:
    def test_run_walkforward_with_fake_engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(exp, "REGISTRY", tmp_path / "exp.jsonl")
        import backtest.fast as fast_mod

        calls = []

        def fake_run(strategy, start, end, **kwargs):
            calls.append((start, end))
            return _fake_result(ret=0.02, sharpe=0.8)

        monkeypatch.setattr(fast_mod, "run_fast", fake_run)
        out = wf.run_walkforward("quality_trend", "2024-01-02", "2026-10-26",
                                 3, 12, 3, 4)
        assert len(out["folds"]) == 3
        assert len(calls) == 9  # 每折 train/valid/test
        assert out["oos"]["total_return"] > 0
        text = wf.format_walkforward(out)
        assert "Walk-forward" in text and "OOS 拼接" in text


class TestPlatform:
    def test_platform_grid_and_stability(self, tmp_path, monkeypatch):
        monkeypatch.setattr(exp, "REGISTRY", tmp_path / "exp.jsonl")
        import backtest.fast as fast_mod

        def fake_run(strategy, start, end, **kwargs):
            params = kwargs.get("params") or {}
            sharpe = 1.0 + float(params.get("vol_max", 0)) * 10
            return _fake_result(ret=0.1, sharpe=sharpe)

        monkeypatch.setattr(fast_mod, "run_fast", fake_run)
        out = platform.run_platform(
            "quality_trend", "2024-07-01", "2026-10-26",
            grid={"vol_max": [0.045, 0.05, 0.055], "ext_max": [0.08]},
        )
        assert len(out["rows"]) == 3
        assert 0 <= out["stability"]["positive_ratio"] <= 1
        assert "参数平台" in platform.format_platform(out)
