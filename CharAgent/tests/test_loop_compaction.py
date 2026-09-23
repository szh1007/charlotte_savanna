"""上下文压缩用例 (difficulties #7): 账本 / 视图分离.

被测的三层, 从纯到脏:
- **估算** (AnchorTokenCounter): 锚 (上游给的 input_tokens) 赢了就用锚, 否则
  字符启发式 —— 权威值只有 API 给的 usage, 估算只用来判阈值
- **策略** (TrimAndSummarize): 三件套 (窗口裁剪 / 工具结果截断 / 滚动摘要) 与
  四条硬不变量 (切点只落在提问处 / system 不裁 / 至少留 1 个提问 /
  视图仍是合法 wire 序列)
- **接缝** (AgentLoop + ChatSession): `state.history` 一个字不动, 送给模型的
  是投影出来的副本; 事件与快照按压缩前后的真实情况落

断言「第 N 次调用实际送进 generate 的 messages」靠 MockLLM 的异步工厂
(脚本元素可以是 `async (messages) -> ModelResponse`), 而不是看返回值 ——
视图是内部产物, 只有从模型的视角才看得见.

大白话版 (这份「验货单」在验什么):
- 越聊越短: 老的那几段被摘要取代、老工具结果被截短, 而最近那个提问原样保留.
- 结构没坏: 裁的刀口永远落在「一个提问」的位置, 绝不会出现「assistant 的
  tool_calls 留下了、它的 tool 消息被裁掉」这种会让上游报 400 的序列.
- 账本没动: 压缩只影响送模型的那一份 (视图); 历史 (账本) 照旧 append-only,
  所以断点续跑的存档仍是全量、回溯仍是全量.
- 压不动 / 摘要失败时不拖垮这句问话: 降级为纯裁剪, 并且如实说一声.
"""

from __future__ import annotations

from typing import Any

import pytest
from doubles import EventCollector
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.agent import (
    AgentLoop,
    CompactionConfigError,
    LoopConfigError,
    LoopGuard,
)
from CharAgent.agent.compaction import (
    AnchorTokenCounter,
    CompiledView,
    TrimAndSummarize,
)
from CharAgent.agent.utils.messages import MESSAGE_OVERHEAD_TOKENS, estimate_tokens
from CharAgent.checkpoint.memory import InMemoryCheckpointSaver
from CharAgent.model.utils.types import ModelMessage, ModelResponse, Usage
from CharAgent.stream.utils.types import EventType
from CharAgent.tool import tool

SYSTEM: ModelMessage = {"role": "system", "content": "你是商城客服"}


def _long_lookup(order_no: str) -> str:
    """查订单: 回一段很长的物流记录 (用来把账本顶过阈值).

    Args:
        order_no: 订单号.
    """
    return f"{order_no} 的物流记录: " + "订" * 400


BIG_TOOL = tool(_long_lookup, name="query_order")


def _user(text: str) -> ModelMessage:
    """一条用户提问 (也是「一段对话」的分界: 切点只落在这里)."""
    return {"role": "user", "content": text}


def _answer(text: str) -> ModelMessage:
    """一条普通助手决策 (不调工具, 直接作答)."""
    return {"role": "assistant", "content": text}


def _tool_decision(text: str, *, call_id: str) -> list[ModelMessage]:
    """一次**工具决策**: 带 tool_calls 的 assistant + 它的工具结果 (配对的一对).

    这对消息是压缩最容易切坏的地方 —— 刀口落在它中间, 上游直接 400 (#10).
    """
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "query_order", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call_id, "content": text},
    ]


def _unit(index: int, *, size: int) -> list[ModelMessage]:
    """一段对话: 提问 + 为该提问做的两次决策 (先查订单, 再作答)."""
    call_id = f"call_{index}"
    return [
        _user(f"第 {index} 个问题: 订单到哪了"),
        *_tool_decision("订" * size, call_id=call_id),
        _answer("已发货"),
    ]


def _history(*sizes: int) -> list[ModelMessage]:
    """一段账本: system + 每个 size 造一段对话 (段数 = 参数个数)."""
    history = [SYSTEM]
    for index, size in enumerate(sizes, start=1):
        history.extend(_unit(index, size=size))
    return history


def _wire_legal(messages: list[ModelMessage]) -> bool:
    """这份序列在 wire 上合法吗: tool 消息必须跟在**开了它的** assistant 后面.

    顺带检查「一批工具结果中间不许插别的消息」—— 插了同样断配对.
    """
    pending: set[str] = set()
    for message in messages:
        role = message.get("role")
        if pending and role != "tool":
            return False
        if role == "assistant":
            pending = {call.get("id") for call in message.get("tool_calls") or []}
        elif role == "tool":
            if message.get("tool_call_id") not in pending:
                return False
            pending.discard(message.get("tool_call_id"))
    return True


def _tool_bodies(messages: list[ModelMessage]) -> list[str]:
    """视图里所有工具结果的正文 (断言截断用)."""
    return [str(m.get("content")) for m in messages if m.get("role") == "tool"]


def _policy(**overrides: Any) -> TrimAndSummarize:
    """一档测试参数 (阈值小、水位线低, 便于用小样本造出压缩)."""
    fields: dict[str, Any] = {
        "threshold_tokens": 400,
        "keep_recent_questions": 1,
        "watermark_ratio": 0.5,
        "tool_result_limit": 20,
        "summary_max_tokens": 128,
    }
    fields.update(overrides)
    return TrimAndSummarize(**fields)


