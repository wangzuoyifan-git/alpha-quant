"""
llm_clients/core.py — L0 核心层

Provider 协议 + 两个具体实现（OpenAI 兼容 / Anthropic 原生）。
零项目依赖：api_key / base_url / model 通过构造参数注入。
_client 参数供测试注入 fake client（依赖倒置）。
"""
from typing import Protocol, runtime_checkable, List, Dict, Any, Optional


@runtime_checkable
class Provider(Protocol):
    """所有 provider 必须实现的协议"""
    name: str

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """返回 {"content": str, "model": str, "provider": str}"""
        ...


class OpenAICompatProvider:
    """
    OpenAI 兼容协议（DeepSeek/Qwen/GLM 直连 + yunwu.ai/OpenRouter 等聚合平台）。
    api_style=openai，聚合平台访问任何模型（含 Claude）均走此类。
    """
    name = "openai_compat"

    def __init__(self, api_key: str, base_url: str, model: str,
                 temperature: float = 0.1, timeout: int = 30,
                 _client=None):
        self.model = model
        self._temperature = temperature
        self._timeout = timeout
        if _client is not None:
            self._client = _client
        else:
            import openai
            self._client = openai.OpenAI(
                api_key=api_key, base_url=base_url, timeout=timeout
            )

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=kwargs.get("temperature", self._temperature),
        )
        return {
            "content": resp.choices[0].message.content,
            "model": self.model,
            "provider": self.name,
        }


class AnthropicProvider:
    """
    Anthropic 官方原生协议（仅 api.anthropic.com 直连）。
    api_style=anthropic。聚合平台访问 Claude 走 OpenAICompatProvider。
    """
    name = "anthropic"

    def __init__(self, api_key: str, model: str,
                 temperature: float = 0.1, max_tokens: int = 1024,
                 _client=None):
        self.model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        if _client is not None:
            self._client = _client
        else:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=kwargs.get("max_tokens", self._max_tokens),
            messages=messages,
        )
        return {
            "content": resp.content[0].text,
            "model": self.model,
            "provider": self.name,
        }
