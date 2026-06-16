"""
tests/test_llm_clients.py — LLM 接入地基测试（阶段四）

对应 requirements.md 8.10 节：
- NT-L4-01: llm_clients 无 key 时完全静默（LLM-R3/R8/AC16）
- NT-L4-02: OpenAICompatProvider 协议测试（LLM-R4）
- NT-L4-03: LLMClient 三级降级链（LLM-R3/R4）
- NT-L4-04: llm_review_regime 无 key 时行为与 OFF 一致（LLM-R3/R5）
- NT-L4-05: llm_review_regime 正常调用结构（LLM-R5/R7）
- NT-L4-06: llm_review_regime LLM 不改变 regime/risk_mode（LLM-R5）
- NT-L4-10: macro_regime_node llm_review 存入 state（LLM-R5/AC17）
- NT-L4-11: api_style=anthropic 走 AnthropicProvider（LLM-R4）

所有测试 mock，不调真实 API。
"""

import pytest


# ── Fake provider 工具（依赖倒置注入） ───────────────────────

def _make_fake_openai_client(content="解读结果"):
    class _Msg:
        pass

    class _Choice:
        pass

    class _Resp:
        pass

    class _Completions:
        def create(self, **kwargs):
            msg = _Msg()
            msg.content = content
            choice = _Choice()
            choice.message = msg
            resp = _Resp()
            resp.choices = [choice]
            return resp

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    return _Client()


def _make_fake_anthropic_client(content="anthropic解读"):
    class _Block:
        pass

    class _Resp:
        pass

    class _Messages:
        def create(self, **kwargs):
            block = _Block()
            block.text = content
            resp = _Resp()
            resp.content = [block]
            return resp

    class _Client:
        messages = _Messages()

    return _Client()


# ── L0 core 层：Provider 协议 ───────────────────────────────

class TestProvider:
    """NT-L4-02 / NT-L4-11: Provider 协议"""

    def test_openai_compat_provider_chat(self):
        """NT-L4-02: OpenAICompatProvider 返回结构化 dict"""
        from llm_clients.core import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key="sk-test", base_url="https://api.test/v1",
            model="deepseek-chat",
            _client=_make_fake_openai_client("解读：市场情绪偏谨慎"),
        )
        out = provider.chat([{"role": "user", "content": "hi"}])
        assert isinstance(out, dict)
        assert out["content"] == "解读：市场情绪偏谨慎"
        assert out["model"] == "deepseek-chat"
        assert "provider" in out

    def test_anthropic_provider_chat(self):
        """NT-L4-11: AnthropicProvider 走 anthropic 协议"""
        from llm_clients.core import AnthropicProvider

        provider = AnthropicProvider(
            api_key="sk-ant-test", model="claude-3-haiku-20240307",
            _client=_make_fake_anthropic_client("anthropic解读"),
        )
        out = provider.chat([{"role": "user", "content": "hi"}])
        assert isinstance(out, dict)
        assert out["content"] == "anthropic解读"
        assert out["model"] == "claude-3-haiku-20240307"


# ── L1 client 层：降级链 ────────────────────────────────────

class TestLLMClient:
    """NT-L4-03: 三级降级链"""

    def test_chat_primary_success(self):
        from llm_clients.client import LLMClient
        from llm_clients.core import OpenAICompatProvider

        primary = OpenAICompatProvider(
            api_key="k", base_url="u", model="m",
            _client=_make_fake_openai_client("ok"),
        )
        client = LLMClient(primary=primary, fallback=None)
        out = client.chat([{"role": "user", "content": "x"}])
        assert out["available"] is True
        assert out["content"] == "ok"

    def test_three_level_fallback(self):
        """primary 失败 → fallback 失败 → 降级返回 available=False"""
        from llm_clients.client import LLMClient

        class _BadProvider:
            name = "bad"

            def chat(self, messages, **kwargs):
                raise RuntimeError("boom")

        client = LLMClient(primary=_BadProvider(), fallback=_BadProvider())
        out = client.chat([{"role": "user", "content": "x"}])
        assert out["available"] is False
        # 不抛异常即可

    def test_fallback_used_when_primary_fails(self):
        from llm_clients.client import LLMClient
        from llm_clients.core import OpenAICompatProvider

        class _BadProvider:
            name = "bad"

            def chat(self, messages, **kwargs):
                raise RuntimeError("boom")

        fallback = OpenAICompatProvider(
            api_key="k", base_url="u", model="fb",
            _client=_make_fake_openai_client("fallback-content"),
        )
        client = LLMClient(primary=_BadProvider(), fallback=fallback)
        out = client.chat([{"role": "user", "content": "x"}])
        assert out["available"] is True
        assert out["content"] == "fallback-content"


# ── L2 集成层：get_client 无 key 静默 ───────────────────────

