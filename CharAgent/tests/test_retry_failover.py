"""主备包装测试 (difficulties #14): 熔断切换 + 记账 + 与重试层的组合.

场景 -> 断言 (全部零真实等待: 时钟注入; 「并发」用一把闸而不是睡眠造):
- 平时都走主模型; 连续失败到阈值的那一跳**立刻**改走备份 (不等重试退避)
- 主模型熔断之后再调用: 假主模型的计数**不再增加** (真的没发请求)
- 不够阈值的失败原样上抛 (交给重试层), 不切备份
- 不算故障的失败 (400 参数错) 既不计数也不切 —— 换个模型一样会错
- 没有备份 / 备份也熔断: 以 CircuitOpenError 收场 (**不可重试**), 原始失败挂在链上
- 与 RetryingChatModel 组合: 闸开之后重试层**立刻放弃**, 一次退避都不等
- 冷却后进半开: 只放一个探测 (并发三个调用, 另外两个走备份)
- 探测成功 -> 合闸 (后续回主模型); 探测失败 -> 回打开 (再切备份)
- 账目: 只有真答了话的模型进台账; 切换过就是两个名字 (组合名)
- 取消 (CancelledError) 不记账; 参数逐次原样透传 (#68); 两侧都关连接池

一个前提 (读用例前先知道, 不然会以为「三次失败」写得莫名): 本包装**一次调用只打
一个模型一下** —— 重试是外层的事 (生产里上面还套着 RetryingChatModel). 于是
「连续三次失败」在用例里是三跳各自失败前两跳、第三跳把闸拨开.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from doubles import FakeClock, RecordingSleep

from CharAgent.model.utils.errors import ModelStatusError
from CharAgent.model.utils.types import FinishReason, ModelMessage, ModelResponse
from CharAgent.retry import (
    CircuitOpenError,
    CircuitPolicy,
    CircuitState,
    FailoverChatModel,
    RetryingChatModel,
    RetryPolicy,
    current_serving,
    is_retryable,
    serving_scope,
)

MESSAGES: list[ModelMessage] = [{"role": "user", "content": "订单到哪了"}]

PRIMARY = "deepseek-flash"
BACKUP = "gpt-6-luna"


def _boom() -> ModelStatusError:
    """500 服务端故障: 瞬态, 算这个模型的账 (默认判据认它)."""
    return ModelStatusError(503, "boom")


def _bad_request() -> ModelStatusError:
    """400 参数错: 永久, 不算账 —— 换个模型发同样的请求一样会错."""
    return ModelStatusError(400, "bad request")


def _response(content: str = "好的") -> ModelResponse:
    return ModelResponse(content=content, finish_reason=FinishReason.STOP)


class _Block:
    """脚本里的一项: 卡住直到测试放行 —— 造「探测在飞」那个瞬间, 不靠时序运气."""

    def __init__(self) -> None:
        self.release = asyncio.Event()


class _FakeModel:
    """按脚本答话的模型替身: 脚本里放异常就抛, 放 _Block 就等放行, 放 None 就成功.

    脚本用尽之后看 `then`: None = 一直成功, 异常 = 一直抛它.

    attributes:
        calls: 被调用了几次 —— 「熔断期间真的没发请求」就是靠它不涨来钉的.
        params: 每次调用的关键字实参 (参数透传断言 #68).
        closed: 被 aclose 了几次.
    """

    def __init__(
        self,
        name: str,
        *,
        script: Sequence[BaseException | _Block | None] = (),
        then: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.model = name  # 适配器形状: 名字能被包装顺着读出来
        self._script = list(script)
        self._then = then
        self._close_error = close_error
        self.calls = 0
        self.params: list[dict] = []
        self.closed = 0

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: object = None,
        **kwargs: object,
    ) -> ModelResponse:
        self.calls += 1
        self.params.append(kwargs)
        action = self._script.pop(0) if self._script else self._then
        if isinstance(action, _Block):
            await action.release.wait()
            return _response()
        if action is not None:
            raise action
        return _response()

    async def aclose(self) -> None:
        self.closed += 1
        if self._close_error is not None:
            raise self._close_error


def _failover(
    clock: FakeClock,
    primary: _FakeModel,
    backup: _FakeModel | None,
    **policy_overrides: object,
) -> FailoverChatModel:
    """默认样本: 连错 3 次跳闸 / 冷却 30 秒 / 固定时钟 (要别的就覆盖规矩本)."""
    policy: dict[str, object] = {
        "failure_threshold": 3,
        "cooldown_seconds": 30.0,
        "time_source": clock,
    }
    policy.update(policy_overrides)
    return FailoverChatModel(
        primary,
        backup,
        policy=CircuitPolicy(**policy),  # type: ignore[arg-type]
    )


async def _fails(model: FailoverChatModel) -> None:
    """一跳失败: 主模型抛出 (闸还开着门), 错误原样上抛."""
    with pytest.raises(ModelStatusError):
        await model.generate(MESSAGES)


async def _trip(model: FailoverChatModel) -> ModelResponse:
    """把主模型的闸拨开: 前两跳各自失败, 第三跳跳闸并当场改走备份 (返回那次响应).

    跑完的账 (默认阈值 3): 主模型被调 3 次, 备份 1 次, 主模型的闸=打开.
    """
    await _fails(model)
    await _fails(model)
    return await model.generate(MESSAGES)


# ---------------------------------------------------------------------------
# 选路与切换
# ---------------------------------------------------------------------------


async def test_everything_goes_to_the_primary_while_the_breaker_is_closed() -> None:
    clock = FakeClock()
    primary = _FakeModel(PRIMARY)
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)

    response = await model.generate(MESSAGES)

    assert response.content == "好的"
    assert (primary.calls, backup.calls) == (1, 0)
    assert model.breakers[0].state is CircuitState.CLOSED


async def test_the_tripping_call_switches_to_the_backup_right_away() -> None:
    """跳到闸的那一跳**同一跳里**就走备份 (不等重试退避) —— 否则备份永远等不到出场."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)

    response = await _trip(model)

    assert response.content == "好的"
    assert (primary.calls, backup.calls) == (3, 1), "第三次失败之后当场改走备份"
    assert model.breakers[0].state is CircuitState.OPEN
    assert model.breakers[1].state is CircuitState.CLOSED, "备份自己的闸没被这一下带开"


async def test_after_the_breaker_opens_the_dead_model_is_not_called_at_all() -> None:
    """熔断期间**真的不发请求**: 假主模型的计数不再增加 (票面验收的第一条)."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)
    await _trip(model)
    assert (primary.calls, backup.calls) == (3, 1)

    await model.generate(MESSAGES)
    await model.generate(MESSAGES)

    assert primary.calls == 3, "闸开着的时候一个请求都不该发出去"
    assert backup.calls == 3


async def test_a_failure_below_the_threshold_goes_back_to_the_retry_layer() -> None:
    """没到阈值就切, 等于把每次抖动都甩给备份 —— 抖动是重试层该管的事."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)

    await _fails(model)

    assert backup.calls == 0
    assert model.breakers[0].failures == 1


async def test_a_400_neither_counts_nor_switches() -> None:
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_bad_request())
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)

    await _fails(model)

    assert backup.calls == 0, "换个模型发同样的请求一样会错 (400)"
    assert model.breakers[0].failures == 0


