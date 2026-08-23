"""阶段 2: 主内容分析 (Issue 07).

LLM 分析归一化材料: 提炼主题 / 摘要 / 核心概念 / 建议检索查询
(查询供阶段 3 搜索增强使用, 体现「材料也搜」的统一管道设计).

LLM 输出经 JSON 提取 + pydantic 校验, 失败按配置重试 (带错误反馈);
超限抛 RuntimeError → 任务 error.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from ...api import config
from ...prompt.analyze import ANALYZE_SYSTEM_PROMPT, ANALYZE_USER_TEMPLATE
from .. import llm
from ..json_utils import extract_json
from ..types import ContentAnalysis, ParsedMaterial, PipelineState

logger = logging.getLogger(__name__)

# 材料输入 LLM 的消息上限 (前 5 万字符, 足够覆盖典型学习材料)
_MATERIAL_LIMIT = 50_000


async def analyze_material(material: ParsedMaterial) -> ContentAnalysis:
    """分析材料 → ContentAnalysis (重试 LLM_RETRIES 次, 带错误反馈)."""
    model = llm.get_chat_model()
    last_error = ""
    for attempt in range(config.LLM_RETRIES + 1):
        feedback = ""
        if attempt:
            feedback = (
                "\n\n上次输出解析失败: "
                + last_error
                + "\n请重新输出合法 JSON (仅 JSON, 无其他内容)."
            )
            logger.warning(
                "分析重试 %d/%d: %s", attempt, config.LLM_RETRIES, last_error
            )
        user_prompt = (
            ANALYZE_USER_TEMPLATE.format(
                input_type=material.origin, material=material.text[:_MATERIAL_LIMIT]
            )
            + feedback
        )
        resp = await model.ainvoke(
            [
                SystemMessage(content=ANALYZE_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )
        try:
            return ContentAnalysis.model_validate(extract_json(resp.content))
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)
    raise RuntimeError(f"主内容分析失败 (LLM 输出无法解析): {last_error}")


async def analyze_node(state: PipelineState) -> dict:
    """主内容分析节点: 产出 ContentAnalysis 写入 state["analysis"]."""
    material = state["material"]
    analysis = await analyze_material(material)
    return {"analysis": analysis}
