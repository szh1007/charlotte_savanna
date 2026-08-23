"""阶段 4: 图谱解构 (Issue 07 + Issue 11 知识库两轮).

LLM 基于材料 + 分析 + 检索结果, 解构为契约知识图谱
(CONTRACT.md v1: 章节 → 知识点 + 依赖边 + 来源引用).

自输入旅程 (text/file/link): 单轮全量解构.
知识库旅程 (Issue 11, kb): RAG 两轮解构 (QA.md Q9/Q10) — 第一轮基于
概览检索资料 (search 阶段产出) 建图谱骨架; 第二轮逐知识点精检索
(KbSource) 并发细化依赖边/摘要/来源. 骨架错了后面全错, 先粗后细.

质量保障: JSON 提取 → 契约本地校验 (contract.validate_graph_dict,
与 Django 落库端校验同逻辑) → 失败重试带错误反馈 → 超限抛异常
(任务 error, 前端可重试). 产出严格遵循 v1 契约, 04/05/06 不受影响.
"""

import asyncio
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from ...api import config
from ...prompt.deconstruct import (
    DECONSTRUCT_SYSTEM_PROMPT,
    DECONSTRUCT_USER_TEMPLATE,
    KB_REFINE_SYSTEM_PROMPT,
    KB_REFINE_USER_TEMPLATE,
    KB_SKELETON_SYSTEM_PROMPT,
    KB_SKELETON_USER_TEMPLATE,
)
from .. import llm
from ..contract import KnowledgePoint, to_contract_dict, validate_graph_dict
from ..json_utils import extract_json
from ..sources import KbSource
from ..types import PipelineState

logger = logging.getLogger(__name__)

_MATERIAL_PREVIEW_LIMIT = 30_000  # 原文预览进 prompt 的上限 (完整内容已分析)
_MAX_SOURCE_ITEMS = 20  # 检索资料进 prompt 上限
_KB_REFINE_CONCURRENCY = 4  # 细化轮 LLM 并发上限 (检索为同步阻塞, 先行收集)
_KB_REFINE_SNIPPET_LIMIT = 5  # 单知识点精检片段进 prompt 上限


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


def _format_kb_snippets(items: list) -> str:
    """知识点精检片段 → 编号列表 (prompt 中 sources 引用编号, Issue 11)."""
    lines = []
    for idx, item in enumerate(items[:_KB_REFINE_SNIPPET_LIMIT], start=1):
        source = item.metadata.get("filename") or item.title
        content = (item.content or "").strip().replace("\n", " ")
        lines.append(f"[{idx}] {source}\n{content[:800]}")
    return "\n\n".join(lines) or "(检索无结果, 请基于骨架摘要与主题分析细化)"