async def test_without_a_backup_each_call_gets_a_fresh_chance_until_the_trip() -> None:
    """没有备份时本包装就是「一个带闸的模型」: 阈值内的失败原样上抛, 跳闸后快失败."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    model = _failover(clock, primary, backup=None)

    await _fails(model)  # 第一次: 没到阈值, 交回重试层
    await _fails(model)  # 第二次: 同上

    with pytest.raises(CircuitOpenError) as caught:  # 第三次: 跳闸, 没有备份可切
        await model.generate(MESSAGES)

    assert primary.calls == 3
    assert is_retryable(caught.value) is False, "闸开了, 重试层该立刻放弃"
    assert isinstance(caught.value.__cause__, ModelStatusError), "原始失败挂在链上"
    with pytest.raises(CircuitOpenError):
        await model.generate(MESSAGES)
    assert primary.calls == 3, "之后一次都不再发出去"


async def test_when_the_backup_is_also_open_the_trip_reports_the_open_breaker() -> None:
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    backup = _FakeModel(BACKUP, then=_boom())
    model = _failover(clock, primary, backup)

    await _fails(model)  # 1 / 2: 主模型各自错一次, 闸还开着门
    await _fails(model)
    await _fails(model)  # 3: 主模型错第 3 次 -> 跳闸 -> 当场改走备份 (备份错第 1 次)
    await _fails(model)  # 4: 主模型闸开着 -> 直接落备份 (错第 2 次)

    with pytest.raises(CircuitOpenError) as tripped:  # 5: 备份错第 3 次 -> 也跳闸
        await model.generate(MESSAGES)
    assert "熔断跳闸" in str(tripped.value)
    assert "另一个模型也不能接这一跳" in str(tripped.value)

    assert model.breakers[0].state is CircuitState.OPEN
    assert model.breakers[1].state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError) as both:  # 6: 两家都熔断着
        await model.generate(MESSAGES)
    assert "主备模型现在都不能用" in str(both.value)
    assert is_retryable(both.value) is False
    assert (primary.calls, backup.calls) == (3, 3), "两边都真的没再发"


# ---------------------------------------------------------------------------
# 与重试层组合 (嵌套顺序的证据: 熔断在里面, 重试在外面)
# ---------------------------------------------------------------------------


async def test_the_retry_layer_gives_up_at_once_when_the_breaker_opens() -> None:
    """票面验收: 打开期间抛的异常不可重试 -> RetryingChatModel 立刻放弃, 不空转."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    model = _failover(clock, primary, backup=None)
    sleep = RecordingSleep()
    wrapped = RetryingChatModel(
        model,
        policy=RetryPolicy(
            max_attempts=5,
            initial_delay=0.5,
            jitter=0.0,
            sleep=sleep,
            time_source=clock,
        ),
    )

    with pytest.raises(CircuitOpenError):
        await wrapped.generate(MESSAGES)

    assert primary.calls == 3, "三次失败跳闸即收场, 用不满 5 次尝试"
    assert len(sleep.delays) == 2, "退避只等前面那两次 —— 闸开之后一次都不等"


