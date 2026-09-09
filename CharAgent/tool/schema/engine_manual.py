"""manual schema 引擎 (教学对照, @tool(schema="manual")): 手写 typing → JSON schema.

零 pydantic 依赖: inspect + typing 递归映射, 证明「注解 → JSON schema」原理
(对比学习点); 同一纯注解 + Google docstring 签名, 本引擎与 pydantic 引擎
产出等值 schema, 由契约测试约束 (test_tool_schema 引擎等值契约).

支持子集: str/int/float/bool / list[T] / dict[str, V] / 单类型|None (anyOf
nullable) / Literal / Enum 子类; 可选性由函数默认值表达.

描述来源约定 (单一事实来源, 见 schema/__init__.py): 参数形状只从函数签名
读取 (manual 引擎不支持 Annotated Field); 参数 description 唯一取自 docstring
Google Style ``Args:`` 节 —— Annotated 里任意字符串 metadata 不解释为描述
(PEP 593 未定义该语义, 避免魔法解读).

无自动参数校验模型 (ToolSchemaInfo.parameter_model=None): 字段类型/值域校验
由工具作者函数内 raise ToolActionableError 承担, 执行层仅形状检查兜底
缺必填 / 未知参数 (executor._manual_shape_check). 对照实现见
tools_demo.query_order_status_manual.
"""

from __future__ import annotations

import inspect
from enum import Enum
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from CharAgent.tool.schema.signature import (
    param_entries,
    parse_google_args_doc,
    split_annotated,
)
from CharAgent.tool.schema.types import Json, ToolSchemaInfo
from CharAgent.tool.utils.errors import ToolConfigError

# manual 引擎 JSON type 名称映射 (bool 需在 int 之前特判, 见 _annotation_to_schema)
_JSON_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    dict: "object",
}


def _annotation_to_schema(ann: Any) -> Json:
    """手写递归映射: 单个类型注解 → JSON schema (manual 引擎核心, 教学对照)."""
    origin = get_origin(ann)
    args = get_args(ann)

    # 联合类型 (X | None / Union[X, None]): 仅支持单类型 + None
    if origin is UnionType or origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) != 1:
            raise ToolConfigError(
                f"manual 引擎不支持多类型 Union {ann}, 只支持单类型 | None"
            )
        return {"anyOf": [_annotation_to_schema(non_none[0]), {"type": "null"}]}

    if origin is Literal:
        values = list(args)
        kind: str | None = None
        for value in values:
            if isinstance(value, bool):
                value_kind = "boolean"
            elif isinstance(value, str):
                value_kind = "string"
            elif isinstance(value, int):
                value_kind = "integer"
            elif isinstance(value, float):
                value_kind = "number"
            else:
                raise ToolConfigError(
                    f"manual 引擎 Literal 值仅支持标量, 实际 {value!r}"
                )
            if kind is not None and kind != value_kind:
                raise ToolConfigError(f"manual 引擎不支持混型 Literal {ann}")
            kind = value_kind
        assert kind is not None
        return {"type": kind, "enum": list(values)}

    if origin is list:
        item = args[0] if args else Any
        return {"type": "array", "items": _annotation_to_schema(item)}

    if origin is dict:
        key, value = args if len(args) == 2 else (str, Any)
        if key is not str:
            raise ToolConfigError(f"manual 引擎 dict 键仅支持 str, 实际 {key}")
        return {"type": "object", "additionalProperties": _annotation_to_schema(value)}

    if isinstance(ann, type):
        if ann is type(None):
            return {"type": "null"}
        if issubclass(ann, bool):
            return {"type": "boolean"}
        if ann in _JSON_TYPE_MAP:
            return {"type": _JSON_TYPE_MAP[ann]}
        if issubclass(ann, Enum):
            values = [member.value for member in ann]
            if not all(isinstance(v, str | int) for v in values):
                raise ToolConfigError(
                    f"manual 引擎枚举值仅支持 str/int, 实际 {ann.__name__}"
                )
            schema: Json = {"enum": values}
            if values:
                if all(isinstance(v, str) for v in values):
                    schema["type"] = "string"
                else:
                    schema["type"] = "integer"
            return schema
        if issubclass(ann, BaseModel):
            raise ToolConfigError(
                f"manual 引擎不支持 pydantic BaseModel 参数 {ann.__name__}: "
                f"嵌套结构请用 list[T] / dict[str, T] 表达 (教学对照)"
            )
    raise ToolConfigError(
        f"manual 引擎暂不支持类型 {ann!r}, 支持: str/int/float/bool/list[T]/"
        f"dict[str, V]/单类型|None/Literal/Enum"
    )


def build_manual_schema(fn: Any) -> ToolSchemaInfo:
    """manual 引擎: 函数签名 → 参数 schema (零 pydantic 依赖, 教学对照).

    参数形状只从签名读取; 参数 description 唯一取自 docstring Google Style
    ``Args:`` 节 (Annotated 里的字符串 metadata 不解释为描述, 见模块注释).
    可选性由函数默认值表达. 无自动参数校验模型 —— 字段校验由工具函数内
    raise ToolActionableError 承担 (见 tools_demo.query_order_status_manual).
    """
    params = param_entries(fn, engine="manual")
    doc_args = parse_google_args_doc(fn)
    hints = get_type_hints(fn, include_extras=True)

    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in params:
        ann = hints.get(param.name, param.annotation)
        base, _ = split_annotated(ann)
        schema = _annotation_to_schema(base)
        description = doc_args.get(param.name)
        if description:
            schema["description"] = description
        if param.default is inspect.Parameter.empty:
            required.append(param.name)
        else:
            # 默认值进 schema (与 pydantic 引擎一致); 枚举默认值序列化为成员值
            default = (
                param.default.value
                if isinstance(param.default, Enum)
                else param.default
            )
            schema["default"] = default
        properties[param.name] = schema

    parameters: Json = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required
    parameters["additionalProperties"] = False
    return ToolSchemaInfo(parameters, None, None)
