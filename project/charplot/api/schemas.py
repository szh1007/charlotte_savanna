"""CharPlot FastAPI 侧请求/响应 Schemas (Issue 03).

对齐 DESIGN.md §4.2 契约: /ai/pipeline 与 /ai/tasks/{id} 载荷.
"""

from typing import Literal

from pydantic import BaseModel, model_validator

InputType = Literal["text", "file", "link", "kb"]
TaskStatus = Literal["running", "done", "error"]


class PipelineRequest(BaseModel):
    """启动知识管道 (DESIGN §4.2): text/link 必带 content, file 可空, kb 必带 kb_id."""

    journey_id: int
    input_type: InputType
    content: str | None = None
    kb_id: int | None = None

    @model_validator(mode="after")
    def _content_required(self):
        if self.input_type in ("text", "link") and not (self.content or "").strip():
            raise ValueError("text/link 输入必须提供 content")
        if self.input_type == "kb":
            if self.kb_id is None:
                raise ValueError("知识库输入必须提供 kb_id")
        elif self.kb_id is not None:
            raise ValueError("仅知识库输入可携带 kb_id")
        return self


class PipelineResponse(BaseModel):
    task_id: str


class LevelGenerateRequest(BaseModel):
    """渐进出题 (DESIGN §4.2): 按关卡序号触发生成任务."""

    journey_id: int
    level_seq: int


class LevelGenerateResponse(BaseModel):
    """出题任务创建结果 (幂等/抢占结果由 SSE 事件与关卡状态反映)."""

    task_id: str


class KbIndexRequest(BaseModel):
    """知识库索引任务 (DESIGN §4.2 POST /ai/kb/index): 全量重建."""

    kb_id: int


class KbIndexResponse(BaseModel):
    """索引任务创建结果 (幂等/拒绝理由由 SSE 事件与知识库状态反映)."""

    task_id: str


class KbSearchRequest(BaseModel):
    """混合检索 (DESIGN §4.2 POST /ai/kb/search, QA.md Q7): 片段检索.

    top_k 可选 (默认精排后 Top 5); kb 未就绪/软删文档自动过滤.
    """

    kb_id: int
    query: str
    top_k: int | None = None


class KbSearchChunk(BaseModel):
    """单条检索片段 (带来源 metadata, 供生成阶段引用与来源展示)."""

    doc_id: int
    title: str
    filename: str
    chunk_index: int
    content: str
    score: float = 0.0


class KbSearchResponse(BaseModel):
    """检索结果 (片段列表, 不是答案 - 生成由调用方 LLM 完成)."""

    chunks: list[KbSearchChunk]


class TaskStatusOut(BaseModel):
    """任务状态 (DESIGN §4.2): {status, stage, progress, error_message?}.

    task_type 标记任务类型 (pipeline / level-generation), 前端状态展示用.
    """

    task_id: str
    status: TaskStatus
    stage: str
    progress: int
    error_message: str | None = None
    task_type: str = "pipeline"


class PipelineEvent(BaseModel):
    """SSE 事件载荷 (DESIGN §4.2): {task_id, stage, progress, message}."""

    task_id: str
    stage: str
    progress: int
    message: str


class StatusSummaryRequest(BaseModel):
    """LLM 状态总结请求 (Issue 13, DESIGN.md §4.2): {user_id} → {summary}.

    user_id 定位聚合数据的用户 (FastAPI 经内部端点取, 单机个人项目
    前端直调, 与现有 /ai/* 接口同认证模型).
    """

    user_id: int


class StatusSummaryResponse(BaseModel):
    """LLM 状态总结响应: markdown 文字报告 (强项 / 弱项 / 学习建议)."""

    summary: str
