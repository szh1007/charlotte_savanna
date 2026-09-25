"""hooks 包单元测试: hook 注册表骨架.

场景 → 断言:
- HookPoint 六个点齐全 (名字与顺序都被钉住)
- 空注册零开销: has/handlers 为假, fire 立即返回且无副作用
- register 按注册顺序保存; 非法参数 (非 callable) → HookConfigError
- fire: 多个 hook 按注册顺序依次执行; sync 与 async hook 都支持; 返回值无意义
- 异常隔离: hook 抛 Exception 被记录到 failures 且不中断其余 hook, 不向外抛
- **CancelledError 不被吞**: 插件不得挡住 kill switch (#3), 直接向上传播
- clear(point) 只清一个点; clear() 清空全部
- **decide (裁决类点)**: 无人注册 → 放行且不产生挂起点; 插件不表态 (None) /
  明确放行 → 放行; 任一拒绝 → 拒绝且原因取第一个拒绝者的; 插件坏了
  (抛异常 / 返回认不出的东西) → **fail closed** (按拒绝处理并记入 failures)
- **第三态 (需人工确认)**: 三种形状各自的构造期校验; 拒绝**优先于**挂起
  (不看注册顺序 —— 顺序是装配的偶然事实)

被测对象是纯注册表 (不接 loop); loop 触发点集成见 test_loop_events.py.

大白话版 (这份「验货单」在验什么):
- 6 个插座时机齐全; 空插座真的不花时间 (不炸、不产生副作用).
- 插上去的插头按登记顺序被叫到, 普通函数和 async 函数都认.
- 观察点的插头坏了只烧自己的保险丝: 记一笔 (failures) 然后继续叫下一个, 不把
  整个问答搞砸. 它说什么都只是说说 (返回值没人看).
- 带开关那个插座 (工具执行前) 上, 插头说的话算数: 说「不许」就真的不通电, 说
  「这台得问人」就停下等人; 它自己坏了按「不许」处理 —— 悄悄失效的护栏比没有
  护栏更危险.
- 两块牌子同时举起来 (既有「不许」又有「得问人」) 时,**「不许」说了算** ——
  要问的那件事压根不该问.
- 但拔总电源 (CancelledError / 取消) 不算插头故障 —— 直接放行, 插件不许挡着
  用户点「停止」.
"""

from __future__ import annotations

import asyncio

import pytest

from CharAgent.hooks import (
    Decision,
    HookConfigError,
    HookError,
    HookFailure,
    HookPoint,
    HookRegistry,
    Verdict,
)
from CharAgent.hooks.registry import INTERCEPT_FAILED_REASON

# 裁决类点 (只有它用 decide 触发; 其余五个是观察类, 只喊一声)
DECIDE_POINT = HookPoint.BEFORE_TOOL_EXECUTE


async def _noop(**kwargs: object) -> None:
    """空 hook 载体 (async 形态)."""


def _sync_noop(**kwargs: object) -> None:
    """空 hook 载体 (sync 形态)."""


def _broken(**kwargs: object) -> Decision:
    """坏插头载体: 被叫到就炸 (插件写错的最小形态)."""
    raise RuntimeError("插件内部炸了")


# ---------------------------------------------------------------------------
# 点集与注册 API
# ---------------------------------------------------------------------------


def test_hook_points_cover_expected_set() -> None:
    """六个 hook 点齐全且与实现一致 (执行前那个紧跟执行后那个, 读着顺)."""
    assert [p.value for p in HookPoint] == [
        "before_turn",
        "after_turn",
        "on_model_call",
        "before_tool_execute",
        "on_tool_executed",
        "on_event",
    ]


def test_empty_registry_has_no_handlers() -> None:
    """空注册: has 为假, handlers 为空元组, failures 为空 (零开销路径)."""
    registry = HookRegistry()

    assert registry.has(HookPoint.BEFORE_TURN) is False
    assert registry.handlers(HookPoint.BEFORE_TURN) == ()
    assert registry.failures == []


