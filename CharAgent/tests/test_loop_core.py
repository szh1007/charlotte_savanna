"""agent loop 核心路径测试 (#1 #10 #11 #62).

场景 → 断言 (不止最终答案, 还断言轨迹 #62):
- 单工具调用: 调用 → tool 消息回填 → 模型二次决策正确, 全程轨迹断言
  (模型每轮看到了什么 / 历史 wire 结构精确形状)
- 消息历史保真: 可续接下一轮对话; 调用方输入列表不被污染
- reasoning 契约 (#11): 回填 wire 历史 (带 tools 时必须回传, 否则真实端点
  400), 但不混入 content 字段 (展示走 reasoning 事件, 另一条通道)
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


def _empty() -> str:
    """空返回载体: 工具返回空串 (回填保真边界)."""
    return ""


def _big() -> str:
    """超长返回载体: 工具返回大段文本 (回填不截断)."""
    return "x" * 5000


# 显式命名注册 (函数名 _echo/_add 带下划线, 注册名应去掉); 各测试共享同
# 一 Tool 实例, 断言 to_spec 时引用一致
ECHO_TOOL = tool(_echo, name="echo")
ADD_TOOL = tool(_add, name="add")
EMPTY_TOOL = tool(_empty, name="empty")
BIG_TOOL = tool(_big, name="big")

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


async def test_reasoning_backfilled_into_history_for_api_contract() -> None:
    """reasoning 回填 wire 历史 (#11 + DeepSeek 思考模式契约).

    官方文档要求带 tools 的请求完整回传 reasoning_content (称缺失即 400);
    本机实测未强制, 框架仍按文档执行以保留交错思考。
    """
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("echo", '{"message": "hi"}')),
            text_response("回声: hi", reasoning="核对参数: message=hi"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    result = await loop.run([dict(USER_MSG)])

    # 终止轮 assistant 消息携带 reasoning_content (API 契约要求回传)
    assert result.messages[-1]["reasoning_content"] == "核对参数: message=hi"
    # 但不混入 content 字段 (展示走 reasoning 事件, 是另一条通道)
    assert result.content == "回声: hi"
    assert result.turns[-1].response.reasoning == "核对参数: message=hi"


async def test_reasoning_visible_in_next_turn_request() -> None:
    """上一轮的 reasoning 出现在下一轮请求里 (API 侧会拼接进上下文)."""
    model = ScriptedModel(
        [
            text_response(
                "前半段",
                finish_reason=FinishReason.LENGTH,
                reasoning="先规划文章结构",
            ),
            text_response("后半段"),
        ]
    )
    loop = AgentLoop(model=model)
    await loop.run([dict(USER_MSG)])

    sent = json.dumps(model.calls[1]["messages"], ensure_ascii=False)
    assert "先规划文章结构" in sent
    # reasoning 与 content 同属一条 assistant 消息 (顺序保真)
    assert model.calls[1]["messages"][1]["reasoning_content"] == "先规划文章结构"
    assert model.calls[1]["messages"][1]["content"] == "前半段"


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


async def test_assistant_content_alongside_tool_calls() -> None:
    """模型边叙述边调工具 (官方思考模式样例的常见形态).

    叙述文本随 assistant 消息进 wire 保真, 但**不进最终答案** (#11 双通道)。
    """
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "hi"}'),
                content="让我先查一下订单",
            ),
            text_response("订单已发货"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    # 叙述保真入 wire: 同一条 assistant 消息同时带 content 与 tool_calls
    assert result.messages[1]["content"] == "让我先查一下订单"
    assert result.messages[1]["tool_calls"][0]["function"]["name"] == "echo"
    # 但不混入最终答案 (最终答案只取终止轮)
    assert result.content == "订单已发货"
    # 下一轮模型能看到这段叙述 (上下文连续)
    assert model.calls[1]["messages"][1]["content"] == "让我先查一下订单"


async def test_content_filter_terminates_without_retry() -> None:
    """finish_reason=content_filter: 按终止处理, 不重试也不进截断路径.

    loop 只负责终止循环并如实上报 finish_reason, 拦截语义 (是否对用户
    展示 / 降级话术) 由调用方决定 (输出护栏归上层)。
    """
    model = ScriptedModel(
        [text_response(None, finish_reason=FinishReason.CONTENT_FILTER)]
    )
    loop = AgentLoop(model=model)
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.finish_reason is FinishReason.CONTENT_FILTER
    assert result.content is None  # 被拦截, 无正文
    assert result.truncation_count == 0  # 不走截断处理
    assert len(model.calls) == 1  # 不重试


async def test_insufficient_system_resource_yields_interrupted_outcome() -> None:
    """finish_reason=insufficient_system_resource: 服务端中断, 半截内容不当答复.

    官方语义是生成被打断 (瞬态), 重放决策归调用方 / 重试层; 本层只如实
    上报 outcome 与 finish_reason, 且不把可能残缺的正文当最终答案返回.
    """
    model = ScriptedModel(
        [
            text_response(
                "订单 20260701123456 已",
                finish_reason=FinishReason.INSUFFICIENT_SYSTEM_RESOURCE,
            )
        ]
    )
    loop = AgentLoop(model=model)
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.SERVER_INTERRUPTED
    assert result.finish_reason is FinishReason.INSUFFICIENT_SYSTEM_RESOURCE
    assert result.content is None  # 半截正文不外泄
    assert result.truncation_count == 0  # 不走截断续写路径
    assert len(model.calls) == 1  # 本层不重试
    # 半截原文保真: wire 历史与轮次快照仍可查到, 供排查 / 重放
    assert result.messages[-1]["content"] == "订单 20260701123456 已"
    assert result.turns[-1].response.content == "订单 20260701123456 已"


async def test_aborted_finish_reason_yields_interrupted_outcome() -> None:
    """finish_reason=aborted: 生成被中断, 同样判 SERVER_INTERRUPTED 而非正常结束."""
    model = ScriptedModel([text_response(None, finish_reason=FinishReason.ABORTED)])
    loop = AgentLoop(model=model)
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.SERVER_INTERRUPTED
    assert result.finish_reason is FinishReason.ABORTED
    assert result.content is None


async def test_tool_result_backfilled_verbatim() -> None:
    """工具返回值原样回填: 空串仍产生 tool 消息, 超长不截断."""
    big = "x" * 5000
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("empty")),
            tool_call_response(make_tool_call("big")),
            text_response("完成"),
        ]
    )
    loop = AgentLoop(model=model, tools=[EMPTY_TOOL, BIG_TOOL])
    result = await loop.run([dict(USER_MSG)])

    tool_msgs = [m for m in result.messages if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    # 空串不丢消息 —— 配对结构 (assistant.tool_calls ↔ tool) 必须完整
    assert tool_msgs[0]["content"] == ""
    assert tool_msgs[0]["tool_call_id"] == "call_empty"
    # 超长原样回填, 框架不做长度裁剪 (上下文治理归 compaction, 非 loop)
    assert tool_msgs[1]["content"] == big


async def test_run_state_isolated_across_runs_on_same_instance() -> None:
    """同一 AgentLoop 实例连续 run: turns / turn_count / tokens 逐 run 独立.

    server 层按 run 复用同一 loop 实例 (见 AgentLoop docstring), 上一 run 的
    轮数与 token 不得累加到下一 run。
    """
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "a"}'),
                usage=Usage(total_tokens=10),
            ),
            text_response("第一次", usage=Usage(total_tokens=20)),
            text_response("第二次", usage=Usage(total_tokens=5)),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    first = await loop.run([dict(USER_MSG)])
    second = await loop.run([dict(USER_MSG)])

    assert (first.turn_count, first.total_tokens, len(first.turns)) == (2, 30, 2)
    # 第二次 run 从零起算, 不承接第一次的轮次与用量
    assert (second.turn_count, second.total_tokens, len(second.turns)) == (1, 5, 1)
    assert second.turns[0].turn == 1  # 轮次编号重新从 1 开始
    assert second.content == "第二次"


async def test_sampling_and_thinking_params_passed_every_turn() -> None:
    """透传契约 (#68 + 思考模式): 构造参数逐轮原样交给模型, 框架不改写."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("echo", '{"message": "hi"}')),
            text_response("好"),
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[ECHO_TOOL],
        temperature=0.1,
        top_p=0.8,
        seed=42,
        max_tokens=512,
        thinking=False,
        reasoning_effort="low",
    )
    await loop.run([dict(USER_MSG)])

    assert len(model.calls) == 2  # 两轮都要带上 (不能只首轮)
    for call in model.calls:
        assert call["temperature"] == 0.1
        assert call["top_p"] == 0.8
        assert call["seed"] == 42
        assert call["max_tokens"] == 512
        assert call["thinking"] is False
        assert call["reasoning_effort"] == "low"


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
