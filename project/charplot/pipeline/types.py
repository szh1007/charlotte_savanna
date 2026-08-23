"""管道状态与数据类型 (Issue 07).

LangGraph StateGraph 的全局状态定义 + 各阶段产出的数据类型.
"""

from dataclasses import dataclass
from typing import TypedDict

from pydantic import BaseModel, Field


@dataclass
class PipelineInput:
    """管道入参 (保持 Issue 03 签名, tasks.py 复用).

    content: text/link 输入为原文/URL; file 输入为空 (文件内容经
    Django 内部端点获取, 见 stages/parse.py).
    """

    journey_id: int
    input_type: str  # text | file | link
    content: str = ""


@dataclass
class ParsedMaterial:
    """阶段 1 产出: 归一化解析后的材料 (统一管道入口产物)."""

    title: str
    text: str  # 纯文本 (解析/归一化后, 供分析与解构)
    origin: str  # text | file | link
    filename: str = ""


class ContentAnalysis(BaseModel):
    """阶段 2 产出: 主内容分析 (LLM 结构化输出)."""

    topic: str
    summary: str = ""
    concepts: list[str] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)


class PipelineState(TypedDict):
    """LangGraph 全局状态: 输入 + 各阶段产出 (节点逐步填充).

    search_report 为 agents.search_agent.SearchReport (pydantic 模型),
    此处不引以避免依赖方向混乱; 解构节点按属性访问.
    """

    inp: PipelineInput
    material: ParsedMaterial
    analysis: ContentAnalysis
    search_report: object
    graph: dict
