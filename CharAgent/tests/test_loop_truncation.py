"""finish_reason=length 截断处理测试 (difficulties #10).

截断语义: 模型输出被 token 上限截断, 内容不完整 —— 不能当正常答案返回.
两种处理路径 (difficulties #10 原文: 要么续写、要么提示模型精简):
- CONTINUE (默认): 保留截断前缀于历史, 回填续写指令, 模型接着中断处输出
- CONDENSE: 截断前缀不完整, 丢弃后提示模型精简重答 (原文保留在
  TurnRecord.response, 不丢)
防护: 截断处理次数超过 AgentLoop 的 max_truncations 重试上限 →
  TRUNCATION_LIMIT 放弃 (防止小窗口下无限续写烧 token, 与 #3 防护同源)
边界: length 与 tool_calls 并存时工具路径优先 (#10: 截断的 arguments 由
  executor 畸形 JSON 可操作错误兜底自纠错)

载体工具就地定义 (Seam 3); 模型为 ScriptedModel (Seam 1).
"""

from __future__ import annotations

from mock_llm import (
    ScriptedModel,
    make_tool_call,
    text_response,
    tool_call_response,
)

from CharAgent.agent import AgentLoop, LoopGuard, LoopOutcome, TruncationStrategy
from CharAgent.model.utils.types import FinishReason, ModelResponse, Usage
from CharAgent.tool import tool

USER_MSG = {"role": "user", "content": "写一篇长文"}


def _echo(
    message: str,
) -> str:
    """回声载体."""
    return f"echo:{message}"


def _length_response(
    content: str | None, *, usage: Usage | None = None
) -> ModelResponse:
    """截断响应工厂: 部分内容 + finish_reason=length (usage 供预算用例)."""
    return text_response(content, finish_reason=FinishReason.LENGTH, usage=usage)


ECHO_TOOL = tool(_echo, name="echo")

PREFIX = "文章开头段落, 已经写了不少内容但被截断了"
SUFFIX = "文章结尾段落, 续写完成的部分"


# ---------------------------------------------------------------------------
# 续写路径 (CONTINUE, 默认)
# ---------------------------------------------------------------------------


async def test_length_continue_keeps_prefix_and_continues() -> None:
    """截断 → 保留前缀 + 续写指令 → 模型续写成功 (#10 默认路径)."""
    model = ScriptedModel([_length_response(PREFIX), text_response(SUFFIX)])
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.truncation_count == 1
    assert result.content == PREFIX + SUFFIX  # 前缀 + 续写段拼合为完整答案

    # 历史保真: 截断的 assistant 前缀在, 续写指令在, 模型续写输出独立成段
    roles = [m["role"] for m in result.messages]
    assert roles == ["user", "assistant", "system", "assistant"]
    assert result.messages[1]["content"] == PREFIX
    assert result.messages[2]["role"] == "system"
    assert "截断" in result.messages[2]["content"]
    assert "继续" in result.messages[2]["content"]
    assert result.messages[3]["content"] == SUFFIX

    # 轨迹: 模型第 2 次决策前已看到前缀 + 指令 (续写上下文完整)
    assert model.calls[1]["messages"] == result.messages[:3]


async def test_length_continue_accumulates_across_truncations() -> None:
    """连续两次截断续写: 最终答案为各段按序拼合 (累积而非只取尾段)."""
    middle = "中段续写的内容, 又被截断了一次"
    model = ScriptedModel(
        [
            _length_response(PREFIX),
            _length_response(middle),
            text_response(SUFFIX),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL], max_truncations=2)
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.truncation_count == 2
    assert result.content == PREFIX + middle + SUFFIX

    # 三段正文各自独立成消息 (拼合只发生在 content, 历史保真不变)
    roles = [m["role"] for m in result.messages]
    assert roles == [
        "user",
        "assistant",
        "system",
        "assistant",
        "system",
        "assistant",
    ]


async def test_content_parts_dropped_when_tool_turn_interrupts() -> None:
    """续写途中转去调工具: 工具轮前的正文属叙述, 不混入最终答案 (#10)."""
    model = ScriptedModel(
        [
            _length_response(PREFIX),
            tool_call_response(make_tool_call("echo", '{"message": "hi"}')),
            text_response(SUFFIX),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL], max_truncations=2)
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.truncation_count == 1
    # 拼合链被工具轮打断: 截断前缀是工具调用前的叙述, 不进最终答案
    assert result.content == SUFFIX
    # 历史保真不受影响: 截断前缀与工具往返仍在 messages 里
    assert result.messages[1]["content"] == PREFIX
    assert result.messages[4]["role"] == "tool"


