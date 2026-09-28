"""跑分环境 (issue 41): 一次问答要用的那套零件, 以及**读回它的轨迹**.

这一页断的是两条验收:

1. **一次问答跑完能读回该次运行的工具轨迹** —— 而且是**只读回这一次的**, 不是
   全库. 这条不是白写的: 记录层的假库按设计不过滤 `WHERE` (见
   `CharAgent/db/testing.py`), 拿仓储的 `list_for_run` 在它上面读会返回全部行 ——
   于是跑分器必须自己按 `run_id` 筛, `calls_of` 就是那一筛.
2. **收尾不炸** —— 假库原先没有 `dispose`, 而服务收尾会调它 (`MinimallService.
   aclose`), 于是「跑完一段问答」会在**模型 / 快照 / 客户端都关掉之后**抛一个与
   真因无关的 `AttributeError`.

模型与商城全走替身 (MockLLM + respx 假商城), 离线可跑 —— 与 `test_recording.py`
同一套写法. 真模型那条路不在 pytest 里 (跑分是独立入口的事).
"""

from __future__ import annotations

from typing import Any

from CharAgent.db.entities import ToolCallStatus
from CharAgent.tests.mock_llm import (
    MockLLM,
    make_tool_call,
    text_response,
    tool_call_response,
)
from CharApp.eval.harness import open_harness


class SpyModel(MockLLM):
    """MockLLM + 一个「被关过了」的记号.

    收尾那条路要能看出它真的走到了模型 —— 只断言「没抛异常」的话, 中途被吞掉的
    那一截照样绿.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        await super().aclose()


async def test_one_question_comes_back_with_that_runs_calls() -> None:
    """问一句 → 读回这一跑调过的那行 (工具 / 终态 / 参数 / 商城真被打到)."""
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("get_my_profile")),
            text_response("余额 9500.00"),
        ]
    )

    async with open_harness(model) as harness:
        session = await harness.session(harness.context("case-01-attempt-1"))
        result = await session.ask("我还有多少钱")
        calls = harness.calls_of(session.last_run_id)

        assert result.content == "余额 9500.00"
        assert [call.tool_name for call in calls] == ["get_my_profile"]
        assert calls[0].status == ToolCallStatus.SUCCEEDED.value
        assert calls[0].run_id == session.last_run_id
        assert harness.routes["GET profile/"].called, "工具没打到商城 (答复就是编的)"


async def test_the_trace_holds_only_that_run() -> None:
    """一段环境里问两次: 每次读回的都是**自己那一条**, 而假库里躺着两条.

    这条就是「只读回这一次的, 不是全库」那条验收 —— 断言落在两个数上 (各 1 条,
    而库里有 2 条): 光看「拿到了想要的那条」是看不出筛没筛的.
    """
    model = MockLLM.scripted(
        [
            # 第一问: 调工具 → 给答复; 第二问同 (脚本是按调用次数弹的)
            tool_call_response(make_tool_call("get_my_profile")),
            text_response("余额 9500.00"),
            tool_call_response(make_tool_call("get_my_cart")),
            text_response("车里有两件"),
        ]
    )

    async with open_harness(model) as harness:
        first = await harness.session(harness.context("case-01"))
        await first.ask("我还有多少钱")
        second = await harness.session(harness.context("case-02"))
        await second.ask("我车里有什么")

        assert first.last_run_id != second.last_run_id, (
            "两段会话本该是两次运行 (不然下面那条断言证明不了什么)"
        )
        assert [call.tool_name for call in harness.calls_of(first.last_run_id)] == [
            "get_my_profile"
        ]
        assert [call.tool_name for call in harness.calls_of(second.last_run_id)] == [
            "get_my_cart"
        ]
        assert len(harness.records.tool_calls) == 2, "假库里两条都在"


async def test_the_record_layer_and_the_memory_agree_on_this_run() -> None:
    """同一次运行: 记录层读回的那几行 与 内存里 `LoopResult.turns` 对得上.

    「同一份事实, 两条路都建得出来」这句话只有这一片能证: 框架侧的自证 (issue 40)
    走的是**内存那条路** (`turns` 里的 `ToolCallFact`), 而读回记录层是这一片才接上
    的那条路 —— issue 41 的更正里点名要的正是它.

    两条各自记着这一跑调了什么 (名字 + 参数原文), 对不上就说明有一条记错了; 而
    跑分的判据全建在记录层那份上, 它错了整套分数跟着错. 一次调两个工具 (同一轮
    并行) 是**故意**的: 同一轮的多条调用在记录层里靠时间戳排序, 那是这条路上唯一
    可能分叉的地方.
    """
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("search_products", '{"keyword": "手机"}'),
                make_tool_call("get_my_profile"),
            ),
            text_response("找到一款, 你的余额是 9500.00"),
        ]
    )

    async with open_harness(model) as harness:
        session = await harness.session(harness.context("case-01"))
        result = await session.ask("有手机吗? 顺便看看我余额")
        calls = harness.calls_of(session.last_run_id)

    from_memory = [
        (fact.tool_name, fact.arguments) for turn in result.turns for fact in turn.calls
    ]
    from_records = [(call.tool_name, call.arguments) for call in calls]

    assert [name for name, _ in from_records] == ["search_products", "get_my_profile"]
    assert from_records == from_memory, "两条路记下来的不是同一份事实"


async def test_a_run_without_calls_reads_back_empty() -> None:
    """不调工具的那一跑读回空 —— 空集与「筛错了」在这条上看得出区别.

    跑分里这类题是有的 (政策咨询那几条期望零调用), 判据要能在空轨迹上算, 所以
    「一问一答零调用」这条路得先成立.
    """
    model = MockLLM.scripted([text_response("我们一般 48 小时内发货")])

    async with open_harness(model) as harness:
        session = await harness.session(harness.context("case-09"))
        result = await session.ask("你们几点发货")
        calls = harness.calls_of(session.last_run_id)

        assert result.content == "我们一般 48 小时内发货"
        assert calls == []


async def test_the_harness_closes_everything_it_built() -> None:
    """退出 `with` 时把模型 / 快照 / 客户端 / 假库一起关掉, 且不炸.

    假库那一环是 issue 41 补上的: `FakeRecordDatabase` 原先没有 `dispose`, 而服务
    收尾会调它 —— 于是这一段在补上之前会抛 `AttributeError`, 而且在其余三件都关掉
    **之后**才抛 (排查时看到的是一个与真因无关的错).
    """
    model = SpyModel([])

    async with open_harness(model) as harness:
        await harness.session(harness.context("case-01"))

    assert model.closed, "收尾没走到模型 —— 那一段中途断了"


async def test_the_trace_carries_the_arguments_the_model_sent() -> None:
    """轨迹里带着模型填的参数原文 —— 参数正确率那条判据要它 (issue 42).

    顺带钉住一件事: 落库的是**模型真发出去的那串 JSON**, 不是工具执行时的解析结果
    (两者在畸形 JSON 上会分叉, 而那正是自纠错路径要看的).
    """
    order_no = "202609191230450000031234"
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("get_my_order", f'{{"order_no": "{order_no}"}}')
            ),
            text_response("那单已经发货了"),
        ]
    )

    async with open_harness(model) as harness:
        session = await harness.session(harness.context("case-03"))
        await session.ask("订单到哪了")
        [call] = harness.calls_of(session.last_run_id)

    assert call.arguments == f'{{"order_no": "{order_no}"}}'