async def _apply(
    policy: TrimAndSummarize,
    history: list[ModelMessage],
    *,
    summary: str | None = None,
    summary_covers: int = 0,
    summarizer: MockLLM | None = None,
) -> CompiledView:
    """跑一次投影 (估算器每次新建: 用例关心策略本身, 不管锚的延续)."""
    return await policy.apply(
        history,
        summary=summary,
        summary_covers=summary_covers,
        counter=AnchorTokenCounter(),
        summarizer=summarizer,
    )


# ---------------------------------------------------------------------------
# 估算: 锚式 (权威值来自 usage) + 字符启发式 (增量)
# ---------------------------------------------------------------------------


def test_estimate_counts_cjk_per_char_and_ascii_per_quarter() -> None:
    """字符启发式: 中文一字 ≈ 一 token, 其余四字符 ≈ 一 token (+ 每条固定开销).

    为什么分两种口径: 中文与英文的「字符 → token」差一倍, 一个固定比值放到
    两种文本上必然偏一边. (tiktoken 那类分词器算中文也不准, 多一个依赖只换来
    假精度 —— 业务要真精度就自己注入一个 TokenCounter.)
    """
    cjk = [{"role": "user", "content": "你好世界"}]
    ascii_text = [{"role": "user", "content": "abcdefgh"}]

    assert estimate_tokens(cjk) == MESSAGE_OVERHEAD_TOKENS + 4
    assert estimate_tokens(ascii_text) == MESSAGE_OVERHEAD_TOKENS + 2


def test_estimate_counts_tool_call_arguments_as_text() -> None:
    """工具调用的参数也是要发出去的文本 (算漏了会低估每一次调用的真实开销)."""
    plain = [_answer("")]
    calling = _tool_decision("", call_id="c1")[0]  # 带 tool_calls 的那条 assistant

    assert estimate_tokens([calling]) > estimate_tokens(plain)


def test_anchor_wins_over_the_estimate_for_the_anchored_prefix() -> None:
    """有锚时: 锚 (上游说的 input_tokens) + 之后新增那些消息的启发式.

    锚比启发式权威 —— 它含工具 schema / 系统提示等启发式根本看不见的固定开销.
    """
    counter = AnchorTokenCounter()
    history = [_user("甲" * 100)]
    assert counter.count(history) == estimate_tokens(history)

    counter.note_usage(Usage(input_tokens=500, total_tokens=520), message_count=1)

    assert counter.count(history) == 500  # 锚赢
    grown = [*history, _answer("乙" * 40)]
    assert counter.count(grown) == 500 + MESSAGE_OVERHEAD_TOKENS + 40


def test_missing_usage_leaves_the_anchor_where_it_was() -> None:
    """上游没给 usage (部分 mock / 流式) 时锚不动, 估算退回启发式."""
    counter = AnchorTokenCounter()
    counter.note_usage(None, message_count=1)
    counter.note_usage(Usage(output_tokens=9), message_count=1)  # 没有 input_tokens

    assert counter.count([_user("你好")]) == MESSAGE_OVERHEAD_TOKENS + 2


def test_counter_falls_back_to_the_estimate_for_a_shorter_list() -> None:
    """锚只覆盖「账本前 N 条」: 拿它量一份更短的列表 (比如压完的视图) 时用启发式."""
    counter = AnchorTokenCounter()
    counter.note_usage(Usage(input_tokens=9999), message_count=3)

    assert counter.count([_user("你好")]) == MESSAGE_OVERHEAD_TOKENS + 2


# ---------------------------------------------------------------------------
# 策略: 三件套 (裁剪 / 截断 / 摘要) 与硬不变量
# ---------------------------------------------------------------------------


async def test_under_the_threshold_nothing_is_touched() -> None:
    """没超阈值: 一个字都不动 (视图内容 = 账本, 不算压缩, 不发事件).

    Note:
        「视图内容 = 账本」只在**还没有摘要**时成立 (这条用例就是这种情形: summary
        是 None). 压过之后低于阈值的那一轮, 视图仍带着摘要 —— 那才是对的 (见
        `test_a_turn_that_does_not_cut_still_sends_the_view`), ticket 23 修的就是
        这个区别.
    """
    history = _history(50)
    compiled = await _apply(_policy(threshold_tokens=10_000), history)

    assert compiled.messages == history
    assert compiled.compacted is False
    assert compiled.dropped == 0
    assert compiled.truncated == 0
    assert compiled.summary is None


async def test_trimming_keeps_whole_questions_and_stays_wire_legal() -> None:
    """裁剪单位是**提问**: 刀口落在提问的位置, tool 消息永远跟着它的 assistant.

    这条是本片最要紧的不变量 —— 拆散配对, 上游直接 400 (见 #10).
    """
    history = _history(200, 200, 200)
    compiled = await _apply(_policy(), history)

    assert compiled.dropped > 0
    assert compiled.messages[0] == SYSTEM  # system 永不裁
    assert _wire_legal(compiled.messages)
    # 最新那个提问完整保留 (工具结果一个字不少)
    assert compiled.messages[-1] == history[-1]
    # 裁掉的段里不留「半截」: 活着的工具结果只剩最后一个提问的
    assert _tool_bodies(compiled.messages) == ["订" * 200]


async def test_the_first_message_is_never_dropped() -> None:
    """第 0 条 (system 提示) 永不裁 —— 裁了模型就不知道自己是干什么的."""
    history = _history(200, 200, 200, 200)
    compiled = await _apply(_policy(keep_recent_questions=3), history)

    assert compiled.messages[0] is history[0]


