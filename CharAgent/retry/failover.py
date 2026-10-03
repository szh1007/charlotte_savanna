"""FailoverChatModel (difficulties #14): 主备两家的 ChatModel 包装.

一句话理解: 主模型 (DeepSeek) 的闸一跳, 这一趟就改走备份模型 (CLOSEAI), 于是
「这家挂了」不至于让用户当场拿不到答案 —— 而它仍是个标准 ChatModel, 上层
(loop / CLI / server) 完全不必知道有两家.

**嵌套顺序是个真决策** (ADR-0025): 本包装**在里面**, 重试包装在外面, 即
``RetryingChatModel(FailoverChatModel(主, 备))``. 好处是熔断闸看到的是**每一次
物理调用** (每次重试它都数得到), 计数才准; 反过来套 (熔断在外面) 则「重试 3 次
全挂」才等于熔断器眼里的 1 次失败, 闸要过很久才跳.

**什么时候切** (两条, 合起来才是完整语义):

1. **选模型时**: 每次发请求前先问闸 (主 -> 备依次), 第一个还能放行的就是这一跳
   的落点; 两家都开着 -> CircuitOpenError (**不可重试** —— 别在退避里空转).
2. **刚跳闸那一跳**: 这次失败正好把主模型的闸拨开时, 同一跳里**立刻**改走备份
   (不等重试层退避) —— 否则「闸开」这一刻恰好也是重试层认输的那一刻 (默认阈值
   3 = 默认重试次数 3), 备份永远等不到出场机会.

**什么时候不切**: 不算故障的失败 (默认判据外的, 如 400 参数错) 原样上抛 —— 换个
模型发同样的请求一样会错, 切过去只是多烧一次钱; 没有备份可切 (本来就没配, 或备份
也熔断着) 时同样以 CircuitOpenError 收场 —— 它是**闸开了**这件事本身, 重试层据此
立刻放弃, 原始失败挂在异常链上 (`from`).

**账目**: 每次**成功的响应**往当前运行的服务台账里记一笔 (`retry/serving.py`),
于是运行行记的是实际在服务的模型名 (切换过就按备份记; 两个都用过记组合名, 金额
留空并写明原因 —— 见 `ServingRecord.model_name`).

**状态是进程内的**: 两个闸跟着本对象走 (进程级共享 = 熔断状态在多次运行之间累积,
正是它该有的样子); 但**多实例部署下各进程各有一份闸** (跨进程共享闸状态属 #21).

**不做的事**: 不做「主模型一失败就切」(那是把每次抖动都甩给备份 —— 重试层本来就
是为抖动准备的, 熔断才是为「这家真的挂了」准备的); 不做冷却期递增 (探测失败只是
从头再数, 见 `retry/circuit.py`); 不记用量明细 (台账只记名字, 逐模型拆账要给
runs 加列, 不在本片范围). 这些都记在 ADR-0025 的边界里.

大白话版: 两个模型、两个闸. 平时都走主模型; 主模型连错几次就把闸拉下来, 之后
这一趟直接走备份 (连试都不试主模型了); 冷却到点放一个探测回去看主模型活没活.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Sequence
from dataclasses import dataclass

from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.types import ModelMessage, ModelResponse, ToolSpec
from CharAgent.retry.circuit import CircuitBreaker, CircuitPolicy
from CharAgent.retry.serving import note_serving
from CharAgent.retry.utils.errors import CircuitOpenError
from CharAgent.retry.utils.types import ModelSwitch, SwitchCallback
from CharAgent.structured_logging import get_logger

logger = get_logger("retry")


def _name_of(model: ChatModel, explicit: str | None, fallback: str) -> str:
    """这一侧在账上叫什么: 显式给的 > 适配器自己说的 > 位置名 (主 / 备).

    适配器多半知道自己的模型名 (`HttpXChatModel.model`), 于是装配处不填也能记对;
    显式参数是为了覆盖它 (同一个适配器按不同价目表条目记账时).
    """
    if explicit:
        return explicit
    own = getattr(model, "model", None)
    if isinstance(own, str) and own:
        return own
    return fallback


@dataclass(frozen=True, slots=True)
class _Slot:
    """一侧 (主 / 备): 它的名字、它的模型、它的闸 —— 三个总是一起用, 打成一份."""

    name: str
    model: ChatModel
    breaker: CircuitBreaker


class FailoverChatModel:
    """ChatModel 协议的主备包装: 主模型熔断打开就改走备份 (difficulties #14).

    除 `generate` / `aclose` 外不新增协议方法: 上层把它当普通 ChatModel 用
    (与 RetryingChatModel 同一个做法).

    Args:
        primary: 主模型 (平时都走它).
        backup: 备份模型; None = 没有备份 —— 这时本包装只是「一个带熔断闸的模型」:
            闸一开, 调用直接以 CircuitOpenError 收场, 不会换个模型重发.
        primary_name / backup_name: 两侧在账上叫什么 (None = 问适配器, 见
            `_name_of`).
        policy: 两侧的闸共用的一份规矩 (阈值 / 冷却 / 两条注入缝 —— 谁也不会给备份
            配一套不同的阈值); None 表示 `CircuitPolicy()` 默认值. 语义见
            `retry/circuit.py`.
        on_switch: 切换通知回调**序列** (可挂多个; 单回调写 ``[callback]``), 每次
            真的改走另一个模型时按序列顺序依次调用 —— 终端提示 / 观测的挂载点
            (语义与 on_retry 完全一致: 串行、异常不隔离).
    """

    def __init__(
        self,
        primary: ChatModel,
        backup: ChatModel | None = None,
        *,
        primary_name: str | None = None,
        backup_name: str | None = None,
        policy: CircuitPolicy | None = None,
        on_switch: Sequence[SwitchCallback] | None = None,
    ) -> None:
        self._on_switch = on_switch
        slots: list[_Slot] = []
        for model, explicit_name, fallback in (
            (primary, primary_name, "primary"),
            (backup, backup_name, "backup"),
        ):
            if model is None:
                continue  # 没配备份: 只有主模型这一个闸
            name = _name_of(model, explicit_name, fallback)
            slots.append(_Slot(name, model, CircuitBreaker(name, policy=policy)))
        self._slots: tuple[_Slot, ...] = tuple(slots)

    async def generate(
        self,
        messages: list[ModelMessage],
        tools: list[ToolSpec] | None = None,
        *,
        temperature: float | None = None,
        top_p: float | None = None,
        seed: int | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        reasoning_effort: str | None = None,
        stream: bool = False,
    ) -> ModelResponse:
        """ChatModel 协议实现: 挑一个还能放行的模型, 失败时按闸的动作切.

        参数语义与 ChatModel.generate 完全一致 —— 包装不吞不改任何参数 (#68).

        Raises:
            CircuitOpenError: 这次请求没有发出去 (两家都熔断着), 或刚跳闸而没有
                备份可切 (不可重试, 见模块说明).
            BaseException: 模型自己的失败 (不算故障的 / 还没到阈值的) 原样上抛,
                交给重试层与上层按既有契约处理.
            asyncio.CancelledError: 被 kill switch 打断 —— 闸只把探测位还回来,
                不记账 (取消是调用方的决定, 不是这家的病).
        """
        slot = self._pick()
        while True:
            try:
                response = await slot.model.generate(
                    messages,
                    tools,
                    temperature=temperature,
                    top_p=top_p,
                    seed=seed,
                    max_tokens=max_tokens,
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                    stream=stream,
                )
            except asyncio.CancelledError:
                slot.breaker.record_abort()
                raise
            except Exception as exc:
                if not slot.breaker.record_failure(exc):
                    raise  # 不算故障 / 还没到阈值: 原样上抛 (重试层照旧可能再试)
                following = self._other_available(slot)
                if following is None:
                    # 这一下把闸拨开了, 却没有备份可切: 把「闸开了」报上去 ——
                    # 不可重试, 重试层立刻放弃; 原始失败挂在异常链上 (`from`)
                    raise CircuitOpenError(
                        f"模型 {slot.name} 熔断跳闸 (连续 "
                        f"{slot.breaker.failures} 次失败), "
                        f"{self._no_alternative_text()} —— "
                        f"这一次调用以失败收场",
                        name=slot.name,
                    ) from exc
                await self._announce(slot, following, exc)
                slot = following
                continue
            slot.breaker.record_success()
            # 只有真答了话的模型才算服务过 (失败的那几次产出 0 个 token, 不上账)
            note_serving(slot.name)
            return response

    async def aclose(self) -> None:
        """释放两侧的连接池 (一侧关不掉也要把另一侧关上 —— 漏在那里的是连接)."""
        failure: BaseException | None = None
        for slot in self._slots:
            try:
                await slot.model.aclose()
            except BaseException as exc:  # 取消也不例外: 先把另一侧关干净再往外抛
                failure = failure or exc
        if failure is not None:
            raise failure

    @property
    def breakers(self) -> tuple[CircuitBreaker, ...]:
        """两侧的闸 (主在前) —— 观测用 (健康检查 / 用例看状态), 模型层不靠它工作."""
        return tuple(slot.breaker for slot in self._slots)

    def _pick(self) -> _Slot:
        """挑这一跳的落点 (主 -> 备); 都不可用 -> CircuitOpenError.

        Raises:
            CircuitOpenError: 没有任何一个模型现在能放行 (闸开着 / 探测位被占).
        """
        slot, refusals = self._first_available(skip=None)
        if slot is not None:
            return slot
        if len(refusals) == 1:
            raise refusals[0]  # 只有一个模型时, 把它的拒词原样报出去
        raise CircuitOpenError(
            "主备模型现在都不能用 —— " + "; ".join(str(item) for item in refusals)
        )

    def _other_available(self, current: _Slot) -> _Slot | None:
        """除了 `current` 之外还有谁能接这一跳; 没有 / 也熔断着 -> None."""
        slot, _ = self._first_available(skip=current)
        return slot

    def _first_available(
        self, *, skip: _Slot | None
    ) -> tuple[_Slot | None, list[CircuitOpenError]]:
        """按主 -> 备的顺序找第一个能放行的模型, 顺手把拒词收齐 (给报错文案用).

        返回拒词而不是就地抛: 两个调用方要的不一样 —— `_pick` 要把「都不能用」抛
        出去, 而 `_other_available` 只想知道「还有没有别人」(没有就让原始失败上抛).
        """
        refusals: list[CircuitOpenError] = []
        for slot in self._slots:
            if slot is skip:
                continue
            try:
                slot.breaker.acquire()
            except CircuitOpenError as refusal:
                refusals.append(refusal)
                continue
            return slot, refusals
        return None, refusals

    def _no_alternative_text(self) -> str:
        """「没有别的模型可以接这一跳」的两种来路 (报错文案要说清是哪一种).

        注意第二种写法对**哪一侧跳闸都成立**: 主模型跳闸而备份不能用、备份跳闸而
        主模型还开着 —— 两件事在文案上都不该说成「备份也熔断着」(那只有前一件对).
        """
        if len(self._slots) == 1:
            return "没有配置备份模型"
        return "另一个模型也不能接这一跳"

    async def _announce(
        self, gone: _Slot, following: _Slot, exc: BaseException
    ) -> None:
        """一次切换的痕迹: 一行日志 + 挂上来的通知回调 (两者都不改变切换本身)."""
        switch = ModelSwitch(
            from_name=gone.name,
            to_name=following.name,
            reason=f"连续 {gone.breaker.failures} 次失败熔断 "
            f"(最后一次: {type(exc).__name__})",
        )
        logger.warning(
            "模型 %s 熔断, 本次调用改走 %s (原因: %s)",
            switch.from_name,
            switch.to_name,
            switch.reason,
        )
        if not self._on_switch:
            return
        for callback in self._on_switch:
            outcome = callback(switch)
            if inspect.isawaitable(outcome):
                await outcome
