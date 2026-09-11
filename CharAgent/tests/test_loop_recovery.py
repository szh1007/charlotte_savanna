"""错误自纠错测试 (issue 04 / difficulties #2): 失败回填 → 模型二次调用修正.

工具执行失败不是终点: execute_tool 的可操作错误 (说清「期望什么 / 实际
怎样」) 作为 tool 消息回填模型, 模型看懂后换参数重试即可成功 —— 本组测试
用脚本化模型编排「错 → 对」两轮调用, 并做轨迹断言 (#62): 模型第二次看到
错误后确实换了参数.

载体工具复用 tool.tools_demo (issue 03 演示工具集):
- query_order_status_manual: manual 引擎 + 函数内 raise ToolActionableError
- convert_length / query_order_status: pydantic 引擎 (校验失败文案路径)
"""

from __future__ import annotations

import json

from mock_llm import ScriptedModel, make_tool_call, text_response, tool_call_response

from CharAgent.agent import AgentLoop, LoopOutcome
from CharAgent.tool.tools_demo import (
    convert_length,
    query_order_status,
    query_order_status_manual,
)

USER_MSG = {"role": "user", "content": "查询我的订单 20260701123456 到哪了"}

GOOD_ORDER = "20260701123456"
BAD_ORDER = "abc123"


async def test_actionable_error_triggers_self_correction() -> None:
    """可操作错误回填 → 模型第二次调用成功 (验收 #2)."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call(
                    "query_order_status_manual",
                    json.dumps({"order_no": BAD_ORDER}),
                )
            ),
            tool_call_response(
                make_tool_call(
                    "query_order_status_manual",
                    json.dumps({"order_no": GOOD_ORDER}),
                )
            ),
            text_response(f"订单 {GOOD_ORDER} 已发货"),
        ]
    )
    loop = AgentLoop(model=model, tools=[query_order_status_manual])
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.turn_count == 3
    assert result.content == f"订单 {GOOD_ORDER} 已发货"

    # 失败轮回填的是「可操作错误」: 说清期望 (14 位数字) 与实际 ('abc123'),
    # 而非甩 traceback / 422 让模型猜
    error_backfill = model.calls[1]["messages"][-1]
    assert error_backfill["role"] == "tool"
    assert "14 位数字" in error_backfill["content"]
    assert BAD_ORDER in error_backfill["content"]
    assert "Traceback" not in error_backfill["content"]

    # 轨迹 (#62): 模型看到错误后, 第二次调用换了参数 (自纠错证据);
    # 该轮历史以 tool 成功消息收尾, 修正后的 assistant 调用在倒数第二条
    second_arguments = model.calls[2]["messages"][-2]["tool_calls"][0]["function"][
        "arguments"
    ]
    assert json.loads(second_arguments) == {"order_no": GOOD_ORDER}


async def test_validation_error_also_self_corrects() -> None:
    """pydantic 校验失败 (pattern) 走同一条回填链, 模型自纠错成功."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("query_order_status", json.dumps({"order_no": "短"}))
            ),
            tool_call_response(
                make_tool_call(
                    "query_order_status", json.dumps({"order_no": GOOD_ORDER})
                )
            ),
            text_response("查询成功"),
        ]
    )
    loop = AgentLoop(model=model, tools=[query_order_status])
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    backfill = model.calls[1]["messages"][-1]["content"]
    assert "order_no" in backfill
    assert "格式" in backfill or "pattern" in backfill  # 字段级可操作提示
    # 第二次工具执行成功: 成功回填里带订单状态 (文案路径走 schema pattern)
    success_backfill = model.calls[2]["messages"][-1]["content"]
    assert "已发货" in success_backfill


async def test_malformed_json_arguments_self_corrects() -> None:
    """arguments 畸形 JSON (#10 保真): 可操作错误回填, 模型重新填参成功."""
    model = ScriptedModel(
        [
            tool_call_response(
                # 缺右括号: 畸形 JSON
                make_tool_call(
                    "convert_length", '{"value": 3.5, "from_unit": "kilometer"'
                )
            ),
            tool_call_response(
                make_tool_call(
                    "convert_length",
                    '{"value": 3.5, "from_unit": "kilometer", "to_unit": "mile"}',
                )
            ),
            text_response("换算完成"),
        ]
    )
    loop = AgentLoop(model=model, tools=[convert_length])
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    error_content = model.calls[1]["messages"][-1]["content"]
    assert "不是合法 JSON" in error_content  # 可操作文案指明期望
    # 第二次调用成功: 工具回填里含换算结果 (3.5 km = 2.1748 mile)
    tool_contents = [m["content"] for m in result.messages if m["role"] == "tool"]
    assert any("2.1748" in content for content in tool_contents)


async def test_model_may_abandon_after_error() -> None:
    """自纠错不强制重试: 模型看到错误后选择放弃并答复用户 (尊重模型决策)."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call(
                    "query_order_status_manual",
                    json.dumps({"order_no": BAD_ORDER}),
                )
            ),
            text_response("订单号格式不对, 已请用户核对后重新提问"),
        ]
    )
    loop = AgentLoop(model=model, tools=[query_order_status_manual])
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert "请用户核对" in result.content  # 模型不再调用工具, 直接收尾
    assert result.turn_count == 2
    assert len(result.messages) == 4  # user + assistant + tool(错误) + assistant
