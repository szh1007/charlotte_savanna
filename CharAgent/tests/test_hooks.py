"""hooks 包单元测试: hook 注册表骨架.

场景 → 断言:
- HookPoint 五个点齐全 (名字与顺序都被钉住)
- 空注册零开销: has/handlers 为假, fire 立即返回且无副作用
- register 按注册顺序保存; 非法参数 (非 callable) → HookConfigError
- fire: 多个 hook 按注册顺序依次执行; sync 与 async hook 都支持
- 异常隔离: hook 抛 Exception 被记录到 failures 且不中断其余 hook, 不向外抛
- **CancelledError 不被吞**: 插件不得挡住 kill switch (#3), 直接向上传播
- clear(point) 只清一个点; clear() 清空全部

被测对象是纯注册表 (不接 loop); loop 触发点集成见 test_loop_events.py.

大白话版 (这份「验货单」在验什么):
- 5 个插座时机齐全; 空插座真的不花时间 (不炸、不产生副作用).
- 插上去的插头按登记顺序被叫到, 普通函数和 async 函数都认.
- 插头坏了只烧自己的保险丝: 记一笔 (failures) 然后继续叫下一个, 不把整个
  问答搞砸.
- 但拔总电源 (CancelledError / 取消) 不算插头故障 —— 直接放行, 插件不许挡着
  用户点「停止」.
"""

from __future__ import annotations

import asyncio

import pytest

from CharAgent.hooks import HookConfigError, HookFailure, HookPoint, HookRegistry


async def _noop(**kwargs: object) -> None:
    """空 hook 载体 (async 形态)."""


def _sync_noop(**kwargs: object) -> None:
    """空 hook 载体 (sync 形态)."""


# ---------------------------------------------------------------------------
# 点集与注册 API
# ---------------------------------------------------------------------------


def test_hook_points_cover_expected_set() -> None:
    """五个 hook 点齐全且与实现一致."""
    assert [p.value for p in HookPoint] == [
        "before_turn",
        "after_turn",
        "on_model_call",
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
