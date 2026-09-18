"""HookRegistry: hook 注册表骨架 (扩展点).

一句话理解: 核心在固定时机「喊一声」(fire), 注册过的插件函数被依次叫到,
没注册就什么都不发生 —— 这是 P2 模块 (memory / cost / observability) 挂载
到核心的挂钩, 核心代码里不出现任何 P2 的 import (依赖方向单向).

为什么是「骨架」: P0 只落注册与分发机制 (本文件), 不实现任何插件. 空注册
时 fire 立即返回 (一次 dict 查询 + 空列表判断), 不产生回调调用与参数拷贝,
也不会在执行中挂起 —— 即「空实现零成本」这一要求, P0/P1 验收不受
P2 进度影响.

大白话版:
- 现实问题: 以后要加「记住用户偏好」「算这次花了多少钱」「把每次工具调用
  记进日志」这类功能. 如果每加一个都去改 agent 的主循环, 主循环迟早被改烂
  —— 需要「插座」: 主循环在固定时机喊一声, 谁想搭把手就自己插个插头.
- 打个比方: 家里的插座排插. 空着的时候电照跑 (空注册零开销, 不多花时间);
  插了电器就供电 (插头函数被调用); 某个电器短路只烧它自己的保险丝 (插件抛
  异常时记一笔然后跳过), 不会把全屋电闸拉掉 (用户的问答照常完成).
- 唯一的例外: 拔总电源 (用户点「停止」/ kill switch) 不算插件故障 —— 插件
  不许挡着取消, 这类信号直接放行.
- 本文件: 插座本体 —— 登记插头 (register) / 拔掉 (clear) / 到点挨个叫
  (fire). 五个「喊一声」的时机见 utils/types.py 的 HookPoint.

五个 hook 点与载荷 (载荷全部为关键字参数, 各 hook 用 **kwargs 接收自己关心的
字段即可):

- before_turn: 每 Turn 模型调用前 (guard 判定通过后才触发). 载荷: turn,
  messages (活引用, 可注入), tools
- after_turn: 每 Turn 记录快照后. 载荷: turn, response, messages, tokens,
  elapsed_ms
- on_model_call: 模型请求发出前 / 响应返回后各触发一次. 载荷: phase
  ("before" / "after"), turn, messages, tools, response (仅 after),
  usage (仅 after), elapsed_ms (仅 after)
- on_tool_executed: 每条工具执行完成 (结果已回填). 载荷: turn, call,
  execution
- on_event: 每个 StreamEvent 分发后. 载荷: event

两条设计约定 (面试可讲):
- **异常隔离**: hook 抛 Exception 只记入 failures 并继续跑其余 hook, 不向
  run 传播 —— 扩展点是可选的, 插件出错不该让用户的任务失败. CancelledError
  不在此列 (BaseException), 直接传播 —— 插件不得挡住 kill switch (#3).
- **载荷是活引用**: before_turn 拿到的 messages 就是 loop 的历史列表本体,
  memory 插件即在此挂载 (注入记忆); 想只读就自己拷贝.

本模块只放 HookRegistry 行为类; 零件在 utils/ 子包: HookPoint / HookFn /
HookFailure 在 utils/types.py, HookConfigError 在 utils/errors.py.
"""

from __future__ import annotations

import inspect
from typing import Any

from CharAgent.hooks.utils.errors import HookConfigError
from CharAgent.hooks.utils.types import HookFailure, HookFn, HookPoint


class HookRegistry:
    """hook 点的注册与分发.

    一个 registry 实例挂在一个 AgentLoop 上 (构造参数 hooks=); 同一实例可被
    多个 hook 点共享. 线程/协程安全说明: 注册在启动期完成, 运行期只读
    (fire), 故不做加锁 (server 层若需动态注册, 应在 run 之间进行).

    attributes:
        failures: 被隔离的 hook 异常记录 (HookFailure), 顺序为发生序. 空注册
            或全部 hook 正常时恒为空; 由调用方检查, 框架不静默吞掉问题.
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
        """触发某点的全部 hook (注册顺序), 逐个 await 到位.

        空注册时立即返回 (零开销路径). 单个 hook 抛 Exception 时记录到
        failures 并继续下一个 (异常隔离); CancelledError 属 BaseException,
        直接向上传播 —— 插件不得挡住 kill switch (#3).

        Args:
            point: 要触发的 hook 点.
            **kwargs: 该点的载荷 (见模块 docstring 表); 原样展开给每个 hook,
                未声明对应形参的 hook 需自行用 **kwargs 接收.
        """
        handlers = self._handlers.get(point)
        if not handlers:
            return
        for hook in handlers:
            try:
                result = hook(**kwargs)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # 扩展点隔离: 插件异常不拖垮核心, 但留痕
                self.failures.append(HookFailure(point=point, hook=hook, error=exc))
