"""pydantic schema 引擎 (默认, SOTA): 签名 → 参数模型 → model_json_schema.

生产推荐路径: 签名参数 Annotated[T, Field(...)] 或唯一 BaseModel 形态,
动态聚合为参数模型 (Field 保留 description/pattern/gt 等约束), schema 与
executor 的运行时校验 (P0-3) 复用同一模型, 天然同源 (#10: schema 质量
直接决定模型填参正确率).

本模块含 pydantic 产物专属后处理 (仅 pydantic model_json_schema 需要):
- _field_spec: 注解 + 函数默认值 + docstring 描述 → create_model 字段规格
  (annotation, default) 二元组; 归并交给 pydantic 原生语义 (Annotated 可叠 /
  from_annotated_attribute), 不调用已弃用的 FieldInfo.merge_field_infos
- _inline_defs: $defs/$ref 递归展开为嵌套 dict (wire 不带引用, 兼容 DeepSeek)
- _finalize_parameters: 去 title (pydantic 字段名产物) / 补 additionalProperties
"""

from __future__ import annotations

import inspect
from typing import Annotated, Any, get_type_hints

from pydantic import BaseModel, Field, create_model
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from CharAgent.tool.schema.signature import (
    param_entries,
    parse_google_args_doc,
    split_annotated,
)
from CharAgent.tool.schema.types import Json, ToolSchemaInfo
from CharAgent.tool.utils.errors import ToolConfigError


def _field_spec(
    ann: Any,
    *,
    function_default: Any,
    doc_description: str | None,
    param_name: str,
    fn_name: str,
) -> tuple[Any, Any]:
    """注解 + 函数默认值 + docstring 描述 → create_model 字段规格 (annotation, default).

    支持两种等价的 Field 写法 (pydantic 类字段语义, 见 schema/__init__ 约定):
    - 注解位: x: Annotated[str, Field(description=..., gt=0)]   (推荐, 主路径)
    - 默认值位: x: str = Field(description=..., gt=0)            (归一化后同路径)
    两处同时写 Field 报错 (防歧义).

    归并全部交给 pydantic 原生语义, 不调用已弃用的 FieldInfo.merge_field_infos:
    - 约束/描述留在 annotation: 原 FieldInfo 缺描述且 docstring 有 → 外层叠
      Annotated[..., Field(description=...)], pydantic 扁平化时外层描述生效
      (实测 2.13: 双层 Annotated 约束合并、外层 description 覆盖内层)
    - default 由字段二元组第二元素表达 (create_model 内部
      from_annotated_attribute 完成归并): 函数默认 > Field(default) > 必填
    """
    _, meta = split_annotated(ann)
    field_info = next((m for m in meta if isinstance(m, FieldInfo)), None)

    # 形态归一: 函数默认值是 FieldInfo (x: str = Field(...) 写法) → FieldInfo
    # 提升进注解 (字段配置), 真正默认值取 FieldInfo.default (类字段语义)
    if isinstance(function_default, FieldInfo):
        if field_info is not None:
            raise ToolConfigError(
                f"工具 {fn_name} 参数 {param_name}: 注解 Annotated 与默认值两处"
                f"都写了 Field, 请只保留一处 (两种写法等价, 见 build 函数注释)"
            )
        ann = Annotated[ann, function_default]
        field_info = function_default
        inner_default = function_default.default
        function_default = (
            inner_default
            if inner_default is not PydanticUndefined
            else inspect.Parameter.empty
        )

    field_default = field_info.default if field_info is not None else PydanticUndefined

    # 默认值以函数签名为准: 与 Field(default) 冲突时报错, 防静默歧义
    has_function_default = function_default is not inspect.Parameter.empty
    if (
        has_function_default
        and field_default is not PydanticUndefined
        and field_default != function_default
    ):
        raise ToolConfigError(
            f"工具 {fn_name} 参数 {param_name}: 函数默认值与 Field(default)"
            f"不一致 ({function_default!r} vs {field_default!r}), 请只保留一处"
        )
    if has_function_default:
        default: Any = function_default
    elif field_default is not PydanticUndefined:
        default = field_default
    else:
        default = ...  # Ellipsis → create_model 必填字段

    # 描述缺失时注入 docstring Args 描述 (仅作者没写 description 时, 不覆盖)
    if doc_description and (field_info is None or field_info.description is None):
        annotation: Any = Annotated[ann, Field(description=doc_description)]
    else:
        annotation = ann
    return annotation, default


