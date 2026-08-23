"""图谱契约校验与类型 (CONTRACT.md v1, Issue 07).

与 Django 侧 app/charplot/services.py::validate_graph 逻辑一致 (权威校验
在 Django 落库端点), 此处为 FastAPI 侧本地快速校验: LLM 解构输出立即
校验 → 失败重试带错误反馈; 避免非法图谱走到落库 400 才暴露.

契约 (v1): version/title/chapters; 章节 ≥1 且含 ≥1 知识点; 临时 id 全局
唯一; prerequisites 引用必须存在 (允许跨章节). 未知字段 (sources 等)
不拒绝, 遵循「只增不改」演进规则.
"""

import logging

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

CONTRACT_VERSION = 1


class KnowledgePoint(BaseModel):
    """知识点: 原子节点 + 前置依赖边 + 来源引用 (07 追加字段)."""

    id: str
    title: str
    summary: str = ""
    prerequisites: list[str] = Field(default_factory=list)
    # v1 追加字段 (CONTRACT.md §1 扩展位): 来源引用, 落库端不校验未知字段
    sources: list[str] = Field(default_factory=list)


class Chapter(BaseModel):
    """章节: 图谱第一层分组."""

    id: str
    title: str
    summary: str = ""
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)

    @field_validator("knowledge_points")
    @classmethod
    def _at_least_one_kp(cls, kps: list[KnowledgePoint]) -> list[KnowledgePoint]:
        if not kps:
            raise ValueError("章节至少需要 1 个知识点")
        return kps


class GraphContract(BaseModel):
    """契约图谱 (v1) 的 pydantic 形态, LLM 输出经 JSON 解析后校验."""

    version: int
    title: str
    chapters: list[Chapter] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def _version_supported(cls, v: int) -> int:
        if v != CONTRACT_VERSION:
            raise ValueError(f"不支持的图谱契约版本: {v!r}")
        return v

    @field_validator("chapters")
    @classmethod
    def _ids_unique(cls, chapters: list[Chapter]) -> list[Chapter]:
        kp_ids: list[str] = []
        for ch in chapters:
            kp_ids.extend(kp.id for kp in ch.knowledge_points)
        if len(kp_ids) != len(set(kp_ids)):
            raise ValueError("知识点临时 id 重复")
        return chapters


def _validate_prerequisites(graph: GraphContract) -> None:
    """prerequisites 引用检查: 引用必须是本 journey 内已定义 kp 临时 id."""
    kp_ids = {kp.id for ch in graph.chapters for kp in ch.knowledge_points}
    for ch in graph.chapters:
        for kp in ch.knowledge_points:
            for prereq in kp.prerequisites:
                if prereq not in kp_ids:
                    raise ValueError(
                        f"知识点 {kp.id!r} 引用了未知的前置知识点: {prereq!r}"
                    )


def validate_graph_dict(data: dict) -> GraphContract:
    """完整契约校验, 返回校验后的 GraphContract; 失败抛 ValueError.

    错误消息为中文, 直接反馈给 LLM 用于修正重试 (deconstruct 节点).
    """
    graph = GraphContract.model_validate(data)
    _validate_prerequisites(graph)
    return graph


def to_contract_dict(graph: GraphContract) -> dict:
    """GraphContract → 契约 dict (与 stub 输出同构, 供落库端点消费)."""
    return graph.model_dump(exclude_none=True)
