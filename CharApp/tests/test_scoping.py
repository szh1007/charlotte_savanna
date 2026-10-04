"""工具范围裁剪 (issue 44): 分组表 + 关键词分类器 + 两个钩子.

这一页断的是交付物 1 / 2 / 3 / 4 / 5 ——

1. **分组表**把全部工具盖满, 且与工具名那份权威清单 (`conftest.TOOL_NAMES`) 对齐;
2. **关键词分类器**把一句话分到对的组 (含口语别名与「手机 vs 手机号」那个陷阱);
3. **两个钩子**: 可见集被原地收窄, 范围外的调用被拒, 而理由与护栏那两句分得开;
4. **四行组合表**逐行钉住 —— 裁剪与护栏同挂一个点时的行为, 尤其「拒绝吃掉挂起」;
5. **裁掉的工具真调不到**: 走一次真会话 (MockLLM + 假商城), 断言那一调**没打到商城**,
   而范围内的一调照常打到 (正对照 —— 少了它, 「没打到」也能用「这跑压根不通」解释).

最后那条是本片最要紧的一条: 框架执行时查的是另一本账, 只裁不拒的话模型照样调得到,
两组比的就不是「少给」而是「看不见」—— A/B 的结论当场作废. 它是**证据**不是推演:
真跑一次会话, 看假商城那边收没收到请求.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import TOOL_NAMES, citations_for_tests, retriever_for_tests

from CharAgent.db.entities import ToolCall, ToolCallStatus
from CharAgent.eval import EvalCase, RunFacts, RunOutcome
from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.hooks.utils.types import Verdict
from CharAgent.model import ToolSpec
from CharAgent.tests.mock_llm import (
    MockLLM,
    make_tool_call,
    text_response,
    tool_call_response,
)
from CharAgent.tool import Tool
from CharApp.eval.fixtures import BIG_CART_BUYER, CART
from CharApp.eval.golden import load_cases
from CharApp.eval.harness import EvalHarness
from CharApp.eval.subject import HarnessSubject
from CharApp.minimall.client import DEFAULT_BASE_URL, MinimallClient
from CharApp.minimall.guardrail import (
    ORDER_APPROVAL_PROMPT,
    PLACE_ORDER_TOOL,
    WriteGuardrail,
)
from CharApp.minimall.scoping import (
    FALLBACK_GROUPS,
    OUT_OF_SCOPE_MARK,
    TOOL_GROUPS,
    ToolScope,
    classify,
    tools_of,
)
from CharApp.minimall.tools import build_tools

# 买家 3 —— 假商城里那只 2598.00 的车 (撞不到护栏那条 5000 上限)
BUYER = 3


# ---------------------------------------------------------------------------
# 交付物 1: 分组表
# ---------------------------------------------------------------------------


def test_the_grouping_table_covers_every_tool_exactly_once() -> None:
    """六组正好盖满全部工具: 不多 (拼错的名字)、不少 (忘了归组的)、也不重.

    归组是这个模块的全部依据 —— 一个工具没归组, 它那一组就永远开不到它, 而症状是
    「裁剪组的召回率低」这种看着像模型问题的数字. 清单的权威在 `TOOL_NAMES`
    (跑分与工具那几组用例共用同一份), 这里与它对齐.
    """
    grouped = [name for names in TOOL_GROUPS.values() for name in names]

    assert sorted(grouped) == sorted(TOOL_NAMES)
    assert len(grouped) == len(set(grouped)), "同一个工具不该出现在两组里"


# ---------------------------------------------------------------------------
# 交付物 2: 关键词分类器
# ---------------------------------------------------------------------------

# (题面, 该命中的组) —— 挑的都是**边界**那几种:
#
#   - 一句话踩两组 (「购物车里那件下单」/ 加购那种要两步的)
#   - 口语别名 (买家说「车里」不说「购物车」)
#   - 一个词都没命中 (兜底那两组)
#   - 商品题里出现「手机」(它不能把账户那一组拉进来 —— 那一组的词是「手机号」)
CLASSIFY_CASES: list[tuple[str, tuple[str, ...]]] = [
    ("有什么 2000 块以下的手机推荐吗", ("product",)),
    ("我购物车里现在有什么", ("cart",)),
    ("帮我把红米 Note 13 加进购物车, 要两件", ("product", "cart")),
    ("我车里攒了不少, 帮我一起下单", ("cart", "order")),
    ("订单 202609191230450000031234 我要退款", ("order", "refund")),
    ("我默认的收货地址是哪一个", ("account",)),
    # L5-b 起政策问题有自己的一组: 「几天 / 发货 / 运费」这类问的是**条文**,
    # 与"我的单据"不是一回事 (跨组那种: 一句里既有退款又有天数, 两组都要开)
    ("你们一般几点发货", ("knowledge",)),
    ("退款要几天才能到账", ("refund", "knowledge")),
    ("你们几点上班", FALLBACK_GROUPS),
]


@pytest.mark.parametrize(("question", "groups"), CLASSIFY_CASES)
def test_the_question_lands_in_the_right_groups(
    question: str, groups: tuple[str, ...]
) -> None:
    """一句话 → 该开哪几组 (断言的是**完全相等**: 多开一组也是错)."""
    assert classify(question) == groups


def test_the_phone_product_does_not_drag_the_account_group_in() -> None:
    """「手机」(商品) 与「手机号」(账户) 只差一个字 —— 账户那组不许被商品题拉进来.

    多给一组不是小事: 那一组的工具凭空进了可见集 (实验组悄悄变宽), 而宽的那一组
    在报告上看上去更好.
    """
    assert "account" not in classify("有什么 2000 块以下的手机推荐吗")


def test_every_case_in_the_case_set_is_classified_correctly() -> None:
    """题集里每一道: 期望工具**全都**在裁剪后的可见集里 (装置自检, 不是分类器的分数).

    分错组的那一跑, 期望工具压根没给模型 —— 失败是装置的问题, 不是模型选错了.
    题集加了一道新题而分类器没有对应的词时这条会红: 那是提醒去补词 (或改题),
    不是分类器坏了.
    """
    wrong: list[str] = []
    for case in load_cases():
        visible = tools_of(classify(case.question))
        missing = sorted(set(case.expect_tools) - visible)
        if missing:
            wrong.append(f"{case.id}: 期望的 {missing} 不在可见集 {sorted(visible)} 里")

    assert not wrong, "分类器把这几道题分错了组:\n- " + "\n- ".join(wrong)


# ---------------------------------------------------------------------------
# 交付物 3: 两个钩子 (单元层)
# ---------------------------------------------------------------------------


class FakeCart:
    """只回购物车的替身 (护栏判金额只用得上它; 协议按形状认, 与 MockLLM 同套做法)."""

    def __init__(self, total: str) -> None:
        self.total = total

    async def get_cart(self, *, user_id: int) -> dict[str, Any]:
        """一份够护栏读 `total_amount` 的车 (其余字段它不看)."""
        return {"total_amount": self.total, "items": []}


def all_tools() -> dict[str, Tool]:
    """全部工具, 按名字索引.

    客户端是张空壳 (构造期不发请求, 与 `golden._real_tool_names` 同一个做法) ——
    这些用例要的是**真的那批 Tool 对象** (名字与注解都是权威), 不是一次真调用.
    """
    client = MinimallClient(base_url=DEFAULT_BASE_URL, token="")
    tools = build_tools(
        client,
        BUYER,
        retriever=retriever_for_tests(),
        citations=citations_for_tests(),
    )
    return {item.name: item for item in tools}


def specs_of() -> list[ToolSpec]:
    """全部工具的 wire 列表 (与跑分时装给会话的是同一份形状)."""
    return [tool.to_spec() for tool in all_tools().values()]


def names_in(specs: list[ToolSpec]) -> list[str]:
    """这一份 wire 列表里的工具名 (顺序照原样)."""
    return [str(spec["function"]["name"]) for spec in specs]


def test_the_scope_installs_itself_on_the_two_points_it_needs() -> None:
    """两个钩子各挂一个点: 可见集靠 before_turn, 拦调用靠 before_tool_execute."""
    registry = HookRegistry()
    ToolScope(question="我购物车里现在有什么").install(registry)

    assert registry.handlers(HookPoint.BEFORE_TURN) != ()
    assert registry.handlers(HookPoint.BEFORE_TOOL_EXECUTE) != ()


def test_the_visible_set_is_narrowed_in_place() -> None:
    """收窄是**原地**改那一份列表 (另起一张表等于什么都没发生), 顺序照旧."""
    scope = ToolScope(question="我购物车里现在有什么")
    specs = specs_of()
    before = id(specs)

    scope.keep_visible(tools=specs)

    assert id(specs) == before, "框架递进来的是列表本体: 换一张表模型那边就看不见"
    assert names_in(specs) == [
        name for name in TOOL_NAMES if name in scope.tool_names
    ], "留下的该是范围内那几个, 且顺序不变"


def test_narrowing_twice_changes_nothing() -> None:
    """每轮都裁是幂等的 (裁的依据只有题面那一句) —— 第二轮起是空操作."""
    scope = ToolScope(question="我购物车里现在有什么")
    specs = specs_of()
    scope.keep_visible(tools=specs)
    once = names_in(specs)

    scope.keep_visible(tools=specs)

    assert names_in(specs) == once


def test_a_session_without_tools_is_left_alone() -> None:
    """没有工具时框架给 None (裁它没有意义) —— 这里原样让过, 不抛."""
    scope = ToolScope(question="我购物车里现在有什么")

    scope.keep_visible(tools=None)  # 不抛就算过


def test_a_call_outside_the_scope_is_rejected_with_its_own_reason() -> None:
    """范围外的调用: 拒绝, 且理由带着那个固定标记 (报告靠它认人)."""
    scope = ToolScope(question="有什么 2000 块以下的手机推荐吗")
    decision = scope(tool=all_tools()[PLACE_ORDER_TOOL])

    assert decision is not None
    assert decision.verdict is Verdict.REJECT
    assert OUT_OF_SCOPE_MARK in decision.reason
    assert PLACE_ORDER_TOOL in decision.reason, "得说清是哪一个工具"


def test_a_call_inside_the_scope_is_left_to_the_other_plugins() -> None:
    """范围内的调用不表态 (None) —— 要不要挂起 / 拒绝是护栏那一层的事."""
    scope = ToolScope(question="把购物车里的东西下单吧")

    assert scope(tool=all_tools()[PLACE_ORDER_TOOL]) is None


# ---------------------------------------------------------------------------
# 交付物 4: 四行组合表 (裁剪 + 护栏, 走真的 HookRegistry.decide)
# ---------------------------------------------------------------------------


async def decide_once(question: str, tool_name: str, *, cart_total: str) -> Decision:
    """照框架的调法问一次裁决: 真注册表 + 真裁剪 + 真护栏 (**顺序照装配处**).

    顺序是装配处的事实 (裁剪先挂、护栏后挂), 而它只影响两条都是拒绝时哪句理由先到
    —— 下面第三行要的正是「裁剪那句胜出」.
    """
    registry = HookRegistry()
    ToolScope(question=question).install(registry)
    cart = FakeCart(cart_total)
    # 护栏只按形状要一个 `get_cart` (与 `test_guardrail.py` 同一个替身做法)
    WriteGuardrail(client=cart, user_id=BUYER).install(registry)
    return await registry.decide(
        HookPoint.BEFORE_TOOL_EXECUTE,
        turn=1,
        call=make_tool_call(tool_name),
        tool=all_tools()[tool_name],
    )


# 这四行就是 `scoping.py` 模块 docstring 里那张表, 逐行一条用例 —— 尤其第二行那种
# 「拒绝吃掉挂起」: 它是刻意的 (工具压根没给模型), 但它是**跨插件**的行为, 只能靠
# 用例钉住, 不能靠推演.


async def test_an_out_of_scope_write_tool_is_rejected() -> None:
    """第一行: 范围外的写工具 + 护栏不管它 → 拒绝 (理由来自裁剪)."""
    decision = await decide_once(
        "有什么 2000 块以下的手机推荐吗", "add_to_cart", cart_total=CART["total_amount"]
    )

    assert decision.verdict is Verdict.REJECT
    assert OUT_OF_SCOPE_MARK in decision.reason


async def test_a_rejection_beats_a_suspension_across_plugins() -> None:
    """第二行: 范围外的 `place_order` **本来会被护栏挂起**, 但拒绝优先 → 拒绝.

    这条正是那个刻意的行为: 工具压根没给模型, 就不该弹一张确认卡让人白点一下.
    """
    decision = await decide_once(
        "有什么 2000 块以下的手机推荐吗",
        PLACE_ORDER_TOOL,
        cart_total=CART["total_amount"],
    )

    assert decision.verdict is Verdict.REJECT
    assert OUT_OF_SCOPE_MARK in decision.reason, "该听到「不在范围里」, 不是「超预算」"


async def test_an_in_scope_order_is_suspended_for_the_buyer() -> None:
    """第三行: 范围内的 `place_order` + 没超金额 → 挂起 (裁剪不表态, 护栏说了算)."""
    decision = await decide_once(
        "把购物车里的东西下单吧", PLACE_ORDER_TOOL, cart_total=CART["total_amount"]
    )

    assert decision.verdict is Verdict.REQUIRES_APPROVAL
    assert decision.prompt == ORDER_APPROVAL_PROMPT


async def test_an_in_scope_order_over_the_limit_is_rejected_by_the_guardrail() -> None:
    """第四行: 范围内的 `place_order` + 超金额 → 拒绝 (理由来自护栏, 不带裁剪那句)."""
    decision = await decide_once(
        "把购物车里的东西下单吧", PLACE_ORDER_TOOL, cart_total="8000.00"
    )

    assert decision.verdict is Verdict.REJECT
    assert OUT_OF_SCOPE_MARK not in decision.reason, "这一条是护栏拦的, 别有裁剪的标记"
    assert "5000" in decision.reason, "护栏那句会写清上限是多少"


async def test_the_scope_reason_wins_when_both_would_reject() -> None:
    """第五行 (表上没有, 实际会撞上): 范围外 **且** 超金额 → 说「不在范围里」那句.

    两处都说得通 (这工具没给模型 / 这一单确实超了), 但前者才是这一调的根本原因 ——
    而「哪句先到」由**装配处的注册顺序**定 (框架取第一个拒绝). 这条走端到端 (真会话
    + 真装配) 而不是直接调 `decide_once`: 顺序在 `service.session_for` 那一边, 只有
    走装配才验得到它 —— 换人的那天, 这条会红.

    买家 4 那只车是 8000 元 (`fixtures.BIG_CART`), 题面只提商品 (于是 `place_order`
    不在可见集里), 模型硬点一次 `place_order` —— 两条闸都会说不, 而报告里该留着
    「不在范围里」那一句.
    """
    case = EvalCase(
        id="both-reject",
        question="有什么 2000 块以下的手机推荐吗",
        expect_tools=("place_order",),
        meta={"scene": "product", "buyer_id": BIG_CART_BUYER},
    )
    facts, _, harness = await run_scoped(
        case,
        [
            tool_call_response(make_tool_call(PLACE_ORDER_TOOL, '{"address_id": 5}')),
            text_response("这一单我没法替你下。"),
        ],
    )
    row = the_call_of(harness, facts)

    assert OUT_OF_SCOPE_MARK in (row.result or "")
    assert "5000" not in (row.result or ""), "护栏那句不该抢在裁剪前面"
    assert harness.routes["POST orders/"].called is False


# ---------------------------------------------------------------------------
# 交付物 5: 端到端 —— 裁掉的工具真调不到
# ---------------------------------------------------------------------------


def case_named(case_id: str) -> EvalCase:
    """题集里那一条 (按题号) —— 用例问的是**真题面**, 不另抄一份."""
    return next(case for case in load_cases() if case.id == case_id)


def environment_of(subject: HarnessSubject) -> EvalHarness:
    """这一跑用过的跑分环境 (假商城的路由从它上面取)."""
    harness = subject.harness
    assert harness is not None, "还没跑过: 取数要等 run_once 之后"
    return harness


async def run_scoped(
    case: EvalCase, script: list[Any]
) -> tuple[RunFacts, MockLLM, EvalHarness]:
    """按跑分那条线跑一跑 (裁剪开着), 交回事实 / 那个假大脑 / 环境."""
    model = MockLLM.scripted(script)
    subject = HarnessSubject(model, conversation=f"{case.id}-1", prune_tools=True)
    facts = await subject.run_once(case)
    return facts, model, environment_of(subject)


def the_call_of(harness: EvalHarness, facts: RunFacts) -> ToolCall:
    """这一跑唯一的那条工具调用行 (记录层里那份, 理由在它身上)."""
    rows = harness.calls_of(facts.run_id)
    assert len(rows) == 1, (
        f"这一跑该正好调一次, 实际: {[row.tool_name for row in rows]}"
    )
    return rows[0]


async def test_the_model_only_sees_the_tools_in_the_scope() -> None:
    """模型**收到**的 tools 只有那几组 —— 收窄这一步真的走到了 wire 上."""
    case = case_named("product-01")  # 题面只提商品, 该开的组只有一个
    _, model, _ = await run_scoped(case, [text_response("有货的, 我看看。")])

    sent = names_in(model.calls[0]["tools"])
    assert set(sent) == tools_of(classify(case.question))
    assert len(sent) < len(TOOL_NAMES), "裁剪组要比全挂组少给 (, 否则这一跑白裁)"


async def test_a_cropped_tool_cannot_be_called() -> None:
    """范围外的调用**一次都打不到商城**, 而模型收到那句理由后照样把话说完.

    这一条是「裁掉 ≠ 调不到」那句话的证据: 剧本里那次 `place_order` 是模型硬点的
    (它压根看不见这个工具), 只裁不拒的话它会**真下出去一单** —— 而假商城的
    `POST orders/` 在下面这条断言里一次都没被碰过.
    """
    case = case_named("product-01")
    facts, _, harness = await run_scoped(
        case,
        [
            tool_call_response(make_tool_call(PLACE_ORDER_TOOL, '{"address_id": 5}')),
            text_response("这一单我没法替你下, 请你自己在结算页完成。"),
        ],
    )
    row = the_call_of(harness, facts)

    assert harness.routes["POST orders/"].called is False, "裁掉的工具真调不到"
    assert row.status == ToolCallStatus.FAILED.value, "那一调该是失败的"
    assert OUT_OF_SCOPE_MARK in (row.result or ""), "回填的理由是「不在范围里」那句"
    assert facts.outcome is RunOutcome.COMPLETED, "拦下之后这一跑照样答完"


async def test_a_tool_inside_the_scope_still_reaches_the_mall() -> None:
    """正对照: 范围内的一调照常打到商城并成功 —— 「没打到」不是因为这跑不通."""
    case = case_named("product-01")
    facts, _, harness = await run_scoped(
        case,
        [
            tool_call_response(
                make_tool_call("search_products", '{"keyword": "手机"}')
            ),
            text_response("给你找到几款, 最便宜的是红米 Note 13。"),
        ],
    )
    row = the_call_of(harness, facts)

    assert harness.routes["GET products/"].called is True, "范围内的一调该真打出去"
    assert row.status == ToolCallStatus.SUCCEEDED.value
    assert facts.outcome is RunOutcome.COMPLETED
