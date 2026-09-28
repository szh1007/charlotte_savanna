"""eval 跑批器 + 自证 (#58 #62 #63).

**这一页里最要紧的一条是「自证」**: 用一个**与电商无关**的极小业务 (加法 / 喊话
两个工具 + 一道闲聊) 在框架内跑完一整批 —— 建对象、跑、判、出报告、对照. 它同时
证明两件事:

1. 跑分这条流水线是通的 (票据验收第 1 条);
2. 框架**不认识业务** —— 载体里一个电商词都没有, 跑出来的报告里也没有.

第 2 条另有一层框架级的守卫 (`test_agent_provider.py` 的
`test_the_framework_never_mentions_the_business`: 扫全部框架源码找业务词, 新加的
`eval/` 已在它的扫描范围里) —— 这里只用载体再证一遍「跑分这条路上也干净」.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.agent import AgentLoop, LoopGuard, LoopOutcome
from CharAgent.db import ToolCallStatus
from CharAgent.db.state import TOOL_CALL_STATUS_FOR_OUTCOME
from CharAgent.eval import (
    CallRecord,
    EvalCase,
    EvalConfigError,
    EvalGroup,
    EvalRunner,
    EvalStartupError,
    Judgment,
    Metric,
    RunFacts,
    RunOutcome,
    run_outcome_of,
)
from CharAgent.model.utils.types import Usage
from CharAgent.tool import tool

FIXED_NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)

# 每道题的模型决策次数上限 (自证里所有题共用: 配置快照才不会因题而异)
MAX_TURNS = 4


# ---------------------------------------------------------------------------
# 载体业务 (与电商无关的极小实现; 模块顶层, 类型需顶层可见)
# ---------------------------------------------------------------------------


def _add(
    a: int,
    b: int,
) -> int:
    """加法载体: 返回两数之和."""
    return a + b


def _shout(
    text: str,
) -> str:
    """喊话载体: 把文本变成大写."""
    return text.upper()


ADD_TOOL = tool(_add, name="add")
SHOUT_TOOL = tool(_shout, name="shout")

# 载体题集: 五道题各压一条路径 (调对 / 少调 / 零期望 / 撞轮数上限)
CASE_ADD = EvalCase(
    id="q1",
    question="把 2 和 3 加起来",
    expect_tools=("add",),
    expect_args={"add": {"a": 2, "b": 3}},
    meta={"场景": "算术"},
)
CASE_SHOUT = EvalCase(id="q2", question="把 hi 喊出来", expect_tools=("shout",))
CASE_WRONG = EvalCase(id="q3", question="1 加 1 等于几", expect_tools=("add",))
CASE_CHAT = EvalCase(id="q4", question="今天心情不错", expect_tools=())
CASE_TRUNCATED = EvalCase(id="q5", question="一直加下去", expect_tools=("add",))
CASES = (CASE_ADD, CASE_SHOUT, CASE_WRONG, CASE_CHAT, CASE_TRUNCATED)


def _usage(tokens: int) -> Usage:
    """每轮固定用量的 usage (`total_tokens` 是报告里平均 token 那一行的来源)."""
    return Usage(input_tokens=tokens - 5, output_tokens=5, total_tokens=tokens)


def _call(name: str, arguments: str, call_id: str) -> Any:
    """一次工具调用的响应 (带固定 usage, 好让报告里的数不是零)."""
    return tool_call_response(
        make_tool_call(name, arguments, call_id=call_id), usage=_usage(15)
    )


def _say(content: str) -> Any:
    """一次纯文本响应."""
    return text_response(content, usage=_usage(9))


def _scripts(pruned: bool) -> dict[str, list[Any]]:
    """两组的脚本 (差异只在 q2 与 q3 —— 裁剪组少了 `shout` 这个工具).

    裁剪组 (pruned=True) 里模型没法喊话, q2 就答不出来; 但 q3 它反而答对了 ——
    于是差异归因那块两边各有一道题, 不是单向的.
    """
    return {
        "q1": [_call("add", '{"a": 2, "b": 3}', "c1"), _say("5")],
        "q2": (
            [_say("我不会喊")]
            if pruned
            else [_call("shout", '{"text": "hi"}', "c2"), _say("HI")]
        ),
        "q3": (
            [_call("add", '{"a": 1, "b": 1}', "c3"), _say("2")]
            if pruned
            else [_say("1 加 1 等于 2")]
        ),
        "q4": [_say("那就好")],
        # 四轮都在调工具 → 轮数上限把它停下 (截断, 不是答错)
        "q5": [
            _call("add", '{"a": 1, "b": 1}', f"c5-{index}")
            for index in range(MAX_TURNS)
        ],
    }


class TinySubject:
    """载体被测对象: 用 `AgentLoop` + 假大脑跑一道题, 把结果抄成 `RunFacts`.

    它同时是**业务侧怎么写 `EvalSubject`** 的样板 —— 注意 `tool_calls` 是从
    `LoopResult.turns` 里摊平的 (L4 走的是记录层读回那条路, 两条都能建出同一份
    事实, 见 `RunFacts` 的 docstring).
    """

    def __init__(
        self, model: MockLLM, tools: Sequence[Any], *, cost: float | None
    ) -> None:
        self._model = model
        self._tools = list(tools)
        self._cost = cost

    async def run_once(self, case: EvalCase) -> RunFacts:
        """跑一次: 建 loop → 问 → 把结果抄成事实."""
        loop = AgentLoop(self._model, self._tools, guard=LoopGuard(max_turns=MAX_TURNS))
        result = await loop.run([{"role": "user", "content": case.question}])
        return RunFacts(
            tool_calls=tuple(self._calls_of(result)),
            answer=result.content,
            outcome=run_outcome_of(result.outcome),
            error=(
                "" if result.outcome is LoopOutcome.FINISHED else result.outcome.value
            ),
            turns=result.turn_count,
            tokens=result.total_tokens,
            elapsed_ms=result.elapsed_ms,
            run_id=f"run-{case.id}",
            cost=self._cost,
            config=self._config(),
        )

    async def aclose(self) -> None:
        """关掉假大脑 (它没有连接, 这里只是把协议那一半补齐)."""
        return None

    def _calls_of(self, result: Any) -> list[CallRecord]:
        """`LoopResult.turns` 里的事实 → `CallRecord` (按发生序摊平)."""
        records: list[CallRecord] = []
        for turn in result.turns:
            for call in turn.calls:
                records.append(
                    CallRecord(
                        tool_name=call.tool_name,
                        arguments=call.arguments,
                        status=TOOL_CALL_STATUS_FOR_OUTCOME[call.outcome],
                        duration_ms=call.duration_ms,
                    )
                )
        return records

    def _config(self) -> dict[str, Any]:
        """这一批的配置快照 (报告头部那块直接摆它)."""
        return {
            "模型": "mock-llm",
            "工具集": [item.name for item in self._tools],
            "工具数": len(self._tools),
            "刹车": {"max_turns": MAX_TURNS},
            "压缩": {"阈值": 32000, "保留轮数": 2, "工具结果截断": 800},
        }


def _factory(*, pruned: bool = False, cost: float | None = 0.000123) -> Any:
    """造一个「给这道题建一个对象」的工厂 (跑批器每一跑调它一次)."""
    scripts = _scripts(pruned)
    tools = [ADD_TOOL] if pruned else [ADD_TOOL, SHOUT_TOOL]

    async def build(case: EvalCase) -> TinySubject:
        """按题号取脚本, 现造一个假大脑与一个对象."""
        return TinySubject(MockLLM.scripted(scripts[case.id]), tools, cost=cost)

    return build


def _group(
    name: str, *, pruned: bool = False, cost: float | None = 0.000123
) -> EvalGroup:
    """一组跑分 (两组只有工具集与脚本不同 —— 这正是「工具数量 A/B」那个形状)."""
    return EvalGroup(
        name=name, build_subject=_factory(pruned=pruned, cost=cost), cases=CASES
    )


class ToolChoiceJudge:
    """载体判据: 期望的工具都调到了吗 (召回率那一路的分子分母都交出去)."""

    def judge(self, case: EvalCase, facts: RunFacts) -> Judgment:
        """判一次: 少调了哪个工具."""
        wanted = list(case.expect_tools)
        called = set(facts.tool_names)
        missed = [name for name in wanted if name not in called]
        return Judgment(
            ok=not missed,
            reason=""
            if not missed
            else f"少调了 {missed}, 实际调了 {facts.tool_names}",
            metrics={"召回率": Metric(len(wanted) - len(missed), len(wanted))},
        )


class AnswerJudge:
    """载体判据: 有没有给出答复 (不带子指标 —— 考的是 `ok` 那一路)."""

    def judge(self, case: EvalCase, facts: RunFacts) -> Judgment:
        """判一次: 答复非空才算过."""
        ok = bool(facts.answer and facts.answer.strip())
        return Judgment(ok=ok, reason="" if ok else "没给出答复")


def _runner() -> EvalRunner:
    """两个判据的跑批器 (载体业务用)."""
    return EvalRunner(judges={"工具选择": ToolChoiceJudge(), "答复": AnswerJudge()})


# ---------------------------------------------------------------------------
# 词汇
# ---------------------------------------------------------------------------


def test_a_loop_outcome_maps_to_whether_this_run_counts() -> None:
    """结束原因 → 四分类: 跑完才算数, 三道刹车与反复截断都归「截断」."""
    assert run_outcome_of(LoopOutcome.FINISHED) is RunOutcome.COMPLETED
    assert run_outcome_of(LoopOutcome.TOKEN_BUDGET) is RunOutcome.TRUNCATED
    assert run_outcome_of(LoopOutcome.MAX_TURNS) is RunOutcome.TRUNCATED
    assert run_outcome_of(LoopOutcome.TIME_LIMIT) is RunOutcome.TRUNCATED
    assert run_outcome_of(LoopOutcome.TRUNCATION_LIMIT) is RunOutcome.TRUNCATED
    assert run_outcome_of(LoopOutcome.SUSPENDED) is RunOutcome.SUSPENDED
    assert run_outcome_of(LoopOutcome.SERVER_INTERRUPTED) is RunOutcome.BROKEN


def test_a_metric_without_a_denominator_has_no_rate() -> None:
    """零期望样本没有分母: 比率是 None 而不是 0 (两者在报告里必须分得开)."""
    assert Metric(0, 0).rate is None
    assert Metric(0, 2).rate == 0.0
    assert Metric(3, 4).rate == 0.75


def test_only_a_completed_run_counts() -> None:
    """只有跑完的跑次进判据的分母 (L4 口径: 截断不算答错)."""
    assert RunFacts(outcome=RunOutcome.COMPLETED).counted is True
    assert RunFacts(outcome=RunOutcome.TRUNCATED).counted is False
    assert RunFacts(outcome=RunOutcome.SUSPENDED).counted is False
    assert RunFacts(outcome=RunOutcome.BROKEN).counted is False


# ---------------------------------------------------------------------------
# 自证: 极小业务跑通整条流水线
# ---------------------------------------------------------------------------


async def test_a_tiny_business_runs_the_whole_batch() -> None:
    """自证: 两组 x 五题 x 三次, 跑完出报告 (JSON 与 Markdown 都成形)."""
    report = await _runner().run(
        [_group("全挂"), _group("裁剪", pruned=True)], times=3, now=FIXED_NOW
    )

    assert report.times == 3
    assert [group.name for group in report.groups] == ["全挂", "裁剪"]
    assert len(report.group("全挂").attempts) == len(CASES) * 3

    data = report.to_dict()
    assert data["schema_version"] == 1
    assert data["generated_at"] == FIXED_NOW.isoformat()
    assert data["judges"] == ["工具选择", "答复"]
    assert [group["name"] for group in data["groups"]] == ["全挂", "裁剪"]

    markdown = report.to_markdown()
    assert markdown.startswith("# 跑分报告")


async def test_the_tiny_business_speaks_no_business_words() -> None:
    """全流程零电商词 —— 报告的文本与 JSON 里都不许出现业务词.

    这一条与 `PLAN.md` §6.1 那条通用性冒烟同源: 「框架不认识业务」不是形容词,
    是一条能红的断言.
    """
    report = await _runner().run(
        [_group("全挂"), _group("裁剪", pruned=True)], times=1, now=FIXED_NOW
    )
    text = report.to_markdown() + report.to_json()
    for word in ("订单", "退款", "商城", "余额", "商品", "买家", "收货"):
        assert word not in text, f"报告里出现了业务词 {word!r}: {text[:200]}"


async def test_the_second_group_is_better_on_one_case_and_worse_on_another() -> None:
    """载体两组的差是**双向**的 (差异归因那块两列都非空, 不是单向碾压)."""
    report = await _runner().run(
        [_group("全挂"), _group("裁剪", pruned=True)], times=3, now=FIXED_NOW
    )
    data = report.to_dict()
    full = {case["id"]: case["rolled"] for case in data["groups"][0]["cases"]}
    pruned = {case["id"]: case["rolled"] for case in data["groups"][1]["cases"]}

    assert full["q2"]["rate"] == 1.0
    assert pruned["q2"]["rate"] == 0.0
    assert full["q3"]["rate"] == 0.0
    assert pruned["q3"]["rate"] == 1.0


async def test_a_truncated_run_does_not_count_as_a_wrong_answer() -> None:
    """撞轮数上限的那道题: 三跑全截断, 一次都不进判据的分母.

    这正是 L4 那条口径的证据 —— 若把截断算作答错, 长前缀那一组的分数会被前缀长度
    压低, 而那个差异与「工具选得对不对」毫无关系.
    """
    report = await _runner().run([_group("全挂")], times=3, now=FIXED_NOW)
    group = report.to_dict()["groups"][0]
    summary = group["summary"]

    assert summary["outcomes"]["truncated"] == 3
    assert summary["outcomes"]["completed"] == 12
    assert summary["counted"] == 12

    truncated = [
        attempt
        for case in group["cases"]
        if case["id"] == "q5"
        for attempt in case["attempts"]
    ]
    assert {attempt["outcome"] for attempt in truncated} == {"truncated"}
    assert {attempt["error"] for attempt in truncated} == {"max_turns"}

    # 截断的那些跑次照样在报告里 (如实计数), 只是不算错
    assert truncated[0]["judgments"]["答复"]["ok"] is False
    assert group["cases"][4]["rolled"]["counted"] == 0
    assert group["cases"][4]["rolled"]["rate"] is None


async def test_a_zero_expectation_case_has_no_denominator() -> None:
    """零期望那道题: 一个工具都没调也算满分, 但它的比率不进池 (没有分母)."""
    report = await _runner().run([_group("全挂")], times=3, now=FIXED_NOW)
    summary = report.to_dict()["groups"][0]["summary"]
    recall = summary["judges"]["工具选择"]["metrics"]["召回率"]

    # 进池的是三道有期望的题各 3 跑: q1 3/3 + q2 3/3 + q3 0/3 = 6/9
    # (q4 零期望没有分母, 单列; q5 三跑全截断, 压根不进池)
    assert recall["hit"] == 6
    assert recall["total"] == 9
    assert recall["no_denominator"] == 3
    assert summary["zero_call"] == 6
    assert summary["judges"]["工具选择"]["ok"] == 9
    assert summary["judges"]["工具选择"]["total"] == 12


# ---------------------------------------------------------------------------
# 跑批器的四条纪律
# ---------------------------------------------------------------------------


async def test_every_attempt_gets_a_fresh_subject() -> None:
    """每一跑现造一个对象, 跑完就关 —— 题与题之间不共会话 (轨迹才不串)."""
    built: list[TinySubject] = []
    closed: list[TinySubject] = []

    def factory(pruned: bool) -> Any:
        base = _factory(pruned=pruned)

        async def build(case: EvalCase) -> TinySubject:
            subject = await base(case)
            built.append(subject)
            return _TrackedSubject(subject, closed)

        return build

    group = EvalGroup(name="全挂", build_subject=factory(False), cases=CASES[:2])
    await _runner().run([group], times=2, now=FIXED_NOW)

    # 开跑前自检多造一个 (造完就关), 于是 2 题 x 2 跑 + 1
    assert len(built) == 5
    assert len(closed) == 5
    assert len({id(item) for item in built}) == 5


class _TrackedSubject:
    """包一层只为记录「关过了没有」(协议是结构化的, 不必继承)."""

    def __init__(self, inner: TinySubject, closed: list[TinySubject]) -> None:
        self._inner = inner
        self._closed = closed

    async def run_once(self, case: EvalCase) -> RunFacts:
        """转发给里面的真对象."""
        return await self._inner.run_once(case)

    async def aclose(self) -> None:
        """记一笔再转发."""
        self._closed.append(self._inner)
        await self._inner.aclose()


async def test_a_run_that_blows_up_is_recorded_and_the_batch_goes_on() -> None:
    """`run_once` 抛出去的那一跑记成 BROKEN, 后面的题照跑 (凭空少一条最难查)."""

    class Exploding:
        """跑不动但关得掉的对象 (`run_once` 抛)."""

        def __init__(self, inner: TinySubject, explodes: bool) -> None:
            self._inner = inner
            self._explodes = explodes

        async def run_once(self, case: EvalCase) -> RunFacts:
            """该炸的这一跑炸掉, 其余照常."""
            if self._explodes:
                raise RuntimeError("上游断了")
            return await self._inner.run_once(case)

        async def aclose(self) -> None:
            """转发."""
            await self._inner.aclose()

    base = _factory(cost=None)
    seen = {"count": 0}

    async def build(case: EvalCase) -> Exploding:
        """第 3 个造出来的对象 (q1 的第 2 跑) 跑不动."""
        seen["count"] += 1
        return Exploding(await base(case), explodes=seen["count"] == 3)

    group = EvalGroup(name="全挂", build_subject=build, cases=CASES[:2])
    report = await _runner().run([group], times=2, now=FIXED_NOW)
    attempts = report.group("全挂").attempts

    assert len(attempts) == 4
    broken = [item for item in attempts if item.facts.outcome is RunOutcome.BROKEN]
    assert len(broken) == 1
    assert "上游断了" in broken[0].facts.error
    assert broken[0].judgments == {}
    assert report.to_dict()["groups"][0]["summary"]["outcomes"]["broken"] == 1


async def test_a_judge_that_raises_does_not_take_the_batch_down() -> None:
    """判据抛错记成一条 judge_error 继续跑 —— 而且**不算作判为不过**."""

    class ExplodingJudge:
        """每道题都抛的判据 (一次笔误)."""

        def judge(self, case: EvalCase, facts: RunFacts) -> Judgment:
            """永远抛."""
            raise ValueError("判据写错了")

    runner = EvalRunner(
        judges={
            "工具选择": ToolChoiceJudge(),
            "坏判据": ExplodingJudge(),
        }
    )
    report = await runner.run([_group("全挂")], times=1, now=FIXED_NOW)
    group = report.to_dict()["groups"][0]
    attempt = group["cases"][0]["attempts"][0]

    assert attempt["judge_errors"] == {"坏判据": "ValueError: 判据写错了"}
    assert "坏判据" not in attempt["judgments"]
    assert group["summary"]["judge_errors"] == len(CASES)
    # 坏掉的那一路没有结论, 于是它既不进分子也不进分母 (q5 截断不进池, 故是 4 不是 5)
    assert group["summary"]["judges"]["坏判据"]["total"] == 0
    assert group["summary"]["judges"]["坏判据"]["errors"] == len(CASES) - 1
    assert group["summary"]["judges"]["工具选择"]["total"] == 4


async def test_the_startup_probe_aborts_before_any_case_runs() -> None:
    """开跑前自检: 连第一个对象都造不出来就当场抛, 一道题都不跑."""
    ran: list[str] = []

    async def build(case: EvalCase) -> TinySubject:
        """恒失败 (缺 key 就是这个形状)."""
        ran.append(case.id)
        raise RuntimeError("DEEPSEEK_API_KEY 没配")

    group = EvalGroup(name="全挂", build_subject=build, cases=CASES)
    with pytest.raises(EvalStartupError) as info:
        await _runner().run([group], times=1, now=FIXED_NOW)

    assert "开跑前自检没过" in str(info.value)
    assert "DEEPSEEK_API_KEY" in str(info.value)
    assert isinstance(info.value.__cause__, RuntimeError)
    assert ran == ["q1"]


async def test_every_group_is_probed_not_just_the_first() -> None:
    """两组各有各的装配 —— 只查第一组会让第二组的缺 key 留到跑到一半才现身.

    issue 44 的两组就是两个工厂 (裁剪组的模型客户端与全挂组不是一个), 所以自检
    必须逐组走一遍.
    """
    ran: list[str] = []

    async def good(case: EvalCase) -> TinySubject:
        """第一组: 正常."""
        ran.append(f"好:{case.id}")
        return await _factory(cost=None)(case)

    async def bad(case: EvalCase) -> TinySubject:
        """第二组: 装配不起来 (缺 key 就是这个形状)."""
        ran.append(f"坏:{case.id}")
        raise RuntimeError("第二组的 key 没配")

    groups = [
        EvalGroup(name="全挂", build_subject=good, cases=CASES[:1]),
        EvalGroup(name="裁剪", build_subject=bad, cases=CASES[:1]),
    ]
    with pytest.raises(EvalStartupError) as info:
        await _runner().run(groups, times=1, now=FIXED_NOW)

    assert "组 '裁剪'" in str(info.value)
    # 两道题一次都没正式跑过: 两个工厂各只被自检叫了一次
    assert ran == ["好:q1", "坏:q1"]


async def test_the_probe_builds_and_closes_without_running() -> None:
    """自检只造一个对象就关掉, 不跑它 (自检不花 model 的钱)."""
    built: list[TinySubject] = []
    closed: list[TinySubject] = []
    base = _factory(pruned=False, cost=None)

    async def build(case: EvalCase) -> _TrackedSubject:
        """造一个记名的对象."""
        subject = _TrackedSubject(await base(case), closed)
        built.append(subject)
        return subject

    group = EvalGroup(name="全挂", build_subject=build, cases=CASES[:1])
    await _runner().run([group], times=1, now=FIXED_NOW)

    assert len(built) == 2  # 自检 1 + 正式 1
    assert len(closed) == 2


@pytest.mark.parametrize(
    ("keyword", "message"),
    [
        ({"times": 0}, "times 必须 >= 1"),
        ({"groups": []}, "一组都没有"),
        ({"times": 1}, None),
    ],
)
async def test_the_parameters_are_checked_before_anything_runs(
    keyword: Mapping[str, Any], message: str | None
) -> None:
    """参数不合法在开跑之前就报 (跑了一半才发现要重来最亏)."""
    groups = keyword.get("groups", [_group("全挂")])
    times = keyword.get("times", 1)
    if message is None:
        await _runner().run(groups, times=times, now=FIXED_NOW)
        return
    with pytest.raises(EvalConfigError) as info:
        await _runner().run(groups, times=times, now=FIXED_NOW)
    assert message in str(info.value)


async def test_three_groups_are_refused() -> None:
    """对照是两两比, 三组以上表达不了 —— 与其含糊地只比前两组, 不如当场说清."""
    groups = [_group("一"), _group("二"), _group("三")]
    with pytest.raises(EvalConfigError) as info:
        await _runner().run(groups, times=1, now=FIXED_NOW)
    assert "最多 2 组" in str(info.value)


async def test_duplicated_group_names_and_case_ids_are_refused() -> None:
    """组名与题号都必须唯一 (报告靠它们区分两列与两行)."""
    with pytest.raises(EvalConfigError) as info:
        await _runner().run([_group("同名"), _group("同名")], times=1, now=FIXED_NOW)
    assert "组名重复" in str(info.value)

    twice = EvalCase(id="q1", question="重复的题号")
    group = EvalGroup(
        name="唯一",
        build_subject=_factory(pruned=False, cost=None),
        cases=[twice, twice],
    )
    with pytest.raises(EvalConfigError) as info:
        await _runner().run([group], times=1, now=FIXED_NOW)
    assert "题号重复" in str(info.value)


async def test_an_empty_judge_table_still_runs() -> None:
    """没配判据也能跑 (只想知道轮数与花销时用得上) —— 报告里判据那几行是空的."""
    report = await EvalRunner(judges={}).run([_group("全挂")], times=1, now=FIXED_NOW)
    group = report.to_dict()["groups"][0]

    assert group["judges"] == []
    assert group["summary"]["judges"] == {}
    assert group["summary"]["counted"] == 4
    assert group["badcases"] == []


async def test_calling_a_closed_subject_is_not_the_runners_problem() -> None:
    """关对象时炸了不影响这一跑的结论 —— 账已经记在 `facts` 里了.

    也不影响开跑前自检: 自检验的是「装得出来吗」, 而关不上的对象照样是装出来了.
    """

    class GrumpyClose:
        """跑得动但关不上的对象 (关的时候抛)."""

        def __init__(self, inner: TinySubject) -> None:
            self._inner = inner

        async def run_once(self, case: EvalCase) -> RunFacts:
            """转发."""
            return await self._inner.run_once(case)

        async def aclose(self) -> None:
            """恒抛."""
            raise RuntimeError("连接池已经断了")

    base = _factory(pruned=False, cost=None)

    async def build(case: EvalCase) -> GrumpyClose:
        """造一个关不上的对象."""
        return GrumpyClose(await base(case))

    group = EvalGroup(name="全挂", build_subject=build, cases=CASES[:1])
    report = await _runner().run([group], times=1, now=FIXED_NOW)

    assert report.group("全挂").attempts[0].facts.outcome is RunOutcome.COMPLETED


def test_tool_call_status_mapping_is_the_frameworks_own() -> None:
    """载体拼 `CallRecord` 用的是框架那张映射表 (不是自己新写一套)."""
    assert TOOL_CALL_STATUS_FOR_OUTCOME  # 存在即可: 内容由 db 侧用例守着
    assert ToolCallStatus.SUCCEEDED.value == "succeeded"