async def test_a_blip_is_absorbed_by_retry_without_switching_to_the_backup() -> None:
    """重试层与熔断的分工: 一次抖动重试就好, 不必惊动备份."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, script=[_boom()])
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)
    wrapped = RetryingChatModel(
        model,
        policy=RetryPolicy(
            max_attempts=3, initial_delay=0.1, jitter=0.0, sleep=RecordingSleep()
        ),
    )

    response = await wrapped.generate(MESSAGES)

    assert response.content == "好的"
    assert (primary.calls, backup.calls) == (2, 0)


async def test_the_whole_run_continues_on_the_backup_after_the_trip() -> None:
    """生产里的完整形状: 重试外面、熔断里面; 跳闸那一跳改走备份, 这一次调用**成功**."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)
    wrapped = RetryingChatModel(
        model,
        policy=RetryPolicy(
            max_attempts=3, initial_delay=0.5, jitter=0.0, sleep=RecordingSleep()
        ),
    )

    with serving_scope() as record:
        response = await wrapped.generate(MESSAGES)

    assert response.content == "好的"
    assert (primary.calls, backup.calls) == (3, 1), "第三次尝试里跳闸并当场改走备份"
    assert record.names == (BACKUP,), "账上只有一个服务过的模型: 备份"


# ---------------------------------------------------------------------------
# 半开: 只放一个探测
# ---------------------------------------------------------------------------


