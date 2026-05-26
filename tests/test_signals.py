"""
test_signals.py — 信号持久化单元测试
覆盖 save_signals / load_signals 往返一致性
"""
import json
import sys
from pathlib import Path
from datetime import datetime

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.base import Signal
from signals.manager import save_signals, load_signals, SIGNAL_DIR


@pytest.fixture
def temp_signal_dir(tmp_path, monkeypatch):
    import signals.manager as sm
    original = sm.SIGNAL_DIR
    sm.SIGNAL_DIR = tmp_path / "processed"
    sm.SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    yield sm.SIGNAL_DIR
    sm.SIGNAL_DIR = original


def _make_signals(n=3):
    return [
        Signal(
            code=f"000{i:03d}",
            name=f"测试股票{i}",
            signal="BUY" if i % 2 == 0 else "SELL",
            price=10.0 + i,
            reason=f"测试原因{i}",
            score=60.0 + i * 5,
            extra={"index": i},
        )
        for i in range(1, n + 1)
    ]


class TestSaveLoadSignals:

    def test_roundtrip_same_count(self, temp_signal_dir):
        sigs = _make_signals(5)
        save_signals(sigs, date="2024-06-01")
        loaded = load_signals("2024-06-01")
        assert len(loaded) == 5

    def test_roundtrip_content(self, temp_signal_dir):
        sigs = _make_signals(3)
        save_signals(sigs, date="2024-06-15")
        loaded = load_signals("2024-06-15")
        codes = {d["code"] for d in loaded}
        assert codes == {"000001", "000002", "000003"}
        for d in loaded:
            assert "signal" in d
            assert "price" in d
            assert "score" in d

    def test_multiple_saves_merge(self, temp_signal_dir):
        """同一天多次保存不会覆盖，而是合并"""
        sigs1 = _make_signals(2)
        sigs2 = [
            Signal(code="000999", name="新增", signal="BUY", price=20.0, reason="later", score=80.0)
        ]
        save_signals(sigs1, date="2024-07-01")
        save_signals(sigs2, date="2024-07-01")
        loaded = load_signals("2024-07-01")
        assert len(loaded) == 3

    def test_load_nonexistent_date(self, temp_signal_dir):
        assert load_signals("2099-01-01") == []

    def test_generated_at_field_present(self, temp_signal_dir):
        sigs = _make_signals(1)
        save_signals(sigs, date="2024-08-01")
        loaded = load_signals("2024-08-01")
        assert "generated_at" in loaded[0]

    def test_default_date_uses_today(self, temp_signal_dir):
        sigs = _make_signals(1)
        path_str = save_signals(sigs)
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in path_str

    def test_save_signals_returns_path(self, temp_signal_dir):
        sigs = _make_signals(1)
        path = save_signals(sigs, date="2024-09-01")
        assert path.endswith("signals_2024-09-01.json")
