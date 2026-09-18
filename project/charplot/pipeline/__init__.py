"""CharPlot 知识管道 (真实管道).

统一管道: 归一化解析 → 主内容分析 → 联网搜索增强 → 知识解构.
LangGraph StateGraph 编排 (pipeline/graph.py), 检索环节由 DeepAgents
subagent 承担 (agents/search_agent.py), 检索源可插拔 (pipeline/sources/).

run_pipeline 签名保持不变 (无缝替换 stub), 阶段
事件/进度契约不变.
"""

from .graph import (
    STAGE_MESSAGES,
    STAGE_PROGRESS,
    STAGES,
    run_pipeline,
)
from .types import PipelineInput

__all__ = [
    "STAGES",
    "STAGE_MESSAGES",
    "STAGE_PROGRESS",
    "PipelineInput",
    "run_pipeline",
]