async def test_after_the_cooldown_only_one_probe_is_let_through() -> None:
    clock = FakeClock()
    probe = _Block()
    primary = _FakeModel(PRIMARY, script=[_boom(), _boom(), _boom(), probe])
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)
    await _trip(model)
    clock.now += 30.0  # 冷却到点: 主模型进入半开

    assert model.breakers[0].state is CircuitState.HALF_OPEN
    # 三个并发调用: 第一个拿到唯一的探测位 (卡在里面), 另外两个只该走备份
    tasks = [asyncio.create_task(model.generate(MESSAGES)) for _ in range(3)]
    await asyncio.sleep(0)  # 让三个任务都跑到各自的 await 点上
    probe.release.set()
    results = await asyncio.gather(*tasks)

    assert [item.content for item in results] == ["好的", "好的", "好的"]
    assert primary.calls == 4, "冷却之后只放了一个探测过去"
    assert backup.calls == 3, "跳闸那一跳 + 探测期间被拒的两个"
    assert model.breakers[0].state is CircuitState.CLOSED, "探测成功 -> 合闸"


async def test_a_failed_probe_reopens_the_breaker_and_switches_again() -> None:
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)
    await _trip(model)
    clock.now += 30.0

    response = await model.generate(MESSAGES)  # 探测失败

    assert response.content == "好的"
    assert model.breakers[0].state is CircuitState.OPEN, "探测失败 -> 回到打开"
    assert (primary.calls, backup.calls) == (4, 2)
    clock.now += 10.0  # 冷却从头再数: 还没到点
    await model.generate(MESSAGES)
    assert (primary.calls, backup.calls) == (4, 3), "这段时间还是不发给它"


async def test_a_successful_probe_hands_the_traffic_back_to_the_primary() -> None:
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, script=[_boom(), _boom(), _boom()])
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)
    await _trip(model)
    clock.now += 30.0

    await model.generate(MESSAGES)  # 探测 (脚本用尽: 主模型这次成功)
    await model.generate(MESSAGES)

    assert model.breakers[0].state is CircuitState.CLOSED
    assert (primary.calls, backup.calls) == (5, 1), "合闸之后回主模型"


# ---------------------------------------------------------------------------
# 账目: 谁服务记谁
# ---------------------------------------------------------------------------


async def test_the_served_model_is_noted_in_the_run_ledger() -> None:
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)

    with serving_scope() as record:
        await _trip(model)  # 跳闸那一跳由备份答
        assert record.names == (BACKUP,), "这一趟只有备份真答了话"
        assert record.model_name("配置里的名字") == BACKUP

    with serving_scope() as record:
        await model.generate(MESSAGES)  # 闸仍开着 -> 还是备份
        assert record.model_name() == BACKUP


async def test_a_run_served_by_both_models_is_recorded_as_both_names() -> None:
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, script=[None, _boom(), _boom(), _boom()])
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)

    with serving_scope() as record:
        await model.generate(MESSAGES)  # 第一跳: 主模型答的
        await _trip(model)  # 之后三跳: 前两跳失败, 第三跳跳闸 -> 备份答
        assert record.names == (PRIMARY, BACKUP)
        assert record.model_name() == f"{PRIMARY}+{BACKUP}", "两个都用过: 记事实"


async def test_nothing_is_noted_without_a_scope() -> None:
    """没开作用域 (直接拿包装当普通模型用) = 一笔都不记, 不该炸也不该攒着."""
    clock = FakeClock()
    model = _failover(clock, _FakeModel(PRIMARY), _FakeModel(BACKUP))

    assert current_serving() is None
    await model.generate(MESSAGES)
    assert current_serving() is None


