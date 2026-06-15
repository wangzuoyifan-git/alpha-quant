"""
llm_clients/prompts.py — L2 Prompt 模板层

项目业务 prompt，grounding 约束写在这里（LLM-R2）。
"""

REGIME_REVIEW_SYSTEM = """你是量化投资宏观分析师，职责是对规则引擎的市场环境判断做二次研判。

约束（严格遵守）：
1. 你的任务是解读给定的数据，不要生成任何数字
2. 不要修改 regime 或 risk_mode，只评估置信度调整
3. 输出严格为 JSON，不要有多余文字

输出格式：
{
  "agree": true/false,
  "confidence_adjust": <float，范围 -0.2 到 0.2>,
  "reasoning": "<推理链：引用具体指标值，说明同意或不同意的理由>"
}"""


def regime_review_user(env: dict) -> str:
    """构造 regime 研判 user prompt，数值全部来自 store（LLM-R2 grounding）。"""
    indicators = env.get("indicators", )
    lines = [f"  {k}: {v}" for k, v in indicators.items()]
    indicators_str = "\n".join(lines) if lines else "  （无指标数据）"
    return (
        f"规则引擎判定：\n"
        f"  regime={env.get('regime')}, risk_mode={env.get('risk_mode')}, "
        f"confidence={env.get('confidence')}\n"
        f"  理由：{env.get('trigger_reason')}\n\n"
        f"当前宏观指标（数据来自本地存储，请勿生成新数字）：\n"
        f"{indicators_str}\n\n"
        f"请按输出格式给出你的研判。"
    )
