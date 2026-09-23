"""上下文压缩用例 (difficulties #7): 账本 / 视图分离.

被测的三层, 从纯到脏:
- **估算** (CalibratedTokenCounter): 字符启发式 + 一个从上游真实用量反推出来的
  固定开销 —— 权威值只有 API 给的 usage, 估算只用来判阈值
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
- 压不动 / 摘要失败时不拖垮这句问话: 退到「只投影、不切刀」, 并且如实留痕.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
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
    CalibratedTokenCounter,
    CompiledView,
    TrimAndSummarize,
)
from CharAgent.agent.utils.messages import (
    MESSAGE_OVERHEAD_TOKENS,
    REASONING_CLEARED_TEXT,
    estimate_tokens,
)
from CharAgent.checkpoint.memory import InMemoryCheckpointSaver
from CharAgent.model.utils.config import THINKING_OFF_EFFORT
from CharAgent.model.utils.errors import ModelErrorKind, ModelStatusError
from CharAgent.model.utils.types import FinishReason, ModelMessage, ModelResponse, Usage
from CharAgent.prompt.ref import identity_message, prompt_ref
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


def _missing_from_view(
    history: list[ModelMessage], compiled: CompiledView
) -> list[ModelMessage]:
    """视图把账本里的哪几条**整个**丢掉了 (按对象身份认, 不看内容).

    压缩的硬不变量是「投影只丢摘要已经覆盖过的那一段」—— 这个 helper 把那条话
    变成可断言的量: 丢掉的那几条必须落在前 `summary_covers` 条里.

    Note:
        只对**没走截断**的那条路成立: 截断会新建一条 dict (同样带 tool_call_id),
        原对象在视图里缺席 —— 那是「截短」不是「丢掉」, 这个 helper 分不出来.
    """
    in_view = {id(message) for message in compiled.messages}
    return [message for message in history if id(message) not in in_view]


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
    """跑一次投影 (估算器每次新建: 用例关心策略本身, 不管校准项的延续)."""
    return await policy.apply(
        history,
        summary=summary,
        summary_covers=summary_covers,
        counter=CalibratedTokenCounter(),
        summarizer=summarizer,
    )


# ---------------------------------------------------------------------------
# 估算: 字符启发式 + 从上游真实用量反推的固定开销
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


def test_reasoning_content_counts_toward_the_estimate() -> None:
    """思维链也是**要发出去的输入**, 估算漏掉它就会系统性偏低.

    它会回填进账本 (DeepSeek 思考模式要求历史轮次的 reasoning_content 完整回传,
    见 `assistant_wire`), 于是每一轮都占着输入 —— 而 reasoner 的思考常比正文长
    数倍, 漏算的量级不小. 差值按与正文**同一把尺子**算 (中文一字 ≈ 一 token).
    """
    without = [_answer("已发货")]
    with_reasoning = [{**without[0], "reasoning_content": "订" * 200}]

    assert estimate_tokens(with_reasoning) == estimate_tokens(without) + 200


def test_the_calibration_starts_as_a_bare_estimate() -> None:
    """还没回灌过权威值: 就是整份启发式 (凭字符猜)."""
    counter = CalibratedTokenCounter()
    history = [_user("甲" * 100)]

    assert counter.count(history) == estimate_tokens(history)


def test_the_calibration_absorbs_the_fixed_overhead() -> None:
    """回灌之后, **同一份列表**量出来正好等于上游说的那个数.

    差值就是启发式看不见的那部分 (工具 schema / 系统提示的模板部分) —— 它被记成
    一个常量加回去, 而不是像从前那样只在「列表正好是账本前 N 条」时才生效.
    """
    counter = CalibratedTokenCounter()
    sent = [_user("甲" * 100)]

    counter.note_usage(Usage(input_tokens=500, total_tokens=520), sent=sent)

    assert counter.count(sent) == 500
    assert counter.count(sent) > estimate_tokens(sent), "固定开销是加项"


def test_every_list_shares_one_calibration() -> None:
    """**账本与视图走同一条算式**: 一份列表怎么算, 任何一份列表都怎么算.

    这是缺陷 1 的直接回归 (2026-09-23 审计). 从前锚记的是「**账本**有几条」, 而
    权威值来自**视图**, 压缩过之后两者不等 —— 于是同一份视图撞上「更短的列表」
    那条守卫退回裸启发式 (**低估 20 倍**), 而账本被算成「视图的真实量 + 账本尾部
    的估算」(**高估一倍**). 实测: 锚 = 8000 / 锚定 4 条时, `count(视图) = 415`,
    `count(账本) = 13291`, 而账本自己的启发式只有 6520.

    现在没有可退的地方, 也没有第二套坐标.
    """
    counter = CalibratedTokenCounter()
    ledger = _history(300, 300)
    view = [ledger[0], _answer("摘要替身"), *ledger[-3:]]

    counter.note_usage(Usage(input_tokens=9000), sent=view)
    overhead = 9000 - estimate_tokens(view)

    assert counter.count(view) == 9000, "量的是上次发出去的那份: 精确"
    assert counter.count(ledger) == estimate_tokens(ledger) + overhead, "同一算式"
    assert counter.count([_user("你好")]) == estimate_tokens([_user("你好")]) + overhead


def test_missing_usage_leaves_the_calibration_alone() -> None:
    """上游没给 usage (部分 mock / 流式) 时校准项不动, 估算照旧可用."""
    counter = CalibratedTokenCounter()
    probe = [_user("你好")]
    baseline = counter.count(probe)

    counter.note_usage(None, sent=probe)
    counter.note_usage(Usage(output_tokens=9), sent=probe)  # 没有 input_tokens

    assert counter.count(probe) == baseline


def test_a_negative_overhead_is_kept() -> None:
    """启发式**高估**时校准项是负的: 如实照着调, 不夹到 0.

    夹到 0 等于假装「启发式从不高估」, 于是估算永远偏大 —— 压缩会压得比该压的更勤.
    """
    counter = CalibratedTokenCounter()
    sent = [_answer("短")]

    counter.note_usage(Usage(input_tokens=1), sent=sent)

    assert counter.count(sent) == 1
    assert counter.count(sent) < estimate_tokens(sent)


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
    # 关掉摘要: 这条测的是**裁剪**那一件, 摘要的成败不该决定刀切不切
    compiled = await _apply(_policy(summarize=False), history)

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
    compiled = await _apply(_policy(keep_recent_questions=3, summarize=False), history)

    assert compiled.messages[0] is history[0]


async def test_at_least_one_question_is_kept() -> None:
    """极端参数下也至少留最近 1 个提问 (裁到只剩 system, 等于把这段对话删了)."""
    history = _history(*([300] * 6))
    compiled = await _apply(
        _policy(keep_recent_questions=99, watermark_ratio=0.01, summarize=False),
        history,
    )

    assert _tool_bodies(compiled.messages)  # 还有工具结果活着
    assert compiled.messages[-1] == history[-1]


async def test_tool_results_outside_the_latest_question_are_truncated() -> None:
    """老工具结果被截到阈值: 省 token 的主力在这里 (工具正文往往是大头)."""
    history = _history(500, 500, 500)
    compiled = await _apply(
        _policy(
            threshold_tokens=1000,
            keep_recent_questions=2,
            watermark_ratio=0.7,
            summarize=False,
        ),
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
    compiled = await _apply(_policy(keep_recent_questions=1, summarize=False), history)

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
        threshold_tokens=1000,
        keep_recent_questions=1,
        watermark_ratio=0.5,
        summarize=False,
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

    这一条同时钉住两件事 —— 水位线 (压完确实落到线下), 与**判据是投影不是账本**
    (账本 append-only 只增不减, 拿它判的话压完一次就永远超线, 于是每轮都切一刀、
    每轮都烧一次摘要).

    Note:
        它只断了「不再切一刀」这半边; **另半边** (这一轮发出去的仍是视图、摘要还在
        里面) 见 `test_a_turn_that_does_not_cut_still_sends_the_view` —— 两者分开
        之前, 这半边正是 ticket 23 那个每隔一轮失效的循环.
    """
    counter = CalibratedTokenCounter()
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

    grown = [*history, *_unit(9, size=100)]
    # 账本**确实**超过了阈值, 而这一轮仍然不切 —— 判的是投影那份的大小
    assert CalibratedTokenCounter().count(grown) >= 1000

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

      - 压完那一轮发的是小视图 → 判定「不用再切」→ 旧实现把**全量账本**原样发了
        出去, 摘要那条根本不带上, 视图就此每隔一轮失效一次
      - 而账本随即把估算顶回大值 → 再下一轮又超阈值 → 又切一刀 (又烧一次摘要)

    修法是两件事分家: **投影每轮都做** (视图 = system + 摘要 + 摘要没覆盖到的一切),
    「再切一刀」(扩大覆盖 + 重压摘要) 才需要阈值说话. 于是在这里断言: 这一轮没切
    (compacted=False), 但发出去的**不是**账本本身, 而且摘要还在里面.

    Note:
        滞回本身来自**投影变小** (2026-09-23 改): 判阈值量的是这一轮要发出去的那份
        投影, 它因为上一刀已经推过切点而小 —— 不再依赖「锚落到小值」, 于是这一轮
        不必再手动灌一个 usage 进来造场景.
    """
    counter = CalibratedTokenCounter()
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
    assert first.estimated_tokens < policy.threshold_tokens, (
        "压完那份视图已经落到阈值以下 —— 这就是下一轮不切的**原因**"
    )

    grown = [*history, *_unit(9, size=20)]

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


@pytest.mark.parametrize(
    "make_summarizer",
    [
        lambda: MockLLM.scripted([]),  # 一调就抛 (脚本耗尽)
        lambda: MockLLM.scripted([text_response(None)]),  # 没吐正文
        lambda: MockLLM.scripted(  # 吐了半截 (length 截断)
            [text_response("半截摘要", finish_reason=FinishReason.LENGTH)]
        ),
        lambda: None,  # 根本没有可用的摘要模型
    ],
    ids=["raises", "empty", "truncated", "no-model"],
)
async def test_a_failed_summary_falls_back_to_projection_only(
    make_summarizer: Callable[[], MockLLM | None],
) -> None:
    """摘要失败 (四种) → 本轮**不切刀**, 只投影: 这句问话照样答得出来.

    修之前这里的行为叫「降级为纯裁剪」—— 听着无害, 实测却会凭空丢信息
    (2026-09-23 复现): `summary_covers` 不推进 (账目对), 而切点照推 (视图错),
    于是账本 17 条时 `dropped=14` 而 `covers=0` —— `history[1:15]` 那 14 条
    **既不在摘要里也不在视图里**. 下一轮接着推 (`dropped=16`), 结果是**每轮只
    看得见最后一个问答, 且永远没有摘要**: 用户问「我刚才说的订单号」, 模型不知道.

    所以降级要退到「只投影」, 保住那条不变量 —— 视图丢掉的每一条都被 covers
    覆盖. 「允许丢」的权力留给 emergency (只在真超限时才用).
    """
    history = _history(300, 300, 300)

    compiled = await _apply(_policy(), history, summarizer=make_summarizer())

    assert compiled.compacted is False, "没切刀 = 没有新丢的东西"
    assert compiled.dropped == 0
    assert compiled.warning is not None, "降级要留痕 (配置选择那支才不留)"
    assert (
        _missing_from_view(history, compiled)
        == history[1 : max(compiled.summary_covers, 1)]
    ), "丢掉的正好是 covers 覆盖过的那一段, 一条多的都没有"
    assert _wire_legal(compiled.messages)


async def test_a_failed_summary_does_not_advance_the_progress() -> None:
    """失败时**进度字段**原样带回: 那段还没进过摘要, 下次压缩还得带上它.

    与上一条的分工: 那条管「视图一条都没多丢」, 这条管「摘要与 covers 没被改」.
    """
    history = _history(300, 300, 300)
    broken = MockLLM.scripted([])

    compiled = await _apply(
        _policy(), history, summary="早前查过订单", summary_covers=2, summarizer=broken
    )

    assert compiled.summary == "早前查过订单"
    assert compiled.summary_covers == 2
    assert any("早前查过订单" in str(m.get("content")) for m in compiled.messages)


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
    """关掉摘要 ≠ 丢掉已有摘要: 视图里那条摘要照旧在位, 但它覆盖的范围跟着切点走.

    关的是「以后不再生成」—— 已有的摘要仍该在视图最前面 (它覆盖的那段已被裁掉,
    拿掉它等于让历史凭空消失). 而 `summary_covers` 必须**跟着切点推进**: 停在旧值
    的话, 下一轮投影会从旧切点开始, 把这一轮裁掉的段又放回视图 —— 视图于是在相邻
    两轮之间跳变 (与 ticket 23 修的那条同类, 这一条藏在「关掉摘要」这条路上).
    """
    history = _history(300, 300, 300, 300)

    compiled = await _apply(
        _policy(summarize=False),
        history,
        summary="早前聊的是查订单",
        summary_covers=3,
    )

    assert compiled.summary == "早前聊的是查订单"
    assert any("早前聊的是查订单" in str(m.get("content")) for m in compiled.messages)
    assert compiled.summary_covers == compiled.dropped + 1, "covers 与切点同进退"


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
    assert call["reasoning_effort"] == THINKING_OFF_EFFORT, (
        "effort 也要显式压到 none —— 不传会回落到**实例默认**的强度, 与 thinking=False"
        " 方向相反, check_thinking_params 会 fail fast 抛错, 然后被吞成降级"
    )


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
    with pytest.raises(CompactionConfigError):
        TrimAndSummarize(clear_at_least_ratio=1.0)
    with pytest.raises(CompactionConfigError):
        TrimAndSummarize(summary_input_limit=-1)
    with pytest.raises(CompactionConfigError):
        TrimAndSummarize(reasoning_keep_turns=0)


# ---------------------------------------------------------------------------
# 治理: clear_at_least / 摘要输入上界 / 思维链清理 (2026-09-23, ticket 26 批②)
# ---------------------------------------------------------------------------


async def test_a_saving_below_clear_at_least_skips_the_cut() -> None:
    """压一次只省一点点 → **不压**, 并记下「为什么没压」.

    花一次摘要调用只换回几百 token 不划算 (Anthropic 的 `clear_at_least` 是同一条
    规矩: 官方样例 trigger 30000 / clear_at_least 5000). 这里用比例表达 —— 它是
    「值不值得花一次钱」的相对门槛, 跟着阈值走才不用两处一起调.
    """
    policy = _policy(threshold_tokens=1000, keep_recent_questions=1, summarize=False)
    # 前面一小段 + 最后一个大提问: 账本超阈值, 但能裁掉的那段很小
    history = [*_history(5), *_unit(9, size=1200)]

    compiled = await _apply(policy, history)

    assert compiled.compacted is False
    assert compiled.skipped == "clear_at_least"
    assert _wire_legal(compiled.messages)


async def test_a_saving_above_clear_at_least_still_cuts() -> None:
    """省得够多就照压 —— 这道闸只挡「不值当」的, 不挡正常的压缩."""
    policy = _policy(threshold_tokens=1000, keep_recent_questions=1, summarize=False)
    history = [*_history(*([400] * 5)), *_unit(9, size=50)]

    compiled = await _apply(policy, history)

    assert compiled.compacted is True
    assert compiled.skipped is None


async def test_clear_at_least_measures_against_the_projection_not_the_ledger() -> None:
    """这道门的基线是**这一轮的投影**, 不是账本 —— 否则压过一次之后它形同虚设.

    账本里含着上一次已经裁掉的段 (它们在投影里根本不出现), 拿账本当基线会把那些段
    **重复计**成这一次的节省. 复现的形状: 前四段大账本已经被压到 `covers` 之外
    (账本 ~1900), 而这一刀能裁掉的只有一条短提问 (真实只省 ~10 token, 够不着 100
    的门槛) —— 旧口径量账本得到 ~1900, 照切照烧摘要, 每轮换一次摘要还把前缀缓存
    整段打掉.

    Note:
        保留段要**撑得大** (投影得超阈值) 又**没有可截的正文** (截断是真实收益, 会把
        saving 顶上去) —— 所以填一串普通 assistant 消息, 不用工具结果.
    """
    policy = _policy(
        threshold_tokens=1000,
        keep_recent_questions=1,
        clear_at_least_ratio=0.1,
        summarize=False,
    )
    history = [
        *_history(*([400] * 4)),  # 大账本: 它在 covers 之前, 只在旧口径里"值钱"
        _user("第 5 个问题"),  # covers 落在这里, 这一刀能裁的就它
        _user("第 9 个问题"),  # 切点
        *[_answer("说明" + "。" * 40) for _ in range(40)],  # 保留段: 大, 但不可截
    ]

    compiled = await _apply(policy, history, summary="早前聊过", summary_covers=17)

    assert compiled.compacted is False, "只省几十 token, 不值当压"
    assert compiled.skipped == "clear_at_least"


async def test_the_summary_material_is_capped_from_the_oldest_end() -> None:
    """摘要材料有**输入上界** (LangChain 的 trim_tokens_to_summarize 同一条规矩).

    砍的是**最旧**那端: 与当前任务的相关性随距离衰减, 越旧的越该被压成摘要. 没有
    上界的话, 被裁的那段可能比它要省的还大 —— 为了省 token 先花一大笔.
    """
    history = _history(*([400] * 6))
    summarizer = _stub_summarizer("压好了")
    policy = _policy(summary_input_limit=150)

    compiled = await _apply(policy, history, summarizer=summarizer)

    assert compiled.summarized is True
    material = summarizer.calls[-1]["messages"][-1]["content"]
    assert "第 1 个问题" not in material, "最旧的那段被砍掉"
    assert "第 5 个问题" in material, "最近的那段留着"
    assert estimate_tokens([{"role": "user", "content": material}]) <= 200, "上界生效"
    # 被削掉的那些**没进过摘要**, 而切点照旧推进 —— 它们就此丢了 (这是输入上界的
    # 代价, 配置驱动). 代价可以认, 但**不能静默**: 摘要成功也要说一声, 否则事后查帧
    # 只会看到「压了一次、摘要也有」, 中间那几条去哪儿了没人答得上来
    assert "没进摘要" in str(compiled.warning), "削了材料就得说一声"


async def test_no_fresh_material_is_not_a_silent_no_op() -> None:
    """摘要开着但**没有新材料**(`covers == cut`)时: 什么都不做, **但要留下原因**.

    这是稳态 —— 同一个提问下的工具轮让账本继续长大, 而切点不动. 切一刀也省不下
    什么 (切点根本推不动), 所以门① 会挡下它; 挡下没问题, 问题在于从前那一路既不发
    事件也不记 skipped, 看起来像「框架什么都没做」.
    """
    history = _history(*([400] * 4))  # 4 段各 4 条 + system = 17 条, 切点落在下标 13

    compiled = await _apply(
        _policy(summarize=True), history, summary="早前聊过", summary_covers=13
    )

    assert compiled.compacted is False, "切了也省不下 —— 这一刀推不动"
    assert compiled.summarized is False
    assert compiled.warning is None, "这不是失败"
    assert compiled.skipped is not None, "但要说清楚「为什么没压」, 不能静默"


async def test_a_failed_summary_still_reports_what_it_cost() -> None:
    """摘要失败也**照记那一次的花费** —— 它确实计费了, 不能因为失败就当没发生.

    失败时策略返回的是「只投影」那份产物, 而它的用量字段本来是空的; 忘了把
    `_summarize` 报回来的 usage 带上去, 这笔钱就既不在运行预算里、也不在成本归因里.
    """
    history = _history(*([400] * 4))
    usage = Usage(input_tokens=200, output_tokens=30)
    summarizer = MockLLM.scripted([text_response(None, usage=usage)])

    compiled = await _apply(_policy(), history, summarizer=summarizer)

    assert compiled.summarized is False
    assert compiled.summarizer_usage == usage, "失败的调用同样花钱"


def _reasoning_history(turns: int) -> list[ModelMessage]:
    """账本: 每一轮的 assistant 都带一段思维链 (验清理开关用)."""
    history = [SYSTEM]
    for index in range(1, turns + 1):
        history.append(_user(f"第 {index} 个问题"))
        history.append({**_answer(f"答{index}"), "reasoning_content": "想" * 30})
    return history


async def test_reasoning_older_than_the_kept_turns_is_clipped() -> None:
    """开了开关: 更早那几轮的思维链换成占位文本, **key 仍在** (wire 形状不变)."""
    policy = _policy(
        threshold_tokens=400,
        keep_recent_questions=3,
        watermark_ratio=0.99,
        reasoning_keep_turns=1,
        summarize=False,
    )

    compiled = await _apply(policy, _reasoning_history(10))

    kept = [
        m.get("reasoning_content")
        for m in compiled.messages
        if m.get("role") == "assistant"
    ]
    assert kept[-1] == "想" * 30, "最近那一轮的思维链留着"
    assert kept[0] == REASONING_CLEARED_TEXT
    assert compiled.reasoning_cleared == len(kept) - 1
    assert all(
        "reasoning_content" in m
        for m in compiled.messages
        if m.get("role") == "assistant"
    ), "占位不删 key —— 下游不该按 key 缺失分支"


async def test_reasoning_is_kept_by_default() -> None:
    """默认**不清理**: DeepSeek 那条「历史 reasoning 须完整回传」没被证伪, 开关默认
    关才能零风险拿到「计入估算」那份修复 (不放开关的话, 唯一的办法是不修)."""
    policy = _policy(
        threshold_tokens=400,
        keep_recent_questions=3,
        watermark_ratio=0.99,
        summarize=False,
    )

    compiled = await _apply(policy, _reasoning_history(10))

    assert compiled.reasoning_cleared == 0
    assert all(
        m.get("reasoning_content") == "想" * 30
        for m in compiled.messages
        if m.get("role") == "assistant"
    )


# ---------------------------------------------------------------------------
# 超限兜底 (2026-09-23, ticket 26 批②): 上游说超了窗口就紧急压一次再发
# ---------------------------------------------------------------------------


async def test_emergency_cuts_to_the_last_question_only() -> None:
    """紧急压缩**不问阈值也不问水位线**: 直接裁到只剩最近 1 个提问."""
    history = _history(*([400] * 5))
    policy = _policy(
        threshold_tokens=100_000,  # 高到平时绝不会压 —— 验的就是「不问它」
        keep_recent_questions=5,  # 平时要留 5 个, 紧急时只剩 1 个
        watermark_ratio=0.1,  # 水位线也够不着 —— 验的还是「不问它」
    )

    compiled = await policy.emergency(
        history,
        summary=None,
        summary_covers=0,
        counter=CalibratedTokenCounter(),
        summarizer=_stub_summarizer("压好了"),
    )

    assert compiled.emergency is True
    assert len(_tool_bodies(compiled.messages)) == 1, "只剩最近那个提问"
    assert compiled.summary == "压好了"


async def test_emergency_cuts_even_when_the_summary_fails() -> None:
    """紧急路径上**摘要失败也照裁** —— 它保的是这一次请求还能不能发出去.

    平时那条「摘要失败就不切刀」的纪律保护的是**信息** (见
    `test_a_failed_summary_falls_back_to_projection_only`); 而触发紧急路径的前提
    本身就是「不裁就发不出去」. 代价记在账目上: `summary_covers` 推进到切点 ——
    那一段被**显式声明为有意放弃** (既不在摘要里, 也不在视图里).
    """
    history = _history(*([400] * 5))

    compiled = await _policy().emergency(
        history,
        summary=None,
        summary_covers=0,
        counter=CalibratedTokenCounter(),
        summarizer=MockLLM.scripted([]),  # 一调就抛
    )

    assert compiled.emergency is True
    assert compiled.dropped > 0, "摘不出来也照裁"
    assert compiled.summarized is False
    assert compiled.summary_covers == compiled.dropped + 1, "那段被记成「有意放弃」"
    assert _wire_legal(compiled.messages)


async def test_emergency_truncates_the_result_it_is_answering() -> None:
    """紧急路径上「正在回答的那段不截」这条保护**解除** —— 不解除它等于什么都没做.

    典型触发就是「这一轮刚取回一个大工具结果」, 而那段正文在只剩 1 个提问时**正好
    就是保留段的全部** (cut 与 latest_start 相等). 复现: 紧急压完 16174 → 16159, 只
    省 15 token, 重发的那份和刚被上游拒掉的那份一样大 —— 白压一次.
    """
    history = [*_history(*([5] * 3)), *_unit(9, size=900)]

    compiled = await _policy().emergency(
        history,
        summary=None,
        summary_covers=0,
        counter=CalibratedTokenCounter(),
        summarizer=_stub_summarizer("压好了"),
    )

    assert compiled.truncated >= 1, "保留段里那条大工具结果该被截短"
    assert _tool_bodies(compiled.messages)[-1] != "订" * 900


async def test_emergency_leaves_a_history_it_cannot_cut_alone() -> None:
    """连一刀都裁不动 (只有一个提问): 原样返回, 让上层照旧失败 —— 不假装成功."""
    history = _history(400)

    compiled = await _policy().emergency(
        history,
        summary=None,
        summary_covers=0,
        counter=CalibratedTokenCounter(),
    )

    assert compiled.dropped == 0
    assert compiled.messages == history


async def test_an_overflow_error_triggers_one_emergency_compaction() -> None:
    """上游报超窗口 → 紧急压一次 + 重发一次, 用户这一句照样问出来.

    这是补上 DeepAgents 那条 `ContextOverflowError` fallback 的 (2026-09-23 审计):
    估算不准是常态, 而在此之前 400 被判「永久错误」直接上抛 —— 这一轮就废了.
    """
    history = _history(*([400] * 5))

    async def overflow(messages: list[ModelMessage]) -> ModelResponse:
        raise ModelStatusError(
            400,
            "This model's maximum context length is 65536 tokens",
            kind=ModelErrorKind.CONTEXT_OVERFLOW,
        )

    sent: list[Any] = []

    async def capture(messages: list[ModelMessage]) -> ModelResponse:
        sent.append(messages)
        return text_response("答完了")

    model = MockLLM.scripted([overflow, text_response("早前查过订单"), capture])
    loop = AgentLoop(
        model=model,
        compactor=_seam_policy(threshold_tokens=100_000),  # 常规轮不压, 靠超限触发
    )

    result = await loop.run(history)

    assert result.content == "答完了"
    assert len(sent) == 1, "只重发一次"
    assert len(sent[0]) < len(history), "重发的那份确实更小 (紧急压过了)"
    assert _wire_legal(sent[0])


async def test_a_second_overflow_is_not_retried_again() -> None:
    """紧急压过之后**再超就直接抛** —— 不把这一轮拖成重试循环."""
    history = _history(*([400] * 5))

    async def overflow(messages: list[ModelMessage]) -> ModelResponse:
        raise ModelStatusError(
            400, "still too long", kind=ModelErrorKind.CONTEXT_OVERFLOW
        )

    model = MockLLM.scripted([overflow, overflow, overflow])
    loop = AgentLoop(
        model=model,
        compactor=_seam_policy(threshold_tokens=100_000, summarize=False),
    )

    with pytest.raises(ModelStatusError):
        await loop.run(history)

    assert len(model.calls) == 2, "常规一次 + 紧急重发一次, 到此为止"


async def test_an_ordinary_bad_request_is_not_rescued() -> None:
    """别的 400 (参数写错之类) 不救: 重发多少次都一样, 如实上抛."""
    history = _history(*([400] * 5))

    async def bad_request(messages: list[ModelMessage]) -> ModelResponse:
        raise ModelStatusError(
            400, "invalid model", kind=ModelErrorKind.INVALID_REQUEST
        )

    model = MockLLM.scripted([bad_request, text_response("不该走到这里")])
    loop = AgentLoop(
        model=model,
        compactor=_seam_policy(threshold_tokens=100_000),
    )

    with pytest.raises(ModelStatusError):
        await loop.run(history)

    assert len(model.calls) == 1, "一次都不多重发"


# ---------------------------------------------------------------------------
# 帧形状与可观测性 (2026-09-23, ticket 26 批③): view 也走引用 + 两个诊断值
# ---------------------------------------------------------------------------


async def test_the_frame_stores_the_view_without_its_identity() -> None:
    """帧里的视图也与进度同构: 身份说明摘出去换成引用, 拼回来**逐字不差**.

    为什么值得做: 身份说明是几千字, 而它**就是**进度那边那一条 (压缩策略拿
    `history[0]` 当视图的第 0 条) —— 每帧在 `metadata.view` 里再抄一遍纯属重复.
    代价写在读取侧: `view.messages` 不再是「可以直接发给模型的完整列表」, 读的人
    要按 `prompt_ref` 把正文补回来 (与进度那边同一条规矩).
    """
    identity = "你是商城客服"
    ref = prompt_ref("system/v2", identity)
    history = _history(*([400] * 4))
    saver = InMemoryCheckpointSaver()
    thread_id = "thread-view-ref"
    sent: list[Any] = []

    async def capture(messages: list[ModelMessage]) -> ModelResponse:
        sent.append(list(messages))
        return text_response("答完了")

    loop = AgentLoop(
        # 第一次是摘要调用 (压缩默认走主模型), 第二次才是真正那一轮
        model=MockLLM.scripted([text_response("早前查过订单"), capture]),
        compactor=_seam_policy(),
        saver=saver,
        thread_id=thread_id,
    )

    await loop.run(history, prompt_ref=ref)

    [frame] = await saver.list_history(thread_id)
    view = frame.metadata.view
    assert view is not None
    assert view["messages"] is not None, "这一轮压过, 视图与账本不同"
    assert view["prompt_ref"] == ref, "与进度那边是同一个引用"
    assert view["messages"][0] != SYSTEM, "身份说明那条被摘掉了"
    assert [identity_message(identity), *view["messages"]] == sent[0], (
        "按引用拼回去逐字等于当时真发出去的那份"
    )


async def test_the_frame_remembers_how_far_off_the_estimate_was() -> None:
    """帧里记下这一轮的**估算偏差**与**缓存命中率** —— 两个事后才知道的诊断值.

    记它们是为了回答「压缩到底估得准不准、花掉多少缓存」: 缺陷 1 (锚的坐标错位)
    此前没有任何一处答得出来, 而那正是它藏了那么久的原因.
    """
    history = _history(*([400] * 4))
    saver = InMemoryCheckpointSaver()
    thread_id = "thread-diagnostics"
    usage = Usage(input_tokens=1000, cache_hit_tokens=300, cache_miss_tokens=700)
    loop = AgentLoop(
        model=MockLLM.scripted(
            [text_response("早前查过订单"), text_response("答完了", usage=usage)]
        ),
        compactor=_seam_policy(),
        saver=saver,
        thread_id=thread_id,
    )

    await loop.run(history)

    [frame] = await saver.list_history(thread_id)
    view = frame.metadata.view
    assert view is not None
    assert view["cache_hit_ratio"] == 0.3
    assert view["estimate_drift"] == view["estimated_tokens"] - 1000


# ---------------------------------------------------------------------------
# 接缝: AgentLoop 怎么用它 (账本不动 / 视图送出去 / 事件 / 快照)
# ---------------------------------------------------------------------------


class _SpyCounter(CalibratedTokenCounter):
    """会记下每次回灌的估算器 (断言「权威值确实回灌了」)."""

    def __init__(self) -> None:
        super().__init__()
        self.notes: list[tuple[Usage | None, Sequence[ModelMessage]]] = []

    def note_usage(self, usage: Usage | None, *, sent: Sequence[ModelMessage]) -> None:
        self.notes.append((usage, sent))
        super().note_usage(usage, sent=sent)


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


async def test_the_authoritative_usage_is_fed_back_with_what_was_sent() -> None:
    """每次调用把 usage 回灌给估算器, 连同**刚发出去的那份** —— 不是账本.

    这是缺陷 1 的接缝级回归 (2026-09-23): 从前这里传的是 `len(state.history)`,
    而权威值对应的是视图 —— 压缩过之后两者条数不等, 估算随之错位.
    """
    history = _history(400, 400, 400, 400)
    usage = Usage(input_tokens=123, total_tokens=150)
    counter = _SpyCounter()
    sent: list[Any] = []

    async def capture(messages: list[ModelMessage]) -> ModelResponse:
        sent.append(messages)
        return text_response("答完了", usage=usage)

    loop = AgentLoop(
        model=MockLLM.scripted([capture]),
        compactor=_policy(threshold_tokens=100_000),  # 不触发, 只看回灌
        counter=counter,
    )

    await loop.run(history)

    assert counter.notes == [(usage, sent[0])], "回灌的那份就是模型收到的那些消息"


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


async def test_a_failed_summary_shows_in_the_frame_instead_of_an_event() -> None:
    """摘要失败不发「已压缩」事件 (什么都没压), 但降级原因**照旧留痕** —— 在帧里.

    旧行为失败时仍裁剪, 于是发一条带 warning 的 context_compacted; 新行为下本轮
    整个不切刀, 一条「压缩过了」的痕迹都不该有 (没压就是没压 —— 前端那行灰字说
    的是进度, 不是诊断). 但那不等于静默: 原因挂在 `metadata.view.warning` 上,
    事后查帧一样答得出来.
    """
    history = _history(400, 400, 400, 400)
    collector = EventCollector()
    saver = InMemoryCheckpointSaver()
    thread_id = "thread-failed-summary"
    loop = AgentLoop(
        model=MockLLM.scripted([text_response("答完了")]),
        # 摘要模型不给正文 —— 降级路径 (真正的失败路径由策略层的用例钉着)
        compactor=_seam_policy(summarizer=_stub_summarizer(None)),
        event_sink=collector,
        saver=saver,
        thread_id=thread_id,
    )

    result = await loop.run(history)

    assert result.content == "答完了"
    assert not [
        event for event in collector.events if event.type is EventType.CONTEXT_COMPACTED
    ], "没压就不该有已压缩的痕迹"
    [frame] = await saver.list_history(thread_id)
    assert frame.metadata.view is not None
    assert "摘要" in str(frame.metadata.view["warning"])


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
    # 新喂的这一段要足够大, 让**投影**(摘要 + 摘要之后的一切, 不裁)超过阈值 ——
    # 判据是投影不是账本, 喂得不够的话这一轮压根不会去压
    second = await continuing.run(
        [*first.messages, *_unit(9, size=900)],
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
        _policy(threshold_tokens=100, keep_recent_questions=1, summarize=False), short
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
        _policy(threshold_tokens=100, keep_recent_questions=1, summarize=False), orphan
    )

    assert compiled.dropped > 0
    assert _wire_legal(compiled.messages)
    assert "c9" not in {m.get("tool_call_id") for m in compiled.messages}


async def test_the_kept_question_survives_an_unmatched_call() -> None:
    """保留段里有欠着结果的调用时, 它原样留着 (压缩不该替它补一条或删一条)."""
    history = [
        SYSTEM,
        _user("订单到哪了"),
        # 被裁的那段要够大: 压完视图里会多一条摘要 (前缀本身就有二三十字), 省不过
        # 它就白压 —— 那属于「省下来不是正的」, 与这条用例想验的事无关
        {"role": "assistant", "content": "我先查一下" + "。" * 60},
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
        # 关掉「省得不够本就不压」那道闸: 这条测的是刀口落在哪, 不是划不划算
        _policy(
            threshold_tokens=100,
            keep_recent_questions=1,
            clear_at_least_ratio=0,
            summarize=False,
        ),
        history,
    )

    assert compiled.dropped > 0
    # 最新那个提问 (含它那条工具结果) 原样保留, 一字未改
    assert compiled.messages[-1] == history[-1]
    assert compiled.messages[-2] == history[-2]