async def test_at_least_one_question_is_kept() -> None:
    """极端参数下也至少留最近 1 个提问 (裁到只剩 system, 等于把这段对话删了)."""
    history = _history(*([300] * 6))
    compiled = await _apply(
        _policy(keep_recent_questions=99, watermark_ratio=0.01), history
    )

    assert _tool_bodies(compiled.messages)  # 还有工具结果活着
    assert compiled.messages[-1] == history[-1]


async def test_tool_results_outside_the_latest_question_are_truncated() -> None:
    """老工具结果被截到阈值: 省 token 的主力在这里 (工具正文往往是大头)."""
    history = _history(500, 500, 500)
    compiled = await _apply(
        _policy(threshold_tokens=1000, keep_recent_questions=2, watermark_ratio=0.7),
        history,
    )

    bodies = _tool_bodies(compiled.messages)
    assert len(bodies) == 2
    assert bodies[-1] == "订" * 500  # 正在回答的那个提问不截
    assert bodies[0].startswith("订" * 20)  # 老的那条截到阈值
    assert len(bodies[0]) < 500
    assert compiled.truncated == 1


async def test_the_latest_question_tool_result_is_kept_whole() -> None:
    """正在回答的那个提问不截: 模型正要拿它答这一句, 截了就是答非所问."""
    history = _history(500, 500)
    compiled = await _apply(_policy(keep_recent_questions=1), history)

    assert compiled.messages[-1] == history[-1]


async def test_nothing_to_drop_means_no_compaction() -> None:
    """只有一个提问时压不动 (没得裁): 如实报告没压, 不凭空发事件."""
    history = _history(400)
    compiled = await _apply(_policy(threshold_tokens=10), history)

    assert compiled.compacted is False
    assert compiled.messages == history


async def test_trimming_stops_under_the_watermark() -> None:
    """压到水位线以下才停手 (压完还超线的话, 下一次调用又得压 = 白花钱)."""
    history = _history(*([400] * 6))
    policy = _policy(
        threshold_tokens=1000, keep_recent_questions=1, watermark_ratio=0.5
    )

    compiled = await _apply(policy, history)

    assert compiled.compacted is True
    assert compiled.estimated_tokens <= 1000 * 0.5


async def test_keeps_the_most_questions_that_fit_the_watermark() -> None:
    """能多留一个提问就多留一个: 到不了水位线才一个一个往下丢 (少压一点是一点)."""
    history = _history(*([400] * 6))
    policy = _policy(
        threshold_tokens=1000, keep_recent_questions=4, watermark_ratio=0.57
    )
    summarizer = MockLLM.scripted([text_response("早前聊的是查订单")])

    compiled = await _apply(policy, history, summarizer=summarizer)

    # 保留提问数被一个一个往下丢: 既不是配置的 4 个, 也不是兜底的 1 个
    assert 1 < len(_tool_bodies(compiled.messages)) < 4


async def test_compacting_twice_in_a_row_does_not_happen() -> None:
    """连压两次的用例: 刚压完紧接着的下一次调用不该再压.

    这一条同时钉住两件事 —— 水位线 (压完确实变小了) 与**锚** (上游回的是压完之后
    那个小请求的用量, 于是锚跟着落到小值; 只按启发式算的话, 一份很长的账本会
    每次都判定超阈值).

    Note:
        它只断了「不再切一刀」这半边; **另半边** (这一轮发出去的仍是视图、摘要还在
        里面) 见 `test_a_turn_that_does_not_cut_still_sends_the_view` —— 两者分开
        之前, 这半边正是 ticket 23 那个每隔一轮失效的循环.
    """
    counter = AnchorTokenCounter()
    policy = _policy(
        threshold_tokens=1000, keep_recent_questions=1, watermark_ratio=0.5
    )
    summarizer = MockLLM.scripted(
        [text_response("第一段摘要"), text_response("第二段摘要")]
    )
    history = _history(300, 300, 300, 300)

    first = await policy.apply(
        history,
        summary=None,
        summary_covers=0,
        counter=counter,
        summarizer=summarizer,
    )
    assert first.compacted is True
    # 这一次真的发出去了: 上游回的是压完之后那个小请求的用量 (锚就此落到小值)
    counter.note_usage(
        Usage(input_tokens=first.estimated_tokens), message_count=len(history)
    )

    grown = [*history, *_unit(9, size=100)]
    # 没有锚的话, 这份逐步变长的账本每次都会被判定超阈值 (启发式只看总量)
    assert AnchorTokenCounter().count(grown) >= 1000

    second = await policy.apply(
        grown,
        summary=first.summary,
        summary_covers=first.summary_covers,
        counter=counter,
        summarizer=summarizer,
    )

    assert second.compacted is False
    assert second.summary == first.summary  # 没重压, 摘要原样带回来


