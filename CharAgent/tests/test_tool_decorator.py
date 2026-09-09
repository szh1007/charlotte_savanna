"""@tool 装饰器行为测试 (issue 03): 注册语义 / 命名 / 描述 / 引擎切换.

- 裸 @tool 与 @tool(name=..., description=...) 两种形态
- name 默认函数名, description 默认 docstring 首段 (可显式覆盖)
- 无 docstring 时 description 兜底为工具名
- 非法工具名 / 非函数入参 / 未知 schema 引擎 → ToolConfigError
- @tool(schema="manual") 切换到 manual 引擎 (无自动校验模型)
"""

from __future__ import annotations

import pytest

from CharAgent.tool import Tool, ToolConfigError, tool


def _demo_fn(
    message: str,
    times: int = 1,
) -> str:
    """重复消息载体 (docstring 首段即 description).

    Args:
        message: 消息内容.
    """


def _no_docstring_fn(x: str) -> str:
    return x


def _weather_fn(location: str) -> str:
    """查询天气. 用户询问天气时使用; 否则不要调用.

    Args:
        location: 城市名.
    """


# ---------------------------------------------------------------------------
# 注册形态与元数据
# ---------------------------------------------------------------------------


def test_bare_tool_uses_fn_name_and_docstring_first_paragraph():
    """裸 @tool: name=函数名, description=docstring 首段."""
    registered = tool(_demo_fn)
    assert isinstance(registered, Tool)
    assert registered.name == "_demo_fn"
    assert registered.description == "重复消息载体 (docstring 首段即 description)."


def test_tool_kwargs_override_name_and_description():
    """@tool(name=..., description=...) 显式覆盖."""
    registered = tool(
        _weather_fn,
        name="query_weather",
        description="查询某城市天气",
    )
    assert registered.name == "query_weather"
    assert registered.description == "查询某城市天气"


def test_description_falls_back_to_fn_name_without_docstring():
    """无 docstring → description 兜底为工具名 (不会空字段)."""
    registered = tool(_no_docstring_fn)
    assert registered.description == "_no_docstring_fn"


def test_decorator_syntax_with_parens():
    """@tool(...) 括号形态应用后返回 Tool."""
    decorator = tool(name="renamed")
    registered = decorator(_demo_fn)
    assert registered.name == "renamed"


def test_tool_keeps_original_fn_reference():
    """Tool.fn 保留原函数 (executor 直接调用, 不包裹)."""
    registered = tool(_demo_fn)
    assert registered.fn is _demo_fn


def test_to_spec_produces_openai_function_wire():
    """to_spec: OpenAI function wire 结构 (model.generate tools 参数直通)."""
    spec = tool(_weather_fn).to_spec()
    assert spec == {
        "type": "function",
        "function": {
            "name": "_weather_fn",
            "description": "查询天气. 用户询问天气时使用; 否则不要调用.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "城市名."}
                },
                "required": ["location"],
                "additionalProperties": False,
            },
        },
    }


# ---------------------------------------------------------------------------
# 配置错误 (ToolConfigError, 面向开发者)
# ---------------------------------------------------------------------------


def test_invalid_name_rejected():
    """非法工具名 (非 [a-zA-Z0-9_-]) → 配置错误."""
    with pytest.raises(ToolConfigError, match="工具名"):
        tool(_weather_fn, name="查询天气")


def test_non_callable_rejected():
    """@tool 用在非函数上 → 配置错误."""
    with pytest.raises(ToolConfigError, match="函数"):
        tool(42)  # type: ignore[arg-type]


def test_unknown_schema_engine_rejected():
    """未知 schema 引擎名 → 配置错误 (列出可选值)."""
    with pytest.raises(ToolConfigError, match="pydantic"):
        tool(_weather_fn, schema="magic")


# ---------------------------------------------------------------------------
# schema 引擎切换
# ---------------------------------------------------------------------------


def test_schema_engine_selection():
    """默认 pydantic 引擎 (有自动校验模型); manual 显式切换 (无校验模型)."""
    pydantic_tool = tool(_weather_fn)
    manual_tool = tool(_weather_fn, schema="manual")
    assert pydantic_tool.parameter_model is not None
    assert manual_tool.parameter_model is None
