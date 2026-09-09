"""工具执行包装: JSON 解析 → 参数校验 → 调用 → 结果 / 错误规范化 (主流程).

供 P0-3 agent loop 消费: loop 拿到 ModelToolCall.arguments (JSON 字符串),
调 execute_tool 得 ToolExecution (对应术语表 ToolResult, CONTEXT.md:31 ——
回填模型的文本; 另携带 error/exception/duration_ms 供事件与日志), 依
ok/content/error 回填 tool_result 消息.

职责边界 (拆分后):
- 面向模型的文案生成 (ValidationError 映射 / 内部错误文案) 在 utils/messages.py
- 异常语义 (ToolActionableError / ToolConfigError) 在 utils/errors.py
- 本模块只留执行链: 解析 → 校验 (pydantic 引擎 model_validate / manual 引擎
  形状检查兜底) → 调用 (sync 进线程池) → 结果文本化 → ToolExecution
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from CharAgent.tool.decorator import Tool
from CharAgent.tool.utils.errors import ToolActionableError
from CharAgent.tool.utils.messages import INTERNAL_ERROR_TEXT, validation_error_text


@dataclass(slots=True)
class ToolExecution:
    """一次工具执行的结果 (loop 由此产出 ToolResult 消息与事件).

    attributes:
        tool_name: 执行的工具名.
        ok: True 执行成功 (content 有值); False 失败 (error 有值).
        content: 成功时回填模型的文本 (dict/list 自动 JSON 序列化).
        error: 失败时的可操作错误文本 (面向模型, #2).
        exception: 失败根因 (ToolActionableError 或意外异常; 仅日志/审计用,
            不回填模型).
        duration_ms: 执行耗时 (含校验), 供事件/metrics.
    """

    tool_name: str
    ok: bool
    content: str = ""
    error: str | None = None
    exception: BaseException | None = None
    duration_ms: float = 0.0


# ---------------------------------------------------------------------------
# 结果文本化
# ---------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    """json.dumps 兜底序列化: BaseModel/枚举/时间等对象."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _stringify(value: Any) -> str:
    """工具返回值 → 回填模型的文本 (dict/list 序列化为 JSON)."""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    return str(value)


# ---------------------------------------------------------------------------
# 参数解析 / 校验
# ---------------------------------------------------------------------------


def _parse_arguments(tool_name: str, arguments: str) -> dict[str, Any]:
    """arguments (模型给的 JSON 字符串) → 参数 dict; 失败给可操作错误."""
    try:
        data = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ToolActionableError(
            f"工具 {tool_name} 的 arguments 不是合法 JSON ({exc}). "
            f'期望 JSON 对象, 形如 {{"参数名": 值}}'
        ) from exc
    if not isinstance(data, dict):
        raise ToolActionableError(
            f"工具 {tool_name} 的 arguments 应为 JSON 对象 (参数名→值), "
            f"实际为 {type(data).__name__}: {data!r}"
        )
    return data


def _manual_shape_check(
    tool: Tool, kwargs: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """manual 引擎 (无 pydantic 校验模型) 的最小形状检查.

    兜底缺必填 / 未知参数给可操作提示 (与 pydantic 引擎同风格文案); 类型与
    值域约束由工具作者函数内 raise ToolActionableError 承担 (#2 教学点).
    """
    signature = inspect.signature(tool.fn)
    allowed = {
        name
        for name, param in signature.parameters.items()
        if param.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }
    unknown = [key for key in kwargs if key not in allowed]
    if unknown:
        return {}, (
            f"工具 {tool.name} 收到未知参数 {unknown}; "
            f"该工具可用参数: {', '.join(sorted(allowed))}"
        )
    missing = [
        name
        for name, param in signature.parameters.items()
        if param.default is inspect.Parameter.empty and name not in kwargs
    ]
    if missing:
        return {}, f"缺少必填参数 {', '.join(missing)}"
    return kwargs, None


def _validate_arguments(
    tool: Tool, kwargs: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """参数校验; 返回 (调用参数, error).

    - pydantic 引擎: 未知参数先拦截 (schema additionalProperties=False 的运行时
      一侧) 给可用参数列表; 校验失败返回可操作错误文本 (逐字段映射, 见
      utils/messages.py); 通过后 kwargs 形态逐字段取实例属性 (嵌套 pydantic
      模型保持实例, model_dump 会退化成 dict), single-model 形态返回模型实例
    - manual 引擎: 仅形状检查 (缺必填/未知参数), 类型约束由作者函数内承担
    """
    model = tool.parameter_model
    if model is None:
        return _manual_shape_check(tool, kwargs)
    unknown = [key for key in kwargs if key not in model.model_fields]
    if unknown:
        allowed = ", ".join(sorted(model.model_fields))
        return {}, (
            f"工具 {tool.name} 收到未知参数 {unknown}; 该工具可用参数: {allowed}"
        )
    try:
        instance = model.model_validate(kwargs)
    except ValidationError as exc:
        return {}, validation_error_text(exc)
    if tool.single_param_name is not None:
        return {tool.single_param_name: instance}, None
    return {name: getattr(instance, name) for name in model.model_fields}, None


# ---------------------------------------------------------------------------
# 调用
# ---------------------------------------------------------------------------


async def _invoke(tool: Tool, call_kwargs: dict[str, Any]) -> Any:
    """调用工具函数: 同步函数进线程池, 协程函数直接 await."""
    fn = tool.fn
    if inspect.iscoroutinefunction(fn):
        return await fn(**call_kwargs)
    return await asyncio.to_thread(fn, **call_kwargs)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


async def execute_tool(tool: Tool, *, arguments: str) -> ToolExecution:
    """执行一次工具调用 (解析 → 校验 → 调用 → 规范化), 永不抛异常.

    Args:
        tool: 注册的 Tool 对象.
        arguments: 模型 ModelToolCall.arguments (JSON 字符串, 保真不预解析,
            畸形 JSON 在此给可操作错误, #10).

    Returns:
        ToolExecution: ok=True 时 content 为回填文本; ok=False 时 error 为
        可操作错误文本, exception 保留失败根因 (仅日志, 不回填模型).
        duration_ms 恒记录执行耗时.
    """
    started = time.perf_counter()
    execution = ToolExecution(tool_name=tool.name, ok=False)
    try:
        # 解析参数, 结果是否为合法 JSON 对象
        kwargs = _parse_arguments(tool.name, arguments)
    except ToolActionableError as exc:
        execution.error = str(exc)
    else:
        # 校验参数, 检查是否有未知多余参数 / 参数校验失败
        call_kwargs, error = _validate_arguments(tool, kwargs)
        if error is not None:
            execution.error = error
        else:
            try:
                # 执行工具
                result = await _invoke(tool, call_kwargs)
            except ToolActionableError as exc:
                # 可操作错误直接原文透传
                execution.error = str(exc)
                execution.exception = exc
            except Exception as exc:
                # 意外异常统一包装, 不外泄 traceback
                execution.error = INTERNAL_ERROR_TEXT
                execution.exception = exc
            else:
                execution.ok = True
                execution.content = _stringify(result)
    finally:
        execution.duration_ms = (time.perf_counter() - started) * 1000
    return execution