async def test_a_turn_that_does_not_cut_still_sends_the_view() -> None:
    """没切新刀的那一轮**也要投影**: 发出去的仍是「摘要 + 摘要之后的一切」.

    上一条断的是「刚压完别立刻再压」(滞回), 这一条断的是**另一半**: 不切 ≠ 不投影.
    两者分开之前, 真机上踩到的形状是 (2026-09-23, ticket 23; 八轮模拟: 2 压 3 跳、
    4 压 5 跳……):

      - 压完那一轮发的是小视图 → 上游回报的 input_tokens 小 → 锚落到小值
      - 下一轮估算 (小锚 + 增量) 低于阈值 → 判定「不用再切」→ 旧实现把**全量账本**
        原样发了出去, 摘要那条根本不带上, 视图就此每隔一轮失效一次
      - 而账本随即把锚顶回大值 → 再下一轮又超阈值 → 又切一刀 (又烧一次摘要)

    修法是两件事分家: **投影每轮都做** (视图 = system + 摘要 + 摘要没覆盖到的一切),
    「再切一刀」(扩大覆盖 + 重压摘要) 才需要阈值说话. 于是在这里断言: 这一轮没切
    (compacted=False), 但发出去的**不是**账本本身, 而且摘要还在里面.
    """
    counter = AnchorTokenCounter()
    policy = _policy(
        threshold_tokens=1000, keep_recent_questions=1, watermark_ratio=0.5
    )
    summarizer = MockLLM.scripted([text_response("早前聊的是查订单")])
    history = _history(300, 300, 300)

    first = await policy.apply(
        history,
        summary=None,
        summary_covers=0,
        counter=counter,
        summarizer=summarizer,
    )
    assert first.summarized is True
    counter.note_usage(
        Usage(input_tokens=first.estimated_tokens), message_count=len(history)
    )

    grown = [*history, *_unit(9, size=20)]
    assert counter.count(grown) < policy.threshold_tokens, "这一轮确实低于阈值"

    second = await policy.apply(
        grown,
        summary=first.summary,
        summary_covers=first.summary_covers,
        counter=counter,
        summarizer=summarizer,
    )

    assert second.compacted is False, "没切新刀 (既没裁也没截)"
    assert second.messages[0] is grown[0], "system 仍是原来那条 (身份)"
    assert any(
        "早前聊的是查订单" in str(message.get("content")) for message in second.messages
    ), "摘要必须还在视图里 —— 它是上一次压缩的成果, 不是一次性的"
    assert len(second.messages) < len(grown), "不能再把全量账本原样发出去"
    assert _wire_legal(second.messages)


async def test_scrolling_summary_feeds_the_previous_summary_back() -> None:
    """滚动摘要: 第二次压缩的输入含上一次摘要 (不是只压新掉的那段).

    只压新段的话, 更早的信息会被逐次稀释到消失 —— 每次压缩都只看见一小段.
    """
    history = _history(300, 300, 300, 300)
    summarizer = MockLLM.scripted(
        [text_response("第一段摘要"), text_response("第二段摘要")]
    )
    policy = _policy(keep_recent_questions=1)

    first = await _apply(policy, history, summarizer=summarizer)
    assert first.summary == "第一段摘要"
    assert first.summarized is True

    longer = [*history, *_unit(9, size=300)]
    second = await _apply(
        policy,
        longer,
        summary=first.summary,
        summary_covers=first.summary_covers,
        summarizer=summarizer,
    )

    assert second.summary == "第二段摘要"
    assert "第一段摘要" in summarizer.calls[-1]["messages"][-1]["content"]
    assert second.summary_covers > first.summary_covers


async def test_summary_failure_degrades_to_trim_only() -> None:
    """摘要失败 → 纯裁剪: 这一句照样答得出来, 且如实报出降级原因 (不静默)."""
    history = _history(300, 300, 300)
    broken = MockLLM.scripted([])  # 一调就抛 (脚本耗尽)

    compiled = await _apply(_policy(), history, summarizer=broken)

    assert compiled.compacted is True  # 裁剪照做
    assert compiled.summarized is False
    assert compiled.warning is not None
    assert _wire_legal(compiled.messages)


async def test_a_failed_summary_does_not_advance_summary_covers() -> None:
    """摘要失败不推进 summary_covers: 那段还没进过摘要, 下次压缩还得带上它."""
    history = _history(300, 300, 300)
    broken = MockLLM.scripted([])

    compiled = await _apply(_policy(), history, summarizer=broken)

    assert compiled.summary is None
    assert compiled.summary_covers == 0


async def test_no_summarizer_degrades_to_trim_only() -> None:
    """没有可用的摘要模型时也只裁剪 (而不是抛异常): 能力缺失不该拦下这句问话."""
    history = _history(300, 300, 300)

    compiled = await _apply(_policy(), history, summarizer=None)

    assert compiled.compacted is True
    assert compiled.summarized is False
    assert compiled.warning is not None


async def test_summarizing_can_be_switched_off() -> None:
    """摘要关掉时只做裁剪与截断: 一次模型调用都不发, 也不留降级原因.

    与上一条的区别是**性质**: 那条是能力缺失 (记 warning), 这条是配置选择 ——
    warning 那个字段说的是「本来要摘要却没成」, 被一个开关长期占着的话, 前端
    每一次压缩都会显示一句降级说明.
    """
    history = _history(300, 300, 300)
    summarizer = _stub_summarizer("这段不该被压出来")

    compiled = await _apply(_policy(summarize=False), history, summarizer=summarizer)

    assert compiled.compacted is True
    assert compiled.summarized is False
    assert compiled.warning is None
    assert summarizer.calls == [], "关掉 = 连一次摘要调用都不发"


async def test_switching_the_summary_off_keeps_the_previous_one() -> None:
    """关掉摘要 ≠ 丢掉已有摘要: 它覆盖的那段已被裁掉, 拿掉它等于让历史凭空消失.

    所以关的是「以后不再生成」, 视图里那条摘要照旧在位 (covers 也不动).
    """
    history = _history(300, 300, 300, 300)

    compiled = await _apply(
        _policy(summarize=False),
        history,
        summary="早前聊的是查订单",
        summary_covers=3,
    )

    assert compiled.summary == "早前聊的是查订单"
    assert compiled.summary_covers == 3
    assert any("早前聊的是查订单" in str(m.get("content")) for m in compiled.messages)


