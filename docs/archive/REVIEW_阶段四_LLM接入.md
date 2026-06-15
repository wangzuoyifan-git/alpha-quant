# 阶段四设计评审 — LLM 接入（仅外围，不碰核心信号）

> 状态：**待 review**（方案A：设计先行，确认后再实现）
> 生成时间：2026-06-11
> 关联：docs/agent.md「阶段四」、docs/requirements.md 8.7 节（LLM-R1~R3）
> review 通过后：内容正式写入 requirements.md/agent.md，删除本评审文档，进入 TDD 实现

---

## 一、目标与边界（先对齐范围）

### 1.1 一句话目标
把 `config/settings.py` 已配置的 LLM **用起来**，但**只在外围环节做定性增强**，核心信号生成路径（扫描/风控/回测）保持纯规则、可复现。

### 1.2 三项交付（来自 agent.md 阶段四）
| 编号 | 交付 | 性质 |
|---|---|---|
| 4.A | `llm_clients/` 统一适配层 | 基础设施（最底层，先做） |
| 4.B | L1 regime LLM 辅助研判 | 调整规则 regime 的 confidence，不改 regime 本身 |
| 4.C | L3 候选标的 LLM 深度分析 | 对 Top N 信号生成定性点评，不改 signal/score |

### 1.3 强制约束（来自 requirements.md LLM-R1~R3）
- **LLM-R1 禁入核心路径**：LLM 不参与信号生成、风控过滤、回测计算
- **LLM-R2 Grounding**：所有数值来自 store 层，LLM 只做定性解读，prompt 明确"不要生成任何数字"
- **LLM-R3 可降级**：LLM 失败（超时/限流/错误/未配置 key）自动回退规则版，记录降级日志

### 1.4 关键设计原则（需你确认）
> **原则 P1：LLM 是"旁路增强"而非"串行依赖"**
> L1 的 regime/risk_mode 由规则决定（已实现且经测试）。LLM 只调整 confidence 和补充 reasoning 文本。
> **即使 LLM 完全不可用，整个 workflow 行为与阶段三完全一致。**

> **原则 P2：LLM 输出不进入 decision_context 的约束字段**
> decision_context 的 regime/risk_mode/max_position/allowed_industries/strategy_keys 全部由规则决定。
> LLM 输出只写入新增的"咨询性"字段（llm_review），供人工参考和日志留痕，不影响下层动作空间。

---

## 二、模块设计：llm_clients/（4.A，洋葱架构降低耦合）

> **Q1 已确认**：放项目根目录（与 agent_tools/ 平级），作为横切基础设施层。
> **Q8 已确认**：支持 OpenAI 兼容（DeepSeek/Qwen/GLM/聚合平台）+ Claude 原生，用 `api_style` 解耦协议与模型。

### 2.0.1 关键概念：`api_style` 解耦「协议」与「模型」

> **混合接入场景**（用户实际用法）：国外模型走聚合平台（yunwu.ai 等），国内模型直连（DeepSeek 等）。

**核心认知**：聚合平台（yunwu.ai / OpenRouter / one-api）= **OpenAI 兼容端点**。
无论背后是 Claude / GPT / DeepSeek，对聚合平台都用 **OpenAI 格式**请求，靠 `model` 字段指定要哪个模型。

因此「模型品牌」与「API 协议」必须解耦，用 `api_style` 字段决定走哪个 Provider：

| 场景 | api_style | base_url | model | 用的 Provider |
|---|---|---|---|---|
| DeepSeek 官方直连 | `openai` | `api.deepseek.com/v1` | `deepseek-chat` | OpenAICompat |
| **yunwu 聚合访问 Claude** | `openai` | `yunwu.ai/v1` | `claude-opus-4` | OpenAICompat |
| **yunwu 聚合访问 GPT** | `openai` | `yunwu.ai/v1` | `gpt-4o` | OpenAICompat |
| 通义千问直连 | `openai` | `dashscope.../compatible-mode/v1` | `qwen-plus` | OpenAICompat |
| **Anthropic 官方原生直连** | `anthropic` | （SDK 默认） | `claude-3-5-sonnet` | Anthropic |

