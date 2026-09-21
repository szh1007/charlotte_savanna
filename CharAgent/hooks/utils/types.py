"""hooks 包静态零件: hook 点枚举、裁决与失败记录.

对齐 agent/utils/types.py 的组织惯例 —— 行为 (HookRegistry 的注册与分发)
在 hooks/registry.py, 静态数据结构集中于此供 registry / 门面 / 消费方共享.

大白话版 (本文件 = 插座规格表):
- 6 个「喊一声」的时机分别叫什么: 每轮开工前 (before_turn) / 每轮收工后
  (after_turn) / 问模型前后 (on_model_call) / 每个工具开跑**前**
  (before_tool_execute) / 每个工具跑完 (on_tool_executed) / 每声喊话之后
  (on_event).
- 插头长什么样: 普通函数或 async 函数都行 (HookFn), 载荷以关键字参数传入.
- **其中只有一个是「说了算数」的**: 工具开跑前那个 —— 插头可以回一句
  Decision.reject("为什么"), 那个工具就真的不跑, 理由当作它的执行结果回填给
  模型 (Decision). 其余五个时机插头说什么都没人听 (返回值被忽略).
- 顺手记两件事: 问模型分「发出前」和「收到后」两个 phase (ModelCallPhase);
  坏插头记成一条记录 (HookFailure), 不静默丢.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from CharAgent.hooks.utils.errors import HookConfigError


class HookPoint(StrEnum):
    """六个生命周期 hook 点 (扩展点; 空注册零开销).

    触发时机与载荷见 registry.HookRegistry docstring. P2 模块 (memory / cost /
    observability) 经此挂载, 核心零 import P2.

    两类点, 区别只在「返回值算不算数」(对应 registry 的两个触发方法):
    - 观察类 (5 个): 喊一嗓子通知插件 —— 记日志 / 记账 / 采集 trace; 返回值被
      忽略, 没人看 (fire).
    - 裁决类 (1 个): BEFORE_TOOL_EXECUTE —— 插件说的话算数, 可以不让工具执行
      (decide). 「值不值得拦截」是只有这个点才有的性质, 所以它由**另一个方法**
      触发, 而不是给 fire 加一个「某些点才管用」的返回值 (见 registry 备注).
    """

    BEFORE_TURN = "before_turn"  # 每 Turn 模型调用前 (memory 注入记忆)
    AFTER_TURN = "after_turn"  # 每 Turn 结束后 (memory 落库 / cost 记账)
    ON_MODEL_CALL = "on_model_call"  # 模型请求发出前 / 响应返回后 (两个 phase)
    BEFORE_TOOL_EXECUTE = "before_tool_execute"  # 工具执行前 (拦截: 拒绝则工具不跑)
    ON_TOOL_EXECUTED = "on_tool_executed"  # 每条工具执行完成 (指标 / 审计)
    ON_EVENT = "on_event"  # 每个 StreamEvent 分发后 (sink 之后, trace 采集)


@dataclass(frozen=True, slots=True)
class Decision:
    """拦截点的裁决 (裁决类 hook 点的返回值): 放行 / 拒绝, 拒绝时必须给原因.

    插件在 BEFORE_TOOL_EXECUTE 上返回它来表态:

    - 返回 None 或 Decision.allow() → 放行, 工具照常执行 (None 是「这事我管不着」
      的自然写法: 插件常常只关心一部分工具调用, 不关心的直接 return)
    - 返回 Decision.reject("原因") → 工具**不执行**, 原因当作这条工具调用失败的
      文本回填给模型 (走框架现成的「工具错误」通道, 与参数校验失败同一条路)

    为什么用类型表态而不是「返回字符串就是拒绝」: 返回值只有一种形状, 插件写错
    了 (返回个 True / 随手一个字符串) 能被认出来并按拒绝处理 (fail closed), 而不是
    被当成放行 —— 一道悄悄失效的护栏比没有护栏更危险 (见 registry.decide).

    为什么**只**有放行 / 拒绝两值 (P0/P1 不做「需要确认」): 那是另一种机制 ——
    挂起 + 存档 + 用户确认 + 从存档点续跑, 与「拒绝」的语义完全不同 (拒绝是当场
    有结论, 挂起是等着别人给结论). 没有真实调用方之前不做第三种返回值, 免得在
    没有需求的情况下发明契约 (#25 HITL 落地时再加).

    attributes:
        allowed: True 放行 / False 拒绝.
        reason: 拒绝的原因 (面向模型的一句话: 说清为什么不行、该怎么改); 放行时
            必须为 None. 两个方向都是构造期校验: 拒绝不给原因 → 模型收到一条没有
            理由的拒绝, 只会换个参数再试一次; 放行却带原因 → 那句话没人读, 是写
            错了 (想拦就别放行).
    """

    allowed: bool
    reason: str | None = None

    def __post_init__(self) -> None:
        """两边都校验 (构造期拦住): 拒绝必须有原因, 放行必须不带原因."""
        if not self.allowed and not (self.reason or "").strip():
            raise HookConfigError(
                "拒绝必须给出原因 (该原因会作为这次工具调用的失败文本回填给模型): "
                "用 Decision.reject('为什么不行 / 该怎么改') 构造"
            )
        if self.allowed and self.reason is not None:
            raise HookConfigError(
                f"放行不该带原因 (没人会读它, 而写它的人多半是想拦): "
                f"reason={self.reason!r}, 用 Decision.allow() 构造"
            )

    @classmethod
    def allow(cls) -> Decision:
        """放行 (与返回 None 等价, 显式写法)."""
        return cls(allowed=True)

    @classmethod
    def reject(cls, reason: str) -> Decision:
        """拒绝并给出原因 (面向模型: 说清为什么不行)."""
        return cls(allowed=False, reason=reason)


# hook 函数形态: 同步或异步都接受 (registry 统一 await 到位).
# 载荷以关键字参数传递, 各点的载荷清单见 registry.HookRegistry docstring.
# 返回值按点解释: 观察类点忽略 (约定返回 None); 裁决类点看它 —— None = 放行,
# Decision = 裁决. 两种形态共用一个别名, 因为「返回什么算数」由 hook 点决定,
# 不由函数签名决定 (同一个函数可以同时挂在两类点上).
type HookFn = Callable[..., Awaitable[Decision | None] | Decision | None]


class ModelCallPhase(StrEnum):
    """ON_MODEL_CALL 的两次触发 (载荷字段 phase 的取值).

    模型调用在 hook 视角是一个区间: 请求发出前 (可观测 / 计量准备) 与响应返回后
    (记账 / 采样参数核对) 语义不同, 故用同一 hook 点分两个 phase 触发.
    """

    BEFORE = "before"  # 请求发出前
    AFTER = "after"  # 响应返回后


@dataclass(slots=True)
class HookFailure:
    """被隔离的 hook 异常记录 (扩展点出错不拖垮核心, 但必须留痕不静默).

    hook 抛出的 Exception 由 HookRegistry 捕获后记入 registry.failures ——
    调用方 (测试 / CLI / P1 server) 可检查; 结构化日志接上后由此转 logger.
    CancelledError 属 BaseException, 不入此记录 (直接向上传播,
    插件不得挡住 kill switch).

    error 有两种来源: 插件自己抛的异常, 或者框架发现插件写错了 (在裁决点上返回
    了既不是 Decision 也不是 None 的东西) 而造出的 HookError —— 两者都是「这个
    插件坏了」, 记在同一处才查得全.
    """

    point: HookPoint
    hook: HookFn
    error: BaseException