async def test_fire_on_empty_registry_is_noop() -> None:
    """空注册 fire: 立即返回, 不产生任何副作用 (可反复调用)."""
    registry = HookRegistry()
    await registry.fire(HookPoint.AFTER_TURN)
    await registry.fire(HookPoint.AFTER_TURN, turn=1)

    assert registry.failures == []


def test_register_keeps_registration_order() -> None:
    """同一 hook 点内多 hook 按注册顺序保存."""
    registry = HookRegistry()
    first, second = _sync_noop, _noop
    registry.register(HookPoint.BEFORE_TURN, first)
    registry.register(HookPoint.BEFORE_TURN, second)

    assert registry.has(HookPoint.BEFORE_TURN) is True
    assert registry.handlers(HookPoint.BEFORE_TURN) == (first, second)
    assert registry.has(HookPoint.AFTER_TURN) is False  # 不串点


def test_register_non_callable_rejected() -> None:
    """注册非 callable → HookConfigError (尽早暴露拼错的注册)."""
    registry = HookRegistry()
    with pytest.raises(HookConfigError, match="可调用"):
        registry.register(HookPoint.ON_EVENT, "not-a-function")  # type: ignore[arg-type]


def test_clear_one_point_or_all() -> None:
    """clear(point) 只清一个点; clear() 清空全部点."""
    registry = HookRegistry()
    registry.register(HookPoint.BEFORE_TURN, _sync_noop)
    registry.register(HookPoint.AFTER_TURN, _sync_noop)

    registry.clear(HookPoint.BEFORE_TURN)
    assert registry.has(HookPoint.BEFORE_TURN) is False
    assert registry.has(HookPoint.AFTER_TURN) is True

    registry.clear()
    assert registry.has(HookPoint.AFTER_TURN) is False


# ---------------------------------------------------------------------------
# fire: 顺序 / 形态
# ---------------------------------------------------------------------------


async def test_fire_runs_hooks_in_order_with_kwargs() -> None:
    """fire 按注册顺序执行, 并把关键字载荷原样交给每个 hook."""
    order: list[str] = []
    seen: list[dict[str, object]] = []

    async def first(**kwargs: object) -> None:
        order.append("first")
        seen.append(kwargs)

    def second(**kwargs: object) -> None:
        order.append("second")
        seen.append(kwargs)

    registry = HookRegistry()
    registry.register(HookPoint.AFTER_TURN, first)
    registry.register(HookPoint.AFTER_TURN, second)
    await registry.fire(HookPoint.AFTER_TURN, turn=2, tokens=30)

    assert order == ["first", "second"]  # async 与 sync 混用都支持
    assert seen == [{"turn": 2, "tokens": 30}] * 2


async def test_hook_can_mutate_payload_object() -> None:
    """hook 收到的是活引用 (非拷贝): 今后的 memory 插件据此注入记忆."""
    messages: list[dict[str, object]] = [{"role": "user", "content": "你好"}]

    def inject(*, messages: list[dict[str, object]], **kwargs: object) -> None:
        messages.insert(0, {"role": "system", "content": "记忆: 用户是 VIP"})

    registry = HookRegistry()
    registry.register(HookPoint.BEFORE_TURN, inject)
    await registry.fire(HookPoint.BEFORE_TURN, messages=messages)

    assert messages[0]["role"] == "system"


# ---------------------------------------------------------------------------
# 异常隔离
# ---------------------------------------------------------------------------


async def test_hook_exception_recorded_and_isolated() -> None:
    """hook 抛 Exception: 记录 failures, 不中断其余 hook, 不向外抛.

    扩展点是可选的, 插件出错不该让用户的任务失败 —— 但必须留痕不静默.
    """
    ran: list[str] = []

    def broken(**kwargs: object) -> None:
        raise RuntimeError("插件内部炸了")

    def healthy(**kwargs: object) -> None:
        ran.append("healthy")

    registry = HookRegistry()
    registry.register(HookPoint.ON_EVENT, broken)
    registry.register(HookPoint.ON_EVENT, healthy)

    await registry.fire(HookPoint.ON_EVENT, event="evt")  # 不抛

    assert ran == ["healthy"]  # 后续 hook 照常执行
    assert len(registry.failures) == 1
    failure = registry.failures[0]
    assert isinstance(failure, HookFailure)
    assert failure.point is HookPoint.ON_EVENT
    assert failure.hook is broken
    assert isinstance(failure.error, RuntimeError)


