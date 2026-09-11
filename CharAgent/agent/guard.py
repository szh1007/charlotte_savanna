"""LoopGuard (issue 04 / difficulties #3): 循环的「刹车」, 防模型无限循环.

一句话理解: 模型可能永远决策「再调一次工具」, 把预算烧光也不停.
LoopGuard 是刹车 —— 设好上限, 到点就不再发起下一次模型决策, 强制结束
这次 run. 注意刹车停时模型其实还想继续, 所以不会有最终答复
(见 agent/loop.py docstring 的两种停法).

三种刹车 (都是软限制, 任一触发即停, run 结果里 outcome 会说明是哪一种):
- max_turns:            最多允许模型决策几次 (Turn 数, 概念见 agent/loop.py).
                         例: max_turns=2 表示这次 run 最多发生 2 次模型决策,
                         第 2 次决策后模型还想调工具 -> 停, 不再问第 3 次.
- max_total_tokens:     整个 run 累计 token 预算 (按每次响应的 usage 累加,
                         无 usage 的响应计 0). 例: 预算 50, 第 1 轮花 30
                         没事, 第 2 轮又花 30 (累计 60 超限) -> 拦住第 3 轮.
- max_duration_seconds: 整个 run 的墙钟时长预算, 从 run 开始计时.

「这一轮结束后才判断」是什么意思 (软限制的检查时机):
检查点设在每轮收尾、准备发起下一次模型决策之前 —— 模型这一轮要求的工具
会正常执行完、结果回填, 消息历史永远是完整的 (不会留下「调了工具却没
结果」的半截对话). 等这一轮的事都办完了才检查上限, 超了就停. 具体落在
AgentLoop.run 的循环顶部: guard.check_after_turn().

软限制 vs kill switch (两种「停」的区别, 面试考点):
- 软限制 (本类): 轮后判断, 温和 —— 跑完当前这轮, 历史保持合法.
- kill switch:  随时立刻打断 —— 外部对运行中的 task 调 asyncio.Task.
  cancel(), 在任意 await 点 (模型调用中 / 工具执行中) 立即抛
  CancelledError 结束; AgentLoop 不吞这个异常, 直接传播. 它不属于本类,
  是 AgentLoop 的外部行为.

token 预算注意: 本类是「事后判定」—— 模型响应返回后按 usage 累计, 超了
就拦住下一次调用. 「调用前先预估 token、不够就不发请求」属 P1-11 token
计量 (retry.py / model.py), 不在本模块.

本模块只放 LoopGuard 行为类; 零件在 utils/ 子包: 结束原因枚举 LoopOutcome
在 utils/types.py, 配置错误 GuardConfigError 在 utils/errors.py. 截断续写
次数上限 (max_truncations) 不是刹车 —— 那是 AgentLoop 截断处理的一部分,
在 agent/loop.py.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from CharAgent.agent.utils.errors import GuardConfigError
from CharAgent.agent.utils.types import LoopOutcome


@dataclass(slots=True)
class LoopGuard:
    """循环软限制参数与判定 (difficulties #3).

    attributes:
        max_turns: 最大模型决策次数 (Turn 数). 达到后若模型还想继续
            (本轮有工具调用 / 截断) 则触发, 默认 10.
        max_total_tokens: 单次 run 累计 token 预算 (usage.total_tokens
            累加, 无 usage 的响应计 0). None 表示不限制.
        max_duration_seconds: 单次 run 墙钟总时长预算, None 表示不限制.
        _time_source: 计时器注入点 (默认 time.monotonic, 测试可换固定时钟,
            见 test_loop_guard.py 的 wall-clock 用例).
    """

    max_turns: int = 10
    max_total_tokens: int | None = None
    max_duration_seconds: float | None = None
    _time_source: Callable[[], float] = field(default=time.monotonic, repr=False)
    _started: float | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        """配置校验: 上限必须为正数, 非法抛 GuardConfigError."""
        if self.max_turns < 1:
            raise GuardConfigError(f"max_turns 必须 >= 1, 实际: {self.max_turns}")
        if self.max_total_tokens is not None and self.max_total_tokens < 1:
            raise GuardConfigError(
                f"max_total_tokens 必须 >= 1 或 None, 实际: {self.max_total_tokens}"
            )
        if self.max_duration_seconds is not None and self.max_duration_seconds <= 0:
            raise GuardConfigError(
                f"max_duration_seconds 必须 > 0 或 None, "
                f"实际: {self.max_duration_seconds}"
            )

    def start(self) -> None:
        """开始计时 (AgentLoop.run 进入循环前调用一次)."""
        self._started = self._time_source()

    @property
    def elapsed_ms(self) -> float:
        """自 start() 起的墙钟耗时 (毫秒); 未 start() 时恒 0."""
        if self._started is None:
            return 0.0
        return (self._time_source() - self._started) * 1000

    def check_after_turn(
        self,
        *,
        turn_count: int,
        total_tokens: int,
    ) -> LoopOutcome | None:
        """每轮分支处理完后的软限制判定 (difficulties #3).

        由 AgentLoop 在每轮结束、准备发起下一轮模型调用前调用 —— 能走到
        这里说明模型还想继续 (本轮有工具调用或截断), 于是检查三种上限,
        命中即返回对应 outcome, 全部未命中返回 None (继续下一轮).

        Args:
            turn_count: 已完成的模型决策次数.
            total_tokens: 已累计的 usage token 数 (无 usage 计 0).

        Returns:
            LoopOutcome: 命中的触发点; None 表示未触发, 可继续下一轮.
        """
        # 判定顺序即优先级: turns 用尽最严重 (循环空转), 其次预算超支
        if turn_count >= self.max_turns:
            return LoopOutcome.MAX_TURNS
        if self.max_total_tokens is not None and total_tokens >= self.max_total_tokens:
            return LoopOutcome.TOKEN_BUDGET
        if (
            self.max_duration_seconds is not None
            and self.elapsed_ms / 1000 >= self.max_duration_seconds
        ):
            return LoopOutcome.TIME_LIMIT
        return None
