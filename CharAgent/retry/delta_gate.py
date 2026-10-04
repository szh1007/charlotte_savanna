"""DeltaGate (difficulties #66): 增量回调的转发 + 吐字闸.

一句话理解: 给两个增量回调包一层转发, 顺手记下「有没有吐过字」—— 重试层与
熔断层各自要这个位来判断「还能不能把整条请求重发 / 换一家发」: 一旦有增量交到
调用方手里, 重发就收不回那半截了 (「半截 + 从头再来」比「半截 + 一句说明」糟).

两处的规矩一模一样, 所以只写在这里一处:

- **先置位再转发**: 回调自己抛错也算吐过字 —— 文本已经交到调用方手里.
- **没挂回调就一个关键字都不传**: 不挂回调的调用在底层模型眼里与从前逐字一致
  (既有用例断言「包装透传的实参一字不差」, #68).

大白话版: 一根「已经出过声」的闸 —— 出了声就别把这句话从头再念一遍.
"""

from __future__ import annotations

from CharAgent.model.protocol import DeltaCallback


class DeltaGate:
    """转发两个增量回调, 并记下「这一趟有没有吐过字」(#66).

    attributes:
        emitted: 这一趟是否已经把增量交给调用方 (任一通道被调用过即置位).
        kwargs: 转发给底层模型的关键字实参 (`**gate.kwargs`) —— 挂了哪个回调
            才有哪个键; 两个都没挂时是空字典 (调用形状与从前一字不差).
    """

    def __init__(
        self,
        on_delta: DeltaCallback | None = None,
        on_reasoning_delta: DeltaCallback | None = None,
    ) -> None:
        self._emitted = False
        forwarded: dict[str, DeltaCallback] = {}
        if on_delta is not None:
            forwarded["on_delta"] = self._observing(on_delta)
        if on_reasoning_delta is not None:
            forwarded["on_reasoning_delta"] = self._observing(on_reasoning_delta)
        self._kwargs = forwarded

    @property
    def emitted(self) -> bool:
        """这一趟是否已经吐过字 (回调被调用过即真, 单调不回退)."""
        return self._emitted

    @property
    def kwargs(self) -> dict[str, DeltaCallback]:
        """转发给底层模型的关键字实参; 拷贝一份出去 (调用方改不动闸里的账)."""
        return dict(self._kwargs)

    def _observing(self, callback: DeltaCallback) -> DeltaCallback:
        """包一层: 原样转发, 转发之前先置位."""

        async def _forward(text: str) -> None:
            self._emitted = True
            await callback(text)

        return _forward