**关键结论**：
- **聚合平台所有场景都落在 `api_style="openai"`**，一份 OpenAICompatProvider 代码全搞定
- `api_style="anthropic"` 仅用于**直连 Anthropic 官方**（走 anthropic SDK）
- `model` 字段是业务层（要哪个模型），`api_style` 是协议层（怎么调），两者正交
- 典型混合配置：primary 走聚合平台访问 Claude，fallback 直连 DeepSeek

### 2.0 架构总览（洋葱模型：依赖由内向外单向）

```
╭──────────────────────────────────────────────╮
│  L2 集成层（integration/）                              │
│  唯一与本项目耦合的层                                     │
│  ┌────────────────────────────────────────╮  │
│  │  L1 能力层（capability/）                          │  │
│  │  纯逻辑，通过参数注入驱动（不 import 项目）         │  │
│  │  ┌──────────────────────────────────╮  │  │
│  │  │  L0 核心层（core/）                         │  │  │
│  │  │  协议定义 + SDK 封装，零项目依赖            │  │  │
│  │  │                                            │  │  │
│  │  │  ChatRequest / ChatResponse（dataclass）   │  │  │
│  │  │  Provider（Protocol，定义 chat 抽象）       │  │  │
│  │  │  OpenAICompatProvider（openai SDK）        │  │  │
│  │  │  AnthropicProvider（anthropic SDK）        │  │  │
│  │  │                                            │  │  │
│  │  │  依赖：仅外部 SDK + stdlib                  │  │  │
│  │  ╰──────────────────────────────────╯  │  │
│  │                                                  │  │
│  │  LLMClient：primary→fallback→降级 三级链          │  │
│  │  retry / timeout / 限幅 / 结果归一化              │  │
│  │                                                  │  │
│  │  依赖：L0 + 显式注入的 config（不主动读项目）      │  │
│  ╰────────────────────────────────────────╯  │
│                                                          │
│  get_client()：读 config.settings.LLM 组装 LLMClient     │
│  prompts.py：本项目 regime/候选点评模板（grounding 约束）│
│                                                          │
│  依赖：config.settings（整个模块唯一的项目 import）      │
╰──────────────────────────────────────────────╯
                      ▲
                      │ 只有这一层暴露给项目
          agents / strategy_agent / llm_analysis_node
```

**关键设计收益**：
- **L0/L1 完全独立**：可原样拷贝到其他项目，不知道本项目存在
- **与 data 层零耦合**：grounding 数值由 Agent 层"喂入" prompt，llm_clients 全程只处理文本，不 import store
- **项目耦合压缩到 1 个函数**：`get_client()` 是整个模块唯一读 `config.settings` 的地方
- **Provider 扩展无影响**：新增 Claude/文心只在 L0 加实现，L1/L2 不动

---

### 2.1 L0 核心层 — `llm_clients/core.py`

**职责**：定义"什么是一次 LLM 调用"，并实现 OpenAI 兼容 + Claude 两种协议的实际请求。

