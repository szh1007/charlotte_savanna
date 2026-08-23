"""阶段 4: 图谱解构 (Issue 07).

LLM 基于材料 + 分析 + 检索结果, 解构为契约知识图谱
(CONTRACT.md v1: 章节 → 知识点 + 依赖边 + 来源引用).

质量保障: JSON 提取 → 契约本地校验 (contract.validate_graph_dict,
与 Django 落库端校验同逻辑) → 失败重试带错误反馈 → 超限抛异常
(任务 error, 前端可重试). 产出严格遵循 v1 契约, 04/05/06 不受影响.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ...api import config
from ...prompt.deconstruct import (
    DECONSTRUCT_SYSTEM_PROMPT,
    DECONSTRUCT_USER_TEMPLATE,
)
from .. import llm
from ..contract import to_contract_dict, validate_graph_dict
from ..json_utils import extract_json
from ..types import PipelineState

logger = logging.getLogger(__name__)

_MATERIAL_PREVIEW_LIMIT = 30_000  # 原文预览进 prompt 的上限 (完整内容已分析)
_MAX_SOURCE_ITEMS = 20  # 检索资料进 prompt 上限


def _format_sources(report) -> str:
    """检索报告 → 编号资料列表 (prompt 中 sources 引用编号)."""
    lines = []
    for idx, item in enumerate(report.results[:_MAX_SOURCE_ITEMS], start=1):
        url = f" ({item.url})" if getattr(item, "url", "") else ""
        content = (item.content or "").strip().replace("\n", " ")
        lines.append(f"[{idx}] {item.title}{url}\n{content[:800]}")
    return "\n\n".join(lines) or "(检索无结果, 请基于材料分析解构)"


async def deconstruct_graph(material, analysis, search_report, journey_id: int) -> dict:
    """解构图谱 (重试 LLM_RETRIES 次, 每次带契约校验错误反馈)."""
    model = llm.get_chat_model()
    sources_text = _format_sources(search_report)
    user_prompt = DECONSTRUCT_USER_TEMPLATE.format(
        topic=analysis.topic,
        analysis=analysis.summary,
        sources=sources_text,
        material_preview=material.text[:_MATERIAL_PREVIEW_LIMIT],
        last_error="",
    )
    last_error = ""
    for attempt in range(config.LLM_RETRIES + 1):
        if attempt:
            logger.warning(
                "解构重试 %d/%d (journey=%s): %s",
                attempt,
                config.LLM_RETRIES,
                journey_id,
                last_error,
            )
            user_prompt = DECONSTRUCT_USER_TEMPLATE.format(
                topic=analysis.topic,
                analysis=analysis.summary,
                sources=sources_text,
                material_preview=material.text[:_MATERIAL_PREVIEW_LIMIT],
                last_error=last_error,
            )
        resp = await model.ainvoke(
            [
                SystemMessage(content=DECONSTRUCT_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )
        try:
            data = extract_json(resp.content)
            graph = validate_graph_dict(data)
            return to_contract_dict(graph)
        except ValueError as exc:
            last_error = str(exc)
    raise RuntimeError(f"图谱解构失败: {last_error}")


async def deconstruct_node(state: PipelineState) -> dict:
    """图谱解构节点: 产出契约图谱 dict 写入 state["graph"]."""
    material = state["material"]
    graph = await deconstruct_graph(
        material,
        state["analysis"],
        state["search_report"],
        journey_id=state["inp"].journey_id,
    )
    logger.info(
        "图谱解构完成 (journey=%s, 章节=%d)",
        state["inp"].journey_id,
        len(graph["chapters"]),
    )
    return {"graph": graph}
