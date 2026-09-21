"""loop 事件接线测试 (#4 #11, 契约定稿于终局事件规则).

场景 → 断言:
- 单工具 / 并行工具 run 的**完整事件序列** (核心验收 seam: 事件序列断言)
- thinking 与 final 的文本边界 (已定案): 非终止轮 (工具轮) 的助手
  正文归 thinking (过程叙述, 不进最终答案), 终止轮正文才是 final
- reasoning 旁路通道: 独立事件 + 不混入 content, 但 wire 历史仍回填
  reasoning_content (#11 + 修正后的契约: 两个「历史」要分清)
- 终局事件选择规则 (已定案): 正常结束 → final; guard 刹车
  (max_turns / token 预算 / wall-clock / 截断超限) 与 content_filter /
  上游中断 → error (code 取 LoopOutcome 值), 且**不发 final**
- delta 与 final 的权威性: final.content 是权威值 (与 LoopResult.content
  一致); CONDENSE 丢弃的截断前缀不进 final
- 工具失败 / 未知工具 → tool_result(status=error, 可操作错误文本 #2)
- hooks 五个观察点的时机与载荷; before_turn 注入的消息被模型看到 (插件挂载
  语义); 插件抛异常不影响 run 完成 (异常隔离)
- **拦截点 (before_tool_execute)**: 插件拒绝 → 工具一次都不跑 + 拒绝原因回填
  模型 + 事件流仍是一条正常的失败结果; 全部放行 / 无人注册 → 与从前逐字一样;
  护栏自己坏了按拒绝处理 (fail closed); 取消不被拦截挡住 (玩具业务的完整一跑
  在最后一条: 一条「写操作最多 3 次」的护栏)

载体工具就地定义 (Seam 3); 模型为 ScriptedModel (Seam 1). 事件经 event_sink
收集 (同步 append), 断言序列与逐字段载荷.

大白话版 (这份「验货单」在验什么):
- 主循环真的按规矩喊话吗: 一轮带工具的问答应该喊成「我在想 → 我要去查 →
  查回来了 → 我答完了」, 且每句话的措辞/编号/轮次都对.
- 过程叙述与最终答案有没有串线: 工具轮的交代话只进「我在想」, 最终答案只在
  「我答完了」里出现一次.
- 用户的「心理活动」(reasoning) 有没有混进答案: 没有, 它单独一条通道; 但它
  仍按官方要求回填给模型 (两条通道互不干扰).
- 被刹车 / 被拦截 / 上游中断时喊的是不是「出问题了」而不是「答完了」(没答案
  就不许说答完了); 模型给了空答复则仍算正常结束.
- 插件坏了、槽位没插满时, 主循环还能不能照常把活干完.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from doubles import FakeClock
from mock_llm import ScriptedModel, make_tool_call, text_response, tool_call_response
from snapshots import project_events

from CharAgent.agent import AgentLoop, LoopGuard, LoopOutcome, TruncationStrategy
from CharAgent.agent.utils.events import TERMINAL_ERROR_TEXT
from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.model.utils.types import FinishReason, ModelResponse, Usage
from CharAgent.stream import EventType, StreamEvent
from CharAgent.tool import Tool, ToolActionableError, tool

USER_MSG = {"role": "user", "content": "订单 20260701123456 到哪了"}

# ---------------------------------------------------------------------------
# 载体工具 (模块顶层, 类型需顶层可见)
# ---------------------------------------------------------------------------


def _echo(
    message: str,
) -> str:
    """回声载体: 返回收到的消息."""
    return f"echo:{message}"


def _require_order(
    order_no: str,
) -> str:
    """校验型载体: 参数不合规时抛可操作错误 (#2, 事件里要能看到怎么改)."""
    if len(order_no) != 14:
        raise ToolActionableError(
            f"order_no 应为 14 位数字, 实际 {len(order_no)} 位: {order_no!r}"
        )
    return f"订单 {order_no} 已发货"


def _big() -> str:
    """超长返回载体: 验证摘要截断 (事件流轻量化)."""
    return "x" * 5000


ECHO_TOOL = tool(_echo, name="echo")
ORDER_TOOL = tool(_require_order, name="require_order")
BIG_TOOL = tool(_big, name="big")

# 「调一次 echo 再答一句」的脚本 (多处用例共用; ScriptedModel 会把列表拷一份,
# 所以常量可以放心重复使用). 要带 usage 或改答复的用例各自写自己的脚本.
ECHO_SCRIPT = [
    tool_call_response(make_tool_call("echo", '{"message": "hi"}')),
    text_response("回声: hi"),
]


class _Collector:
    """事件收集 sink (同步): 供事件序列与载荷断言."""

    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    def __call__(self, event: StreamEvent) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.type.value for e in self.events]

    def of(self, event_type: EventType) -> list[StreamEvent]:
        return [e for e in self.events if e.type is event_type]

    def type_finals(self) -> list[str]:
        """终局事件类型 (final / error), 应为恰好一个."""
        return [
            e.type.value
            for e in self.events
            if e.type in (EventType.FINAL, EventType.ERROR)
        ]


