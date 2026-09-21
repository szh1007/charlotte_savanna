"""HookRegistry: hook 注册表骨架 (扩展点).

一句话理解: 核心在固定时机「喊一声」(fire), 注册过的插件函数被依次叫到,
没注册就什么都不发生 —— 这是 P2 模块 (memory / cost / observability) 挂载
到核心的挂钩, 核心代码里不出现任何 P2 的 import (依赖方向单向).

为什么是「骨架」: P0 只落注册与分发机制 (本文件), 不实现任何插件. 空注册
时 fire 立即返回 (一次 dict 查询 + 空列表判断), 不产生回调调用与参数拷贝,
也不会在执行中挂起 —— 即「空实现零成本」这一要求, P0/P1 验收不受
P2 进度影响.

喊一声之外还有第二种触发: **请插件裁决** (decide) —— 工具执行前那一个点上,
插件说的话算数 (可以不让工具执行). 那一句「不行」会让本次工具调用变成一条
失败结果回填给模型, 插件因此能拦住权限外的操作 / 危险操作 (P0 骨架里没有它,
当时的扩展点只能事后观察).

大白话版:
- 现实问题: 以后要加「记住用户偏好」「算这次花了多少钱」「把每次工具调用
  记进日志」这类功能. 如果每加一个都去改 agent 的主循环, 主循环迟早被改烂
  —— 需要「插座」: 主循环在固定时机喊一声, 谁想搭把手就自己插个插头.
- 打个比方: 家里的插座排插. 空着的时候电照跑 (空注册零开销, 不多花时间);
  插了电器就供电 (插头函数被调用); 某个电器短路只烧它自己的保险丝 (插件抛
  异常时记一笔然后跳过), 不会把全屋电闸拉掉 (用户的问答照常完成).
- 唯一的例外: 拔总电源 (用户点「停止」/ kill switch) 不算插件故障 —— 插件
  不许挡着取消, 这类信号直接放行.
- 工具开跑前那一个点装的是**带开关的插座**: 插头说一句「这台不许开」, 主循环
  就真的不给它通电 (那个工具不执行), 并把插头给的理由转告模型. 放行 / 拒绝的
  裁决只在这一个点上算数.
- 一个必须说清的分寸: 「短路只烧自己保险丝」那套 (插件坏了记一笔、照常往下走)
  只管观察点; 带开关的插座上, 插头自己坏了按**不让开**处理 (fail closed) ——
  悄悄失效的护栏比没有护栏更危险 (没有护栏时人还知道自己没装).
- 本文件: 插座本体 —— 登记插头 (register) / 拔掉 (clear) / 到点挨个叫
  (fire) / 挨个问一句「能不能开」并听回答 (decide). 六个时机见
  utils/types.py 的 HookPoint.

六个 hook 点与载荷 (载荷全部为关键字参数, 各 hook 用 **kwargs 接收自己关心的
字段即可):

- before_turn: 每 Turn 模型调用前 (guard 判定通过后才触发). 载荷: turn,
  messages (活引用, 可注入), tools
- after_turn: 每 Turn 记录快照后. 载荷: turn, response, messages, tokens,
  elapsed_ms
- on_model_call: 模型请求发出前 / 响应返回后各触发一次. 载荷: phase
  ("before" / "after"), turn, messages, tools, response (仅 after),
  usage (仅 after), elapsed_ms (仅 after)
- before_tool_execute: **工具执行前** (裁决类, 用 decide 触发). 载荷: turn,
  call (要调的工具), tool (这个调用命中的 Tool 对象 —— 插件判断「这算什么操作」
  就看它的 annotations; 模型编出来的工具名在到达这里之前就被拦下了, 故必有值).
  插件返回 None / Decision.allow() 放行, 返回 Decision.reject(原因) 则工具不跑
- on_tool_executed: 每条工具执行完成 (结果已回填; **被拒绝的调用也走这里** ——
  它同样产出一条失败态的 ToolExecution). 载荷: turn, call, execution
- on_event: 每个 StreamEvent 分发后. 载荷: event

三条设计约定 (面试可讲):
- **异常隔离**: hook 抛 Exception 只记入 failures 并继续跑其余 hook, 不向
  run 传播 —— 扩展点是可选的, 插件出错不该让用户的任务失败. CancelledError
  不在此列 (BaseException), 直接传播 —— 插件不得挡住 kill switch (#3).
- **载荷是活引用**: before_turn 拿到的 messages 就是 loop 的历史列表本体,
  memory 插件即在此挂载 (注入记忆); 想只读就自己拷贝.
- **观察与裁决分成两个方法**: fire 只负责「喊一声」, 返回值无人看; decide 专门
  触发裁决类点, 按返回的 Decision 给结论. 不给 fire 加返回值, 是因为「返回值
  算不算数」是**只有某些点才有的性质** —— 在一个方法上附加「某些点的返回值管用、
  某些点的不算」这条隐式规则, 是以后一定会踩的坑; 用两个方法把这个区别写进 API
  形状里更牢 (代价只是多一个方法名).

**异常策略两条不同的规矩, 别照抄**: 观察类点的插件坏了, 顶多少一条日志 (记一笔
就跳过, 即上面的异常隔离); 裁决类点的插件坏了, 那道护栏就**等于不存在** ——
所以 decide 采取 **fail closed**: 插件抛异常 (或返回了认不出的东西) 一律按拒绝
处理, 并记入 failures. 一个悄悄失效的护栏比没有护栏更危险: 没有护栏时人还知道
自己没装, 悄悄失效时人以为装着. 代价是插件写错会让工具调不通, 但那是**看得见**
的故障 (reason 里写着「拦截插件出错」, failures 里躺着原始异常), 而不是静默放行.

本模块只放 HookRegistry 行为类; 零件在 utils/ 子包: HookPoint / HookFn /
Decision / HookFailure 在 utils/types.py, HookConfigError 在 utils/errors.py.
"""

