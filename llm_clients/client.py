"""
llm_clients/client.py — L1 能力层

LLMClient：三级降级链（primary → fallback → degraded）。
Provider 通过构造参数注入（依赖倒置），无项目依赖。
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEGRADED = {"available": False, "content": None, "model": None, "provider": None}


class LLMClient:
    """三级降级链 LLM 客户端。"""

    def __init__(self, primary=None, fallback=None):
        self._primary = primary
        self._fallback = fallback

    @property
    def is_available(self) -> bool:
        return self._primary is not None or self._fallback is not None

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """
        调用 LLM，三级降级：primary → fallback → 降级静默返回。
        永不抛异常（LLM-R3）。
        """
        for provider in (self._primary, self._fallback):
            if provider is None:
                continue
            try:
                result = provider.chat(messages, **kwargs)
                result["available"] = True
                return result
            except Exception as exc:
                logger.warning("LLM provider %s 失败，尝试下一级: %s",
                               getattr(provider, "name", "?"), exc)
        logger.info("所有 LLM provider 均不可用，静默降级")
        return dict(_DEGRADED)