```python
from dataclasses import dataclass, field
from typing import Protocol

@dataclass
class ChatRequest:
    """标准化请求（不绑定任何具体 API）"""
    system: str
    user: str
    model: str
    temperature: float = 0.1
    timeout: int = 30

@dataclass
class ChatResponse:
    """标准化响应（归一化 OpenAI/Claude/降级的返回）"""
    success: bool
    content: str = ""
    provider: str = ""        # openai / deepseek / claude / none
    error: str | None = None
    elapsed: float = 0.0
    usage: dict = field(default_factory=dict)  # {"prompt_tokens": ..., "completion_tokens": ...}


class Provider(Protocol):
    """LLM Provider 协议，L1 只依赖这个抽象"""
    def chat(self, req: ChatRequest) -> ChatResponse: ...


class OpenAICompatProvider:
    """OpenAI 兼容实现，一份代码服务 DeepSeek/OpenAI/Qwen/GLM/Kimi/MiniMax"""
    def __init__(self, api_key: str, base_url: str, provider_name: str = "openai"):
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=30)
        self._name = provider_name
    
    def chat(self, req: ChatRequest) -> ChatResponse:
        import time
        t0 = time.time()
        try:
            resp = self._client.chat.completions.create(
                model=req.model,
                messages=[
                    {"role": "system", "content": req.system},
                    {"role": "user", "content": req.user},
                ],
                temperature=req.temperature,
                timeout=req.timeout,
            )
            return ChatResponse(
                success=True,
                content=resp.choices[0].message.content,
                provider=self._name,
                elapsed=time.time() - t0,
                usage={"prompt_tokens": resp.usage.prompt_tokens,
                       "completion_tokens": resp.usage.completion_tokens},
            )
        except Exception as e:
            return ChatResponse(success=False, error=str(e), elapsed=time.time()-t0)


class AnthropicProvider:
    """Claude (Anthropic) 实现，API 与 OpenAI 不兼容"""
    def __init__(self, api_key: str, provider_name: str = "claude"):
        from anthropic import Anthropic
        self._client = Anthropic(api_key=api_key)
        self._name = provider_name
    
    def chat(self, req: ChatRequest) -> ChatResponse:
        import time
        t0 = time.time()
        try:
            # Claude API: system 是独立参数，不在 messages 里
            resp = self._client.messages.create(
                model=req.model,
                system=req.system,
                messages=[{"role": "user", "content": req.user}],
                temperature=req.temperature,
                max_tokens=2048,
                timeout=req.timeout,
            )
            return ChatResponse(
                success=True,
                content=resp.content[0].text,
                provider=self._name,
                elapsed=time.time() - t0,
                usage={"prompt_tokens": resp.usage.input_tokens,
                       "completion_tokens": resp.usage.output_tokens},
            )
        except Exception as e:
            return ChatResponse(success=False, error=str(e), elapsed=time.time()-t0)
```

**依赖**：只有外部 SDK（`openai` / `anthropic`）和 stdlib。
**关键特性**：**不知道本项目存在**。不知道什么是 regime、不知道 config.settings、不碰 data.store。

---

### 2.2 L1 能力层 — `llm_clients/client.py`

**职责**：把"单次调用"组合成"可靠调用"——降级链、重试、结果归一化。

```python
from llm_clients.core import Provider, ChatRequest, ChatResponse
import logging

logger = logging.getLogger(__name__)


class LLMClient:
    """三级降级链：primary → fallback → 规则降级（degraded）"""
    
    def __init__(self,
                 primary: Provider | None,
                 fallback: Provider | None = None,
                 max_retries: int = 2):
        """
        注意：传入的是 Provider 实例（依赖倒置）。
        L1 不主动读配置，配置由 L2 翻译成参数注入。
        """
        self._primary = primary
        self._fallback = fallback
        self._max_retries = max_retries
    
    def chat(self, system: str, user: str,
             model: str = "deepseek-chat",
             temperature: float = 0.1,
             timeout: int = 30) -> ChatResponse:
        """
        单轮对话，三级降级链。
        
        返回的 ChatResponse.success == False 时，调用方必须使用规则结果（LLM-R3）。
        永不向上抛异常（符合 agent 层"异常不上抛"风格）。
        """
        req = ChatRequest(system=system, user=user, model=model,
                          temperature=temperature, timeout=timeout)
        
        # 试 primary
        if self._primary:
            resp = self._try_provider(self._primary, req)
            if resp.success:
                return resp
            logger.warning(f"Primary provider 失败: {resp.error}，尝试 fallback")
        
        # 试 fallback
        if self._fallback:
            resp = self._try_provider(self._fallback, req)
            if resp.success:
                return resp
            logger.warning(f"Fallback provider 失败: {resp.error}，降级为规则")
        
        # 都失败 → degraded
        logger.info("LLM 全部不可用，降级为纯规则")
        return ChatResponse(
            success=False,
            provider="none",
            error="All providers failed or not configured",
        )
    
    def _try_provider(self, provider: Provider, req: ChatRequest) -> ChatResponse:
        """带重试的单次 provider 调用"""
        for attempt in range(self._max_retries):
            resp = provider.chat(req)
            if resp.success:
                return resp
            if attempt < self._max_retries - 1:
                logger.debug(f"Retry {attempt+1}/{self._max_retries}")
        return resp
    
    def is_available(self) -> bool:
        """primary 或 fallback 任一可用"""
        return self._primary is not None or self._fallback is not None
```

