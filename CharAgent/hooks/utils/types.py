"""hooks 包静态零件: hook 点枚举、裁决与失败记录.

对齐 agent/utils/types.py 的组织惯例 —— 行为 (HookRegistry 的注册与分发)
在 hooks/registry.py, 静态数据结构集中于此供 registry / 门面 / 消费方共享.

大白话版 (本文件 = 插座规格表):
- 6 个「喊一声」的时机分别叫什么: 每轮开工前 (before_turn) / 每轮收工后
  (after_turn) / 问模型前后 (on_model_call) / 每个工具开跑**前**
  (before_tool_execute) / 每个工具跑完 (on_tool_executed) / 每声喊话之后
  (on_event).
- 插头长什么样: 普通函数或 async 函数都行 (HookFn), 载荷以关键字参数传入.
- **其中只有一个是「说了算数」的**: 工具开跑前那个 —— 插头能表三种态
  (Verdict 与 Decision): 放行 (工具照跑) / 拒绝 (工具不跑, 理由当作它的执行
  结果回填给模型) / 需人工确认 (工具不跑, **整次运行停在半路等人**, #25 HITL).
  其余五个时机插头说什么都没人听 (返回值被忽略).
- 顺手记两件事: 问模型分「发出前」和「收到后」两个 phase (ModelCallPhase);
  坏插头记成一条记录 (HookFailure), 不静默丢.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
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


class Verdict(StrEnum):
    """裁决点上插件能表的三种态 (对应 Decision.verdict).

    - ALLOW: 放行 —— 工具照常执行 (与「不表态」等价).
    - REJECT: **当场有结论** —— 工具不跑, 原因当作这条调用失败的文本回填给模型,
      模型下一轮自己换个说法接着答 (一次普通的工具失败).
    - REQUIRES_APPROVAL: **等着别人给结论** —— 工具先不跑, 整次运行就此**停在
      半路**存档等人 (挂起, #25 HITL); 人给了结论才从存档点接着跑.

    后两者都让工具不执行, 区别在**这个结论由谁、在哪一刻给** (见 Decision).
    """

    ALLOW = "allow"
    REJECT = "reject"
    REQUIRES_APPROVAL = "requires_approval"


@dataclass(frozen=True, slots=True)
class Decision:
    """拦截点的裁决 (裁决类 hook 点的返回值): 放行 / 拒绝 / 需人工确认 三选一.

    插件在 BEFORE_TOOL_EXECUTE 上返回它来表态:

    - 返回 None 或 `Decision.allow()` → 放行, 工具照常执行 (None 是「这事我管
      不着」的自然写法: 插件常常只关心一部分工具调用, 不关心的直接 return)
    - 返回 `Decision.reject("原因")` → 工具**不执行**, 原因当作这条工具调用失败的
      文本回填给模型 (走框架现成的「工具错误」通道, 与参数校验失败同一条路)
    - 返回 `Decision.requires_approval(prompt=..., needs=...)` → 工具**先不执行**,
      整次运行**停在半路**等人给结论 (挂起 + 存档 + 从存档点恢复, #25 HITL)

    **拒绝与挂起的分野是本类最要紧的一处** (CONTEXT.md 有对应词条), 而它们的
    区别**不在「允许不允许」, 在「这个结论由谁、在哪一刻给」**:

    | | 谁给结论 | 模型这一轮 | 留下什么 |
    |---|---|---|---|
    | reject | 插件自己 (当场) | 换个说法接着答 | 一条工具失败结果 |
    | requires_approval | 用户本人 (可能是明天) | **不继续** | 一帧挂起点 + 等人批 |

    一句话: 拒绝是插件说「不行」, 挂起是插件说「这得问人」. 把两者混起来会得到
    一个错的口径 —— 一个「该问人」的操作被写成「当场拒绝」, 用户永远等不到那张
    确认卡 (模型只会道歉然后绕过去).

    **`prompt` 与 `needs` 都是业务语义, 框架只搬运** —— 与 `Tool.annotations`、
    `RunContext.payload` 同一条纪律: 框架不解释 `"payment_password"` 是什么,
    只把它原样带进确认事件与记录, 由业务决定怎么渲染、怎么取.

    为什么用类型表态而不是「返回字符串就是拒绝」: 返回值只有一种形状, 插件写错
    了 (返回个 True / 随手一个字符串) 能被认出来并按拒绝处理 (fail closed), 而不是
    被当成放行 —— 一道悄悄失效的护栏比没有护栏更危险 (见 registry.decide).

    为什么把第三态加在**同一个类型**上而不是新开一个平行类型: `decide()` 的契约
    是「返回值有语义」, 而 registry 对**认不出来**的返回值一律 fail-closed 拒绝 ——
    加一个平行类型意味着「新类型要显式登记才算数」, 登记漏了就会被静默当成拒绝.
    加在同一类型上则相反: 第三种态是**显式**的, 不表态就是放行.

    attributes:
        verdict: 三种态之一 (Verdict).
        reason: **拒绝**的原因 (面向模型的一句话: 说清为什么不行、该怎么改);
            只在 reject 时非空.
        prompt: **挂起**时给用户看的一句话 (如「这一单要付款了, 需要你输一次
            支付密码」) —— 它是**话术**, 由业务写, 框架只搬运到确认事件与记录里;
            只在 requires_approval 时非空 (类型上是空串而不是 None: 消费方拿到它
            就要往卡片上印, 于是「有值」这件事由构造期校验保证, 不必处处判空).
        needs: **挂起**时还缺什么 (机器可读的短名字, 如 `("payment_password",)`);
            框架只透传不解释. 空元组是合法的 —— 纯「是 / 否」的确认 (如「下单
            前确认一下」) 不需要额外提供任何东西.

    三种形状各自的字段**构造期校验** (写错了当场报, 不让它走到运行里):
    - 拒绝不给原因 → 模型收到一条没有理由的拒绝, 只会换个参数再试一次
    - 放行却带原因 / 话术 → 那句话没人读, 是写错了 (想拦就别放行)
    - 挂起不给 prompt → 前端弹出一张没有字的卡, 用户不知道该确认什么
    """

    verdict: Verdict
    reason: str | None = None
    prompt: str = ""
    needs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """三种形状各自校验 (构造期拦住): 该有的字段缺了就报, 不该有的报了也报."""
        if self.verdict is Verdict.REJECT:
            if not (self.reason or "").strip():
                raise HookConfigError(
                    "拒绝必须给出原因 (该原因会作为这次工具调用的失败文本回填给"
                    "模型): 用 Decision.reject('为什么不行 / 该怎么改') 构造"
                )
            if self.prompt or self.needs:
                raise HookConfigError(
                    f"拒绝不该带确认话术与缺失项 (它是当场有结论, 没有要去问的人): "
                    f"prompt={self.prompt!r}, needs={self.needs!r}"
                )
            return
        if self.verdict is Verdict.REQUIRES_APPROVAL:
            if not self.prompt.strip():
                raise HookConfigError(
                    "挂起必须给出 prompt (它是给用户看的那句话 —— 没有它, 前端弹出"
                    "一张没有字的确认卡): 用 Decision.requires_approval(prompt=...)"
                )
            if self.reason is not None:
                raise HookConfigError(
                    f"挂起不该带拒绝原因 (reason 是回填给模型的失败文本, 而挂起"
                    f"这一轮不继续): reason={self.reason!r}"
                )
            if any(not item.strip() for item in self.needs):
                raise HookConfigError(
                    f"needs 里每一项都要是非空短名字 (空串会让前端渲染出一个没有"
                    f"名字的输入框): {self.needs!r}"
                )
            return
        if self.reason is not None or self.prompt or self.needs:
            raise HookConfigError(
                f"放行不该带原因 / 话术 / 缺失项 (没人会读它们, 而写它们的人多半是"
                f"想拦): reason={self.reason!r}, prompt={self.prompt!r}, "
                f"needs={self.needs!r}, 用 Decision.allow() 构造"
            )

    @property
    def allowed(self) -> bool:
        """是不是放行 (放行之外的两态工具都不执行, 但它们的去向完全不同)."""
        return self.verdict is Verdict.ALLOW

    @classmethod
    def allow(cls) -> Decision:
        """放行 (与返回 None 等价, 显式写法)."""
        return cls(Verdict.ALLOW)

    @classmethod
    def reject(cls, reason: str) -> Decision:
        """拒绝并给出原因 (面向模型: 说清为什么不行)."""
        return cls(Verdict.REJECT, reason=reason)

    @classmethod
    def requires_approval(cls, prompt: str, needs: Sequence[str] = ()) -> Decision:
        """挂起等人给结论: prompt 是给用户看的一句话, needs 是还缺什么.

        Args:
            prompt: 给用户看的那句话 (浮在确认卡上).
            needs: 机器可读的缺失项 (框架只透传); 空 = 纯是 / 否的确认.
        """
        return cls(Verdict.REQUIRES_APPROVAL, prompt=prompt, needs=tuple(needs))


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
