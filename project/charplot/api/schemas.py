"""CharPlot FastAPI 侧请求/响应 Schemas (Issue 03).

对齐 DESIGN.md §4.2 契约: /ai/pipeline 与 /ai/tasks/{id} 载荷.
"""

from typing import Literal

from pydantic import BaseModel, model_validator

InputType = Literal["text", "file", "link"]
TaskStatus = Literal["running", "done", "error"]


class PipelineRequest(BaseModel):
    """启动知识管道 (DESIGN §4.2): text/link 必带 content, file 可空."""

    journey_id: int
    input_type: InputType
    content: str | None = None

    @model_validator(mode="after")
    def _content_required(self):
        if self.input_type in ("text", "link") and not (self.content or "").strip():
            raise ValueError("text/link 输入必须提供 content")
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
