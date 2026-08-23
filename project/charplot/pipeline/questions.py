"""闯关题目生成 (Issue 08): LLM 生成 + pydantic 校验 + 重试带错误反馈.

输入为 Django 内部端点提供的出题素材 (build_level_generation_input):
知识点标题/概述/前置依赖 + 章节信息 + 目标题数/难度. LLM 只生成新题
(new_count 道), 间隔复习题由 Django 透传混入 (tasks.py 拼接), 本模块
不感知复习逻辑.

LLM 输出经 JSON 提取 + 结构校验, 失败按配置重试 (带错误反馈), 超限抛
RuntimeError → 任务 error (前端可重试). 与 stages/analyze.py 同款样板.
"""

import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..api import config
from ..prompt.questions import QUESTION_SYSTEM_PROMPT, QUESTION_USER_TEMPLATE
from . import llm
from .json_utils import extract_json

logger = logging.getLogger(__name__)

# 单次出题生成的素材上限 (知识点概述可能很长, 截断保护上下文)
_KP_INFO_LIMIT = 600
_QUESTION_TYPES = ("choice", "judge", "fill")


class QuestionDraft(BaseModel):
    """单题 (与 Django 侧 validate_question_dict 同规则)."""

    question_type: Literal["choice", "judge", "fill"]
    content: str
    options: list[str] = Field(default_factory=list)
    answer: list = Field(default_factory=list)
    explanation: str
    sources: list[str] = Field(default_factory=list)

    @field_validator("content", "explanation")
    @classmethod
    def _non_blank(cls, value):
        if not value.strip():
            raise ValueError("题干/讲解不能为空")
        return value.strip()

    @field_validator("question_type")
    @classmethod
    def _known_type(cls, value):
        if value not in _QUESTION_TYPES:
            raise ValueError(f"未知题型: {value}")
        return value

    @field_validator("options")
    @classmethod
    def _choice_options(cls, value, info):
        if info.data.get("question_type") == "choice":
            if len(value) < 3:
                raise ValueError("选择题至少 3 个选项")
            if len(set(value)) != len(value):
                raise ValueError("选择题选项不能重复")
        return value

    @field_validator("answer")
    @classmethod
    def _answer_shape(cls, value, info):
        qtype = info.data.get("question_type")
        if qtype == "choice":
            if (
                len(value) != 1
                or not isinstance(value[0], int)
                or isinstance(value[0], bool)
            ):
                raise ValueError("选择题答案必须为单个选项下标")
            options = info.data.get("options") or []
            if not 0 <= value[0] < len(options):
                raise ValueError("选择题答案下标越界")
        elif qtype == "judge":
            if len(value) != 1 or str(value[0]) not in ("true", "false"):
                raise ValueError("判断题答案必须为 true/false")
        elif qtype == "fill":
            if not value or not all(isinstance(a, str) and a.strip() for a in value):
                raise ValueError("填空题至少 1 个可接受答案")
        return value

    @field_validator("sources")
    @classmethod
    def _sources_list(cls, value):
        if not isinstance(value, list):
            raise ValueError("来源引用必须是数组")
        return value


class QuestionsDraft(BaseModel):
    questions: list[QuestionDraft] = Field(min_length=1)


def validate_questions_dict(data: dict, expected_count: int) -> list[dict]:
    """JSON → 校验 → dict 列表 (落库端同构); 失败抛 ValueError (触发重试)."""
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError("输出必须为 {questions: [...]} 结构")
    try:
        draft = QuestionsDraft.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"题目结构校验失败: {exc}") from exc
    if len(draft.questions) != expected_count:
        raise ValueError(
            f"题目数量不符: 期望 {expected_count}, 实际 {len(draft.questions)}"
        )
    return [q.model_dump() for q in draft.questions]


def _format_kp_infos(kp_infos: list[dict]) -> str:
    """知识点素材格式化 (标题/概述/前置依赖, 截断保护上下文)."""
    lines = []
    for kp in kp_infos:
        title = kp.get("title", "")
        summary = (kp.get("summary") or "")[:_KP_INFO_LIMIT]
        prereqs = "、".join(kp.get("prereq_titles") or []) or "无"
        lines.append(f"- 知识点: {title}\n  概述: {summary}\n  前置依赖: {prereqs}")
    return "\n".join(lines) or "(无知识点素材)"


async def generate_level_questions(input_data: dict) -> list[dict]:
    """LLM 生成 new_count 道新题 (重试 LLM_RETRIES 次, 带错误反馈).

    返回新题 dict 列表 (不含复习题); 校验失败重试, 超限抛 RuntimeError
    (任务转 error, 前端可重试).
    """
    model = llm.get_chat_model()
    new_count = int(input_data.get("new_count", 0))
    if new_count <= 0:
        return []  # 全部为复习题的边缘情况, 无新题可生成
    difficulty = input_data.get("difficulty", "medium")
    kp_infos = _format_kp_infos(input_data.get("kp_infos") or [])
    last_error = ""
    for attempt in range(config.LLM_RETRIES + 1):
        feedback = ""
        if attempt:
            feedback = (
                "\n\n上次输出校验失败: "
                + last_error
                + "\n请重新输出合法 JSON (仅 JSON, 无其他内容)."
            )
            logger.warning(
                "出题重试 %d/%d: %s", attempt, config.LLM_RETRIES, last_error
            )
        user_prompt = (
            QUESTION_USER_TEMPLATE.format(
                question_count=new_count,
                difficulty=difficulty,
                kp_infos=kp_infos,
                last_error=last_error,
            )
            + feedback
        )
        resp = await model.ainvoke(
            [
                SystemMessage(content=QUESTION_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )
        try:
            return validate_questions_dict(extract_json(resp.content), new_count)
        except ValueError as exc:
            last_error = str(exc)
    raise RuntimeError(f"题目生成失败 (LLM 输出无法解析): {last_error}")
