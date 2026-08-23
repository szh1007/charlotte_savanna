"""阶段 3: 联网搜索增强 (Issue 07, ADR-0002 统一管道核心差异).

DeepAgents 检索 subagent 承担检索环节: 挂可插拔检索源工具
(网络搜索 / Context7 官方文档 / 输入材料文档 / 知识库预留), 按
分析阶段的建议查询自主编排, 输出结构化检索报告.

材料输入也执行搜索增强 (统一管道): 材料可能不完整或过时, 联网
补全知识面并交叉验证.
"""

import logging

from ...agents.search_agent import SearchReport, run_search_agent
from ...pipeline.sources import build_sources
from ..types import PipelineState

logger = logging.getLogger(__name__)


async def search_node(state: PipelineState) -> dict:
    """搜索增强节点: 构建源集合 → 检索 subagent → SearchReport."""
    material = state["material"]
    analysis = state["analysis"]
    sources = build_sources(material_text=material.text)
    if not sources:
        logger.warning("无可用检索源 (journey=%s), 搜索增强跳过", material.origin)
        report = SearchReport(topic=analysis.topic, queries=analysis.suggested_queries)
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
