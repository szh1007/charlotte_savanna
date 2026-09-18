"""tool schema 生成测试 (#70): pydantic + manual 双引擎.

- pydantic 引擎: Annotated[..., Field(...)] 的 description / pattern / gt 等约束
  进入 schema; 默认值参数不进 required; Literal/StrEnum 映射 enum; 嵌套
  BaseModel 的 $defs 展开无残留引用; 唯一 BaseModel 参数平铺形态
- manual 引擎: 纯注解递归映射 (str/int/float/bool/list/dict/Optional/Literal/
  Enum), docstring Google Args 节描述注入
- 引擎等值契约: 同一纯注解 + docstring 签名喂两引擎产出等值 schema
  (用户对比学习点: 证明手写映射与 pydantic 生成一致)

载体工具就地定义 (Seam 3); 类型需模块顶层可见 (get_type_hints 以函数
__globals__ 求值注解).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

import pytest
from helpers import TOOL_SCHEMA
from pydantic import BaseModel, Field

from CharAgent.tool import (
    ToolConfigError,
    build_manual_schema,
    build_pydantic_schema,
)
from CharAgent.tool.schema import parse_google_args_doc

# ---------------------------------------------------------------------------
# 载体函数 / 类型 (模块顶层, 注解可被 get_type_hints 求值)
# ---------------------------------------------------------------------------


class Color(StrEnum):
    """无 docstring (避免 pydantic 注入枚举 docstring 覆盖字段描述)."""

    RED = "red"
    BLUE = "blue"


class NestedInput(BaseModel):
    """嵌套输入模型: 标量 + 枚举字段 (内嵌模型用)."""

    label: str = Field(description="标签")
    amount: float = Field(description="金额, 大于 0", gt=0, examples=[9.9])
    color: Color = Field(description="颜色")


class FilterModel(BaseModel):
    """唯一 BaseModel 参数形态载体: express 必填 + days 默认."""

    express: str = Field(description="快递公司")
    days: int = Field(description="预计天数", ge=1, default=3)


def _sota_fn(
    name: Annotated[
        str,
        Field(description="姓名", pattern=r"^[一-龥]{2,4}$"),
    ],
    age: Annotated[
        int,
        Field(description="年龄, 18-99", ge=18, le=99, examples=[25]),
    ],
) -> str:
    """SOTA 签名载体 (每参数 Annotated + Field 约束)."""


def _defaults_fn(
    mode: Annotated[
        Literal["iso", "date"],
        Field(description="返回格式"),
    ] = "iso",
    pretty: Annotated[bool, Field(description="是否美化输出")] = False,
    color: Annotated[Color, Field(description="颜色")] = Color.RED,
) -> str:
    """默认值参数载体 (默认值放函数签名, 应全部不进 required)."""


def _nested_fn(
    items: Annotated[
        list[NestedInput],
        Field(description="输入列表"),
    ],
    tag: Annotated[str, Field(description="统一标签")],
) -> str:
    """嵌套 BaseModel 载体 ($defs 展开断言用)."""


def _single_model_fn(params: FilterModel) -> str:
    """唯一 BaseModel 参数载体 (平铺形态)."""


def _contract_fn(
    query: str,
    top_k: int = 5,
    fuzzy: bool = False,
    tags: list[str] | None = None,
) -> str:
    """引擎等值契约载体: 纯注解 + Google docstring, 无 pydantic Field.

    Args:
        query: 搜索关键词, 必填.
        top_k: 返回条数上限, 默认 5.
        fuzzy: 是否模糊匹配, 默认 False.
        tags: 标签过滤列表, None 表示不过滤.
    """


def _no_annotation_fn(x) -> str:
    """缺注解载体 (schema 引擎应报配置错误)."""


def _vararg_fn(*args: str) -> str:
    """*args 载体 (schema 引擎应报配置错误)."""


def _wire_sim_fn(order_no: str) -> str:
    """与 helpers.TOOL_SCHEMA 同构载体 (pydantic 引擎输出对比手工样本).

    Args:
        order_no: 14 位订单号
    """


# ---------------------------------------------------------------------------
# docstring 解析
# ---------------------------------------------------------------------------


def test_parse_google_args_doc_extracts_continuation_lines():
    """Args 节解析: 参数名 → 描述, 多行续行合并, 章节边界截断."""
    doc = """第一段.

    Args:
        query: 搜索关键词.
        top_k: 返回条数上限,
            默认 5.
        fuzzy: 是否模糊匹配.

    Returns:
        结果文本.
    """
    parsed = parse_google_args_doc(_docstring_fn(doc))
    assert parsed["query"] == "搜索关键词."
    assert parsed["top_k"] == "返回条数上限, 默认 5."
    assert parsed["fuzzy"] == "是否模糊匹配."
    assert "Returns" not in parsed


def test_parse_google_args_doc_section_header_without_blank_line():
    """章节头 (Returns:) 无空行分隔时也不并入上一条参数描述."""
    doc = """摘要.

    Args:
        query: 搜索关键词.
    Returns:
        结果文本.
    """
    parsed = parse_google_args_doc(_docstring_fn(doc))
    assert parsed == {"query": "搜索关键词."}


def _docstring_fn(doc: str):
    def carrier() -> None:
        pass

    carrier.__doc__ = doc
    return carrier


# ---------------------------------------------------------------------------
# pydantic 引擎 (默认 / SOTA)
# ---------------------------------------------------------------------------


def test_pydantic_field_constraints_flow_into_schema():
    """Annotated Field 的 description/pattern/ge/le/examples 全部进 schema."""
    parameters = build_pydantic_schema(_sota_fn).parameters
    name = parameters["properties"]["name"]
    assert name["description"] == "姓名"
    assert name["pattern"] == r"^[一-龥]{2,4}$"
    assert name["type"] == "string"
    age = parameters["properties"]["age"]
    assert age["minimum"] == 18 and age["maximum"] == 99
    assert age["examples"] == [25]
    assert parameters["required"] == ["name", "age"]


def test_pydantic_default_params_are_optional_with_default():
    """默认值放函数签名 → 参数不进 required, default 值进 schema."""
    parameters = build_pydantic_schema(_defaults_fn).parameters
    assert "required" not in parameters
    assert parameters["properties"]["mode"]["default"] == "iso"
    assert parameters["properties"]["pretty"]["default"] is False
    assert parameters["properties"]["mode"]["enum"] == ["iso", "date"]
    assert parameters["properties"]["color"]["enum"] == ["red", "blue"]


def test_pydantic_nested_model_defs_inlined():
    """嵌套 BaseModel 展开为内嵌 dict: 无 $defs/$ref/title 残留 (wire 兼容)."""
    parameters = build_pydantic_schema(_nested_fn).parameters
    raw = parameters
    assert "$defs" not in raw and "$ref" not in repr(raw)
    assert "title" not in repr(raw)
    item_schema = raw["properties"]["items"]["items"]
    assert item_schema["type"] == "object"
    assert item_schema["properties"]["label"]["description"] == "标签"
    assert item_schema["properties"]["amount"]["exclusiveMinimum"] == 0
    assert item_schema["properties"]["amount"]["examples"] == [9.9]
    assert item_schema["properties"]["color"]["enum"] == ["red", "blue"]
    assert item_schema["additionalProperties"] is False


def test_pydantic_single_base_model_param_flattens():
    """唯一 BaseModel 参数: 模型字段平铺为顶层参数对象."""
    info = build_pydantic_schema(_single_model_fn)
    assert info.single_param_name == "params"
    parameters = info.parameters
    assert set(parameters["properties"]) == {"express", "days"}
    assert parameters["required"] == ["express"]
    assert parameters["properties"]["days"]["default"] == 3
    assert parameters["additionalProperties"] is False


def test_pydantic_default_conflict_raises():
    """函数默认值与 Field(default) 冲突 → 配置错误 (防静默歧义)."""

    def conflicted(
        x: Annotated[int, Field(description="数值", default=1)] = 2,
    ) -> str:
        """冲突载体."""

    with pytest.raises(ToolConfigError, match="只保留一处"):
        build_pydantic_schema(conflicted)


def test_pydantic_default_position_field_equivalent_with_annotated():
    """两种等价 Field 写法产出相同 schema: 注解位 vs 默认值位 (类字段语义)."""

    def annotated_way(
        order_no: Annotated[
            str,
            Field(description="14 位订单号", pattern=r"^\d{14}$"),
        ],
    ) -> str:
        """注解位载体."""

    def default_way(
        order_no: str = Field(description="14 位订单号", pattern=r"^\d{14}$"),
    ) -> str:
        """默认值位载体."""

    info_annotated = build_pydantic_schema(annotated_way)
    info_default = build_pydantic_schema(default_way)
    assert info_annotated.parameters == info_default.parameters
    assert info_annotated.parameter_model is not None
    assert info_default.parameter_model is not None


def test_pydantic_default_position_field_takes_default_from_field():
    """默认值位 Field: 真实默认值取 Field(default), 进 schema 且不进 required."""

    def with_default(
        days: int = Field(description="天数", ge=1, default=3),
    ) -> str:
        """默认值载体."""

    parameters = build_pydantic_schema(with_default).parameters
    assert parameters["properties"]["days"] == {
        "type": "integer",
        "description": "天数",
        "minimum": 1,
        "default": 3,
    }
    assert "required" not in parameters


def test_pydantic_dual_field_positions_rejected():
    """注解 Annotated 与默认值两处都写 Field → 配置错误 (防歧义)."""

    def dual(
        x: Annotated[str, Field(description="注解侧")] = Field(description="默认值侧"),
    ) -> str:
        """双 Field 载体."""

    with pytest.raises(ToolConfigError, match="只保留一处"):
        build_pydantic_schema(dual)


def test_pydantic_missing_annotation_raises():
    """参数缺类型注解 → 配置错误 (schema 由注解生成)."""
    with pytest.raises(ToolConfigError, match="类型注解"):
        build_pydantic_schema(_no_annotation_fn)


def test_pydantic_varargs_raises():
    """*args 无法生成具名参数 schema → 配置错误."""
    with pytest.raises(ToolConfigError, match=r"\*args"):
        build_pydantic_schema(_vararg_fn)


def test_pydantic_wire_schema_matches_handwritten_sample():
    """pydantic 引擎产出与 helpers.TOOL_SCHEMA 手工样本同构 (wire 契约)."""
    parameters = build_pydantic_schema(_wire_sim_fn).parameters
    assert parameters == TOOL_SCHEMA["function"]["parameters"]


# ---------------------------------------------------------------------------
# manual 引擎 (教学对照)
# ---------------------------------------------------------------------------


def test_manual_primitive_mapping_and_optional_defaults():
    """manual: 基础类型映射 + 默认值不进 required + docstring 描述注入."""
    info = build_manual_schema(_contract_fn)
    assert info.parameter_model is None  # manual 无自动校验模型
    properties = info.parameters["properties"]
    assert properties["query"] == {"type": "string", "description": "搜索关键词, 必填."}
    assert properties["top_k"] == {
        "type": "integer",
        "default": 5,
        "description": "返回条数上限, 默认 5.",
    }
    assert properties["fuzzy"] == {
        "type": "boolean",
        "default": False,
        "description": "是否模糊匹配, 默认 False.",
    }
    assert info.parameters["required"] == ["query"]
    assert info.parameters["additionalProperties"] is False


def test_manual_optional_uses_anyof_null():
    """manual: T|None → anyOf [T, null] (与 pydantic nullable 表达一致)."""
    properties = build_manual_schema(_contract_fn).parameters["properties"]
    assert properties["tags"]["anyOf"] == [
        {"type": "array", "items": {"type": "string"}},
        {"type": "null"},
    ]


def test_manual_literal_and_enum():
    """manual: Literal / Enum 子类 → enum + type."""

    def literal_fn(kind: Literal["a", "b"], color: Color = Color.RED) -> str:
        """载体.

        Args:
            kind: 类型.
        """

    info = build_manual_schema(literal_fn)
    properties = info.parameters["properties"]
    assert properties["kind"] == {
        "type": "string",
        "enum": ["a", "b"],
        "description": "类型.",
    }
    assert properties["color"]["enum"] == ["red", "blue"]
    assert "type" in properties["color"]


def test_manual_annotated_str_metadata_not_used_as_description():
    """manual: Annotated 字符串 metadata 不解释为描述 (PEP 593 无该语义).

    描述唯一来源是 docstring Args —— 防魔法解读 (方案 A, 见 schema/__init__).
    """

    def annotated_manual_fn(
        q: Annotated[str, "带 metadata 的查询词"],
    ) -> str:
        """载体.

        Args:
            q: docstring 描述 (唯一来源).
        """

    properties = build_manual_schema(annotated_manual_fn).parameters["properties"]
    assert properties["q"]["type"] == "string"  # Annotated 剥掉后正常映射
    assert properties["q"]["description"] == "docstring 描述 (唯一来源)."


def test_manual_unsupported_types_raise():
    """manual 不支持 pydantic BaseModel 参数 / 多类型 Union → 配置错误."""

    def with_model(params: FilterModel) -> str:
        """载体."""

    def with_union(x: int | str) -> str:
        """载体."""

    with pytest.raises(ToolConfigError, match="BaseModel"):
        build_manual_schema(with_model)
    with pytest.raises(ToolConfigError, match="Union"):
        build_manual_schema(with_union)


# ---------------------------------------------------------------------------
# 引擎等值契约 (用户对比学习点)
# ---------------------------------------------------------------------------


def test_engines_produce_equivalent_schema_for_pure_annotated_fn():
    """同一纯注解 + docstring 签名, pydantic 与 manual 产出等值 schema.

    证明手写 typing 映射 (manual) 与 pydantic 模型生成对同签名的结果一致;
    差异仅在运行时校验能力 (pydantic 有 parameter_model).
    """
    pydantic_info = build_pydantic_schema(_contract_fn)
    manual_info = build_manual_schema(_contract_fn)
    assert pydantic_info.parameters == manual_info.parameters
    assert pydantic_info.parameter_model is not None
    assert manual_info.parameter_model is None