async def test_length_continue_when_content_empty() -> None:
    """纯 length 无任何内容 (极短 max_tokens): 指令引导重答, 不抛错."""
    model = ScriptedModel([_length_response(None), text_response("完整回答")])
    loop = AgentLoop(model=model)
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.truncation_count == 1
    assert result.content == "完整回答"


# ---------------------------------------------------------------------------
# 精简路径 (CONDENSE)
# ---------------------------------------------------------------------------


async def test_length_condense_drops_prefix_and_reasks() -> None:
    """CONDENSE: 截断内容丢弃 (不完整无价值), 模型精简重答 (#10 第二路径)."""
    model = ScriptedModel([_length_response(PREFIX), text_response("精简后的要点")])
    loop = AgentLoop(
        model=model,
        truncation=TruncationStrategy.CONDENSE,
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.truncation_count == 1
    assert result.content == "精简后的要点"

    # 历史里不含被丢弃的截断前缀; 指令提示模型精简重答
    roles = [m["role"] for m in result.messages]
    assert roles == ["user", "system", "assistant"]
    assert PREFIX not in [m.get("content") for m in result.messages]
    assert "精炼" in result.messages[1]["content"]  # 指令: 精简重答

    # 原文不丢: 完整保留在 TurnRecord.response (日志/checkpoint 可取)
    assert result.turns[0].response.content == PREFIX


async def test_truncation_tokens_count_toward_guard_budget() -> None:
    """截断轮的 token 同样计入 guard 预算 (#3 x #10 交叉).

    真实场景: 小窗口下模型反复截断同样烧 token —— 熔断由 token 预算兜底,
    不能因为「不是在调工具」就漏计.
    """
    model = ScriptedModel(
        [
            _length_response(PREFIX, usage=Usage(total_tokens=40)),
            _length_response(PREFIX, usage=Usage(total_tokens=40)),
            text_response(SUFFIX),  # 不会走到: 第 2 轮后即被预算拦住
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[ECHO_TOOL],
        max_truncations=5,
        guard=LoopGuard(max_turns=10, max_total_tokens=80),
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.TOKEN_BUDGET
    assert result.truncation_count == 2  # 两次截断的 token 都被计入
    assert result.total_tokens == 80
    assert len(model.calls) == 2  # 未发起第 3 次
    assert result.content is None  # 刹车停, 无最终答复


# ---------------------------------------------------------------------------
# 截断次数用尽 (TRUNCATION_LIMIT)
# ---------------------------------------------------------------------------


async def test_truncation_limit_stops_repeated_length() -> None:
    """连续截断超过 max_truncations → 放弃, 不再无限续写烧 token (#3/#10)."""
    model = ScriptedModel(
        [_length_response("第 1 次截断"), _length_response("第 2 次截断")]
    )
    loop = AgentLoop(
        model=model,
        tools=[ECHO_TOOL],
        max_truncations=1,
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.TRUNCATION_LIMIT
    assert result.truncation_count == 2  # 第 2 次截断时超过上限 1
    assert result.turn_count == 2  # 每次截断响应消耗一轮
    assert result.content is None  # 最终没有完整答案
    assert len(model.calls) == 2  # 不再发起第 3 次调用
    # 最后一次截断内容仍保真入史 (可人工续写/降级用)
    assert result.messages[-1]["content"] == "第 2 次截断"


# ---------------------------------------------------------------------------
# length 与 tool_calls 并存
# ---------------------------------------------------------------------------


async def test_tool_calls_take_priority_over_length() -> None:
    """length + 完整 tool_calls: 工具路径优先执行, 不走截断处理 (#10)."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "hi"}'),
                finish_reason=FinishReason.LENGTH,  # 截断发生在工具调用上
            ),
            text_response("回声执行完成"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    result = await loop.run([dict(USER_MSG)])

    # 工具的 arguments 完整 → 正常执行并回填; length 不作为截断处理
    assert result.outcome is LoopOutcome.FINISHED
    assert result.truncation_count == 0
    assert result.messages[2]["role"] == "tool"
    assert result.messages[2]["content"] == "echo:hi"
    assert result.content == "回声执行完成"