async def _build_kb_skeleton(
    material, analysis, search_report, journey_id: int
) -> dict:
    """第一轮: 概览检索资料 → LLM 建图谱骨架 (契约同构, prerequisites 可空).

    重试 LLM_RETRIES 次, 每次带契约校验错误反馈 (与单轮解构同款样板).
    """
    model = llm.get_chat_model()
    sources_text = _format_sources(search_report)
    last_error = ""
    for attempt in range(config.LLM_RETRIES + 1):
        if attempt:
            logger.warning(
                "骨架构建重试 %d/%d (journey=%s): %s",
                attempt,
                config.LLM_RETRIES,
                journey_id,
                last_error,
            )
        user_prompt = KB_SKELETON_USER_TEMPLATE.format(
            kb_name=material.title,
            topic=analysis.topic,
            analysis=analysis.summary,
            sources=sources_text,
            last_error=last_error,
        )
        resp = await model.ainvoke(
            [
                SystemMessage(content=KB_SKELETON_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )
        try:
            skeleton = validate_graph_dict(extract_json(resp.content))
            return skeleton.model_dump()
        except ValueError as exc:
            last_error = str(exc)
    raise RuntimeError(f"图谱骨架构建失败: {last_error}")


async def _refine_kp(
    skeleton_kp: dict, all_kps: list[tuple[str, str]], snippets: list, journey_id: int
) -> dict:
    """第二轮: 单知识点细化 (LLM 并发执行体) — 补全摘要/依赖边/来源.

    校验: 输出 id 必须保持骨架 id (防 LLM 改名改编号) + prerequisites 必须
    是骨架 id 集子集 + 契约结构校验; 失败重试带错误反馈, 耗尽抛 RuntimeError
    (与现有解构 fail-fast 语义一致, 不做质量回退).
    """
    model = llm.get_chat_model()
    kp_id = skeleton_kp["id"]
    skeleton_ids = {kp_id for kp_id, _ in all_kps}
    kp_list_text = "\n".join(f"- {kp_id} ({kp_title})" for kp_id, kp_title in all_kps)
    snippets_text = _format_kb_snippets(snippets)
    last_error = ""
    for attempt in range(config.LLM_RETRIES + 1):
        if attempt:
            logger.warning(
                "知识点细化重试 %d/%d (journey=%s, kp=%s): %s",
                attempt,
                config.LLM_RETRIES,
                journey_id,
                kp_id,
                last_error,
            )
        user_prompt = KB_REFINE_USER_TEMPLATE.format(
            kp_id=kp_id,
            kp_title=skeleton_kp["title"],
            all_kps=kp_list_text,
            snippets=snippets_text,
            last_error=last_error,
        )
        resp = await model.ainvoke(
            [
                SystemMessage(content=KB_REFINE_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )
        try:
            data = extract_json(resp.content)
            if not isinstance(data, dict) or data.get("id") != kp_id:
                raise ValueError(f"知识点 id 必须保持为 {kp_id}, 禁止改名")
            if data.get("title") != skeleton_kp["title"]:
                raise ValueError(f"知识点标题必须保持为 {skeleton_kp['title']}")
            prereqs = data.get("prerequisites") or []
            unknown = [p for p in prereqs if p not in skeleton_ids]
            if unknown:
                raise ValueError(
                    f"prerequisites 引用了未定义的 id: {unknown}, 只能引用骨架知识点"
                )
            kp = KnowledgePoint.model_validate(data)
            return kp.model_dump()
        except ValueError as exc:
            last_error = str(exc)
    raise RuntimeError(f"知识点细化失败 ({kp_id}): {last_error}")


async def _deconstruct_kb(
    material, analysis, search_report, journey_id: int, kb_id: int
) -> dict:
    """知识库 RAG 两轮解构 (Issue 11, QA.md Q9/Q10).

    第一轮: 概览检索资料 (search 阶段产出) → 骨架 (章节 + 知识点粗结构).
    第二轮: 逐知识点精检索 (同步收集, 检索阻塞不并发) → LLM 并发细化
    (asyncio.gather + Semaphore 限流) → 合并回骨架 → 整体契约校验.
    """
    skeleton = await _build_kb_skeleton(material, analysis, search_report, journey_id)
    all_kps = [
        (kp["id"], kp["title"])
        for chapter in skeleton["chapters"]
        for kp in chapter["knowledge_points"]
    ]
    # 同步收集精检片段 (KbSource.search 走同步阻塞检索链路, 并发无收益);
    # 单知识点检索失败降级为空片段 (LLM 仍可基于骨架摘要细化, 不中断管道)
    kb_source = KbSource(kb_id)
    snippets_by_kp: dict[str, list] = {}
    for kp_id, kp_title in all_kps:
        try:
            snippets_by_kp[kp_id] = kb_source.search(
                kp_title, max_results=_KB_REFINE_SNIPPET_LIMIT
            )
        except Exception as exc:
            logger.warning("知识点精检失败 (kp=%s): %s", kp_id, exc)
            snippets_by_kp[kp_id] = []

    # 并发 LLM 细化 (ainvoke 纯异步, Semaphore 限流; 失败抛首个异常 → 任务 error)
    sem = asyncio.Semaphore(_KB_REFINE_CONCURRENCY)

    async def _refine_with_sem(kp: dict):
        async with sem:
            return await _refine_kp(kp, all_kps, snippets_by_kp[kp["id"]], journey_id)

    refined_list = await asyncio.gather(
        *(
            _refine_with_sem(kp)
            for chapter in skeleton["chapters"]
            for kp in chapter["knowledge_points"]
        ),
        return_exceptions=True,
    )
    first_error = next((e for e in refined_list if isinstance(e, Exception)), None)
    if first_error is not None:
        raise first_error

    # 回填骨架: 章节结构保持不变, 细化结果按 id 替换
    refined_by_id = {kp["id"]: kp for kp in refined_list}
    for chapter in skeleton["chapters"]:
        chapter["knowledge_points"] = [
            refined_by_id[kp["id"]] for kp in chapter["knowledge_points"]
        ]
    graph = validate_graph_dict(skeleton)
    return to_contract_dict(graph)


async def deconstruct_node(state: PipelineState) -> dict:
    """图谱解构节点: 产出契约图谱 dict 写入 state["graph"]."""
    material = state["material"]
    journey_id = state["inp"].journey_id
    if state["inp"].kb_id is not None:
        graph = await _deconstruct_kb(
            material,
            state["analysis"],
            state["search_report"],
            journey_id=journey_id,
            kb_id=state["inp"].kb_id,
        )
    else:
        graph = await deconstruct_graph(
            material,
            state["analysis"],
            state["search_report"],
            journey_id=journey_id,
        )
    logger.info(
        "图谱解构完成 (journey=%s, 章节=%d)",
        journey_id,
        len(graph["chapters"]),
    )
    return {"graph": graph}