class TestLLMClientNoKey:
    """NT-L4-01: 无 key 静默降级"""

    def test_get_client_no_key_silent(self, monkeypatch):
        """无任何 key 时 get_client 返回不可用 client，chat 不抛异常"""
        # 清空所有可能的 key 环境变量
        for var in ["DEEPSEEK_API_KEY", "YUNWU_API_KEY", "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY", "QWEN_API_KEY"]:
            monkeypatch.delenv(var, raising=False)

        from llm_clients import get_client

        client = get_client()
        # 即使没有 key，也不应抛异常
        out = client.chat([{"role": "user", "content": "hi"}])
        assert out["available"] is False


# ── 业务层：llm_review_regime ───────────────────────────────

class TestRegimeReview:
    """NT-L4-04/05/06: L1 regime LLM 研判"""

    def _env(self):
        return {
            "regime": "risk_off",
            "risk_mode": "defensive",
            "confidence": 0.7,
            "indicators": {"vix": 35.0, "pmi": 47.0},
            "trigger_reason": "VIX=35 > 30，风险收缩",
        }

    def test_llm_review_regime_no_key_equals_off(self, monkeypatch):
        """NT-L4-04: 无 key 时返回 llm_available=False，行为等价 OFF"""
        from agents import strategy_agent

        class _UnavailClient:
            def chat(self, messages, **kwargs):
                return {"available": False}

        monkeypatch.setattr(
            "llm_clients.get_client", lambda *a, **k: _UnavailClient()
        )

        result = strategy_agent.llm_review_regime(self._env())
        assert result["llm_available"] is False

    def test_llm_review_regime_normal(self, monkeypatch):
        """NT-L4-05: 正常研判输出结构 + confidence_adjust clamp"""
        from agents import strategy_agent
        import json

        class _GoodClient:
            def chat(self, messages, **kwargs):
                return {
                    "available": True,
                    "provider": "deepseek",
                    "content": json.dumps({
                        "agree": True,
                        "confidence_adjust": 0.5,  # 超出 0.2，应被 clamp
                        "reasoning": "VIX 高位确认防御",
                    }),
                }

        monkeypatch.setattr(
            "llm_clients.get_client", lambda *a, **k: _GoodClient()
        )

        result = strategy_agent.llm_review_regime(self._env())
        assert result["llm_available"] is True
        assert "agree" in result
        assert "reasoning" in result
        assert "provider" in result
        # clamp 在 ±0.2
        assert abs(result["confidence_adjust"]) <= 0.2 + 1e-9

    def test_llm_never_changes_regime(self, monkeypatch):
        """NT-L4-06: LLM 试图改 regime 也无效"""
        from agents import strategy_agent
        import json

        class _MaliciousClient:
            def chat(self, messages, **kwargs):
                return {
                    "available": True,
                    "provider": "deepseek",
                    "content": json.dumps({
                        "agree": False,
                        "confidence_adjust": 0.1,
                        "reasoning": "我觉得应该是牛市",
                        "regime": "risk_on",       # 试图篡改
                        "risk_mode": "aggressive",  # 试图篡改
                    }),
                }

        monkeypatch.setattr(
            "llm_clients.get_client", lambda *a, **k: _MaliciousClient()
        )

        env = self._env()
        result = strategy_agent.llm_review_regime(env)
        # llm_review_regime 只返回咨询字段，不含 regime/risk_mode 覆盖
        assert result.get("regime") is None or result["regime"] == env["regime"]
        # env 本身不被修改
        assert env["regime"] == "risk_off"
        assert env["risk_mode"] == "defensive"


# ── 上层：macro_regime_node 接入 ────────────────────────────

class TestMacroNodeLLM:
    """NT-L4-10: macro_regime_node llm_review 存入 state"""

    def test_macro_node_stores_llm_review(self, monkeypatch):
        from agents import orchestrator, strategy_agent

        # mock 规则评估，避免依赖本地数据
        monkeypatch.setattr(
            strategy_agent, "evaluate_market_condition",
            lambda: {
                "regime": "balanced", "risk_mode": "balanced",
                "confidence": 0.6, "indicators": {},
                "trigger_reason": "测试",
            },
        )
        # mock LLM 研判
        monkeypatch.setattr(
            strategy_agent, "llm_review_regime",
            lambda env, **k: {
                "llm_available": True, "agree": True,
                "confidence_adjust": 0.1, "reasoning": "ok",
                "provider": "deepseek",
            },
        )

        state = {}
        out = orchestrator.macro_regime_node(state)
        assert "macro_regime" in out
        mr = out["macro_regime"]
        # regime 保持规则值
        assert mr["regime"] == "balanced"
        # llm_review 被存入
        assert "llm_review" in mr


# ── 业务层：llm_review_candidates（LLM-R6） ──────────────────

