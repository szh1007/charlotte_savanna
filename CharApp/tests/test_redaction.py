"""工具事件脱敏: 载荷换成人话, 敏感数据不出本进程 (ADR-0003).

测的就三件事:

1. **该藏的藏了**: 订单号 / 地址 / 余额 / slug 这些工具返回正文与参数原文, 脱敏
   之后一个都搜不到 —— 断言在**序列化后的整段**上搜 (那才是浏览器 devtools 里
   看得到的东西), 而不是逐个字段比对 (漏掉一个字段就假绿).
2. **该留的留了**: `tool_name` / `tool_call_id` / `status` / `duration_ms` / `turn`
   一个不少 —— 演示时要靠 `tool_name` 讲「模型选了哪个工具」.
3. **别的没动**: `thinking` / `reasoning` / `final` / `error` 的载荷原样 (那是模型
   自己的话与终局事实, 遮掉它们会让调试与演示都失去意义).

样本用框架自己的载荷构造器 (`tool_call_data` / `tool_result_data`) 造, 不手写
dict —— 事件长什么样是框架的契约, 抄一份进测试就等于把它冻在这里.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from conftest import ORDER_NO, PROFILE, TOOL_NAMES

from CharAgent.agent.utils.events import tool_call_data, tool_result_data
from CharAgent.stream import EventSink, EventType, StreamEvent
from CharAgent.tests.mock_llm import make_tool_call
from CharAgent.tool import ToolExecution
from CharApp.minimall.redaction import (
    FALLBACK_PHRASE,
    KEPT_KEYS,
    LABEL_KEY,
    TOOL_PHRASES,
    phrase_for,
    redact,
    redacting_sink,
)

# 一次工具调用的样本: 参数里带着订单号 (和模型真会填的一样)
ORDER_CALL = make_tool_call("get_my_order", f'{{"order_no": "{ORDER_NO}"}}')

# 话术里不该出现的字符 (拉丁字母): 页面上显示的是中文短语
_LATIN = re.compile(r"[A-Za-z]")

# 工具返回的正文样本: 序列化后的订单详情 (订单号 / 收货人 / 金额 / 余额都在里面)
ORDER_BODY = json.dumps(
    {**PROFILE, "order_no": ORDER_NO, "receiver_name": "张三", "slug": "iphone-17-pro"},
    ensure_ascii=False,
)


def call_event(name: str = "get_my_order", arguments: str = "{}") -> StreamEvent:
    """一个真的 `tool_call` 事件 (载荷按框架的构造器来)."""
    call = make_tool_call(name, arguments)
    data = tool_call_data(call, turn=1)
    return StreamEvent(type=EventType.TOOL_CALL, seq=1, data=data)


def result_event(name: str = "get_my_order", *, ok: bool = True) -> StreamEvent:
    """一个真的 `tool_result` 事件 (成功带正文 / 失败带可操作错误)."""
    call = make_tool_call(name, "{}")
    execution = ToolExecution(
        tool_name=name,
        ok=ok,
        content=ORDER_BODY if ok else "",
        error=None if ok else f"商城拒绝了这次操作 (order_not_found): {ORDER_NO}",
        duration_ms=12.5,
    )
    data = tool_result_data(call, execution, turn=1)
    return StreamEvent(type=EventType.TOOL_RESULT, seq=2, data=data)


def as_text(event: StreamEvent) -> str:
    """事件序列化成一整段文本 —— 浏览器拿到的东西就是这个形状."""
    return json.dumps(event.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# 该藏的藏了
# ---------------------------------------------------------------------------


def test_a_tool_call_loses_its_arguments() -> None:
    """参数原文整段消失, 换上的是一句中文短语.

    断言在**整段序列化**上搜 `ORDER_NO`: 逐个字段检查的话, 只要还有一处没删干净
    (比如漏了 `arguments`) 就会假绿 —— 而那正是要防的失效.
    """
    event = call_event("get_my_order", f'{{"order_no": "{ORDER_NO}"}}')

    redact(event)

    assert ORDER_NO not in as_text(event), "订单号还留在事件里"
    assert "arguments" not in event.data
    assert event.data[LABEL_KEY] == TOOL_PHRASES["get_my_order"].calling


def test_a_successful_result_loses_the_body() -> None:
    """工具返回的正文 (订单号 / 收货人 / 余额 / slug) 一个字都不留."""
    event = result_event("get_my_order", ok=True)

    redact(event)

    text = as_text(event)
    for leak in (ORDER_NO, "张三", PROFILE["balance"], "iphone-17-pro"):
        assert leak not in text, f"工具返回正文里的 {leak!r} 漏到了事件上"
    assert "summary" not in event.data
    assert event.data[LABEL_KEY] == TOOL_PHRASES["get_my_order"].done


def test_a_failed_result_loses_the_error() -> None:
    """失败那一支同样: 错误正文 (里面往往带着单号) 换成一句「没成功」."""
    event = result_event("get_my_order", ok=False)

    redact(event)

    assert ORDER_NO not in as_text(event), "错误文案里的订单号漏了"
    assert "error" not in event.data
    assert event.data[LABEL_KEY] == TOOL_PHRASES["get_my_order"].failed


def test_a_result_without_status_ok_counts_as_failed() -> None:
    """`status` 不是 `ok` 一律按失败说 —— 少说一句「成功」比说反了强."""
    event = call_event("get_my_order")
    event.type = EventType.TOOL_RESULT
    event.data["status"] = None

    redact(event)

    assert event.data[LABEL_KEY] == TOOL_PHRASES["get_my_order"].failed


# ---------------------------------------------------------------------------
# 该留的留了 (演示与页面都靠它们)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event", [call_event(), result_event()])
def test_the_kept_fields_survive(event: StreamEvent) -> None:
    """白名单里的字段一个都不动: 工具名 / 调用编号 / 状态 / 耗时 / 轮次."""
    before = {key: event.data[key] for key in KEPT_KEYS if key in event.data}

    redact(event)

    assert {key: event.data[key] for key in KEPT_KEYS if key in event.data} == before
    assert event.data["tool_name"] == "get_my_order", "演示时要靠它讲模型选了哪个工具"


def test_the_label_replaces_rather_than_joins() -> None:
    """脱敏之后载荷里**只剩**白名单与 label —— 这是白名单式脱敏的意思.

    写成「删掉已知敏感字段」也能过前面几条, 但框架以后往载荷里加一个
    `order_snapshot`, 它会直接进浏览器. 这条用例把方向钉死: 没在表里的键,
    默认进不来.
    """
    event = result_event("get_my_order")
    event.data["something_new"] = ORDER_NO  # 假设框架以后加了一个字段

    redact(event)

    assert set(event.data) == (KEPT_KEYS & set(event.data)) | {LABEL_KEY}
    assert ORDER_NO not in as_text(event)


# ---------------------------------------------------------------------------
# 别的没动
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (EventType.THINKING, {"message": "我先查一下订单"}),
        (EventType.REASONING, {"delta": "订单号是 " + ORDER_NO, "turn": 1}),
        (EventType.FINAL, {"content": "你的订单已发货", "tokens": 12}),
        (EventType.ERROR, {"error": {"code": "time_limit", "message": "超时"}}),
    ],
)
def test_the_other_events_are_left_alone(kind: EventType, data: dict[str, Any]) -> None:
    """模型自己的话与终局事实原样保留 (ADR-0003 的取舍).

    `reasoning` 里会有订单号 —— 那是模型复述刚查到的东西, 遮掉它会让 L3 的
    「当时它看到了什么」不可解释; 真要收敛是 prompt 的事 (L4), 不是展示层的.
    """
    event = StreamEvent(type=kind, seq=3, data=dict(data))

    redact(event)

    assert event.data == data


def test_a_stranger_tool_gets_a_neutral_phrase() -> None:
    """表里没有的工具: 兜底话术里**不含工具名** —— 页面上一行半英文比模糊更糟.

    注意 `tool_name` 本身是**故意留着**的 (演示时讲「模型选了哪个工具」靠它), 所以
    这里查的是那句话术, 不是整段载荷 —— 页面上显示的是话术.
    """
    event = call_event("some_future_tool")

    redact(event)

    assert event.data[LABEL_KEY] == FALLBACK_PHRASE.calling
    assert "some_future_tool" not in event.data[LABEL_KEY]


# ---------------------------------------------------------------------------
# 标签表本身
# ---------------------------------------------------------------------------


def test_every_tool_has_something_to_say() -> None:
    """**18 个工具一个不少** —— 少一个的表现是页面上那一行掉进兜底.

    两边都要钉: 少了 = 买家看到一句模糊的话 (而且开发时看不出来); 多了 = 表里留着
    一个已经不存在的工具, 没人会去删.
    """
    assert set(TOOL_PHRASES) == set(TOOL_NAMES)


def test_no_phrase_leaks_an_english_name() -> None:
    """话术里不出现任何拉丁字母 —— 命令行走过一道道中文化的门, 别在最后一步漏了.

    判据查的是**拉丁字母**, 不是「整句不是纯 ASCII」: 后者只要句子里有一个汉字就
    通过, 而 `正在处理 add_to_cart` 这种半英文恰恰是要拦下的那一种 (兜底话术带着
    工具名, 页面上就是这么漏出去的).
    """
    for phrase in [*TOOL_PHRASES.values(), FALLBACK_PHRASE]:
        for line in phrase:
            assert _LATIN.search(line) is None, f"这句话里混了英文: {line!r}"


def test_the_phrase_for_an_unknown_name_is_the_fallback() -> None:
    """查表只认工具名; 没配过的名字走兜底 (不炸, 也不编)."""
    assert phrase_for("never_seen") is FALLBACK_PHRASE
    assert phrase_for("get_my_order") is TOOL_PHRASES["get_my_order"]


# ---------------------------------------------------------------------------
# 出口包装
# ---------------------------------------------------------------------------


async def test_the_wrapper_redacts_before_handing_the_event_on() -> None:
    """包装出来的出口: 原出口收到的**就是脱敏后的那个对象** (同一个, 不是副本).

    同一个对象这一点值得钉: 事件是框架按契约发出来的那一份, 换新对象会让
    「脱敏的那份」与「别处还在用的那份」分家, 而分家之后总有一处是原文.
    """
    seen: list[StreamEvent] = []
    original = call_event("add_to_cart", '{"slug": "iphone-17-pro"}')

    await redacting_sink(seen.append)(original)

    assert seen == [original], "原出口没收到那个事件对象"
    assert seen[0].data[LABEL_KEY] == TOOL_PHRASES["add_to_cart"].calling
    assert "iphone-17-pro" not in as_text(seen[0])


async def test_the_wrapper_also_takes_an_async_sink() -> None:
    """异步出口照样能包 (框架的 `EventSink` 两种都允许)."""
    seen: list[StreamEvent] = []

    async def collator(event: StreamEvent) -> None:
        seen.append(event)

    await redacting_sink(collator)(call_event("get_my_cart"))

    assert [event.data[LABEL_KEY] for event in seen] == [
        TOOL_PHRASES["get_my_cart"].calling
    ]


async def test_the_wrapper_keeps_a_sync_sink_sync() -> None:
    """包出来的出口对同步出口**不额外等一次** —— 它自己是个协程, 调法不变.

    这条守的是形状: 服务进程递进来的是框架的同步路由 (`EventRouter`), 包一层之后
    框架仍按 `EventSink` 调用它 (同步或异步都接受) —— 谁都不需要知道里面包过.
    """

    def sink(event: StreamEvent) -> None:  # 同步出口: 返回 None
        seen.append(event.data[LABEL_KEY])

    seen: list[str] = []
    wrapped: EventSink = redacting_sink(sink)

    await wrapped(result_event("get_my_profile"))

    assert seen == [TOOL_PHRASES["get_my_profile"].done]
