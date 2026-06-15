"""
llm_clients/__init__.py — L2 集成层

get_client() 是唯一读 config.settings.LLM 的函数（项目耦合收敛点，LLM-R4）。
"""
import logging
import os
from typing import Optional

from .client import LLMClient

logger = logging.getLogger(__name__)


def get_client() -> LLMClient:
    """
    按 config.settings.LLM 配置构建三级降级链 LLMClient。
    任何 key 缺失/配置异常均静默降级，不抛异常（LLM-R3/R8）。
    autonomy_level=OFF 或无 key 时返回不可用 client。
    """
    try:
        from config.settings import LLM as cfg
    except Exception:
        return LLMClient()

    autonomy = cfg.get("autonomy_level", "OFF")
    if autonomy == "OFF":
        return LLMClient()

    primary = _make_provider(cfg.get("primary", {}), cfg)
    fallback = _make_provider(cfg.get("fallback", {}), cfg)
    return LLMClient(primary=primary, fallback=fallback)


def _make_provider(pcfg: dict, global_cfg: dict):
    """构建单个 Provider，失败返回 None。"""
    if not pcfg:
        return None
    try:
        api_key_env = pcfg.get("api_key_env", "")
        api_key = os.getenv(api_key_env, "") if api_key_env else ""
        if not api_key:
            return None

        api_style = pcfg.get("api_style", "openai")
        model = pcfg.get("model", "")
        temperature = global_cfg.get("temperature", 0.1)
        timeout = global_cfg.get("timeout", 30)

        if api_style == "anthropic":
            from .core import AnthropicProvider
            return AnthropicProvider(
                api_key=api_key, model=model,
                temperature=temperature,
            )
        else:
            from .core import OpenAICompatProvider
            base_url = pcfg.get("base_url") or os.getenv(
                pcfg.get("base_url_env", ""), ""
            )
            if not base_url:
                return None
            return OpenAICompatProvider(
                api_key=api_key, base_url=base_url, model=model,
                temperature=temperature, timeout=timeout,
            )
    except Exception as exc:
        logger.warning("构建 LLM provider 失败: %s", exc)
        return None