class TestLLMCandidateReview:
    """LLM-R6: L3 候选 LLM 点评"""

    def _signals(self, n=8):
        return [
            {"code": f"60000{i}", "name": f"股{i}", "signal": "WATCH",
             "price": 10.0 + i, "score": 90 - i, "reason": f"理由{i}",
             "position_type": "config", "time_horizon": "medium"}
            for i in range(n)
        ]

    def test_no_key_returns_empty(self, monkeypatch):
        """无 key/OFF 时返回空列表，行为等价 OFF（LLM-R3）"""
        from agents import strategy_agent

        class _Unavail:
            is_available = False
            def chat(self, messages, **kwargs):
                return {"available": False}

        monkeypatch.setattr("llm_clients.get_client", lambda *a, **k: _Unavail())
        result = strategy_agent.llm_review_candidates(self._signals())
        assert result == []

    def test_only_top_n_reviewed(self, monkeypatch):
        """只点评 Top N（candidate_top_n），控制成本（LLM-R6）"""
        from agents import strategy_agent
        import json

        calls = {"count": 0}

        class _Good:
            is_available = True
            def chat(self, messages, **kwargs):
                calls["count"] += 1
                return {"available": True, "provider": "deepseek",
                        "content": json.dumps({
                            "code": "x", "view": "看好",
                            "reasoning": "财务稳健"})}

        monkeypatch.setattr("llm_clients.get_client", lambda *a, **k: _Good())
        result = strategy_agent.llm_review_candidates(self._signals(8), top_n=3)
        # 只调 3 次（Top 3），返回 3 条点评
        assert calls["count"] == 3
        assert len(result) == 3

    def test_review_does_not_modify_signal_fields(self, monkeypatch):
        """点评不修改 signal/score/position_type（LLM-R6）"""
        from agents import strategy_agent
        import json

        class _Good:
            is_available = True
            def chat(self, messages, **kwargs):
                return {"available": True, "provider": "deepseek",
                        "content": json.dumps({
                            "code": "x", "view": "看好", "reasoning": "r",
                            "score": 999, "signal": "SELL"})}  # 试图改字段

        monkeypatch.setattr("llm_clients.get_client", lambda *a, **k: _Good())
        signals = self._signals(2)
        orig = [dict(s) for s in signals]
        strategy_agent.llm_review_candidates(signals, top_n=2)
        # 原 signals 不被修改
        assert signals == orig

    def test_review_output_structure(self, monkeypatch):
        """点评输出结构含 code/reasoning/provider（LLM-R7 结构化）"""
        from agents import strategy_agent
        import json

        class _Good:
            is_available = True
            def chat(self, messages, **kwargs):
                return {"available": True, "provider": "deepseek",
                        "content": json.dumps({
                            "code": "600001", "view": "看好",
                            "reasoning": "ROE 高，估值合理"})}

        monkeypatch.setattr("llm_clients.get_client", lambda *a, **k: _Good())
        result = strategy_agent.llm_review_candidates(self._signals(1), top_n=1)
        assert len(result) == 1
        r = result[0]
        assert "code" in r and "reasoning" in r and "provider" in r

    def test_empty_signals_returns_empty(self, monkeypatch):
        from agents import strategy_agent
        monkeypatch.setattr("llm_clients.get_client",
                            lambda *a, **k: type("C", (), {"is_available": True, "chat": lambda s, m, **k: {"available": True, "content": "{}"}})())
        assert strategy_agent.llm_review_candidates([], top_n=5) == []

    def test_malformed_json_skipped(self, monkeypatch):
        """LLM 输出非法 JSON 时跳过该条，不崩溃（LLM-R3）"""
        from agents import strategy_agent

        class _Bad:
            is_available = True
            def chat(self, messages, **kwargs):
                return {"available": True, "provider": "deepseek",
                        "content": "这不是JSON"}

        monkeypatch.setattr("llm_clients.get_client", lambda *a, **k: _Bad())
        result = strategy_agent.llm_review_candidates(self._signals(3), top_n=3)
        assert result == []


class TestLLMCandidateReviewNode:
    """LLM-R6: llm_candidate_review_node 接入工作流"""

    def test_node_off_skips(self, monkeypatch):
        """autonomy OFF / 无 key 时节点静默跳过，不改 filtered_signals"""
        from agents import orchestrator
        monkeypatch.setattr("agents.strategy_agent.llm_review_candidates",
                            lambda *a, **k: [])
        state = {
            "filtered_signals": [{"code": "600001", "name": "x", "signal": "WATCH",
                                   "price": 10.0, "score": 80, "reason": "r",
                                   "position_type": "config", "time_horizon": "medium"}],
            "log": [],
        }
        out = orchestrator.llm_candidate_review_node(state)
        # filtered_signals 不变
        assert out["filtered_signals"] == state["filtered_signals"]
        assert out.get("llm_candidate_reviews", []) == []

    def test_node_stores_reviews(self, monkeypatch):
        """有点评时存入 state['llm_candidate_reviews']"""
        from agents import orchestrator
        monkeypatch.setattr("agents.strategy_agent.llm_review_candidates",
                            lambda *a, **k: [{"code": "600001", "view": "看好",
                                              "reasoning": "r", "provider": "deepseek"}])
        state = {
            "filtered_signals": [{"code": "600001", "name": "x", "signal": "WATCH",
                                   "price": 10.0, "score": 80, "reason": "r"}],
            "log": [],
        }
        out = orchestrator.llm_candidate_review_node(state)
        assert len(out["llm_candidate_reviews"]) == 1
        assert out["llm_candidate_reviews"][0]["code"] == "600001"
