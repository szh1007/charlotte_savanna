"""CharAgent 测试共享替身 (test doubles): 时钟 / 睡眠 / 随机源 (difficulties #61).

与 `tests/helpers.py` 的分工: helpers 放 wire 样本与常量 (「真实响应长什么样」),
本文件放**测试替身** —— 注入被测代码的缝, 让时间与随机性在测试里完全确定
(#61 注入随机 / 时间源), 且不真等待一秒.

三个替身 (都是「可调用对象」, 直接当参数注入, 无需 mock 框架):
- `FakeClock`:   手动推进的固定时钟 (LoopGuard.time_source /
                 RetryPolicy.time_source)
- `RecordingSleep`: 记录式睡眠 (RetryPolicy.sleep): 记下每次等待时长并推进
                 固定时钟 —— 于是「等待多久」与「过了多久」用同一份状态
- `FixedRandom`: 固定随机源 (RetryPolicy.random_source): 依序吐预设值

第 3 处出现才抽到这里 (项目 DRY 阈值: 重复 >= 3 次抽取) —— test_loop_guard.py
与 test_loop_events.py 的本地 `_FakeClock` 已改为 import 本模块.
"""

from __future__ import annotations


class FakeClock:
    """固定时钟: 值由测试手动推进 (时间源注入缝, #61 确定性的做法)."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class RecordingSleep:
    """记录式睡眠: 记下等待时长并推进固定时钟 (不真等待).

    attributes:
        delays: 每次睡眠的秒数, 按调用顺序 (退避序列断言的证据).
    """

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.delays: list[float] = []
        self._clock = clock

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        if self._clock is not None:
            self._clock.now += seconds


class FixedRandom:
    """固定随机源: 依序返回预设值 (用尽后停在最后一个), 使抖动完全确定 (#61)."""

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]