**依赖**：只有 L0（`Provider` / `ChatRequest` / `ChatResponse`）。
**关键特性**：**config 通过构造参数注入**（依赖倒置），自己不读 `config.settings`。
**测试性**：测试时注入 mock Provider，完全不碰真实 SDK 和项目配置。

---

### 2.3 L2 集成层 — `llm_clients/__init__.py` + `prompts.py`

**职责**：唯一与本项目对接的地方。把项目配置"翻译"成 L1 需要的参数。

```python
# llm_clients/__init__.py
import os
import logging
from llm_clients.core import OpenAICompatProvider, AnthropicProvider, ChatResponse
from llm_clients.client import LLMClient

logger = logging.getLogger(__name__)

__all__ = ["get_client", "ChatResponse"]


def get_client() -> LLMClient:
    """
    从项目配置组装 LLMClient。
    
    这是整个 llm_clients 唯一 import config.settings 的函数（项目耦合收敛点）。
    按 api_style 选 Provider（解耦协议与模型）：
      - api_style="anthropic" → AnthropicProvider（仅 Anthropic 官方原生直连）
      - api_style="openai"（默认）→ OpenAICompatProvider
        （覆盖 DeepSeek/Qwen/GLM 直连 + yunwu.ai/OpenRouter 等聚合平台）
    """
    from config.settings import LLM  # ← 唯一的项目 import
    
    def _build_provider(cfg: dict):
        """按单个 provider 配置构造 Provider 实例，key 未配置返回 None"""
        key = os.getenv(cfg.get("api_key_env", ""), "")
        if not key:
            return None
        api_style = cfg.get("api_style", "openai")
        name = cfg.get("provider", api_style)  # 日志用的友好名
        if api_style == "anthropic":
            # 仅 Anthropic 官方原生（聚合平台访问 Claude 走 openai 分支）
            return AnthropicProvider(key, provider_name=name)
        else:  # "openai" — 直连各家 + 所有聚合平台
            base_url = os.getenv(cfg.get("base_url_env", ""), cfg.get("base_url", ""))
            return OpenAICompatProvider(api_key=key, base_url=base_url, provider_name=name)
    
    return LLMClient(
        primary=_build_provider(LLM.get("primary", {})),
        fallback=_build_provider(LLM.get("fallback", {})),
        max_retries=LLM.get("max_retries", 2),
    )
```

```python
# llm_clients/prompts.py — 本项目业务模板（grounding 约束语写在这）
"""
LLM prompt 模板，集中管理 grounding 约束。
"""

REGIME_REVIEW_SYSTEM = """你是宏观分析助手，协助评估市场环境。

规则：
1. 你的任务是解读给定的宏观数据，判断市场趋势的合理性
2. 禁止生成任何数字，所有数值由用户提供
3. 只输出 JSON 格式：{"agree": true/false, "confidence_adjust": float, "reasoning": str}
4. confidence_adjust 范围 [-0.2, 0.2]，表示你对规则判断的认同/质疑程度
"""

def build_regime_prompt(indicators: dict, rule_regime: str, rule_confidence: float) -> tuple[str, str]:
    """
    构建 L1 regime 研判 prompt（grounding）。
    
    Args:
        indicators: 来自 store 的宏观数值（VIX/PMI/M2/bond_yield/...）
        rule_regime: 规则版判定的 regime
        rule_confidence: 规则版置信度
    
    Returns:
        (system_prompt, user_prompt)
    """
    user = f"""规则版判定：{rule_regime}（置信度 {rule_confidence:.2f}）

宏观数据（来自数据库）：
- VIX: {indicators.get('vix', 'N/A')}
- PMI: {indicators.get('pmi', 'N/A')}
- M2同比: {indicators.get('m2_yoy', 'N/A')}%
- 10年国债收益率: {indicators.get('cn_10y', 'N/A')}%

请判断这个判定是否合理，给出你的看多/看空理由和置信度调整建议。"""
    
    return (REGIME_REVIEW_SYSTEM, user)


CANDIDATE_ANALYSIS_SYSTEM = """你是股票分析助手，对候选标的做定性点评。

规则：
1. 基于给定的信号评分和触发理由做定性分析
2. 禁止生成新的数字或价格预测
3. 输出 JSON 格式：{"flag": "positive/neutral/caution", "comment": str}
"""

def build_candidate_prompt(signal: dict) -> tuple[str, str]:
    """构建 L3 候选点评 prompt（grounding）"""
    user = f"""标的：{signal['name']}（{signal['code']}）
信号：{signal['signal']}
评分：{signal['score']}
触发理由：{signal['reason']}
价格：{signal['price']}

请对该标的做定性点评，重点关注信号强度和触发理由的合理性。"""
    
    return (CANDIDATE_ANALYSIS_SYSTEM, user)
```

