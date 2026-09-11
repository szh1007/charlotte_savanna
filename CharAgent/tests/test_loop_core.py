"""agent loop 核心路径测试 (issue 04 / #1 #10 #11 #62).

场景 → 断言 (不止最终答案, 还断言轨迹 #62):
- 单工具调用: 调用 → tool 消息回填 → 模型二次决策正确, 全程轨迹断言
  (模型每轮看到了什么 / 历史 wire 结构精确形状)
- 消息历史保真: 可续接下一轮对话; 调用方输入列表不被污染
- reasoning 分离 (#11): 不入消息历史, 完整保留在 TurnRecord.response
- 无工具路径: 单轮直接出答案; tools 参数直通协议
- 构造校验: 工具名重复报错

载体工具就地定义 (Seam 3); 模型为 ScriptedModel (Seam 1, mock_llm).
"""

from __future__ import annotations

import json

import pytest
from mock_llm import ScriptedModel, make_tool_call, text_response, tool_call_response

from CharAgent.agent import AgentLoop, LoopConfigError, LoopGuard, LoopOutcome
from CharAgent.model.utils.types import FinishReason, Usage
from CharAgent.tool import tool

# ---------------------------------------------------------------------------
# 载体工具 (模块顶层, 类型需顶层可见)
# ---------------------------------------------------------------------------


def _echo(
    message: str,
) -> str:
    """回显载体: 返回收到的消息."""
    return f"echo:{message}"


def _add(
    a: int,
    b: int,
) -> int:
    """加法载体: 返回两数之和."""
    return a + b


# 显式命名注册 (函数名 _echo/_add 带下划线, 注册名应去掉); 各测试共享同
# 一 Tool 实例, 断言 to_spec 时引用一致
ECHO_TOOL = tool(_echo, name="echo")
ADD_TOOL = tool(_add, name="add")

USER_MSG = {"role": "user", "content": "你好"}


# ---------------------------------------------------------------------------
# 单工具调用 + 轨迹断言
# ---------------------------------------------------------------------------


async def test_single_tool_call_then_final_answer() -> None:
    """工具调用 → 结果回填 → 模型第二次决策给出最终答案 (验收 #1 首项)."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("echo", json.dumps({"message": "hi"}))),
            text_response("回声: hi"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.content == "回声: hi"
    assert result.finish_reason is FinishReason.STOP
    assert result.turn_count == 2
    assert result.total_tokens == 0  # 脚本未带 usage → 计 0

    # 历史 wire 结构: user → assistant(tool_calls) → tool → assistant(答案)
    history = result.messages
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "assistant"]

    assistant_call = history[1]
    assert assistant_call["content"] is None
    assert assistant_call["tool_calls"] == [
        {
            "id": "call_echo",
            "type": "function",
            "function": {"name": "echo", "arguments": '{"message": "hi"}'},
        }
    ]
    # tool 消息与 assistant tool_calls 配对 (#10: id 原样携带)
    assert history[2] == {
        "role": "tool",
        "tool_call_id": "call_echo",
        "content": "echo:hi",
    }
    assert history[3]["role"] == "assistant"
    assert history[3]["content"] == "回声: hi"
    assert "tool_calls" not in history[3]

    # 轨迹 (#62): 模型第 2 次决策前已看到完整回填, 且工具列表每轮都传入
    assert model.calls[1]["messages"] == history[:3]
    assert model.calls[0]["tools"] == [ECHO_TOOL.to_spec()]


async def test_multiple_tool_turns_follow_real_flow() -> None:
    """多轮工具接力 (A 的结果是 B 的输入), 验证历史累积与参数保真 (#62)."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("add", '{"a": 1, "b": 2}')),
            tool_call_response(make_tool_call("echo", '{"message": "3"}')),
            text_response("结果是 3"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ADD_TOOL, ECHO_TOOL])
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.content == "结果是 3"
    assert result.turn_count == 3

    # 轨迹: 每轮模型看到的工具结果按调用顺序回填
    assert [m["role"] for m in result.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
    ]
    # 模型第 2 轮看到的消息前缀 = 第 1 轮结束的历史 (与第 1 轮 TurnRecord 一致)
    assert model.calls[1]["messages"] == result.turns[0].messages
    assert model.calls[2]["messages"] == result.turns[1].messages

    # 每轮快照长度递增 (turn 结束时消息数 = 3, 5, 6)
    assert [len(t.messages) for t in result.turns] == [3, 5, 6]
    assert [t.turn for t in result.turns] == [1, 2, 3]


async def test_arguments_kept_raw_json_string() -> None:
    """arguments 以原始 JSON 字符串入历史, 不预解析 (#10 保真约定)."""
    raw_args = '{"a": 1,"b": 2}'  # 无空格间隔, 预解析后序列化会改变原串
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("add", raw_args)),
            text_response("3"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ADD_TOOL])
    result = await loop.run([dict(USER_MSG)])

    assert result.messages[1]["tool_calls"][0]["function"]["arguments"] == raw_args
    assert result.messages[2]["content"] == "3"