from __future__ import annotations

import inspect
from typing import Any

from CharAgent.hooks.utils.errors import HookConfigError, HookError
from CharAgent.hooks.utils.types import Decision, HookFailure, HookFn, HookPoint

# 插件抛异常时的哨兵返回值 (异常已记入 failures): 必须与「插件返回 None」(不表态,
# 放行) 分得开 —— 前者在裁决点上是 fail closed, 后者是放行.
_HOOK_FAILED = object()

# 拦截插件坏了 (抛异常 / 返回值认不出) 时的拒绝原因: 走现成的工具错误通道回填给
# 模型. 只陈述事实 + 劝退重试 (原始异常在 failures 里, 不往模型/用户那边倒).
INTERCEPT_FAILED_REASON = (
    "工具调用被拒绝: 拦截插件执行出错, 无法放行 (重试相同调用也不会通过). "
    "请如实告知用户这一步没能完成."
)


class HookRegistry:
    """hook 点的注册与分发.

    一个 registry 实例挂在一个 AgentLoop 上 (构造参数 hooks=); 同一实例可被
    多个 hook 点共享. 线程/协程安全说明: 注册在启动期完成, 运行期只读
    (fire / decide), 故不做加锁 (server 层若需动态注册, 应在 run 之间进行).

    attributes:
        failures: 被隔离的 hook 异常记录 (HookFailure), 顺序为发生序. 空注册
            或全部 hook 正常时恒为空; 由调用方检查, 框架不静默吞掉问题.
            「插件坏了」的两种都记在这里: 插件自己抛的异常, 以及裁决点上返回
            了认不出的值的 (后者由框架造一条 HookError 记下).
    """

    def __init__(self) -> None:
        self._handlers: dict[HookPoint, list[HookFn]] = {}
        self.failures: list[HookFailure] = []

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(self, point: HookPoint, hook: HookFn) -> None:
        """把 hook 注册到某个点 (同一 hook 可注册到多个点; 同点按注册顺序执行).

        Args:
            point: 挂载的 hook 点 (HookPoint).
            hook: 同步或异步函数, 载荷以关键字参数传入 (见模块 docstring 表).
                挂在裁决类点上的还要返回裁决 (None = 放行 / Decision), 见 decide.

        Raises:
            HookConfigError: hook 不可调用 (拼错注册时尽早报错, 不留到运行期).
        """
        if not callable(hook):
            raise HookConfigError(
                f"hook 必须可调用, 实际: {type(hook).__name__} ({point.value})"
            )
        self._handlers.setdefault(point, []).append(hook)

    def clear(self, point: HookPoint | None = None) -> None:
        """清空注册: 指定点只清该点, 不传则清空全部点 (failures 不受影响)."""
        if point is None:
            self._handlers.clear()
            return
        self._handlers.pop(point, None)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def has(self, point: HookPoint) -> bool:
        """该点是否注册过 hook (供调用方判断是否需准备载荷 / 测试断言)."""
        return bool(self._handlers.get(point))

    def handlers(self, point: HookPoint) -> tuple[HookFn, ...]:
        """该点已注册的 hook 列表 (快照元组, 注册顺序)."""
        return tuple(self._handlers.get(point, ()))

    # ------------------------------------------------------------------
    # 触发
    # ------------------------------------------------------------------

    async def fire(self, point: HookPoint, **kwargs: Any) -> None:
        """触发某点的全部 hook (注册顺序), 逐个 await 到位 (观察类点用).

        空注册时立即返回 (零开销路径). 单个 hook 抛 Exception 时记录到
        failures 并继续下一个 (异常隔离); CancelledError 属 BaseException,
        直接向上传播 —— 插件不得挡住 kill switch (#3).

        **返回值无人看** —— 这里只负责「通知到」. 要听插件表态 (放行 / 拒绝)
        用 decide.

        Args:
            point: 要触发的 hook 点.
            **kwargs: 该点的载荷 (见模块 docstring 表); 原样展开给每个 hook,
                未声明对应形参的 hook 需自行用 **kwargs 接收.
        """
        handlers = self._handlers.get(point)
        if not handlers:
            return
        for hook in handlers:
            await self._call(point, hook, kwargs)  # 返回值按观察类点处置: 忽略

    async def decide(self, point: HookPoint, **kwargs: Any) -> Decision:
        """请某点的插件裁决 (注册顺序), 返回放行 / 拒绝 —— 裁决类点用.

        与 fire 的两处不同, 都是刻意的:

        1. **返回值有语义**: 插件返回 Decision.reject(原因) 即拒绝; 返回
           Decision.allow() 或 None (不表态) 即放行. **任一插件拒绝就拒绝**
           (原因取第一个拒绝者的 —— 拒绝的当场结论就一条, 后面几条对模型没有
           增量信息, 于是不去收集它们: 没有消费方的收集只是白存). 全部放行 /
           无人注册 → 放行.
        2. **异常 fail closed**: 插件抛异常, 或返回了既不是 Decision 也不是
           None 的东西 (写错了), 一律**按拒绝处理**并记入 failures —— 与 fire
           的「插件出错不拖垮用户任务」相反. 理由见模块 docstring: 这道护栏坏
           掉了就等于没有, 而用户以为有. 拒绝原因是框架给的固定文案 (原始异常
           在 failures 里, 不回填给模型).

        空注册时立即返回放行, 且**不产生挂起点** (没有 await 到任何人 ——
        判定与返回在同一个事件循环步里完成).

        CancelledError 不吞 (BaseException), 直接向上传播 —— 与 fire 同一条
        纪律: 插件不得挡住 kill switch.

        Args:
            point: 要请裁决的 hook 点 (裁决类点: before_tool_execute).
            **kwargs: 该点的载荷 (见模块 docstring 表).

        Returns:
            Decision: 放行 (无人注册 / 全部放行) 或拒绝 (含原因).
        """
        handlers = self._handlers.get(point)
        if not handlers:
            return Decision.allow()
        rejection: Decision | None = None  # 第一个拒绝者的裁决 (后面的不再收集)
        for hook in handlers:
            verdict = self._verdict(point, hook, await self._call(point, hook, kwargs))
            if verdict is not None and rejection is None:
                rejection = verdict
        return rejection if rejection is not None else Decision.allow()

    def _verdict(self, point: HookPoint, hook: HookFn, outcome: Any) -> Decision | None:
        """把一次插件调用的结果翻译成裁决: None = 放行, Decision = 说了算的那句.

        三种结果, 只有一种算放行:
        - 抛了异常 (哨兵) → 拒绝: **fail closed** —— 护栏坏了等于没有护栏, 而
          用户以为它在. 宁可拒 (故障看得见: 原因里写着插件出错, failures 里有原始
          异常), 也不静默放行
        - 返回 None → 放行 (插件常常只关心一部分工具调用, 不关心的直接 return)
        - 返回 Decision → 按它说的算
        - 返回别的 (写错的 return) → 拒绝 + 记一条 HookError: 若当成放行, 一个写错
          `return "拒绝"` 的护栏就会**静默失效** —— 正是上面那条要防的样子
        """
        if outcome is _HOOK_FAILED:
            return Decision.reject(INTERCEPT_FAILED_REASON)
        if outcome is None:
            return None
        if isinstance(outcome, Decision):
            return None if outcome.allowed else outcome
        self.failures.append(
            HookFailure(
                point=point,
                hook=hook,
                error=HookError(
                    f"拦截插件的返回值必须是 Decision 或 None, 实际 "
                    f"{type(outcome).__name__}: {outcome!r}"
                ),
            )
        )
        return Decision.reject(INTERCEPT_FAILED_REASON)

    async def _call(
        self, point: HookPoint, hook: HookFn, kwargs: dict[str, Any]
    ) -> Any:
        """调用一个 hook (同步 / 异步都 await 到位), **异常不外抛**.

        定位是给 fire / decide 共用的底座: 「插件出错只记一笔、且不许挡住
        CancelledError」这条纪律只写一份 —— 两个方法各自复述一遍, 迟早有一份
        先走样.

        Returns:
            正常返回 → 插件的返回值 (原样, 由调用方按其点的语义解释);
            抛异常 → 哨兵 _HOOK_FAILED (原始异常已记入 failures).
        """
        try:
            result = hook(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # 扩展点隔离: 插件异常不拖垮核心, 但留痕
            self.failures.append(HookFailure(point=point, hook=hook, error=exc))
            return _HOOK_FAILED
        return result