async def test_failed_attempts_do_not_reach_the_ledger() -> None:
    """失败的尝试产出 0 个 token, 不该出现在账上 (免得按错单价算一笔钱)."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, then=_boom())
    model = _failover(clock, primary, backup=None)

    with serving_scope() as record:
        await _fails(model)
        assert record.names == ()
        assert record.model_name("配置里的名字") == "配置里的名字"


async def test_the_ledger_is_per_scope_not_per_model_object() -> None:
    """台账挂在作用域上而不是模型对象上: 两句问话各算各的 (server 并发也靠这条)."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, script=[_boom()])  # 第一跳失败, 之后恢复
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)

    with serving_scope() as first:
        await _fails(model)  # 这一趟只有失败, 没有服务过的模型
    with serving_scope() as second:
        await model.generate(MESSAGES)

    assert first.names == ()
    assert second.names == (PRIMARY,), "上一趟的失败不该记到这一趟头上"


# ---------------------------------------------------------------------------
# 痕迹 / 透传 / 生命周期
# ---------------------------------------------------------------------------


async def test_the_switch_is_announced(log_stream) -> None:
    primary = _FakeModel(PRIMARY, then=_boom())
    backup = _FakeModel(BACKUP)
    seen: list[object] = []
    model = FailoverChatModel(primary, backup, on_switch=[seen.append])

    await _trip(model)

    assert len(seen) == 1
    switch = seen[0]
    assert (switch.from_name, switch.to_name) == (PRIMARY, BACKUP)
    assert "熔断" in switch.reason
    written = log_stream.getvalue()
    assert "熔断, 本次调用改走" in written
    assert BACKUP in written


async def test_the_parameters_are_passed_through_unchanged() -> None:
    """包装不吞不改任何采样参数 (#68): 主备哪一侧收到的都是同一份."""
    clock = FakeClock()
    primary = _FakeModel(PRIMARY)
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)

    await model.generate(
        MESSAGES,
        [{"type": "function", "function": {"name": "x"}}],
        temperature=0.3,
        top_p=0.9,
        seed=7,
        max_tokens=128,
        thinking=False,
        reasoning_effort="none",
        stream=True,
    )

    assert primary.params[0] == {
        "temperature": 0.3,
        "top_p": 0.9,
        "seed": 7,
        "max_tokens": 128,
        "thinking": False,
        "reasoning_effort": "none",
        "stream": True,
    }


async def test_a_cancelled_call_is_not_counted_as_a_failure() -> None:
    clock = FakeClock()
    blocking = _Block()
    primary = _FakeModel(PRIMARY, script=[blocking])
    model = _failover(clock, primary, backup=None)

    task = asyncio.create_task(model.generate(MESSAGES))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert model.breakers[0].failures == 0, "取消是调用方的决定, 不是这家的病"
    assert model.breakers[0].state is CircuitState.CLOSED


async def test_aclose_closes_both_sides_even_when_one_fails() -> None:
    clock = FakeClock()
    primary = _FakeModel(PRIMARY, close_error=RuntimeError("连接池关不掉"))
    backup = _FakeModel(BACKUP)
    model = _failover(clock, primary, backup)

    with pytest.raises(RuntimeError):
        await model.aclose()

    assert (primary.closed, backup.closed) == (1, 1), "一侧关不掉也要把另一侧关上"


async def test_the_slot_names_are_taken_from_the_adapters_own_name() -> None:
    """记账要用**模型名** (价目表的键), 不是「主 / 备」这种位置名."""
    clock = FakeClock()
    model = _failover(clock, _FakeModel(PRIMARY), _FakeModel(BACKUP))

    assert [breaker.name for breaker in model.breakers] == [PRIMARY, BACKUP]

    named = FailoverChatModel(
        _FakeModel(PRIMARY), _FakeModel(BACKUP), primary_name="配的别名"
    )
    assert named.breakers[0].name == "配的别名"