# ---------------------------------------------------------------------------
# 事件序列 (核心验收 seam)
# ---------------------------------------------------------------------------


async def test_single_tool_run_event_sequence() -> None:
    """单工具 run 的完整序列: thinking → tool_call → tool_result → final."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("require_order", '{"order_no": "20260701123456"}'),
                content="让我先查一下订单",
            ),
            text_response("订单已发货"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, tools=[ORDER_TOOL], event_sink=sink)
    result = await loop.run([dict(USER_MSG)])

    assert sink.types() == ["thinking", "tool_call", "tool_result", "final"]
    assert [e.seq for e in sink.events] == [1, 2, 3, 4]

    # thinking: 工具轮正文 (过程叙述), 带轮次
    assert sink.events[0].data == {"message": "让我先查一下订单", "turn": 1}
    # tool_call: 参数保真为原始 JSON 字符串 (#10 框架不预解析)
    assert sink.events[1].data == {
        "tool_call_id": "call_require_order",
        "tool_name": "require_order",
        "arguments": '{"order_no": "20260701123456"}',
        "status": "started",
        "turn": 1,
    }
    # tool_result: 成功摘要 + 耗时
    tool_result = sink.events[2].data
    assert tool_result["tool_call_id"] == "call_require_order"
    assert tool_result["tool_name"] == "require_order"
    assert tool_result["status"] == "ok"
    assert tool_result["summary"] == "订单 20260701123456 已发货"
    assert tool_result["duration_ms"] >= 0
    assert tool_result["turn"] == 1
    # final: content 为权威值 (与 LoopResult.content 一致)
    final = sink.events[3].data
    assert final["content"] == "订单已发货" == result.content
    assert final["finish_reason"] == "stop"
    assert final["outcome"] == "finished"
    assert final["tokens"] == 0  # 脚本未带 usage


async def test_parallel_tool_events_in_call_order() -> None:
    """并行工具: 同一轮多条 tool_call, 结果事件按**调用顺序**(非完成序)产出."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "a"}', call_id="call_1"),
                make_tool_call("echo", '{"message": "b"}', call_id="call_2"),
            ),
            text_response("两个都完成"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, tools=[ECHO_TOOL], event_sink=sink)
    await loop.run([dict(USER_MSG)])

    # 无叙述 → 无 thinking (不产空事件)
    assert sink.types() == [
        "tool_call",
        "tool_call",
        "tool_result",
        "tool_result",
        "final",
    ]
    assert [e.data["tool_call_id"] for e in sink.of(EventType.TOOL_CALL)] == [
        "call_1",
        "call_2",
    ]
    assert [e.data["tool_call_id"] for e in sink.of(EventType.TOOL_RESULT)] == [
        "call_1",
        "call_2",
    ]
    assert [e.data["summary"] for e in sink.of(EventType.TOOL_RESULT)] == [
        "echo:a",
        "echo:b",
    ]


async def test_tool_result_summary_truncated() -> None:
    """超长工具结果只进摘要 (事件流轻量化; 全文在 wire 历史里, 不丢)."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("big")),
            text_response("完成"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, tools=[BIG_TOOL], event_sink=sink)
    result = await loop.run([dict(USER_MSG)])

    summary = sink.of(EventType.TOOL_RESULT)[0].data["summary"]
    assert summary == "x" * 200 + "..."
    # 全文仍回填进历史 (保真不丢, #10) —— 事件截断只影响展示通道
    assert any(m.get("content") == "x" * 5000 for m in result.messages)


# ---------------------------------------------------------------------------
# thinking / final 的文本边界
# ---------------------------------------------------------------------------


async def test_tool_turn_narration_goes_to_thinking_only() -> None:
    """两个工具轮各有叙述 → 两条 thinking; 终止轮正文只进 final (第 7 项定案)."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "1"}'), content="先查第一步"
            ),
            tool_call_response(
                make_tool_call("echo", '{"message": "2"}'), content="再查第二步"
            ),
            text_response("最终答复"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, tools=[ECHO_TOOL], event_sink=sink)
    result = await loop.run([dict(USER_MSG)])

    assert [e.data["message"] for e in sink.of(EventType.THINKING)] == [
        "先查第一步",
        "再查第二步",
    ]
    assert [e.data["turn"] for e in sink.of(EventType.THINKING)] == [1, 2]
    # 最终答复只在 final 里出现一次 (不重复进 thinking)
    assert sink.of(EventType.FINAL)[0].data["content"] == "最终答复"
    assert result.content == "最终答复"