async def test_the_summary_call_never_thinks_and_gets_its_own_budget() -> None:
    """摘要那一次调用**固定关思考**, 并用自己那份预算 (2026-09-23 真机踩出来的).

    不钉死思考时它落回上游默认 (开启): 推理先把预算花光, 正文只剩空 —— 真机上的
    表现是「摘要模型没有给出正文, 本次只做裁剪」每次都出现, 也就是压缩的第三件套
    在生产里**从没生效过**. 这两条都是契约: 关掉不该想的那一半, 预算给足.
    """
    history = _history(300, 300, 300)
    summarizer = _stub_summarizer("早前聊的是查订单")

    compiled = await _apply(_policy(), history, summarizer=summarizer)

    assert compiled.summarized is True, "这一次调用真的产出了摘要"
    call = summarizer.calls[-1]
    assert call["thinking"] is False, "摘要调用必须显式关掉思考"
    assert call["max_tokens"] == _policy().summary_max_tokens


def test_the_summary_budget_default_leaves_room_for_a_rolling_summary() -> None:
    """默认预算是 1024 (2026-09-23 从 512 调上来, 与「固定关思考」配套).

    它是**滚动**摘要的长度上限: 上一条摘要连新裁掉的段一起重压, 所以这个数既决定
    单次压缩装多少, 也决定摘要最终能长到多大. 拍过的数, 往回降等于把刚修好的那条
    路又堵上.
    """
    assert TrimAndSummarize().summary_max_tokens == 1_024


def test_policy_rejects_a_nonsense_configuration() -> None:
    """构造期校验 (与 LoopGuard / AgentLoop 同一条纪律: 配置错在装配时报)."""
    with pytest.raises(CompactionConfigError):
        TrimAndSummarize(threshold_tokens=0)
    with pytest.raises(CompactionConfigError):
        TrimAndSummarize(keep_recent_questions=0)
    with pytest.raises(CompactionConfigError):
        TrimAndSummarize(watermark_ratio=1.0)
    with pytest.raises(CompactionConfigError):
        TrimAndSummarize(tool_result_limit=0)


# ---------------------------------------------------------------------------
# 接缝: AgentLoop 怎么用它 (账本不动 / 视图送出去 / 事件 / 快照)
# ---------------------------------------------------------------------------


class _SpyCounter(AnchorTokenCounter):
    """会记下每次回灌的估算器 (断言「权威值确实回灌了」)."""

    def __init__(self) -> None:
        super().__init__()
        self.notes: list[tuple[Usage | None, int]] = []

    def note_usage(self, usage: Usage | None, *, message_count: int) -> None:
        self.notes.append((usage, message_count))
        super().note_usage(usage, message_count=message_count)


def _capturing_model(seen: list[Any], answer: str = "答完了") -> MockLLM:
    """把「每一次模型调用实际收到的 messages」记进 seen 的脚本模型.

    视图是内部产物, 从返回值看不出来 —— 只有站在模型的位置才看得见 (异步工厂
    拿到的第一个参数就是这次请求的 messages). 存的是**那个列表本身** (不拷贝):
    不配压缩策略时它就该是账本本体, 这条只能靠身份断言.
    """

    async def capture(messages: list[ModelMessage]) -> ModelResponse:
        seen.append(messages)
        return text_response(answer)

    return MockLLM.scripted([capture] * 4)


def _stub_summarizer(text: str | None) -> MockLLM:
    """摘要替身: 给一段摘要正文, 或给 None (模拟「模型没吐正文」的降级)."""
    return MockLLM.scripted([text_response(text)])


def _summary_then_answer(
    summary: str = "早前查过订单", answer: str = "答完了"
) -> MockLLM:
    """常见脚本: 主模型先被用来压一次摘要, 再用来作答 (摘要默认走主模型)."""
    return MockLLM.scripted([text_response(summary), text_response(answer)])


def _seam_policy(**overrides: Any) -> TrimAndSummarize:
    """接缝用例常用的一档: 阈值 1000 (小账本也能压), 只留最近 1 个提问."""
    fields: dict[str, Any] = {"threshold_tokens": 1000, "keep_recent_questions": 1}
    fields.update(overrides)
    return _policy(**fields)


async def test_the_model_gets_the_view_while_the_ledger_stays_whole() -> None:
    """接缝的全部要点在一句话里: 送出去的是视图, 账本一个字不动."""
    history = _history(400, 400, 400, 400)
    seen: list[Any] = []
    loop = AgentLoop(
        model=_capturing_model(seen, "已经帮你查过了"),
        compactor=_seam_policy(summarizer=_stub_summarizer("早前查过订单")),
    )

    result = await loop.run(history, loop_id="loop-compaction")

    view = seen[0]
    assert len(view) < len(history)  # 送出去的那份短了
    assert view[0] is history[0]  # system 原样在场
    assert "摘要" in str(view[1].get("content"))  # 摘要占了一条 system
    # 账本 (返回值 / 快照) 仍是全量: 已经发生过的事一条不少
    assert result.messages[: len(history)] == history