**依赖**：`config.settings`（本项目）+ L1。
**关键**：**全项目对 config 的耦合，只发生在 `get_client()` 这一个函数里**。

---

### 2.4 目录结构（最终）

```
llm_clients/
├── __init__.py          # L2 集成：get_client() + 导出 ChatResponse
├── core.py              # L0 核心：Provider协议 + OpenAI/Claude实现
├── client.py            # L1 能力：LLMClient 降级链
└── prompts.py           # L2 集成：本项目 prompt 模板（grounding 约束）
```

---

### 2.5 依赖清单（需补充 requirements.txt）

```txt
# LLM SDK（阶段四新增）
openai>=2.40.0                # 已装，显式声明
anthropic>=0.40.0             # 新增，支持 Claude
```

### 2.6 环境变量（补充 .env.example）

```bash
# ── LLM API Keys（阶段四，可选；未配置则自动降级为纯规则）──
# 直连国内模型
DEEPSEEK_API_KEY=
# 聚合平台（yunwu.ai / OpenRouter 等，OpenAI 兼容，一个 key 访问多模型）
YUNWU_API_KEY=
YUNWU_BASE_URL=https://yunwu.ai/v1
# 直连 OpenAI（可选代理）
OPENAI_API_KEY=
OPENAI_BASE_URL=
# 直连 Anthropic 官方原生（可选；走聚合平台访问 Claude 则不需要这个）
ANTHROPIC_API_KEY=
```

### 2.7 config/settings.py 扩展

```python
LLM = {
    "primary": {
        "api_style": "openai",           # openai（兼容/聚合） / anthropic（官方原生）
        "provider": "deepseek",          # 友好名，仅日志用
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
    },
    "fallback": {
        "api_style": "openai",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",  # 可选代理
    },
    "temperature": 0.1,
    "timeout": 30,
    "max_retries": 2,
    # 阶段四业务开关（新增）
    "enable_regime_review": True,         # 4.B L1 regime LLM 研判
    "enable_candidate_analysis": True,    # 4.C L3 候选 LLM 点评
    "candidate_top_n": 5,                 # 4.C 分析数量
    "confidence_adjust_max": 0.2,         # 4.B 置信度调整上限
}
```

**配置示例 A — 混合模式（用户典型用法）**：primary 走聚合平台访问 Claude，fallback 直连国内 DeepSeek
```python
"primary": {
    "api_style": "openai",                  # 聚合平台用 OpenAI 格式
    "provider": "yunwu-claude",             # 友好名
    "model": "claude-opus-4",               # 聚合平台靠 model 指定背后模型
    "api_key_env": "YUNWU_API_KEY",
    "base_url_env": "YUNWU_BASE_URL",       # https://yunwu.ai/v1
},
"fallback": {
    "api_style": "openai",
    "provider": "deepseek",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    "base_url": "https://api.deepseek.com/v1",
},
```

