"""retry_async (difficulties #13): 通用重试驱动器.

一句话理解: 把「调用 → 失败 → 判断 → 等待 → 再来一次」这条流水线做成一个
函数 —— 调用方只给出「怎么调」, 判断与等待的规矩在 policy.py (RetryPolicy),
本文件负责照规矩执行.

两条失败通道 (都会重试, 收场方式不同):

1. **异常路径**: 操作抛异常且 ``retryable`` 判定为瞬态 (429 / 5xx / 连接失败 /
   超时) —— 等待后重试; 耗尽时抛**最后一个**异常 (原始异常对象原样上抛,
   不包装不吞, 它自带原始 traceback).
2. **响应不合格路径**: 操作正常返回, 但结果被 ``retry_on_result`` 判据拒绝
   (模型场景: finish_reason=insufficient_system_resource, 官方指引「稍后重试」)
   —— 等待后重试; 耗尽时返回**最后一个结果** (不伪造异常: agent loop 仍该看到
   那个半截响应, 并按原契约如实上报 SERVER_INTERRUPTED).

为什么响应路径要单列一条 (#13): 那条路上的响应**通常已经计费** (带 usage),
重试等于再烧一次 token —— 这正是 on_retry 存在的理由: 每次「决定再试」都把一条
RetryAttempt (含原样结果) 递给调用方, 供 token 计量 / 观测挂钩.
异常路径拿不到 usage (请求没成功), 故其 RetryAttempt.result 为 None.

取消语义: ``CancelledError`` 属 BaseException, 不被 ``except Exception`` 捕获 ——
kill switch (``asyncio.Task.cancel``) 在任意 await 点 (含退避等待) 立即结束,
重试绝不吞取消 (#3).

大白话版: 「再试一次」的执行器. 给它一个动作和一个规矩本, 它负责: 动作失败了
看看这个错值不值得再试 (值得才试), 等一会儿 (越等越久 + 抖动), 再来一次; 试够
次数或时间用光就认输 —— 认输时把最后一次的失败原样交回, 不美化不掩盖.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence

from CharAgent.retry.policy import RetryPolicy, is_retryable
from CharAgent.retry.utils.errors import RetryConfigError
from CharAgent.retry.utils.types import RetryAttempt, RetryCallback


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    retryable: Callable[[BaseException], bool] = is_retryable,
    retry_on_result: Callable[[T], str | None] | None = None,
    on_retry: Sequence[RetryCallback] | None = None,
) -> T:
    """按策略重试一个异步操作, 返回它最终成功的返回值 (#13).

    Args:
        operation: 无参协程工厂 (每次重试都重新调用它, 见下方说明), 例如::

            await retry_async(lambda: model.generate(messages, tools), policy=policy)

        policy: 重试策略 (退避序列 / 三个上限 / 三条注入缝), None 表示用
            RetryPolicy() 默认值 (3 次尝试, 首次退避 0.5s, 总耗时预算 60s).
        retryable: 瞬态判据 (异常 -> 是否值得重试), 默认 is_retryable
            (只认自带 retryable=True 的异常, 即 model 层的 429/5xx/超时族).
        retry_on_result: 结果验收判据 (响应不合格路径): 返回 None 或空串表示
            结果可接受, 返回非空字符串表示不合格, 该字符串即重试原因; None
            表示只看异常不看结果. 注意注解里的 ``str | None`` 描述的是**判据
            被调用后的返回值**, 判据本身是个函数 —— 例如 RetryingChatModel
            传进来的就是绑定方法 ``self._reject_response`` (绑定方法自带
            self, 与普通函数一样可直接调用).
        on_retry: 重试通知回调**序列** (可挂多个; 单回调写 ``[callback]``),
            每次「决定再试」时在等待**之前**按序列顺序**依次**调用 (同步回调
            直接调用, 异步回调 await 到位). 某个回调抛异常即中止其后的回调并
            把异常向上抛 (观测链断了要让它看见, 不静默跳过 —— 与 hooks 注册表
            的插件隔离语义相反); 空序列 / None 表示不挂观测 (零开销).

    Returns:
        T: 操作成功时的返回值.

    Raises:
        BaseException: 不可重试的异常直接上抛; 可重试但耗尽 (尝试次数 / 耗时
            预算) 时抛**最后一个**异常. 响应不合格路径耗尽**不抛异常**, 返回
            最后一个结果.

    Note:
        ``operation`` 必须是无参可调用对象而不是现成的协程对象 —— 协程对象
        只能 await 一次, 直接传进来等于把「重试」写死了. 要传参请用 ``lambda``
        或 ``functools.partial`` 包一层.
    """
    if policy is None:
        # 归一默认策略: 后续各处不必再判空 (对齐 guard=None -> LoopGuard() 惯例)
        policy = RetryPolicy()
    if callable(on_retry):
        # 迁移护栏: 本参数曾收单个回调, 改收序列后, 旧写法 (裸函数) 只在「真的
        # 发生重试」时才炸 TypeError —— 首轮成功或 max_attempts=1 的运行根本
        # 走不到通知处, 传错的参数会被静默吞掉. 故在入口拦下并给出正确写法
        raise RetryConfigError(
            "on_retry 收回调序列: 单回调请写 [callback] (裸函数写法已不适用)"
        )
    attempt = 1  # 当前正在进行的第几次尝试 (1-based: 1 = 首次调用)
    start = policy.time_source()  # 起点时刻, 供下面 elapsed (总耗时预算) 计算
    while True:
        # 重试循环: 出口全在内部 (成功 return / 不可重试 raise / 耗尽 return|raise).
        # 用 while True 而非 for attempt in range(max_attempts) —— 放弃条件有两类
        # (次数 + 耗时), 且响应路径耗尽要 return 而不是 raise, for 装不下这语义
        reason: str | None = None  # 本轮失败理由 (异常路径拼的 / 判据给的)
        failure: BaseException | None = None  # 本轮异常; 响应路径下恒 None
        # 两个槽位各答一问: failure 只在异常路径有值 (通道判别位 + 耗尽时抛它);
        # reason 两条通道都有值 (本轮为什么不算数, 最终进记录单给人看)
        try:
            # operation 是"无参协程工厂", 每轮重新调用 —— 协程对象只能 await
            # 一次, 直接传现成协程等于把重试写死 (见上方 Note)
            result = await operation()
        except Exception as exc:  # CancelledError 属 BaseException, 不在此列
            if not retryable(exc):  # 是否为瞬态异常: 值不值得重试
                raise  # 持久异常 (4xx / 配置错 / 编程错): 立刻上抛, 不值得等待重试
            failure = exc
            reason = _describe(exc)  # 异常分支的重试原因
        else:
            if retry_on_result is not None:  # 是否有判据函数
                # 这里的 is not None 判的是"判据传没传" (省一次调用, 与 hook 的
                # 空注册零开销同一思路); 下面 not reason 判的是"判据说什么"
                reason = retry_on_result(result)  # 调用判据函数, 获取重试原因
            if not reason:  # 是否有返回判据
                return result  # None / 空串 = 结果可接受, 成功返回, 循环结束

        # 走到这里说明本轮尝试不可接受 (异常 / 结果被拒), 需要重试
        # 1. 异常分支 - 瞬态异常 - 值得重试: failure + reason
        # 2. 响应分支 - 有响应结果 - 是否有可重试的判据 - 有则重试: reason

        # 开始裁决"还可以重试吗"
        elapsed = policy.time_source() - start  # 总耗时(含调用耗时与已发生的等待)
        delay = policy.next_delay(attempt=attempt, elapsed=elapsed)
        if delay is None:
            # 上限用尽 (次数 / 耗时预算): 两条通道的收场方式不同 ——
            # 异常路径抛最后一个异常 (原始对象原样上抛, 它自带原始 traceback,
            # 只是多挂一帧本函数); 响应路径返回最后一个结果, 不伪造异常
            if failure is not None:
                raise failure
            return result

        # 失败尝试的记账单: 异常路径带 error, 响应路径带原样结果 —— 模型场景那次
        # 响应已经计费, 调用方 (token 计量) 可读其 usage (#13 的账目就在这一项)
        record = RetryAttempt(
            attempt=attempt, delay=delay, elapsed=elapsed, reason=reason
        )
        if failure is not None:
            record.error = failure
        else:
            record.result = result

        await _notify_retry(on_retry, record)  # 通知在等待之前: 调用方立刻拿到 delay
        await policy.sleep(delay)  # 走注入的 sleep, 测试零真实等待 (#61)
        attempt += 1  # 下一轮


def _describe(exc: BaseException) -> str:
    """失败原因简述 (异常类型 + 消息), 进 RetryAttempt.reason 供观测 / 日志."""
    return f"{type(exc).__name__}: {exc}"


async def _notify_retry(
    on_retry: Sequence[RetryCallback] | None, record: RetryAttempt
) -> None:
    """按序列顺序调用重试通知回调 (同步/异步都接受; 异常向上传播, 不做隔离).

    顺序语义: 前一个回调跑完 (含 await 到位) 才轮到下一个 —— 观测链是串行的,
    别在这里放慢 I/O (它在重试关键路径上, notify 之后才 sleep).

    异常语义: 某个回调抛异常即中止其后的回调并把异常抛出。与 hooks 注册表的
    「插件异常隔离 + failures 留痕」相反: on_retry 是调用方自己的观测链,
    某一环坏了要让它看见 —— 静默跳过会让账目悄悄少记一笔.
    """
    if not on_retry:
        return  # 未挂观测 / 空序列: 零开销 (不建协程, 不 await)
    for callback in on_retry:
        outcome = callback(record)
        if inspect.isawaitable(outcome):
            await outcome  # 同步回调返回 None, 异步回调返回协程, 同一条路收口