async def test_the_frame_remembers_what_was_actually_sent() -> None:
    """帧里记着**这一轮真的发出去的那一份** (ticket 22 第 4 件).

    压缩把「账本」与「送给模型的」分成两份, 而帧里原本只有账本 —— 「当时它看到了
    什么」(L3 要回答的那句) 因此答不上来. 这一条断的是: **视图与账本不一样的那些
    轮, 帧里记着的那份与模型实际收到的逐条一致** (拿模型替身的调用记录比 —— 视图
    是内部产物, 只有站在模型的位置才看得见).

    两帧正好是判据的两侧: **第一轮没切刀** (低于阈值) → 视图就是账本, `messages`
    记 None; **第二轮切了一刀** (工具结果把它顶过线) → 裁段 + 摘要顶上来, 视图
    明显短于账本, `messages` 把那份留下来.
    """
    saver = InMemoryCheckpointSaver()
    thread_id = "thread-view-in-frame"
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("query_order", '{"order_no": "1"}')),
            text_response("答完了"),
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[BIG_TOOL],
        # 阈值卡在两次决策之间: 第一次不压, 工具结果把它顶过线
        compactor=_policy(
            threshold_tokens=900, summarizer=_stub_summarizer("压过一段")
        ),
        saver=saver,
        thread_id=thread_id,
    )

    await loop.run(_history(300, 300), loop_id="loop-view")

    frames = await saver.list_history(thread_id)
    assert len(frames) == 2
    first, second = frames

    # 第一帧: 没切刀, 视图与账本逐条相同 → 不抄第二份
    assert first.metadata.view is not None, "这一轮有模型调用, 视图就该记下来"
    assert first.metadata.view["messages"] is None
    assert (first.metadata.view["dropped"], first.metadata.view["truncated"]) == (0, 0)

    # 第二帧: 切了一刀 —— 留下的那份与模型第二次决策实际收到的逐条一致
    sent = second.metadata.view["messages"]
    assert sent is not None, "视图与账本不一样, 这一份必须留下来"
    assert sent == model.calls[-1]["messages"]
    assert any("压过一段" in str(m.get("content")) for m in sent), "摘要那条在场"
    assert second.metadata.view["dropped"] > 0
    assert len(sent) < len(second.state.messages), "视图比账本短 (这就是它存在的意义)"
    assert second.metadata.view["estimated_tokens"] > 0


async def test_a_view_identical_to_the_ledger_is_not_copied_into_the_frame() -> None:
    """视图与账本一模一样时, 帧里**不抄第二份**: `messages` 给 None.

    语义是「**视图就是账本本身**」: 帧里已经有全量账本 (`state.messages`), 再抄一份
    会让每帧体积翻倍 —— 而审计价值全在视图真不一样的那些轮. 判据是**直接比**, 不看
    `compacted`: 后者今天恰好等价, 但那是 view-oscillation 那条缺陷的副产品 (见
    `view_payload`).
    """
    saver = InMemoryCheckpointSaver()
    thread_id = "thread-view-same-as-ledger"
    # 阈值与截断线都调得极高: 不裁段也不截工具结果 → 视图与账本逐条相同
    loop = AgentLoop(
        model=MockLLM.scripted([text_response("答完了")]),
        compactor=_policy(threshold_tokens=10**6, tool_result_limit=10**6),
        saver=saver,
        thread_id=thread_id,
    )

    await loop.run(_history(300), loop_id="loop-same")

    [frame] = await saver.list_history(thread_id)
    assert frame.metadata.view is not None
    assert frame.metadata.view["messages"] is None
    assert (frame.metadata.view["dropped"], frame.metadata.view["truncated"]) == (0, 0)


async def test_the_authoritative_usage_is_fed_back_into_the_counter() -> None:
    """每次调用把 usage 回灌给估算器 (锚就是从这里来的), 条数是**账本**的长度."""
    history = _history(400, 400, 400, 400)
    usage = Usage(input_tokens=123, total_tokens=150)
    counter = _SpyCounter()
    model = MockLLM.scripted([text_response("答完了", usage=usage)])
    loop = AgentLoop(
        model=model,
        compactor=_policy(threshold_tokens=100_000),  # 不触发, 只看回灌
        counter=counter,
    )

    await loop.run(history)

    assert counter.notes == [(usage, len(history))]


async def test_without_a_compactor_the_ledger_itself_goes_to_the_model() -> None:
    """不配 compactor: 送给 generate 的就是账本本体 (与今天逐字一样)."""
    seen: list[Any] = []
    model = _capturing_model(seen)
    loop = AgentLoop(model=model)

    result = await loop.run([dict(SYSTEM), _user("在吗")])

    assert seen[0] is result.messages  # 同一个列表对象 (不是副本)


async def test_a_counter_without_a_compactor_is_a_configuration_error() -> None:
    """配了估算器却没有策略 = 白配: 装配期就报, 别等它悄悄不生效."""
    with pytest.raises(LoopConfigError):
        AgentLoop(model=MockLLM.fixed(text_response("答完了")), counter=_SpyCounter())


async def test_the_summary_call_uses_the_main_model_by_default() -> None:
    """摘要默认用主模型 + 一个单独的小 max_tokens (它不该长篇大论)."""
    history = _history(400, 400, 400, 400)
    model = MockLLM.scripted([text_response("早前查过订单"), text_response("答完了")])
    loop = AgentLoop(
        model=model,
        compactor=_policy(threshold_tokens=1000, keep_recent_questions=1),
    )

    await loop.run(history)

    summary_call, main_call = model.calls
    assert summary_call["tools"] is None  # 摘要不需要工具
    assert summary_call["max_tokens"] == 128
    assert main_call["max_tokens"] is None


