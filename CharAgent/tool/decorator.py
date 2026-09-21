"""@tool 装饰器 + Tool 数据类: 把 Python 函数注册为可被模型调用的 Tool.

生产 SOTA 用法 (OpenAI function calling 官方实践)::

    @tool
    def query_order_status(
        order_no: Annotated[
            str,
            Field(
                description="14 位订单号",
                pattern=r"^\\d{14}$",
            ),
        ],
    ) -> str:
        \"\"\"查询订单状态. 用户报出订单号询问物流时使用.\"\"\"
        ...

装饰器返回 Tool 对象 (非包装函数), 由调用方显式收集为列表传给 agent loop,
无全局注册表 (与 model.generate 的 tools: list[ToolSpec] 参数对齐).

schema 引擎: 默认 pydantic (SOTA, 自动校验); @tool(schema="manual") 切换
手写 typing 对照引擎 (教学, 无自动校验, 见 schema.build_manual_schema).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from CharAgent.model.utils.types import ToolSpec
from CharAgent.tool.schema import (
    ToolSchemaInfo,
    build_manual_schema,
    build_pydantic_schema,
    first_paragraph,
)
from CharAgent.tool.utils.errors import ToolConfigError

# 工具名生产规范: 动词短语 snake_case, OpenAI 允许 [a-zA-Z0-9_-], 建议 1-64 字符
_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_ENGINES: dict[str, Callable[[Any], ToolSchemaInfo]] = {
    "pydantic": build_pydantic_schema,
    "manual": build_manual_schema,
}


@dataclass(slots=True)
class Tool:
    """注册后的工具对象: 元数据 + 原函数 + 参数 schema + 校验模型 + 注解.

    attributes:
        name: 模型调用名 (默认函数名, 动词短语 snake_case).
        description: 模型视角的工具说明 (docstring 首段, 何时用/不用 + 返回).
        parameters: 参数 JSON schema (wire, OpenAI function 格式).
        fn: 原函数 (同步或异步), executor 负责调用.
        parameter_model: pydantic 参数模型 (executor 校验 arguments); manual
            引擎为 None (作者函数内自行校验).
        single_param_name: 唯一 BaseModel 参数形态的参数名 (executor 以模型
            实例作为该位置实参), kwargs 形态为 None.
        annotations: 工具作者给这个工具打的标记 (键与值的含义都由**业务自己**
            定, 例如「这个工具会改数据」), 空字典表示没打. 框架**只透传不解释**
            —— 它一个键都不读, 只把工具对象原样交到插件手里 (hooks 的
            before_tool_execute 载荷带着本对象), 由插件去认. 与 RunContext.payload
            同一条纪律: 框架一旦认了某个键名, 换个业务就得改框架. 也**不进**
            to_spec() —— 模型的视角里没有这一层.
    """

    name: str
    description: str
    fn: Callable[..., Any]
    parameters: dict[str, Any]
    parameter_model: type[BaseModel] | None = None
    single_param_name: str | None = None
    annotations: Mapping[str, Any] = field(default_factory=dict)

    def to_spec(self) -> ToolSpec:
        """产出 /chat/completions tools 参数的 wire 结构 (OpenAI 兼容)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _make_tool(
    fn: Callable[..., Any],
    *,
    name: str | None,
    description: str | None,
    schema_engine: str,
    annotations: Mapping[str, Any] | None,
) -> Tool:
    """函数 → Tool: 生成 schema + 校验模型 (注册期配置错误抛 ToolConfigError)."""
    engine = _ENGINES.get(schema_engine)
    if engine is None:
        raise ToolConfigError(
            f"未知 schema 引擎 {schema_engine!r}, 可选: {sorted(_ENGINES)}"
        )
    tool_name = name if name is not None else fn.__name__
    if not _NAME_PATTERN.match(tool_name):
        raise ToolConfigError(
            f"工具名 {tool_name!r} 不符合规范: 仅限字母/数字/下划线/连字符, "
            f"1-64 字符, 建议动词短语 snake_case"
        )
    if annotations is not None and not isinstance(annotations, Mapping):
        # 框架不解释注解的内容, 但形状要是键值对 —— 传了个字符串进来多半是
        # 参数写错了 (写成位置参数 / 忘了大括号), 注册期就说清比运行期好查
        raise ToolConfigError(
            f"工具 {tool_name} 的 annotations 应为键值映射 (dict), 实际: "
            f"{type(annotations).__name__}"
        )
    tool_description = description or first_paragraph(fn) or tool_name
    info = engine(fn)
    return Tool(
        name=tool_name,
        description=tool_description,
        fn=fn,
        parameters=info.parameters,
        parameter_model=info.parameter_model,
        single_param_name=info.single_param_name,
        annotations=dict(annotations) if annotations else {},
    )


def tool(
    fn: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    description: str | None = None,
    schema: str = "pydantic",
    annotations: Mapping[str, Any] | None = None,
) -> Tool | Callable[[Callable[..., Any]], Tool]:
    """把函数注册为 Tool (裸 @tool 或 @tool(name=..., description=...) 均可).

    Args:
        fn: 被装饰的工具函数 (裸 @tool 时直接传函数).
        name: 模型调用名, 默认取函数名.
        description: 工具说明, 默认取 docstring 首段.
        schema: schema 引擎, "pydantic" (默认, SOTA) / "manual" (教学对照).
        annotations: 给这个工具打的标记 (键值含义由业务自己定, 框架不解释也不
            进模型可见的 schema; 见 Tool.annotations).

    Returns:
        裸 @tool 返回 Tool; @tool(...) 返回装饰器, 应用到函数后返回 Tool.

    Raises:
        ToolConfigError: 参数形态 / 类型注解 / 引擎名 / annotations 形状不合法.
    """
    if fn is not None:
        if not callable(fn):
            raise ToolConfigError(f"@tool 只能装饰函数, 实际收到 {type(fn).__name__}")
        return _make_tool(
            fn,
            name=name,
            description=description,
            schema_engine=schema,
            annotations=annotations,
        )

    def decorator(target: Callable[..., Any]) -> Tool:
        return _make_tool(
            target,
            name=name,
            description=description,
            schema_engine=schema,
            annotations=annotations,
        )

    return decorator
