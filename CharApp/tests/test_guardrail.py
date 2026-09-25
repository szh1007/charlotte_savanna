"""护栏插件: 写操作预算 8 次 + 单笔金额上限 5000 元 + 代付挂起.

这一条插件是 L2 的验收核心 —— 那时 17 个工具里有 7 个能改数据, 而框架级的人工确认
(L3) 还没来, 所以「模型连环下单」这件事全靠它拦. 测的就两件事:

1. **拦得住**: 越界的那一次不执行 (商城那边一次请求都收不到), 理由回填给模型;
2. **不误伤**: 只读操作一律不管; 预算内, 金额内的写操作照常放行; 新一轮运行归零.

L3b 起多了第三种表态 (issue 35): 代付那一条不说"不行", 说"等一下" —— 工具不执行,
整次运行停在那里等人给结论. 它对预算的用法与前两条一样 (**拒绝优先于挂起**: 该拒
的当场拒, 别让人白输一次密码).

守卫的另一半 (身份与密码不进 schema) 在 `test_provider.py`; 端到端那一次在
`test_cli.py` 与 `test_server.py` —— 这里测的是规则本身.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import BUYER_ID, WRITE_TOOL_NAMES

from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.hooks.utils.types import Verdict
from CharAgent.tests.mock_llm import make_tool_call
from CharAgent.tool import Tool
from CharApp.minimall.client import MinimallError
from CharApp.minimall.guardrail import (
    MAX_ORDER_AMOUNT,
    PAY_APPROVAL_PROMPT,
    PAY_ORDER_TOOL,
    PLACE_ORDER_TOOL,
    WRITE_BUDGET,
    WriteGuardrail,
)
from CharApp.minimall.tools import (
    PAYMENT_PASSWORD_FIELD,
    WRITE_ANNOTATION_KEY,
    build_tools,
)

# 假购物车: 护栏判金额只用得上 `get_cart` 这一个方法, 所以替身也就只有它
# (协议按形状认, 与 MockLLM 同一套做法 —— 不必为它造一个完整的 MinimallClient).
CART_UNDER_LIMIT = "2598.00"


class FakeCart:
    """只回购物车的替身; 记下被问过几次 (判「有没有白问」要用)."""

    def __init__(
        self, total: str = CART_UNDER_LIMIT, *, error: Exception | None = None
    ) -> None:
        self.total = total
        self.error = error
        self.calls = 0

    async def get_cart(self, *, user_id: int) -> dict[str, Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"items": [], "total_count": 0, "total_amount": self.total}


def tools_of(mall: FakeCart) -> dict[str, Tool]:
    """**真的**工具集 (带真的 annotations) —— 护栏靠它认人, 替身工具就没意义了."""
    return {item.name: item for item in build_tools(mall, BUYER_ID)}  # type: ignore[arg-type]


async def verdict(
    guardrail: WriteGuardrail, mall: FakeCart, name: str
) -> Decision | None:
    """按框架的调法问一次裁决 (载荷是关键字参数, 见 HookRegistry.decide).

    载荷里多给的 `turn` / `call` 不是摆设: 框架每次都传,**护栏用不上而已** ——
    这里照它的形状传, 证明多给字段不会把插件打挂.
    """
    return await guardrail(
        turn=7,
        call=make_tool_call(name),
        tool=tools_of(mall)[name],
    )


def make_guardrail(mall: FakeCart) -> WriteGuardrail:
    """装一条护栏 (买家身份与工具闭包用的是同一个来源)."""
    return WriteGuardrail(client=mall, user_id=BUYER_ID)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 只读那一半: 一律不管
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["search_products", "get_my_cart", "get_my_order"])
async def test_read_only_calls_are_never_blocked(name: str) -> None:
    """只读操作不占预算, 也不看金额 —— 买家问多少句话都不该被拦."""
    mall = FakeCart(total="9900.00")  # 连金额超限都不管: 它不是下单
    guardrail = make_guardrail(mall)

    for _ in range(WRITE_BUDGET * 3):
        assert await verdict(guardrail, mall, name) is None

    assert guardrail.used == 0
    assert mall.calls == 0, "只读调用连购物车都不该去问"


async def test_the_marking_is_exactly_the_data_changing_tools() -> None:
    """会改数据的**每一个**都打了标记, 其余一个都没打 —— 两边都要钉.

    少打一个 = 那道闸少一格; 多打一个 = 买家白占一次额度 (他会发现"我就问了几句
    退款, 怎么就不能下单了").

    唯一值得单独点出来的边界是 `list_my_refunds`: 它与那 7 个是同一批加进来的
    (issue 11 说的"8 个写端点"包含了它那条 GET), 但它**只是读**退款列表 ——
    护栏不该管它.
    """
    mall = FakeCart()
    tools = tools_of(mall)

    marked = {
        name
        for name, item in tools.items()
        if item.annotations.get(WRITE_ANNOTATION_KEY)
    }

    assert marked == set(WRITE_TOOL_NAMES)
    assert "list_my_refunds" not in marked, "看退款进度是读操作, 不占写预算"


# ---------------------------------------------------------------------------
# 预算: 一次运行内 8 次
# ---------------------------------------------------------------------------


async def test_the_budget_stops_the_ninth_write() -> None:
    """前 8 次放行, 第 9 次被拒 —— 而且拒绝时给出的是模型看得懂的话."""
    mall = FakeCart()
    guardrail = make_guardrail(mall)

    allowed = [
        await verdict(guardrail, mall, "add_to_cart") for _ in range(WRITE_BUDGET)
    ]
    refusal = await verdict(guardrail, mall, "add_to_cart")

    assert allowed == [None] * WRITE_BUDGET
    assert guardrail.used == WRITE_BUDGET
    assert refusal is not None and refusal.allowed is False
    assert str(WRITE_BUDGET) in refusal.reason
    assert "页面上完成" in refusal.reason, "要给出下一步, 否则模型只会换个参数再试一次"
    assert "不要重复调用" in refusal.reason


async def test_a_refused_call_does_not_spend_the_budget() -> None:
    """被拒的那一次**没有执行**, 所以不占额度 (拒绝了还记账 = 额度会越拦越少)."""
    mall = FakeCart()
    guardrail = make_guardrail(mall)
    for _ in range(WRITE_BUDGET):
        await verdict(guardrail, mall, "add_to_cart")

    await verdict(guardrail, mall, "add_to_cart")
    await verdict(guardrail, mall, "clear_cart")

    assert guardrail.used == WRITE_BUDGET


async def test_the_budget_is_per_run_not_per_session() -> None:
    """下一轮提问归零: 买家接着问一句, 额度重新算 (`before_turn` 的 turn 回到 1).

    不归零的后果是**会话越聊越不敢动手**: 网页版一个会话要服务一整个标签页,
    聊到第 9 件商品之后助手就再也改不动数据了.
    """
    mall = FakeCart()
    guardrail = make_guardrail(mall)
    for _ in range(WRITE_BUDGET + 1):
        await verdict(guardrail, mall, "add_to_cart")

    guardrail.start_run(turn=1)
    assert await verdict(guardrail, mall, "add_to_cart") is None
    assert guardrail.used == 1


async def test_repeated_calls_never_reset_the_budget() -> None:
    """**裁决方法自己从不归零** —— 这条曾经写错过, 后果是那半道闸不存在.

    第一版把「归零」写在裁决方法里 (`turn == 1` 就清账本), 而框架允许模型在同一
    轮里并行发好几次工具调用, 它们带的 turn 是同一个数 —— 于是模型第一轮并发发
    8 次写操作, 每一次都把账本清一遍, 一次都拦不住. 归零交给 `before_turn`
    (每轮恰好一次) 之后, 这个洞就没了: 无论问多少次, 账本只增不减.
    """
    mall = FakeCart()
    guardrail = make_guardrail(mall)

    for _ in range(WRITE_BUDGET + 3):
        await verdict(guardrail, mall, "add_to_cart")

    assert guardrail.used == WRITE_BUDGET, "第 9 次起不该再记账"
    assert await verdict(guardrail, mall, "add_to_cart") is not None


async def test_resuming_a_run_does_not_reset_the_budget() -> None:
    """断点续跑的 `before_turn` 带的不是 1 (轮次是累计值) → 不归零.

    这正是想要的: 打断一下就把额度刷满, 那道闸等于没有.
    """
    mall = FakeCart()
    guardrail = make_guardrail(mall)
    for _ in range(WRITE_BUDGET):
        await verdict(guardrail, mall, "add_to_cart")

    guardrail.start_run(turn=5)  # 续跑时的下一轮: 从 5 接着数

    assert await verdict(guardrail, mall, "add_to_cart") is not None
    assert guardrail.used == WRITE_BUDGET


def test_the_guardrail_installs_itself_on_the_two_points_it_needs() -> None:
    """挂载点由护栏自己声明: 每轮开工前 (归零) + 工具执行前 (裁决)."""
    registry = HookRegistry()

    make_guardrail(FakeCart()).install(registry)

    assert registry.handlers(HookPoint.BEFORE_TURN) != ()
    assert registry.handlers(HookPoint.BEFORE_TOOL_EXECUTE) != ()


# ---------------------------------------------------------------------------
# 金额: 单笔 5000 元
# ---------------------------------------------------------------------------


async def test_an_order_over_the_limit_is_refused() -> None:
    """超限的那一单**商城侧一次请求都收不到** —— 这是护栏与「事后发现」的分界.

    断言落在三个地方: 拒绝的裁决 / 理由里写着这两个数 / 购物车**被问过一次**
    (判金额确实是查出来的, 不是猜的).
    """
    mall = FakeCart(total="9900.00")
    guardrail = make_guardrail(mall)

    refusal = await verdict(guardrail, mall, PLACE_ORDER_TOOL)

    assert refusal is not None and refusal.allowed is False
    assert "9900.00" in refusal.reason and str(MAX_ORDER_AMOUNT) in refusal.reason
    assert "结算页" in refusal.reason, "要告诉买家去哪儿下单"
    assert "拆成几单" in refusal.reason, "拆单能绕过金额上限, 理由里得把话说死"
    assert mall.calls == 1
    assert guardrail.used == 0, "没下成的单不该占额度"


@pytest.mark.parametrize("total", ["0.00", "4000.00", "4999.99", str(MAX_ORDER_AMOUNT)])
async def test_an_order_within_the_limit_goes_through(total: str) -> None:
    """上限是**含等于**的: 差一分钱都不能误伤 (边界钉死, 免得以后改出个「小于」)."""
    mall = FakeCart(total=total)
    guardrail = make_guardrail(mall)

    assert await verdict(guardrail, mall, PLACE_ORDER_TOOL) is None
    assert guardrail.used == 1


async def test_the_amount_rule_only_looks_at_the_order() -> None:
    """金额只在下单那一刻成为事实 —— 加购, 取消, 退款都不为它去打一次商城.

    「车里已经有 9900 元的东西了, 还能不能加一件」的答案是能: 上限管的是**下单**
    那一步, 不是车里的金额. 把它提前到加购上会让「先攒着, 回头再删」这种再正常
    不过的用法变成一串拒绝.
    """
    mall = FakeCart(total="9900.00")
    guardrail = make_guardrail(mall)

    for name in ("add_to_cart", "update_cart_item", "clear_cart", "request_refund"):
        assert await verdict(guardrail, mall, name) is None
    assert mall.calls == 0
    assert guardrail.used == 4


# ---------------------------------------------------------------------------
# 代付: 不执行, 等人给结论 (L3b 的挂起)
# ---------------------------------------------------------------------------


async def test_pay_is_suspended_instead_of_refused() -> None:
    """代付那一条的裁决是**挂起**而不是拒绝 —— 两者都不执行, 去向完全不同.

    判据全落在 `Decision` 上: `verdict` 是第三态, 而且**带着两样给人看的东西** ——
    一句话术 (前端印在卡上) 与一个缺失项 (前端据此渲染一个密码框). 少任何一样,
    前端就弹不出那张能用的卡.

    `needs` 里那个名字必须与工具/装配用的是同一个 (PAYMENT_PASSWORD_FIELD) ——
    用户输的值就是按这个名字送回来的 (见 tools.py 的那条注释).
    """
    mall = FakeCart()
    guardrail = make_guardrail(mall)

    decision = await verdict(guardrail, mall, PAY_ORDER_TOOL)

    assert decision is not None
    assert decision.verdict is Verdict.REQUIRES_APPROVAL
    assert decision.allowed is False
    assert decision.needs == (PAYMENT_PASSWORD_FIELD,)
    assert decision.prompt == PAY_APPROVAL_PROMPT
    assert "支付密码" in decision.prompt, "这句话是给买家看的, 要说清他要做什么"
    assert decision.reason is None, "挂起不是拒绝: 它没有回填给模型的失败文本"


async def test_a_suspended_call_does_not_spend_the_budget() -> None:
    """挂起那一次**没有执行**, 所以不占额度 (与拒绝同一条口径).

    它确实会占掉恢复那一段的额度 —— 但那是另一次运行的另一本账 (恢复时会话要
    重新装配, 账本从零开始), 不在这里.
    """
    mall = FakeCart()
    guardrail = make_guardrail(mall)

    await verdict(guardrail, mall, PAY_ORDER_TOOL)

    assert guardrail.used == 0
    assert mall.calls == 0, "挂起连购物车都不该去问 (它不判金额)"


async def test_the_budget_beats_the_suspension() -> None:
    """预算用完时**当场拒绝**, 不弹确认卡 —— 顺序上拒绝优先于挂起.

    反过来的后果是白折腾: 买家输一次密码、点一次确认, 然后才被告知「今天的额度
    用完了」. 那一次密码本来就不该被要.
    """
    mall = FakeCart()
    guardrail = make_guardrail(mall)
    for _ in range(WRITE_BUDGET):
        await verdict(guardrail, mall, "add_to_cart")

    decision = await verdict(guardrail, mall, PAY_ORDER_TOOL)

    assert decision is not None
    assert decision.verdict is Verdict.REJECT
    assert str(WRITE_BUDGET) in decision.reason


# ---------------------------------------------------------------------------
# 判不了就不放行 (与框架对裁决插件的异常策略同一条)
# ---------------------------------------------------------------------------


async def test_a_mall_failure_fails_closed() -> None:
    """查不到购物车时**按拒绝处理** (框架的 fail closed), 且留痕.

    走的是真的 `HookRegistry.decide` 而不是直接调 `__call__`: 这条断言的是
    「护栏坏了 = 那道闸不存在」这件事由框架接住 —— 而用户以为它一直在.
    """
    mall = FakeCart(error=MinimallError("商城没答上来"))
    guardrail = make_guardrail(mall)
    registry = HookRegistry()
    guardrail.install(registry)

    decision = await registry.decide(
        HookPoint.BEFORE_TOOL_EXECUTE,
        turn=1,
        call=make_tool_call(PLACE_ORDER_TOOL),
        tool=tools_of(mall)[PLACE_ORDER_TOOL],
    )

    assert decision.allowed is False
    assert len(registry.failures) == 1, "原始异常要留痕, 不能静默吞掉"
    assert isinstance(registry.failures[0].error, MinimallError)