async def test_summarizer_tokens_count_toward_the_budget_but_not_the_turns() -> None:
    """摘要花的钱照记 (总预算), 但它不是一次模型决策 (不占 max_turns)."""
    history = _history(400, 400, 400, 400)
    model = MockLLM.scripted(
        [
            text_response("早前查过订单", usage=Usage(total_tokens=77)),
            text_response("答完了", usage=Usage(total_tokens=100)),
        ]
    )
    loop = AgentLoop(
        model=model,
        compactor=_policy(threshold_tokens=1000, keep_recent_questions=1),
        guard=LoopGuard(max_turns=3),
    )

    result = await loop.run(history)

    assert result.turn_count == 1  # 只有那一次真决策
    assert result.total_tokens == 177  # 摘要的 77 也花了


async def test_the_compaction_event_carries_what_happened() -> None:
    """第七类事件 (让压缩看得见): 只在真压了的时候发, 载荷是这次做了什么."""
    history = _history(400, 400, 400, 400)
    collector = EventCollector()
    model = MockLLM.scripted([text_response("早前查过订单"), text_response("答完了")])
    loop = AgentLoop(
        model=model,
        compactor=_policy(threshold_tokens=1000, keep_recent_questions=1),
        event_sink=collector,
    )

    await loop.run(history)

    compacted = [
        event for event in collector.events if event.type is EventType.CONTEXT_COMPACTED
    ]
    assert len(compacted) == 1
    payload = compacted[0].data
    assert payload["dropped"] > 0
    assert payload["truncated"] == 0
    assert payload["summarized"] is True
    assert payload["turn"] == 1
    assert payload["estimated_tokens"] > 0
    assert payload["saved_tokens"] > 0
    assert payload["warning"] is None
    # 顺序: 压缩在决策之前 (它就是「这一次调用送什么」的一部分)
    assert collector.events.index(compacted[0]) < collector.events.index(
        next(e for e in collector.events if e.type is EventType.FINAL)
    )


async def test_nothing_to_compact_means_no_event() -> None:
    """没超阈值就一条事件都不发 (前端不该看到「压缩过」的痕迹)."""
    collector = EventCollector()
    loop = AgentLoop(
        model=MockLLM.scripted([text_response("答完了")]),
        compactor=_policy(threshold_tokens=10**6),
        event_sink=collector,
    )

    await loop.run(_history(50))

    assert not [
        event for event in collector.events if event.type is EventType.CONTEXT_COMPACTED
    ]


async def test_a_failed_summary_is_reported_through_the_event() -> None:
    """摘要失败走降级: 这一句照答, 降级原因挂在那条事件上 (不静默)."""
    history = _history(400, 400, 400, 400)
    collector = EventCollector()
    loop = AgentLoop(
        model=MockLLM.scripted([text_response("答完了")]),
        # 摘要模型不给正文 —— 降级路径 (真正的失败路径由策略层的用例钉着)
        compactor=_seam_policy(summarizer=_stub_summarizer(None)),
        event_sink=collector,
    )

    result = await loop.run(history)

    assert result.content == "答完了"
    compacted = [
        event for event in collector.events if event.type is EventType.CONTEXT_COMPACTED
    ]
    assert compacted[0].data["summarized"] is False
    assert "摘要" in str(compacted[0].data["warning"])


async def test_the_summary_rides_along_with_the_checkpoint() -> None:
    """摘要与「压到第几条」跟着快照走: 断了再续时**回灌**进 LoopState."""
    history = _history(400, 400, 400, 400)
    saver = InMemoryCheckpointSaver()
    thread_id = "thread-compaction"
    loop = AgentLoop(
        model=_summary_then_answer(),
        compactor=_seam_policy(),
        saver=saver,
        thread_id=thread_id,
    )
    await loop.run(history, loop_id="loop-compaction")

    frame = await saver.load_latest(thread_id)
    assert frame is not None
    assert frame.state.summary == "早前查过订单"
    assert frame.state.summary_covers > 0
    assert frame.state.messages[: len(history)] == history  # 快照仍是全量

    resumed = AgentLoop(
        model=MockLLM.scripted([text_response("接着说")]),
        compactor=_policy(threshold_tokens=10**6),  # 不触发, 只看回灌
        saver=saver,
        thread_id=thread_id,
    )
    result = await resumed.resume(frame)

    # 没回灌的话这里会是 None (新 run 的 LoopState 初值) —— 摘要跨断点没丢
    assert result.summary == "早前查过订单"
    assert result.summary_covers == frame.state.summary_covers


async def test_a_summary_from_the_previous_run_keeps_scrolling() -> None:
    """跨 run 续接 (会话连续问几句走的就是这条路): 滚动摘要接着滚, 不从零重压."""
    history = _history(400, 400, 400, 400)
    loop = AgentLoop(
        model=_summary_then_answer(),
        compactor=_seam_policy(),
    )
    first = await loop.run(history)
    assert first.summary == "早前查过订单"

    summarizer = _stub_summarizer("第二段摘要")
    continuing = AgentLoop(
        model=MockLLM.scripted([text_response("接着说")]),
        compactor=_seam_policy(summarizer=summarizer),
    )
    second = await continuing.run(
        [*first.messages, *_unit(9, size=400)],
        summary=first.summary,
        summary_covers=first.summary_covers,
    )

    assert second.summary == "第二段摘要"
    # 第二次压缩的输入里带着上一条摘要 (只压新段的话, 早前的事会被逐次稀释掉)
    assert "早前查过订单" in summarizer.calls[-1]["messages"][-1]["content"]