def _inline_defs(schema: Json, defs: dict[str, Any]) -> Json:
    """递归展开 $defs/$ref 为嵌套 dict (wire 不带引用, 兼容 DeepSeek).

    自引用模型 (ref 环) 保留原 $ref 不展开 (演示范围无自引用, 仅防死循环).
    """

    def resolve(node: Any, seen: set[str]) -> Any:
        if isinstance(node, list):
            return [resolve(item, seen) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if ref is not None:
            name = ref.rsplit("/", 1)[-1]
            if name not in defs or name in seen:
                return node  # 环: 保留引用
            target = resolve(defs[name], seen | {name})
            target.pop("title", None)
            # $ref 旁挂的字段级键 (description/examples 等) 需并回展开结果,
            # 且优先于类型内建值 (字段描述比枚举/模型 docstring 权威)
            sibling = {key: value for key, value in node.items() if key != "$ref"}
            if sibling:
                target = {**target, **sibling}
            return target
        return {key: resolve(value, seen) for key, value in node.items()}

    schema.pop("$defs", None)
    return resolve(schema, set())


def _finalize_parameters(schema: Json) -> Json:
    """
    规范化参数 schema: 全层去 title (pydantic 字段名产物, wire 无意义),
    具名 object 补 additionalProperties False (与 TOOL_SCHEMA 样本一致).
    """

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        node.pop("title", None)
        if node.get("type") == "object" and "properties" in node:
            node.setdefault("additionalProperties", False)
        return {key: walk(value) for key, value in node.items()}

    schema.pop("title", None)
    return walk(schema)


def _pydantic_parameters_from_model(model: type[BaseModel]) -> Json:
    """BaseModel → 参数 schema: 取 model_json_schema + 展开 $defs + 规范化."""
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})
    return _finalize_parameters(_inline_defs(raw, defs))


def build_pydantic_schema(fn: Any) -> ToolSchemaInfo:
    """pydantic 引擎: 函数签名 → 参数 schema + 校验模型 (#10).

    参数形态:
    - 唯一 BaseModel 参数 (Pydantic): 参数对象即该模型 (schema/校验同源)
    - 常规多参数 / Annotated[..., Field(...)]: 动态聚合为参数模型
      (Field 保留 description/pattern/gt 等约束; 默认值以函数签名为准)
    """
    fn_name = getattr(fn, "__name__", repr(fn))
    params = param_entries(fn, engine="pydantic")
    doc_args = parse_google_args_doc(fn)
    hints = get_type_hints(fn, include_extras=True)

    # 唯一 BaseModel 参数形态 (Pydantic): 参数对象即该模型
    if len(params) == 1:
        ann = hints.get(params[0].name, params[0].annotation)
        base, _ = split_annotated(ann)
        if isinstance(base, type) and issubclass(base, BaseModel):
            return ToolSchemaInfo(
                _pydantic_parameters_from_model(base),
                base,
                params[0].name,
            )

    fields: dict[str, Any] = {}
    for param in params:
        ann = hints.get(param.name, param.annotation)
        annotation, default = _field_spec(
            ann,
            function_default=param.default,
            doc_description=doc_args.get(param.name),
            param_name=param.name,
            fn_name=fn_name,
        )
        fields[param.name] = (annotation, default)

    model = create_model(f"{fn_name}Params", **fields)
    return ToolSchemaInfo(_pydantic_parameters_from_model(model), model, None)
