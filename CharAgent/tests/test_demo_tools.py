"""演示工具集冒烟测试 (issue 03): 6 个工具可注册、schema 有效、执行正确.

同时验证 issue 03 的核心教学对照:
- #5 query_order_status (pydantic, Field(pattern) 约束进 schema → 模型填参即被约束)
  vs #6 query_order_status_manual (manual 引擎, 手写校验 raise 可操作错误 #2)
- 双路径同能力工具输出一致 (用户对比学习点)
"""

from __future__ import annotations

import json

from CharAgent.tool import execute_tool
from CharAgent.tool.tools_demo import (
    MOCK_ORDERS,
    batch_convert_lengths,
    convert_length,
    count_text_stats,
    get_current_time,
    query_order_status,
    query_order_status_manual,
)

DEMO_TOOLS = [
    get_current_time,
    convert_length,
    batch_convert_lengths,
    count_text_stats,
    query_order_status,
    query_order_status_manual,
]

# 每个工具的典型合法参数 (冒烟执行用)
VALID_ARGUMENTS: dict[str, str] = {
    "get_current_time": '{"fmt": "date"}',
    "convert_length": '{"value": 3.5, "from_unit": "kilometer", "to_unit": "mile"}',
    "batch_convert_lengths": (
        '{"items": [{"value": 10, "unit": "kilometer"}, '
        '{"value": 100, "unit": "meter"}], "to_unit": "foot"}'
    ),
    "count_text_stats": '{"text": "hello world", "mode": "words"}',
    "query_order_status": '{"order_no": "20260701123456"}',
    "query_order_status_manual": '{"order_no": "20260701123456"}',
}


def test_all_demo_tools_registered_with_valid_wire_spec():
    """6 个工具均可注册且 to_spec 是完整 wire 结构 (键齐全)."""
    for demo_tool in DEMO_TOOLS:
        spec = demo_tool.to_spec()
        assert spec["type"] == "function"
        function = spec["function"]
        assert function["name"] == demo_tool.name
        assert function["description"]
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False
        assert isinstance(parameters["properties"], dict)
        # schema 可 JSON 序列化 (wire 无非法对象如枚举实例/FieldInfo)
        json.dumps(parameters, ensure_ascii=False)


def test_manual_tool_has_no_auto_validation_model():
    """#6 manual 引擎: 无自动校验模型 (pydantic 路径有), 是两路径的本质差异."""
    assert query_order_status_manual.parameter_model is None
    assert query_order_status.parameter_model is not None


def test_pydantic_tool_schema_carries_pattern_constraint():
    """#5 pydantic: Field(pattern) 把 14 位规则送进 schema (模型填参被约束)."""
    order_no_schema = query_order_status.to_spec()["function"]["parameters"][
        "properties"
    ]["order_no"]
    assert order_no_schema["pattern"] == r"^\d{14}$"
    assert order_no_schema["examples"] == ["20260701123456"]


def test_manual_tool_schema_carries_description_only():
    """#6 manual: schema 只有类型 + docstring 描述 (规则靠运行时校验)."""
    order_no_schema = query_order_status_manual.to_spec()["function"]["parameters"][
        "properties"
    ]["order_no"]
    assert order_no_schema["type"] == "string"
    assert "14 位数字订单号" in order_no_schema["description"]
    assert "pattern" not in order_no_schema


async def test_each_demo_tool_runs_with_valid_arguments():
    """每个工具典型输入执行成功, content 非空."""
    for demo_tool in DEMO_TOOLS:
        execution = await execute_tool(
            demo_tool, arguments=VALID_ARGUMENTS[demo_tool.name]
        )
        assert execution.ok, f"{demo_tool.name} 应执行成功: {execution.error}"
        assert execution.content


async def test_time_formats_by_literal():
    """get_current_time: Literal 三格式分别产出对应格式文本."""
    for fmt, pattern_mark in [("date", "-"), ("time", ":"), ("iso", " ")]:
        execution = await execute_tool(
            get_current_time, arguments=f'{{"fmt": "{fmt}"}}'
        )
        assert execution.ok
        assert pattern_mark in execution.content


async def test_convert_length_roundtrip():
    """convert_length: 米 ↔ 千米往返换算一致 (确定性)."""
    forward = await execute_tool(
        convert_length,
        arguments='{"value": 2, "from_unit": "kilometer", "to_unit": "meter"}',
    )
    backward = await execute_tool(
        convert_length,
        arguments='{"value": 2000, "from_unit": "meter", "to_unit": "kilometer"}',
    )
    assert forward.ok and backward.ok
    assert "2000 meter" in forward.content
    assert "2 kilometer" in backward.content


async def test_query_order_status_known_and_unknown():
    """query_order_status: 已知订单返回状态, 未知订单返回核对提示."""
    known = await execute_tool(
        query_order_status, arguments='{"order_no": "20260701123456"}'
    )
    assert known.ok and "已发货" in known.content
    unknown = await execute_tool(
        query_order_status, arguments='{"order_no": "11111111111111"}'
    )
    assert unknown.ok and "未查询到订单" in unknown.content


async def test_manual_order_no_validation_raises_actionable():
    """#6 manual: 非法 order_no (绕过 schema 约束) → 函数内可操作错误 (#2)."""
    execution = await execute_tool(
        query_order_status_manual, arguments='{"order_no": "abc"}'
    )
    assert not execution.ok
    assert "order_no 应为 14 位数字" in execution.error
    assert "实际 'abc'" in execution.error


async def test_manual_missing_order_no_is_actionable():
    """#6 manual: 缺参由执行层形状检查兜底 (可操作, 非「内部错误」)."""
    execution = await execute_tool(query_order_status_manual, arguments="{}")
    assert not execution.ok
    assert "缺少必填参数 order_no" in execution.error
    assert "内部错误" not in execution.error


async def test_pydantic_order_no_pattern_rejects_invalid():
    """#5 pydantic: 非法 order_no 在校验层即被拦 (pattern), 函数体不会执行."""
    execution = await execute_tool(query_order_status, arguments='{"order_no": "abc"}')
    assert not execution.ok
    assert "格式不符合要求" in execution.error
    assert r"^\d{14}$" in execution.error  # schema 的 pattern 约束生效


async def test_dual_implementations_same_output_for_same_input():
    """#5/#6 同能力双实现: 同一订单号输出完全一致 (用户对比学习点)."""
    args = '{"order_no": "20260630024680"}'
    pydantic_execution = await execute_tool(query_order_status, arguments=args)
    manual_execution = await execute_tool(query_order_status_manual, arguments=args)
    assert pydantic_execution.ok and manual_execution.ok
    assert (
        pydantic_execution.content
        == manual_execution.content
        == (f"订单 20260630024680: {MOCK_ORDERS['20260630024680']}")
    )