# ---------------------------------------------------------------------------
# 消息历史保真 / 复用 / 隔离
# ---------------------------------------------------------------------------


async def test_result_history_feeds_next_run() -> None:
    """run 的完整历史可直接作为下一次 run 的输入 (多轮对话续接载体)."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("echo", '{"message": "hi"}')),
            text_response("回声: hi"),
            # 第二次 run 的脚本
            text_response("第二次回答"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    first = await loop.run([dict(USER_MSG)])
    second = await loop.run([*first.messages, {"role": "user", "content": "再来"}])

    assert second.messages[: len(first.messages)] == first.messages
    assert second.turn_count == 1  # 历史续接不重放, 新一轮只决策一次
    assert second.content == "第二次回答"
    assert model.calls[2]["messages"][-1] == {"role": "user", "content": "再来"}


async def test_input_messages_not_mutated() -> None:
    """run 不污染调用方传入的消息列表 (隔离性, 内部拷贝)."""
    messages = [dict(USER_MSG)]
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("echo", '{"message": "hi"}')),
            text_response("回声: hi"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    await loop.run(messages)

    assert messages == [USER_MSG]
    assert len(messages) == 1


# ---------------------------------------------------------------------------
# reasoning 分离 / 无工具路径 / 构造校验
# ---------------------------------------------------------------------------


async def test_reasoning_never_backfilled_into_history() -> None:
    """reasoning 分离 (#11): 不入消息历史, 完整保留在 TurnRecord.response."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("echo", '{"message": "hi"}')),
            text_response("回声: hi", reasoning="核对参数: message=hi"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    result = await loop.run([dict(USER_MSG)])

    for message in result.messages:
        assert "reasoning_content" not in message
    # 第二轮 (自然终止轮) 的响应全文在 TurnRecord 里, reasoning 不丢
    assert result.turns[-1].response.reasoning == "核对参数: message=hi"
    # reasoning 也不出现在模型下一轮看到的输入里 (不入上下文 #11)
    assert "核对参数" not in json.dumps(model.calls[-1]["messages"], ensure_ascii=False)


async def test_no_tools_single_round_answer() -> None:
    """未开放工具时: 模型单轮直接出答案, tools 以 None 直通 (#10 语义)."""
    model = ScriptedModel([text_response("你好, 我是 CharAgent")])
    loop = AgentLoop(model=model)  # 无 tools
    result = await loop.run([dict(USER_MSG)])

    assert result.content == "你好, 我是 CharAgent"
    assert result.turn_count == 1
    assert result.outcome is LoopOutcome.FINISHED
    assert model.no_tools_calls == 1  # tools=None 直通协议
    assert model.calls[0]["tools"] is None


async def test_tools_passed_every_turn_when_declared() -> None:
    """声明工具后每轮 generate 都收到完整 tools 列表 (协议直通)."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("echo", "{}")),
            text_response("完成"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    await loop.run([dict(USER_MSG)])

    assert all(call["tools"] == [ECHO_TOOL.to_spec()] for call in model.calls)


def test_duplicate_tool_name_raises() -> None:
    """工具名重复 → 构造期报错 (模型无法区分同名工具)."""
    duplicate = tool(_echo, name="echo")
    with pytest.raises(LoopConfigError, match="工具名重复"):
        AgentLoop(model=ScriptedModel([]), tools=[ECHO_TOOL, duplicate])


def test_guard_config_validated() -> None:
    """LoopGuard 非法上限 → 构造期 GuardConfigError (#3 防护参数有效性)."""
    with pytest.raises(ValueError, match="max_turns"):
        LoopGuard(max_turns=0)
    with pytest.raises(ValueError, match="max_total_tokens"):
        LoopGuard(max_total_tokens=0)
    with pytest.raises(ValueError, match="max_duration_seconds"):
        LoopGuard(max_duration_seconds=0)


def test_loop_max_truncations_validated() -> None:
    """AgentLoop 非法截断重试上限 → 构造期报错 (截断防护参数有效性)."""
    with pytest.raises(LoopConfigError, match="max_truncations"):
        AgentLoop(model=ScriptedModel([]), max_truncations=0)


async def test_usage_accumulated() -> None:
    """usage 统计: 每次模型响应的 token 数累加到结果."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "hi"}'),
                usage=Usage(input_tokens=10, output_tokens=5),
            ),
            text_response("再见", usage=Usage(total_tokens=30)),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    result = await loop.run([dict(USER_MSG)])

    # total 缺失时按 input+output 兜底 (15); 第二轮 total=30 优先
    assert result.total_tokens == 15 + 30
    assert result.turns[0].tokens == 15
    assert result.turns[1].tokens == 30