async def test_a_resumed_run_does_not_re_compact_what_was_already_summarized() -> None:
    """续跑不重压已进过摘要的那一段: summary_covers 是这么用的."""
    history = _history(400, 400, 400, 400)
    saver = InMemoryCheckpointSaver()
    thread_id = "thread-compaction-resume"
    loop = AgentLoop(
        model=_summary_then_answer(),
        compactor=_seam_policy(),
        saver=saver,
        thread_id=thread_id,
    )
    await loop.run(history, loop_id="loop-compaction")
    frame = await saver.load_latest(thread_id)
    assert frame is not None

    summarizer = _stub_summarizer("第二段摘要")
    resumed = AgentLoop(
        model=MockLLM.scripted([text_response("接着说")]),
        compactor=_policy(threshold_tokens=10**6, summarizer=summarizer),
        saver=saver,
        thread_id=thread_id,
    )
    await resumed.resume(frame)

    assert summarizer.calls == []  # 没触发压缩, 也就没有再问一次摘要


async def test_frames_before_the_compaction_stay_as_they_were() -> None:
    """压缩只影响**之后**落的帧: 之前那些帧一字不变, 帧数照旧一次决策一帧.

    这条是「账本 / 视图分离」在存档线上的样子 —— 压的是这次请求的输入, 存的
    仍是全量过程; 于是回放时既看得见「当时压过」, 也看得见压之前发生过什么.
    """
    saver = InMemoryCheckpointSaver()
    thread_id = "thread-mid-run-compaction"
    # 阈值卡在「第一次决策之后、第二次决策之前」: 第一次不压, 工具结果把它顶过线
    loop = AgentLoop(
        model=MockLLM.scripted(
            [
                tool_call_response(make_tool_call("query_order", '{"order_no": "1"}')),
                text_response("答完了"),
            ]
        ),
        tools=[BIG_TOOL],
        compactor=_policy(
            threshold_tokens=900, summarizer=_stub_summarizer("压过一段")
        ),
        saver=saver,
        thread_id=thread_id,
    )
    history = _history(300, 300)

    await loop.run(history, loop_id="loop-compaction")

    frames = await saver.list_history(thread_id)
    assert len(frames) == 2  # 一次决策一帧, 没多也没少
    # 第一帧落盘时还没压过: 它记的是那一次决策结束时账本的样子 (全量)
    assert frames[0].state.summary is None
    assert frames[0].turn_number == 1
    assert frames[1].state.summary == "压过一段"  # 第二帧才带上压缩的账
    # 两帧都是全量账本的前缀 (压缩不删任何东西, 帧与帧之间只增不改)
    assert frames[1].state.messages[: len(frames[0].state.messages)] == (
        frames[0].state.messages
    )


async def test_odd_tool_counts_do_not_break_the_cut() -> None:
    """工具结果多于/少于调用数的畸形账本: 裁完仍然是它原来那个合法程度.

    这两种形状框架自己不会产生 (它总是配对回填), 但它们可能来自别处 (手改过的
    快照 / 上游给的怪数据) —— 压缩要保证的是**不把畸形扩大**: 刀口只落在提问处,
    于是「欠着结果的 assistant」与它那些结果要么一起留下、要么一起被裁掉.
    """
    # 少于调用数: 一条 assistant 开了两个调用, 只回填了一个
    short = [
        SYSTEM,
        _user("订单到哪了"),
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "query_order", "arguments": "{}"},
                },
                {
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "query_refund", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "订" * 300},
        _user("现在呢"),
        {"role": "assistant", "content": "刚刚查过"},
    ]
    compiled = await _apply(
        _policy(threshold_tokens=100, keep_recent_questions=1), short
    )

    assert compiled.dropped > 0
    assert compiled.messages[0] is SYSTEM
    # 欠着 c2 的那条 assistant 跟着它的结果一起被裁掉了 (没有「半条」留下)
    assert _wire_legal(compiled.messages)
    assert not [m for m in compiled.messages if m.get("role") == "tool"]

    # 多于调用数: 多出来的那条孤儿 tool 消息也跟着它所在的段一起走
    orphan = [
        SYSTEM,
        _user("订单到哪了"),
        *_tool_decision("订" * 300, call_id="c1"),
        {"role": "tool", "tool_call_id": "c9", "content": "凭空回来的结果"},
        _user("现在呢"),
        {"role": "assistant", "content": "刚刚查过"},
    ]
    compiled = await _apply(
        _policy(threshold_tokens=100, keep_recent_questions=1), orphan
    )

    assert compiled.dropped > 0
    assert _wire_legal(compiled.messages)
    assert "c9" not in {m.get("tool_call_id") for m in compiled.messages}


async def test_the_kept_question_survives_an_unmatched_call() -> None:
    """保留段里有欠着结果的调用时, 它原样留着 (压缩不该替它补一条或删一条)."""
    history = [
        SYSTEM,
        _user("订单到哪了"),
        {"role": "assistant", "content": "我先查一下"},
        _user("订单到哪了"),  # 换个说法再问一次 (于是有得裁)
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "query_order", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "订" * 300},
    ]
    compiled = await _apply(
        _policy(threshold_tokens=100, keep_recent_questions=1), history
    )

    assert compiled.dropped > 0
    # 最新那个提问 (含它那条工具结果) 原样保留, 一字未改
    assert compiled.messages[-1] == history[-1]
    assert compiled.messages[-2] == history[-2]
