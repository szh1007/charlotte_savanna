"""hooks 包: hook 注册表骨架 (issue 05, ADR-0007 扩展点之一).

设计依据 (CharAgent/docs): ADR-0007 (轻量扩展点: 事件总线 + hook + SPI) 与
design/01-architecture.md §4.2 (五个 hook 点与 P2 消费者).

大白话版 (这个包 = 给主循环装的插座):
- 现实问题: 以后要加「记住用户偏好」「算钱」「记日志」这些功能, 不能让它们
  去改 agent 的主循环 —— 主循环会被改烂.
- 本包做法: 主循环在 5 个固定时机「喊一声」, 想搭把手的功能自己插个插头
  (注册一个函数). 没人插时一声就跳过 (空注册零开销); 插头坏了只记一笔,
  不影响用户正在问的问题.
- 打个比方: 家里墙上的排插 —— 空着照常供电, 插了就用, 某个电器短路只烧
  自己的保险丝. 详见 registry.py 开头的模块注释.

结构总览 (对齐 model / tool / agent 包惯例):
- registry.py  HookRegistry 行为主体: 注册 / 查询 / fire (空注册零开销,
               异常隔离留痕)
- utils/       支撑子包: HookPoint / HookFn / HookFailure / ModelCallPhase
               (types) + HookError / HookConfigError (errors)

用法 (P2 模块挂载):

    from CharAgent.hooks import HookPoint, HookRegistry

    registry = HookRegistry()

    def remember_before_turn(*, turn: int, messages: list, **kwargs) -> None:
        messages.insert(0, {"role": "system", "content": "记忆: 用户是 VIP"})

    registry.register(HookPoint.BEFORE_TURN, remember_before_turn)

核心零 import P2: 插件方向是 P2 → 核心, 本包不感知任何具体插件.

模块内部 import 走具体模块路径 (hooks.registry, hooks.utils.types), 不绕包
门面; 对外公共 API 统一由本文件 __all__ 导出.
"""

from __future__ import annotations

from CharAgent.hooks.registry import HookRegistry
from CharAgent.hooks.utils.errors import HookConfigError, HookError
from CharAgent.hooks.utils.types import (
    HookFailure,
    HookFn,
    HookPoint,
    ModelCallPhase,
)

__all__ = [
    "HookConfigError",
    "HookError",
    "HookFailure",
    "HookFn",
    "HookPoint",
    "HookRegistry",
    "ModelCallPhase",
]
