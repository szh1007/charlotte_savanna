"""错误自纠错测试 (difficulties #2): 失败回填 → 模型二次调用修正.

工具执行失败不是终点: execute_tool 的可操作错误 (说清「期望什么 / 实际
怎样」) 作为 tool 消息回填模型, 模型看懂后换参数重试即可成功 —— 本组测试
用脚本化模型编排「错 → 对」两轮调用, 并做轨迹断言 (#62): 模型第二次看到
错误后确实换了参数. **超时 (#15) 不在这条路上**: 它意味着结果未知 (可能
已生效), 继续问模型就有重复写操作的风险, 于是框架当场中断本次运行 ——
本文件最后一条用例钉它 (取舍见 ADR-0024).

载体工具复用 tool.tools_demo (演示工具集):
- query_order_status_manual: manual 引擎 + 函数内 raise ToolActionableError
- convert_length / query_order_status: pydantic 引擎 (校验失败文案路径)
- hang_forever: 永不返回的协程载体 (超时路径)
"""

from __future__ import annotations

import json
import time

from doubles import EventCollector, hang_forever
from mock_llm import ScriptedModel, make_tool_call, text_response, tool_call_response

from CharAgent.agent import AgentLoop, LoopGuard, LoopOutcome
from CharAgent.stream import EventType
from CharAgent.tool import tool
from CharAgent.tool.tools_demo import (
    convert_length,
    query_order_status,
    query_order_status_manual,
)

USER_MSG = {"role": "user", "content": "查询我的订单 20260701123456 到哪了"}

GOOD_ORDER = "20260701123456"
BAD_ORDER = "abc123"


# 超时 0.05 秒: 够短 (用例不必真等), 又远大于「函数调用本身」的开销
HANG_FOREVER = tool(hang_forever, name="hang_forever", timeout=0.05)


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


async def test_a_tool_that_never_returns_interrupts_the_run() -> None:
    """工具卡住 → 本次运行当场中断: 不再问模型, 也不再让它接着决策 (#15).

    这一片要堵的洞是**整轮不结束** (此前 execute_tool 没有超时, 一个卡住的工具
    调用会让这次 run 永远停在那里); 改判之后还多一层 (ADR-0024): 超时 = 结果
    未知, 把决定权交回模型就有重复写操作的风险 —— 于是收场是**中断** (不是
    「模型看到文案后自己接着答」). 断言落在四处: run 有终点且是 INTERRUPTED /
    没有答复 / 模型只被问过一次 (后面那一轮根本没发生) / 那条 tool 消息留在历史
    里 (配对完整, 且写清了「结果未知, 不要直接重试」).
    """
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("hang_forever", "{}")),
            text_response("这一轮不该发生"),
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[HANG_FOREVER],
        guard=LoopGuard(max_turns=3, max_duration_seconds=30),
    )

    started = time.perf_counter()
    result = await loop.run([dict(USER_MSG)])
    elapsed = time.perf_counter() - started

    assert result.outcome is LoopOutcome.INTERRUPTED, "是超时中断的, 不是软限制刹的"
    assert result.content is None, "中断那次没有答复 (事件流里是 error 不是 final)"
    assert len(model.calls) == 1, "中断之后不再问模型 —— 决定权没有交回去"

    backfill = result.messages[-1]
    assert backfill["role"] == "tool", "配对完整: 那条 tool_call 后面必须有消息"
    assert backfill["tool_call_id"] == "call_hang_forever"
    assert "执行超过 0.05 秒" in backfill["content"]
    assert "结果未知" in backfill["content"]
    assert "不要直接重试" in backfill["content"]
    assert elapsed < 2, f"一次卡住的工具不该拖住整轮, 实测 {elapsed:.2f} 秒"


async def test_a_timeout_still_backfills_every_call_in_the_batch() -> None:
    """一批里「超时 + 成功」: 两条结果都回填、都发事件, 然后才收线 (#15).

    未闭合的 tool_call 会让终局事件发不出去 (stream/bus.py 的状态机), 也会在
    落库那边留下 PENDING 孤儿 —— 于是中断**不省略**任何一条回填. 事件顺序同样
    是契约: 两个 tool_result 都在终局的 error 之前, 且按模型给的调用顺序.
    """
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("query_order_status", '{"order_no": "20260701123456"}'),
                make_tool_call("hang_forever", "{}", call_id="call_hang"),
            ),
            text_response("这一轮不该发生"),
        ]
    )
    collector = EventCollector()
    loop = AgentLoop(
        model=model,
        tools=[query_order_status, HANG_FOREVER],
        event_sink=collector,
    )

    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.INTERRUPTED
    assert [m["role"] for m in result.messages] == ["user", "assistant", "tool", "tool"]
    assert result.messages[2]["tool_call_id"] == "call_query_order_status"
    assert "已发货" in result.messages[2]["content"], "成功那条的结果原样保留"
    assert result.messages[3]["content"].startswith("工具 hang_forever 执行超过")

    assert collector.terminal_types == ["error"], "终局是 error, 没有 final"
    types = [event.type for event in collector.events]
    assert types == [
        EventType.TOOL_CALL,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.TOOL_RESULT,
        EventType.ERROR,
    ], "结果按调用顺序回填, 终局事件最后 (且没有 final)"
    assert collector.events[-1].data["error"]["code"] == "interrupted"
    assert collector.events[2].data["status"] == "ok"
    assert collector.events[3].data["status"] == "error"


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