**配置示例 B — 直连 Anthropic 官方原生**（不经聚合平台）：
```python
"primary": {
    "api_style": "anthropic",               # ← 走 anthropic SDK
    "provider": "claude",
    "model": "claude-3-5-sonnet-20241022",
    "api_key_env": "ANTHROPIC_API_KEY",
    # Anthropic 官方不需要 base_url
},
```

---

## 三、L1 regime LLM 辅助研判（4.B）

### 3.1 接入点
`agents/strategy_agent.py` 新增函数（不改 `evaluate_market_condition` 本体）：

```python
def llm_review_regime(env: dict) -> dict:
    """
    对规则版 regime 做 LLM 定性研判（旁路增强，LLM-R1/R2/R3）。

    输入：evaluate_market_condition() 的结果（含 indicators 数值）
    输出：{
        "llm_available": bool,        # LLM 是否真正参与（False=降级）
        "agree": bool | None,         # LLM 是否认同规则 regime（None=降级）
        "confidence_adjust": float,   # 置信度调整量 [-0.2, +0.2]，降级时 0
        "reasoning": str,             # LLM 定性分析文本（降级时为规则说明）
        "provider": str,
    }

    grounding：indicators 数值由调用方注入 prompt，LLM 只判断方向是否合理。
    """
```

### 3.2 confidence 调整逻辑（关键：受限调整，不翻转决策）
- LLM 只能在 `[-0.2, +0.2]` 范围微调 confidence
- **regime 和 risk_mode 永不被 LLM 改变**（P1 原则）
- 调整后 confidence 仍 clamp 到 [0, 1]
- 示例：规则 confidence=0.6，LLM 强烈认同 → 0.6+0.2=0.8；LLM 质疑 → 0.6-0.2=0.4

> **请确认这个 ±0.2 的调整幅度。** 太大会让 LLM 实质影响决策（违反 P1），太小则无意义。

### 3.3 Bull/Bear 辩论（借鉴 TradingAgents，可选简化）
> agent.md 提到"Bull/Bear 辩论思路"。完整辩论（多轮多角色）成本高、不确定性大。
>
> **本阶段建议简化为"单次双视角研判"**：一个 prompt 让 LLM 同时给出看多理由和看空理由，
> 再给出净倾向。**不做多轮辩论循环**（留给后续按需扩展）。
>
> **请确认：本阶段做"单次双视角"还是要完整多轮辩论？** 我倾向单次（够用、可控、易测）。

### 3.4 orchestrator 接入
`macro_regime_node` 在规则评估后、构建 decision_context 前，调用 `llm_review_regime`：
- 把 `confidence_adjust` 应用到 env["confidence"]
- 把 llm_review 完整结果存入 `state["macro_regime"]["llm_review"]`（供日志/审计）
- **decision_context 不变**（regime/risk_mode/strategy_keys 仍由规则定）

---

## 四、L3 候选标的 LLM 深度分析（4.C）

### 4.1 接入点
`agent_tools/strategy_tools.py` 或新增 `agent_tools/llm_tools.py`：

```python
def analyze_candidates_llm(signals: list[dict], top_n: int = 5) -> dict:
    """
    对扫描信号的 Top N（按 score 降序）做 LLM 定性点评。

    输入：signals（run_scan 输出的信号 dict 列表）
    输出：{
        "llm_available": bool,
        "reviews": [
            {"code": str, "name": str,
             "llm_comment": str,      # 定性点评（基本面/消息面提示），不含新数字
             "llm_flag": str},        # "positive"/"neutral"/"caution"，定性标签
        ],
        "provider": str,
    }

    约束：
    - 只分析 Top N（默认 5），控制成本和延迟
    - signal 的 score/price/reason 注入 prompt，LLM 不生成新数字（LLM-R2）
    - 降级时 reviews=[]，llm_available=False
    """
```

### 4.2 数据来源限制（grounding）
- 当前 signal dict 只有 code/name/signal/price/score/reason
- LLM 点评基于这些既有信息 + 可选注入的基本面摘要（若调 query 工具取 store 数据）
- **本阶段简化：只用 signal 已有字段 + 行业归属，不额外拉基本面**
  > **请确认：4.C 是否需要为 LLM 额外查询基本面数据（store_fundamental）注入 prompt？**
  > 简化版只用 signal 现有字段。增强版查财务摘要注入（更有信息量但更复杂）。
  > 我倾向**本阶段简化**，基本面注入留到验证 LLM 链路稳定后再加。