# ---------------------------------------------------------------------------
# reasoning 旁路通道 (#11)
# ---------------------------------------------------------------------------


async def test_reasoning_is_separate_channel_but_backfilled_in_history() -> None:
    """reasoning: 独立事件 + 不混入 content; wire 历史仍回填 reasoning_content."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "hi"}'),
                reasoning="先核对参数",
            ),
            text_response("回声: hi", reasoning="现在可以作答了"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, tools=[ECHO_TOOL], event_sink=sink)
    result = await loop.run([dict(USER_MSG)])

    # 两条 reasoning 事件 (每次响应一条完整增量), 各自带轮次
    assert [e.data["delta"] for e in sink.of(EventType.REASONING)] == [
        "先核对参数",
        "现在可以作答了",
    ]
    assert [e.data["turn"] for e in sink.of(EventType.REASONING)] == [1, 2]
    # 不混入正文: final.content 与 thinking.message 都不含思维链
    final = sink.of(EventType.FINAL)[0].data["content"]
    assert final == "回声: hi"
    assert all(
        "核对参数" not in e.data.get("message", "") for e in sink.of(EventType.THINKING)
    )
    # 但 wire 历史照旧回填 (修正后契约: 模型侧上下文与前端展示
    # 分属两条通道, 前者必须带 reasoning_content)
    assert result.messages[1]["reasoning_content"] == "先核对参数"
    assert result.messages[-1]["reasoning_content"] == "现在可以作答了"


# ---------------------------------------------------------------------------
# 终局事件选择规则 (已定案)
# ---------------------------------------------------------------------------


async def test_max_turns_brake_emits_error_not_final() -> None:
    """max_turns 刹车: 发 error(code=max_turns), 不发 final (没有答复就没有答复事件)."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("echo", '{"message": "1"}')),
            tool_call_response(make_tool_call("echo", '{"message": "2"}')),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(
        model=model, tools=[ECHO_TOOL], guard=LoopGuard(max_turns=2), event_sink=sink
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.MAX_TURNS
    assert result.content is None
    assert sink.type_finals() == ["error"]
    assert sink.of(EventType.ERROR)[0].data["error"]["code"] == "max_turns"
    assert sink.of(EventType.ERROR)[0].data["error"]["message"]


async def test_token_budget_brake_emits_error() -> None:
    """token 预算刹车: error(code=token_budget)."""
    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "1"}'),
                usage=Usage(total_tokens=30),
            ),
            text_response("不该被问到"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(
        model=model,
        tools=[ECHO_TOOL],
        guard=LoopGuard(max_total_tokens=10),
        event_sink=sink,
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.TOKEN_BUDGET
    assert sink.of(EventType.ERROR)[0].data["error"]["code"] == "token_budget"
    assert len(model.calls) == 1  # 超限后不再发起模型调用


async def test_time_limit_brake_emits_error() -> None:
    """wall-clock 刹车: error(code=time_limit) (固定时钟, 零抖动)."""
    clock = FakeClock()

    async def slow_step(messages: list[dict[str, Any]]) -> ModelResponse:
        """脚本元素: 把时钟推过预算, 返回工具轮让 loop 走到 guard 判定点."""
        clock.now = 6.0
        return tool_call_response(make_tool_call("echo", '{"message": "x"}'))

    sink = _Collector()
    loop = AgentLoop(
        model=ScriptedModel([slow_step]),
        tools=[ECHO_TOOL],
        guard=LoopGuard(max_duration_seconds=5.0, time_source=clock),
        event_sink=sink,
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.TIME_LIMIT
    assert sink.of(EventType.ERROR)[0].data["error"]["code"] == "time_limit"


async def test_truncation_limit_emits_error() -> None:
    """截断重试超限: error(code=truncation_limit), 不发 final."""
    model = ScriptedModel(
        [
            text_response(f"第 {i} 段", finish_reason=FinishReason.LENGTH)
            for i in range(3)
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, max_truncations=2, event_sink=sink)
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.TRUNCATION_LIMIT
    assert result.content is None
    assert sink.of(EventType.ERROR)[0].data["error"]["code"] == "truncation_limit"


async def test_content_filter_emits_error() -> None:
    """finish_reason=content_filter: 被安全策略拦截 → error, 不发 final."""
    model = ScriptedModel(
        [text_response(None, finish_reason=FinishReason.CONTENT_FILTER)]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, event_sink=sink)
    result = await loop.run([dict(USER_MSG)])

    assert (
        result.outcome is LoopOutcome.FINISHED
    )  # loop 语义不变, 只是事件归类为异常终止
    assert result.finish_reason is FinishReason.CONTENT_FILTER
    assert sink.of(EventType.ERROR)[0].data["error"]["code"] == "content_filter"
    assert sink.of(EventType.FINAL) == []


async def test_stop_with_empty_content_still_final() -> None:
    """模型正常结束但没吐正文: 仍发 final (content=null), 不是 error.

    判据是「run 怎么结束的」而非「有没有正文」: 空答复属正常
    结束, 前端按空答复处理; 只有异常结束 (刹车 / 中断 / 被拦截) 才走 error.
    """
    model = ScriptedModel([text_response(None)])
    sink = _Collector()
    loop = AgentLoop(model=model, event_sink=sink)
    result = await loop.run([dict(USER_MSG)])

    assert result.outcome is LoopOutcome.FINISHED
    assert result.content is None
    assert sink.type_finals() == ["final"]
    assert sink.of(EventType.FINAL)[0].data["content"] is None


async def test_server_interrupted_emits_error() -> None:
    """上游中断 (资源不足): 半截正文不当答复 → error(code=server_interrupted)."""
    model = ScriptedModel(
        [
            text_response(
                "订单 20260701123456 已",
                finish_reason=FinishReason.INSUFFICIENT_SYSTEM_RESOURCE,
            )
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, event_sink=sink)
    await loop.run([dict(USER_MSG)])

    assert sink.type_finals() == ["error"]
    assert sink.of(EventType.ERROR)[0].data["error"]["code"] == "server_interrupted"


def test_terminal_error_text_covers_every_outcome() -> None:
    """终局错误文案表覆盖全部非正常结束原因 (漏一个就会 KeyError 在运行期炸)."""
    expected = {
        LoopOutcome.MAX_TURNS.value,
        LoopOutcome.TOKEN_BUDGET.value,
        LoopOutcome.TIME_LIMIT.value,
        LoopOutcome.TRUNCATION_LIMIT.value,
        LoopOutcome.SERVER_INTERRUPTED.value,
        "content_filter",  # FINISHED 分支里唯一走 error 的 finish_reason
    }
    assert set(TERMINAL_ERROR_TEXT) == expected
    assert all(text.strip() for text in TERMINAL_ERROR_TEXT.values())


# ---------------------------------------------------------------------------
# delta 与 final 的权威性 (#10)
# ---------------------------------------------------------------------------


async def test_continue_truncation_final_matches_joined_content() -> None:
    """CONTINUE 续写: final.content 是拼合后的完整答复 (权威值, 前端覆盖缓冲)."""
    model = ScriptedModel(
        [
            text_response("前半段", finish_reason=FinishReason.LENGTH),
            text_response("后半段"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, event_sink=sink)
    result = await loop.run([dict(USER_MSG)])

    assert result.content == "前半段后半段"
    assert sink.of(EventType.FINAL)[0].data["content"] == "前半段后半段"
    # 续写前缀是答案素材 (不是过程叙述), 不发 thinking —— 否则与 final 重复展示
    assert sink.of(EventType.THINKING) == []


async def test_condense_discards_prefix_from_final() -> None:
    """CONDENSE 丢弃的截断前缀不进 final (只累加会显示作废内容)."""
    model = ScriptedModel(
        [
            text_response("作废的前半段", finish_reason=FinishReason.LENGTH),
            text_response("精简后的答复"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(
        model=model, truncation=TruncationStrategy.CONDENSE, event_sink=sink
    )
    result = await loop.run([dict(USER_MSG)])

    assert result.content == "精简后的答复"
    assert sink.of(EventType.FINAL)[0].data["content"] == "精简后的答复"
    # 截断轮正文不发 thinking: 它是答案素材且已被丢弃, 推进事件流等于把作废内容
    # 展示给用户 (与「final 是权威值」同一考量)
    assert sink.of(EventType.THINKING) == []
    # P0 无 content delta 通道 (未实现 token 级流式), 故作废前缀不出现在任何事件里;
    # P1 接入流式后此断言随契约更新 (前端靠 final 覆盖缓冲)
    dumped = json.dumps([e.to_dict() for e in sink.events], ensure_ascii=False)
    assert "作废的前半段" not in dumped


# ---------------------------------------------------------------------------
# 工具失败 / 未知工具 (#2)
# ---------------------------------------------------------------------------


async def test_tool_actionable_error_in_tool_result_event() -> None:
    """工具报可操作错误: tool_result(status=error) 携带可操作文本 (#2)."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("require_order", '{"order_no": "123"}')),
            text_response("我明白了"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, tools=[ORDER_TOOL], event_sink=sink)
    await loop.run([dict(USER_MSG)])

    failed = sink.of(EventType.TOOL_RESULT)[0].data
    assert failed["status"] == "error"
    assert "14 位数字" in failed["error"]  # 说清期望, 而不是甩 422
    assert "summary" not in failed  # 失败时不带成功摘要


async def test_unknown_tool_in_tool_result_event() -> None:
    """模型幻觉调用不存在的工具: 同样以可操作错误回到事件流."""
    model = ScriptedModel(
        [
            tool_call_response(make_tool_call("ghost")),
            text_response("换一个工具"),
        ]
    )
    sink = _Collector()
    loop = AgentLoop(model=model, tools=[ECHO_TOOL], event_sink=sink)
    await loop.run([dict(USER_MSG)])

    failed = sink.of(EventType.TOOL_RESULT)[0].data
    assert failed["status"] == "error"
    assert "不存在工具" in failed["error"]
    assert "echo" in failed["error"]  # 附带可用工具清单


# ---------------------------------------------------------------------------
# hooks 触发点 (扩展点)
# ---------------------------------------------------------------------------


async def test_hooks_fire_at_documented_points_and_order() -> None:
    """单轮 run 的 hook 顺序: before_turn → on_model_call x2 → after_turn → on_event."""
    order: list[str] = []
    events: list[StreamEvent] = []

    def on_event(*, event: StreamEvent) -> None:
        order.append("on_event")
        events.append(event)

    registry = HookRegistry()
    registry.register(HookPoint.BEFORE_TURN, lambda **kw: order.append("before_turn"))
    registry.register(
        HookPoint.ON_MODEL_CALL,
        lambda **kw: order.append(f"on_model_call:{kw['phase']}"),
    )
    registry.register(HookPoint.ON_EVENT, on_event)
    registry.register(HookPoint.AFTER_TURN, lambda **kw: order.append("after_turn"))

    model = ScriptedModel([text_response("你好")])
    loop = AgentLoop(model=model, hooks=registry)
    await loop.run([dict(USER_MSG)])

    assert order == [
        "before_turn",
        "on_model_call:before",
        "on_model_call:after",
        "after_turn",  # 轮次收尾: 先全轮跑完
        "on_event",  # 终局 final 事件在最后一轮收尾之后产出
    ]
    assert [e.type for e in events] == [EventType.FINAL]


async def test_hook_payloads_carry_loop_state() -> None:
    """各点载荷携带 loop 状态 (插件据此记账 / 落库 / 采集 trace)."""
    seen: dict[str, list[dict[str, Any]]] = {
        "model": [],
        "tool": [],
        "turn": [],
    }
    registry = HookRegistry()
    registry.register(
        HookPoint.ON_MODEL_CALL,
        lambda **kw: seen["model"].append(kw),
    )
    registry.register(HookPoint.ON_TOOL_EXECUTED, lambda **kw: seen["tool"].append(kw))
    registry.register(HookPoint.AFTER_TURN, lambda **kw: seen["turn"].append(kw))

    model = ScriptedModel(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "hi"}'),
                usage=Usage(total_tokens=12),
            ),
            text_response("回声: hi"),
        ]
    )
    loop = AgentLoop(model=model, tools=[ECHO_TOOL], hooks=registry)
    await loop.run([dict(USER_MSG)])

    # 两轮, 每轮模型调用前后各触发一次 (before / after 各两次)
    assert [kw["phase"] for kw in seen["model"]] == [
        "before",
        "after",
        "before",
        "after",
    ]
    first_before = seen["model"][0]
    assert first_before["turn"] == 1
    assert first_before["tools"] == [ECHO_TOOL.to_spec()]  # 请求前能看到工具清单
    first_after = seen["model"][1]
    assert first_after["turn"] == 1
    assert first_after["usage"] == Usage(total_tokens=12)
    assert first_after["response"].finish_reason is FinishReason.TOOL_CALLS
    assert first_after["elapsed_ms"] >= 0
    # 工具执行完成 (结果已回填): 观测插件据此统计工具成功率
    assert len(seen["tool"]) == 1
    assert seen["tool"][0]["call"].name == "echo"
    assert seen["tool"][0]["execution"].ok is True
    # 轮次收尾: tokens 为本轮 usage 增量
    assert [(t["turn"], t["tokens"]) for t in seen["turn"]] == [(1, 12), (2, 0)]


async def test_before_turn_can_inject_messages_for_memory() -> None:
    """before_turn 拿到的 messages 是活引用: memory 插件注入的记忆被模型看到."""
    registry = HookRegistry()

    def inject(*, messages: list[dict[str, Any]], **kwargs: Any) -> None:
        messages.insert(0, {"role": "system", "content": "记忆: 用户是 VIP"})

    registry.register(HookPoint.BEFORE_TURN, inject)
    model = ScriptedModel([text_response("你好, VIP")])
    loop = AgentLoop(model=model, hooks=registry)
    result = await loop.run([dict(USER_MSG)])

    assert model.calls[0]["messages"][0]["content"] == "记忆: 用户是 VIP"
    assert result.messages[0]["content"] == "记忆: 用户是 VIP"


async def test_hook_exception_does_not_break_run() -> None:
    """插件抛异常: run 照常完成, 失败记录在 registry.failures (异常隔离)."""

    def broken(**kwargs: Any) -> None:
        raise RuntimeError("插件内部炸了")

    registry = HookRegistry()
    registry.register(HookPoint.BEFORE_TURN, broken)
    registry.register(HookPoint.AFTER_TURN, broken)

    model = ScriptedModel([text_response("照常答复")])
    loop = AgentLoop(model=model, hooks=registry)
    result = await loop.run([dict(USER_MSG)])

    assert result.content == "照常答复"
    assert [f.point for f in registry.failures] == [
        HookPoint.BEFORE_TURN,
        HookPoint.AFTER_TURN,
    ]


async def test_loop_runs_without_event_sink() -> None:
    """不接 sink 与 hooks 时: loop 行为与之前一致 (回归保护)."""
    model = ScriptedModel(ECHO_SCRIPT)
    loop = AgentLoop(model=model, tools=[ECHO_TOOL])
    result = await loop.run([dict(USER_MSG)])

    assert (result.outcome, result.content, result.turn_count) == (
        LoopOutcome.FINISHED,
        "回声: hi",
        2,
    )


# ---------------------------------------------------------------------------
# 拦截点 (before_tool_execute): 工具执行前请插件裁决
# ---------------------------------------------------------------------------


def _ledger_tool(calls: list[str]) -> Tool:
    """造一个「写操作」玩具工具: 每被执行一次就往 calls 里记一笔.

    用造工具函数而不是模块级常量, 是因为用例要断言「这个函数一次都没被调用」
    —— 计数用的列表得由用例自己拿着.
    """

    def write_entry(note: str) -> str:
        """往账本里记一笔.

        Args:
            note: 记什么.
        """
        calls.append(note)
        return f"已记账: {note}"

    return tool(write_entry, name="write_entry", annotations={"writes": True})


def _run_loop(
    script: list[Any],
    *,
    tools: list[Tool],
    hooks: HookRegistry | None = None,
    sink: _Collector | None = None,
) -> tuple[AgentLoop, _Collector]:
    """装配一次 run 的常用件 (脚本 + 工具 + 可选的插件与事件出口)."""
    collector = sink if sink is not None else _Collector()
    return (
        AgentLoop(
            model=ScriptedModel(script),
            tools=tools,
            hooks=hooks,
            event_sink=collector,
        ),
        collector,
    )


async def test_a_rejected_call_never_reaches_the_tool() -> None:
    """插件拒绝 → 工具函数一次都没被调用, 拒绝原因当作它的失败结果回填模型.

    三样都要成立 (缺一样这事儿就没闭环): 工具真的没跑 / 模型真收到了那句话 /
    事件流里它是一条正常的失败结果 (不是新增一种事件).
    """
    calls: list[str] = []
    seen_executions: list[Any] = []
    registry = HookRegistry()
    registry.register(
        HookPoint.BEFORE_TOOL_EXECUTE,
        lambda **kw: Decision.reject("账本今天封账了, 明天再来"),
    )
    registry.register(
        HookPoint.ON_TOOL_EXECUTED,
        lambda **kw: seen_executions.append(kw["execution"]),
    )
    loop, sink = _run_loop(
        [
            tool_call_response(make_tool_call("write_entry", '{"note": "买咖啡"}')),
            text_response("今天记不了了, 明天再记这笔"),
        ],
        tools=[_ledger_tool(calls)],
        hooks=registry,
    )

    result = await loop.run([dict(USER_MSG)])

    assert calls == [], "被拒的工具一次都不许执行"
    assert sink.types() == ["tool_call", "tool_result", "final"], "序列形状不变"
    assert [e.seq for e in sink.events] == [1, 2, 3], "seq 连续, 没有断号"

    failed = sink.of(EventType.TOOL_RESULT)[0].data
    assert failed["status"] == "error"
    assert failed["error"] == "账本今天封账了, 明天再来"
    assert "summary" not in failed, "被拒不是成功, 不带成功摘要"

    backfilled = [message for message in result.messages if message["role"] == "tool"]
    assert [message["content"] for message in backfilled] == [
        "账本今天封账了, 明天再来"
    ]
    assert result.content == "今天记不了了, 明天再记这笔", "模型看着这句话接着答"

    # 「执行完成」这条通道照常走: 被拒的调用同样产出失败态的 ToolExecution,
    # 观测插件 (记账 / 审计) 因此看得见每一次被拦下的调用
    assert [execution.ok for execution in seen_executions] == [False]


async def test_an_allowing_plugin_changes_the_event_stream_in_no_way() -> None:
    """插件全部放行 → 与没有插件时**逐字段一样** (事件序列 + 答复).

    拦截点接进主循环最怕的就是顺手改了既有形状 —— 这条用例把「放行 = 什么都没
    发生」钉死: 同一份脚本跑两遍 (一遍不插插件, 一遍插一个不表态的), 事件流
    逐字段比对.
    """
    registry = HookRegistry()
    registry.register(HookPoint.BEFORE_TOOL_EXECUTE, lambda **kw: None)

    plain_loop, plain_sink = _run_loop(ECHO_SCRIPT, tools=[ECHO_TOOL])
    hooked_loop, hooked_sink = _run_loop(ECHO_SCRIPT, tools=[ECHO_TOOL], hooks=registry)

    plain = await plain_loop.run([dict(USER_MSG)])
    hooked = await hooked_loop.run([dict(USER_MSG)])

    assert project_events(hooked_sink.events) == project_events(plain_sink.events)
    assert (hooked.content, hooked.outcome, hooked.turn_count) == (
        plain.content,
        plain.outcome,
        plain.turn_count,
    )
    assert registry.failures == []


async def test_no_registration_is_exactly_the_old_behavior() -> None:
    """一个插件都没注册 → 与从前逐字一样 (逐字段投影比对).

    「空注册零开销」的另一半 (没有挂起点) 钉在注册表层: test_hooks.py 用事件
    循环探针断言空注册的 decide 不会挂起 —— 那是「没有挂起点」唯一测得准的地方;
    本层多出来的只是**一次同步返回的 await** (没有回调、没有调度), 与别的 hook
    点 (fire) 的既有做法一致.
    """
    bare_loop, bare_sink = _run_loop(ECHO_SCRIPT, tools=[ECHO_TOOL], hooks=None)
    empty_loop, empty_sink = _run_loop(
        ECHO_SCRIPT, tools=[ECHO_TOOL], hooks=HookRegistry()
    )

    bare = await bare_loop.run([dict(USER_MSG)])
    empty = await empty_loop.run([dict(USER_MSG)])

    assert project_events(empty_sink.events) == project_events(bare_sink.events)
    assert empty.content == bare.content


async def test_the_guardrail_sees_the_call_the_tool_and_the_turn() -> None:
    """插件拿得到判断所需的一切: 第几轮、要调什么、那个工具带着什么标记.

    「这个工具是不是写操作」由业务自己写在注解里, 框架不认识它 —— 插件读到
    的就是业务当初打上去的那份键值 (`Tool.annotations` 只透传不解释).
    """
    seen: list[dict[str, Any]] = []
    registry = HookRegistry()
    registry.register(
        HookPoint.BEFORE_TOOL_EXECUTE,
        lambda **kw: seen.append(kw) or None,  # 看一眼就放行
    )
    loop, _ = _run_loop(
        [
            tool_call_response(make_tool_call("write_entry", '{"note": "买咖啡"}')),
            text_response("记好了"),
        ],
        tools=[_ledger_tool([])],
        hooks=registry,
    )

    await loop.run([dict(USER_MSG)])

    (payload,) = seen
    assert payload["turn"] == 1
    assert payload["call"].name == "write_entry"
    assert payload["call"].arguments == '{"note": "买咖啡"}'
    assert payload["tool"].annotations == {"writes": True}, "业务打的标记原样到手"
    assert payload["tool"].name == "write_entry"


async def test_an_unknown_tool_never_reaches_the_guardrail() -> None:
    """模型报一个不存在的工具名: 不给插件裁决的机会 (没有可拦的东西).

    那道门管的是「真实存在、但这次不许跑」的工具; 一个不存在的工具本来也不会
    执行 —— 顺手还省掉了一次插件调用 (插件因此不必处理 tool 为空的情况).
    """
    seen: list[str] = []
    registry = HookRegistry()
    registry.register(
        HookPoint.BEFORE_TOOL_EXECUTE,
        lambda *, call, **kw: seen.append(call.name) or None,
    )
    loop, sink = _run_loop(
        [tool_call_response(make_tool_call("ghost")), text_response("换一个工具")],
        tools=[ECHO_TOOL],
        hooks=registry,
    )

    await loop.run([dict(USER_MSG)])

    assert seen == [], "不存在的工具不该打扰拦截插件"
    assert "不存在工具" in sink.of(EventType.TOOL_RESULT)[0].data["error"]


async def test_a_broken_guardrail_fails_closed_at_the_loop() -> None:
    """护栏自己抛异常 → 这次调用被拦下 (fail closed), 而不是悄悄放行.

    与观察点的「插件坏了照常跑完」是两条相反的规矩: 护栏坏了按拒绝处理, 用户
    看到的是「这一步没做成」而不是一次没人拦的写操作; 故障本身记在 failures
    里等运维去看.
    """

    def broken(**kwargs: Any) -> Decision:
        raise RuntimeError("护栏内部炸了")

    calls: list[str] = []
    registry = HookRegistry()
    registry.register(HookPoint.BEFORE_TOOL_EXECUTE, broken)
    loop, sink = _run_loop(
        [
            tool_call_response(make_tool_call("write_entry", '{"note": "买咖啡"}')),
            text_response("这一步没能做成"),
        ],
        tools=[_ledger_tool(calls)],
        hooks=registry,
    )

    result = await loop.run([dict(USER_MSG)])

    assert calls == [], "护栏坏了也不许放行"
    failed = sink.of(EventType.TOOL_RESULT)[0].data
    assert failed["status"] == "error"
    assert "拦截插件执行出错" in failed["error"]
    assert result.outcome is LoopOutcome.FINISHED, "run 本身照常收尾"
    assert [type(failure.error).__name__ for failure in registry.failures] == [
        "RuntimeError"
    ]


async def test_interception_never_blocks_the_kill_switch() -> None:
    """取消不被拦截点挡住 (#3): 插件挂在半路时 cancel 照样即时中断.

    从**真取消路径**触发 (create_task + cancel, 与 test_loop_guard.py 同款):
    一个卡住的护栏插件不能变成用户按不动的停止按钮.
    """
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def stuck(**kwargs: Any) -> Decision:
        entered.set()
        await gate.wait()  # 永远等不到: 取消必须从这里打断它
        return Decision.allow()

    registry = HookRegistry()
    registry.register(HookPoint.BEFORE_TOOL_EXECUTE, stuck)
    loop, _ = _run_loop(
        [tool_call_response(make_tool_call("write_entry", '{"note": "买咖啡"}'))],
        tools=[_ledger_tool([])],
        hooks=registry,
    )
    task = asyncio.create_task(loop.run([dict(USER_MSG)]))

    await entered.wait()  # 已经进了拦截点
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert registry.failures == [], "取消不是插件失败, 不入 failures"
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert pending == [], "取消传播干净, 没有留下后台任务"


async def test_a_write_budget_guardrail_stops_the_last_call() -> None:
    """玩具业务的完整一跑: 一条「写操作最多 3 次」的护栏, 第 4 次被拦下.

    这是拦截点的**真实用法** (业务侧写法的最小样板, 与 issue 12 的护栏同形):
    工具自己带 `writes` 标记, 插件用它认人并记数, 框架全程不知道 `writes` 是
    什么意思. 断言落在真事件序列上 —— 前三次 tool_result 是成功, 第四次是失败,
    且那个写函数总共只跑了三次.
    """
    calls: list[str] = []
    registry = HookRegistry()
    budget = 3
    used = 0

    def guardrail(*, tool: Tool, **kwargs: Any) -> Decision | None:
        nonlocal used
        if not tool.annotations.get("writes"):
            return None  # 只读操作不管
        if used >= budget:
            return Decision.reject(
                f"这次对话里已经记了 {budget} 笔, 请让用户在页面上继续"
            )
        used += 1
        return None

    registry.register(HookPoint.BEFORE_TOOL_EXECUTE, guardrail)
    script: list[Any] = [
        tool_call_response(make_tool_call("write_entry", f'{{"note": "第 {n} 笔"}}'))
        for n in range(1, budget + 2)
    ] + [text_response("后面几笔记不下了")]
    loop, sink = _run_loop(script, tools=[_ledger_tool(calls)], hooks=registry)

    result = await loop.run([dict(USER_MSG)])

    assert calls == ["第 1 笔", "第 2 笔", "第 3 笔"], "超预算那一笔真没执行"
    statuses = [e.data["status"] for e in sink.of(EventType.TOOL_RESULT)]
    assert statuses == ["ok", "ok", "ok", "error"]
    assert "已经记了 3 笔" in sink.of(EventType.TOOL_RESULT)[-1].data["error"]
    assert result.content == "后面几笔记不下了", "模型收到拒绝原因后继续作答"