async def test_cancelled_error_not_swallowed() -> None:
    """CancelledError 不被吞 (#3): 插件不得挡住 kill switch, 直接向上传播."""

    async def cancelled(**kwargs: object) -> None:
        raise asyncio.CancelledError

    registry = HookRegistry()
    registry.register(HookPoint.BEFORE_TURN, cancelled)

    with pytest.raises(asyncio.CancelledError):
        await registry.fire(HookPoint.BEFORE_TURN)
    assert registry.failures == []  # 取消不是插件失败, 不入 failures


# ---------------------------------------------------------------------------
# 裁决 (decide): 拦截点的返回值语义
# ---------------------------------------------------------------------------


def test_a_rejection_must_carry_a_reason() -> None:
    """拒绝不给原因 → 构造期就报错 (模型收到一条没有理由的拒绝只会再试一次)."""
    with pytest.raises(HookConfigError, match="必须给出原因"):
        Decision.reject("   ")
    with pytest.raises(HookConfigError, match="必须给出原因"):
        Decision(Verdict.REJECT)

    assert Decision.reject("金额超上限").reason == "金额超上限"
    assert Decision.reject("金额超上限").verdict is Verdict.REJECT
    assert Decision.allow().allowed is True
    assert Decision.allow().verdict is Verdict.ALLOW
    assert Decision.allow().reason is None
    with pytest.raises(HookConfigError, match="放行不该带原因"):
        Decision(Verdict.ALLOW, reason="没人会读的备注")


def test_a_suspension_must_carry_a_prompt() -> None:
    """挂起不给话术 → 构造期就报错 (前端会弹出一张没有字的确认卡)."""
    with pytest.raises(HookConfigError, match="必须给出 prompt"):
        Decision.requires_approval("   ")
    with pytest.raises(HookConfigError, match="必须给出 prompt"):
        Decision(Verdict.REQUIRES_APPROVAL)

    decision = Decision.requires_approval(
        "这一单要付款了, 需要你输一次支付密码", needs=("payment_password",)
    )
    assert decision.verdict is Verdict.REQUIRES_APPROVAL
    # 三种态里只有放行算「放行」: 挂起的工具同样不执行
    assert decision.allowed is False
    assert decision.prompt == "这一单要付款了, 需要你输一次支付密码"
    assert decision.needs == ("payment_password",)
    assert decision.reason is None


def test_the_three_shapes_refuse_each_others_fields() -> None:
    """三种形状各自只带自己该带的东西 (混着填 = 写错了, 当场报)."""
    # 挂起不是拒绝: reason 是回填给模型的失败文本, 而挂起这一轮不继续
    with pytest.raises(HookConfigError, match="挂起不该带拒绝原因"):
        Decision(Verdict.REQUIRES_APPROVAL, prompt="确认一下", reason="顺手的备注")
    # 拒绝没有要去问的人, 自然也没有话术与缺失项
    with pytest.raises(HookConfigError, match="拒绝不该带确认话术"):
        Decision(Verdict.REJECT, reason="超预算", prompt="要确认吗")
    with pytest.raises(HookConfigError, match="拒绝不该带确认话术"):
        Decision(Verdict.REJECT, reason="超预算", needs=("payment_password",))
    # 放行什么都没说
    with pytest.raises(HookConfigError, match="放行不该带原因"):
        Decision(Verdict.ALLOW, prompt="没人会读的话")
    with pytest.raises(HookConfigError, match="放行不该带原因"):
        Decision(Verdict.ALLOW, needs=("payment_password",))
    # needs 里每一项都要有名字: 空串会让前端渲染出一个没有名字的输入框
    with pytest.raises(HookConfigError, match="非空短名字"):
        Decision.requires_approval("确认一下", needs=("  ",))