### 4.3 orchestrator 接入（新增节点 or 现有节点扩展？）
**两个选项：**
- **选项 1（推荐）**：在 `risk_filter` 后新增 `llm_analysis_node`，对通过风控的信号做点评，结果存 `state["llm_candidate_reviews"]`，随 decision_log 落盘
- **选项 2**：在 notify 前内联调用，不新增节点

> **请确认：用选项 1（新增节点，更清晰可审计）还是选项 2（内联，更轻量）？**
> 我推荐选项 1，与分层架构的"每层可观测"一致。

### 4.4 是否进入推送内容？
- LLM 点评可附加到推送消息（notify_signals）作为辅助信息
- **本阶段建议：只落盘 decision_log，暂不改推送格式**（推送改动影响面大，单独评估）

---

## 五、配置与环境变量

### 5.1 config/settings.py 扩展
LLM 配置已存在。新增可选开关（默认关闭，避免无 key 时报错）：
```python
LLM = {
    ...现有配置...,
    "enable_regime_review": True,      # 4.B 开关
    "enable_candidate_analysis": True, # 4.C 开关
    "candidate_top_n": 5,              # 4.C 分析数量
    "confidence_adjust_max": 0.2,      # 4.B 置信度调整上限
    "cache_dir": "data/cache/llm",     # LLM 响应缓存（可选）
}
```

### 5.2 .env.example 补充
```bash
# LLM API Keys（阶段四，可选；未配置则自动降级为纯规则）
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
OPENAI_BASE_URL=
```

### 5.3 LLM 响应缓存（可选，需你定）
> 同一交易日多次运行，regime 研判输入相同，可缓存 LLM 响应省钱。
> **请确认：本阶段是否做响应缓存？** 我倾向**先不做**（简单优先，省钱是次要矛盾）。

---

## 六、新增需求编号（写入 requirements.md 8.7 节）

| 编号 | 需求 | 验收 |
|---|---|---|
| LLM-R4 | `llm_clients` 统一适配层，三级降级（primary→fallback→规则） | mock 各级失败，验证降级链 |
| LLM-R5 | L1 regime LLM 研判只调整 confidence（±0.2 内），不改 regime/risk_mode | 验证 regime 不被 LLM 改变 |
| LLM-R6 | L3 候选 LLM 点评只对 Top N，不改 signal/score | 验证 signals 数据不被修改 |
| LLM-R7 | 所有 LLM 接入点 key 未配置时静默降级，workflow 行为不变 | 无 key 时全测试通过 |
| AC16 | LLM 调用全部经 llm_clients，禁止 agents 直接 import openai | 代码检查 |
| AC17 | LLM 输出只写咨询性字段（llm_review/llm_candidate_reviews），不进 decision_context 约束字段 | 验证 decision_context 结构不含 LLM 字段 |

---

## 七、测试设计（TDD，写入 requirements.md 8.x + traceability）

| 用例 | 覆盖 | mock 方式 |
|---|---|---|
| NT-L4-01 | OpenAICompatProvider chat 成功（L0） | mock openai create 返回正常 |
| NT-L4-02 | AnthropicProvider chat 成功（L0） | mock anthropic messages.create |
| NT-L4-03 | LLMClient primary 成功（L1） | 注入 mock Provider |
| NT-L4-04 | primary 失败 → fallback 成功（L1） | 注入 mock：primary 抛异常、fallback 正常 |
| NT-L4-05 | 全部失败 → degraded（L1） | 注入 mock：两者都失败 |
| NT-L4-06 | key 未配置 → get_client 组装空 client（L2） | monkeypatch 环境变量为空 |
| NT-L4-06b | api_style 选 Provider（openai/anthropic 分流）（L2） | monkeypatch 配置 api_style，断言构造对应 Provider |
| NT-L4-07 | llm_review_regime 不改 regime/risk_mode | mock get_client 返回，断言 regime 不变 |
| NT-L4-08 | confidence 调整限幅 ±0.2 且 clamp [0,1] | 构造极端调整值 |
| NT-L4-09 | LLM 降级时 regime confidence 用规则原值 | mock 降级，断言 confidence 不变 |
| NT-L4-10 | analyze_candidates 只分析 Top N | 构造 10 信号，断言 reviews ≤ 5 |
| NT-L4-11 | analyze_candidates 不修改输入 signals | 断言 signals 原 dict 不变 |
| NT-L4-12 | workflow 全程无 key 时与阶段三行为一致 | 集成测试，mock 无 key |

