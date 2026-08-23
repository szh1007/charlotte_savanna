"""阶段 3: 联网搜索增强 (Issue 07, ADR-0002 统一管道核心差异).

DeepAgents 检索 subagent 承担检索环节: 挂可插拔检索源工具
(网络搜索 / Context7 官方文档 / 输入材料文档 / 知识库), 按
分析阶段的建议查询自主编排, 输出结构化检索报告.

材料输入也执行搜索增强 (统一管道): 材料可能不完整或过时, 联网
补全知识面并交叉验证. 知识库旅程 (Issue 11): 不走 subagent — 骨架轮
需要确定的概览资料 (subagent 是否产出报告不确定), 对建议查询逐个
确定性检索知识库 (QA.md Q7/Q8: 知识来源 = Milvus 检索片段).
"""

import logging

from ...agents.search_agent import SearchItem, SearchReport, run_search_agent
from ...pipeline.sources import build_sources
from ..types import PipelineState

logger = logging.getLogger(__name__)

_KB_OVERVIEW_QUERY_LIMIT = 3  # 概览检索建议查询上限 (骨架轮输入)
_KB_OVERVIEW_RESULT_LIMIT = 20  # 概览检索结果总量上限 (对齐解构源上限)


async def _kb_overview_report(kb_id: int, analysis) -> SearchReport:
    """知识库概览检索: 对建议查询逐个确定性检索 (失败降级, 不抛)."""
    kb_source = build_sources(kb_id=kb_id)[0]
    queries = (analysis.suggested_queries or [])[:_KB_OVERVIEW_QUERY_LIMIT] or [
        analysis.topic
    ]
    items: list[SearchItem] = []
    for query in queries:
        try:
            results = kb_source.search(query, max_results=5)
        except Exception as exc:
            logger.warning("知识库概览检索失败 (query=%s): %s", query, exc)
            continue
        items.extend(
            SearchItem(
                title=r.title,
                url=r.url,
                content=r.content,
                source_type=r.source_type,
            )
            for r in results
        )
        if len(items) >= _KB_OVERVIEW_RESULT_LIMIT:
            break
    return SearchReport(topic=analysis.topic, queries=queries, results=items)


async def search_node(state: PipelineState) -> dict:
    """搜索增强节点: 构建源集合 → 检索 subagent → SearchReport."""
    material = state["material"]
    analysis = state["analysis"]
    if state["inp"].kb_id is not None:
        report = await _kb_overview_report(state["inp"].kb_id, analysis)
        sources = build_sources(kb_id=state["inp"].kb_id)
    else:
        sources = build_sources(material_text=material.text)
        if not sources:
            logger.warning("无可用检索源 (journey=%s), 搜索增强跳过", material.origin)
            report = SearchReport(
                topic=analysis.topic, queries=analysis.suggested_queries
            )
        else:
            report = await run_search_agent(
                sources, analysis.topic, analysis.suggested_queries
            )
    logger.info(
        "搜索增强完成 (topic=%s, 源=%d, 结果=%d)",
        analysis.topic,
        len(sources),
        len(report.results),
    )
    return {"search_report": report}