def test_a_pure_yes_no_confirmation_needs_nothing_extra() -> None:
    """纯是 / 否的确认 (如「下单前确认一下」) 的 needs 是空的 —— 合法形状."""
    decision = Decision.requires_approval("确认要下单吗")

    assert decision.needs == ()
    assert decision.prompt == "确认要下单吗"


async def test_decide_on_empty_registry_allows_without_suspending() -> None:
    """空注册: 放行, 且**不产生挂起点** —— 判定与返回在同一步里完成.

    零开销这条不只看「结果一样」, 还看「没有多一次 await 链条」: 用 call_soon
    排一个回调当探针 —— 协程只要把控制权交回事件循环, 它就会跑.
    """
    registry = HookRegistry()
    ticks: list[str] = []
    asyncio.get_running_loop().call_soon(ticks.append, "轮到了事件循环")

    decision = await registry.decide(DECIDE_POINT, turn=1)

    assert decision.allowed is True
    assert ticks == [], "空注册的 decide 不该挂起 (把控制权交回事件循环)"


async def test_decide_allows_when_nobody_objects() -> None:
    """插件不表态 (None) / 明确放行 → 放行 (只有说「不许」的才算拒绝)."""
    registry = HookRegistry()
    registry.register(DECIDE_POINT, lambda **kw: None)  # 不关心的直接 return
    registry.register(DECIDE_POINT, lambda **kw: Decision.allow())

    decision = await registry.decide(DECIDE_POINT, turn=1)

    assert decision.allowed is True
    assert decision.reason is None
    assert registry.failures == []


async def test_decide_uses_the_first_rejection_reason() -> None:
    """任一插件拒绝 → 拒绝; 原因取注册顺序上**第一个**拒绝者的.

    两条拒绝都看得到被叫过 (裁决点不短路), 但结论只有一条 —— 对模型来说,
    后面几条原因没有增量信息, 于是不收集.
    """
    calls: list[str] = []

    def first(**kwargs: object) -> Decision:
        calls.append("first")
        return Decision.reject("写操作次数已达上限")

    async def second(**kwargs: object) -> Decision:
        calls.append("second")
        return Decision.reject("单笔金额超过上限")

    registry = HookRegistry()
    registry.register(DECIDE_POINT, first)
    registry.register(DECIDE_POINT, second)

    decision = await registry.decide(DECIDE_POINT, turn=3)

    assert decision.allowed is False
    assert decision.reason == "写操作次数已达上限"
    assert calls == ["first", "second"], "后面的插件照常被叫到 (不短路)"
    assert registry.failures == []


async def test_decide_returns_the_first_suspension() -> None:
    """要人工确认的那一条: 结论原样带回 (话术与缺失项都要在), 取第一个说的人.

    两条都要看到被叫过 (裁决点不短路), 但一次只挂一条 —— 后面那条对前端没有
    增量信息 (确认卡一次只弹一张).
    """
    calls: list[str] = []

    def first(**kwargs: object) -> Decision:
        calls.append("first")
        return Decision.requires_approval("要输支付密码", needs=("payment_password",))

    async def second(**kwargs: object) -> Decision:
        calls.append("second")
        return Decision.requires_approval("要确认下单")

    registry = HookRegistry()
    registry.register(DECIDE_POINT, first)
    registry.register(DECIDE_POINT, second)

    decision = await registry.decide(DECIDE_POINT, turn=1)

    assert decision.verdict is Verdict.REQUIRES_APPROVAL
    assert decision.prompt == "要输支付密码"
    assert decision.needs == ("payment_password",)
    assert calls == ["first", "second"], "第二条照常被叫到 (不短路)"
    assert registry.failures == []


async def test_a_rejection_outranks_a_suspension_whatever_the_order() -> None:
    """拒绝优先于挂起 —— 两个注册顺序各验一遍.

    业务侧会同时挂「护栏 (超预算就拒)」与「需确认」两条规则, 而**顺序是装配的
    偶然事实**: 靠顺序的话, 一个会超预算的操作在「需确认」排在前面时会先弹一张
    确认卡, 用户输完密码才被告知「不行」.
    """
    reject = Decision.reject("单笔金额超过上限")
    suspend = Decision.requires_approval("要输支付密码", needs=("payment_password",))
    for order in ((reject, suspend), (suspend, reject)):
        registry = HookRegistry()
        for verdict in order:
            registry.register(DECIDE_POINT, lambda v=verdict, **kw: v)

        decision = await registry.decide(DECIDE_POINT, turn=1)

        assert decision.verdict is Verdict.REJECT, f"顺序 {order} 下没让拒绝说了算"
        assert decision.reason == "单笔金额超过上限"