> **测试不调真实 API，全部 mock Provider 实例（洋葱架构优势）：**
> - L0 测试：mock `openai.OpenAI().chat.completions.create` / `anthropic.Anthropic().messages.create`
> - L1 测试：注入 mock Provider 实例（直接构造 `LLMClient(primary=mock_p, fallback=mock_f)`），**不碰 SDK**
> - L2 测试：monkeypatch 环境变量，测试 `get_client()` 组装逻辑
> - 业务测试（NT-L4-05~10）：mock `get_client()` 返回整个 `LLMClient`，完全隔离

---

## 八、实现顺序（从底往上，确认后执行）

1. **文档先行**：requirements.md（LLM-R4~R8/AC16~17/NT-L4-*）+ agent.md（阶段四标记 in-progress）
2. **TDD**：写 tests/test_llm_clients.py + tests/test_llm_regime_review.py（红）
3. **L0**：llm_clients/core.py（Provider 协议 + OpenAICompat + Anthropic 实现）
4. **L1**：llm_clients/client.py（LLMClient 降级链）
5. **L2**：llm_clients/__init__.py（get_client）+ prompts.py
6. **中层**：strategy_agent.llm_review_regime + agent_tools.analyze_candidates_llm
7. **上层**：orchestrator macro_regime_node 接入 + 新增 llm_analysis_node
8. **配置**：settings.py 开关 + .env.example + requirements.txt（openai/anthropic）
9. **验证**：新测试 + 全回归（确保无 key 时 294 测试不破）
10. **复查**：对照 LLM-R1~R8 逐条核实，特别是"核心路径无 LLM"
11. **提交**

---

## 九、需要你拍板的决策点（汇总）

| # | 决策点 | 我的建议 | 状态 |
|---|---|---|---|
| Q1 | llm_clients 放项目根 or agents/llm/ | **项目根**（横切基础设施，洋葱 L0/L1/L2 分层） | ✅ 已确认（项目根 + 洋葱架构） |
| Q2 | confidence 调整幅度 ±0.2 | **±0.2**（受限，不翻转决策） | ⏳ 待确认 |
| Q3 | Bull/Bear 单次双视角 or 多轮辩论 | **单次双视角**（够用、可控、易测） | ⏳ 待确认 |
| Q4 | 4.C 是否注入基本面数据 | **本阶段不注入**（先验证链路，后增强） | ⏳ 待确认 |
| Q5 | L3 LLM 用新增节点 or 内联 | **新增 llm_analysis_node**（可审计） | ⏳ 待确认 |
| Q6 | 是否做 LLM 响应缓存 | **本阶段不做**（简单优先） | ⏳ 待确认 |
| Q7 | LLM 点评是否进推送 | **本阶段只落盘**（推送改动单独评估） | ⏳ 待确认 |
| Q8 | LLM Provider 支持范围 | **`api_style` 解耦协议与模型**：openai 兼容（DeepSeek/Qwen/GLM 直连 + yunwu/OpenRouter 聚合平台）+ anthropic 原生。支持混合模式（聚合+直连）。不引入 langchain | ✅ 已确认（含聚合平台 + Claude） |

---

**剩余待确认**：Q2/Q3/Q4/Q5/Q6/Q7（Q1/Q8 已敲定）。
你可逐条回复，或说"按建议执行"。确认后我会把设计正式写入 requirements.md/agent.md，删除本评审文档，开始 TDD 实现。