async def test_decide_fails_closed_when_a_plugin_raises() -> None:
    """插件抛异常 → **按拒绝处理** 并记入 failures (与 fire 的隔离策略相反).

    这里刻意不照抄 fire: 观察点的插件坏了顶多少一条日志, 拦截点的插件坏了
    等于那道护栏不存在 —— 而用户以为它在. 拒绝原因是框架给的固定文案 (异常
    原始信息只进 failures, 不往模型那边倒).
    """
    registry = HookRegistry()
    registry.register(DECIDE_POINT, _broken)

    decision = await registry.decide(DECIDE_POINT, turn=1)

    assert decision.allowed is False
    assert decision.reason == INTERCEPT_FAILED_REASON
    assert len(registry.failures) == 1
    failure = registry.failures[0]
    assert failure.point is DECIDE_POINT
    assert failure.hook is _broken
    assert isinstance(failure.error, RuntimeError)


async def test_decide_fails_closed_when_a_plugin_returns_a_stranger() -> None:
    """插件返回了认不出的东西 (写错的 return) → 同样按拒绝处理.

    如果认不出的返回值被当成放行, 一个写错 `return "拒绝"` 的护栏就会**静默
    失效** —— 正是 fail closed 要防的那个样子.
    """
    registry = HookRegistry()
    registry.register(
        DECIDE_POINT,
        lambda **kw: "拒绝",  # type: ignore[arg-type,return-value]
    )

    decision = await registry.decide(DECIDE_POINT, turn=1)

    assert decision.allowed is False
    assert decision.reason == INTERCEPT_FAILED_REASON
    assert len(registry.failures) == 1
    assert isinstance(registry.failures[0].error, HookError)
    assert "Decision" in str(registry.failures[0].error)


async def test_decide_keeps_going_after_a_broken_plugin() -> None:
    """坏插件不中断其余插件: 后面的照常被叫到 (它的裁决也照常算数).

    只是「第一个说不的说了算」这条规矩下, 坏插件 (按拒绝处理) 排在前面时,
    框架文案就是这次的原因 —— 坏的那道护栏要显出来, 不能被后面的具体原因盖住
    (否则运维永远不知道有个插件在崩).
    """

    def healthy(**kwargs: object) -> Decision:
        return Decision.reject("金额超过上限")

    registry = HookRegistry()
    registry.register(DECIDE_POINT, _broken)
    registry.register(DECIDE_POINT, healthy)

    decision = await registry.decide(DECIDE_POINT, turn=1)

    assert decision.allowed is False
    assert decision.reason == INTERCEPT_FAILED_REASON
    assert len(registry.failures) == 1


async def test_decide_does_not_swallow_cancelled_error() -> None:
    """裁决点同样不许挡取消 (#3): CancelledError 直接向上传播, 不入 failures."""

    async def cancelled(**kwargs: object) -> Decision:
        raise asyncio.CancelledError

    registry = HookRegistry()
    registry.register(DECIDE_POINT, cancelled)

    with pytest.raises(asyncio.CancelledError):
        await registry.decide(DECIDE_POINT, turn=1)
    assert registry.failures == []


async def test_fire_ignores_whatever_a_plugin_returns() -> None:
    """观察类点: 插件返回什么都不算数 (fire 没有返回值, 也不记 failures).

    这条是「为什么不给 fire 加返回值」那条决定的守卫 —— 哪天 fire 也开始看
    返回值, 这里立刻红.
    """
    registry = HookRegistry()
    registry.register(HookPoint.ON_EVENT, lambda **kw: Decision.reject("不许"))

    assert await registry.fire(HookPoint.ON_EVENT, event="evt") is None
    assert registry.failures == []
