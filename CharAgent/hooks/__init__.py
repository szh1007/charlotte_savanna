"""hooks 包: hook 注册表骨架 (扩展点之一).

大白话版 (这个包 = 给主循环装的插座):
- 现实问题: 以后要加「记住用户偏好」「算钱」「记日志」这些功能, 不能让它们
  去改 agent 的主循环 —— 主循环会被改烂.
- 本包做法: 主循环在 6 个固定时机「喊一声」, 想搭把手的功能自己插个插头
  (注册一个函数). 没人插时一声就跳过 (空注册零开销); 插头坏了只记一笔,
  不影响用户正在问的问题.
- 其中工具执行前那一个点还**听劝**: 插头说「不许」, 工具就真的不跑 —— 权限
  校验 / 危险操作拦截挂在这里 (那一个点上插件坏了按拒绝处理, 与其余点不同).
- 打个比方: 家里墙上的排插 —— 空着照常供电, 插了就用, 某个电器短路只烧
  自己的保险丝 (带开关的那一个例外, 见上条). 详见 registry.py 开头的模块注释.

结构总览 (对齐 model / tool / agent 包惯例):
- registry.py  HookRegistry 行为主体: 注册 / 查询 / fire (空注册零开销,
               异常隔离留痕) / decide (裁决类点: 放行或拒绝)
- utils/       支撑子包: HookPoint / HookFn / Decision / HookFailure /
               ModelCallPhase (types) + HookError / HookConfigError (errors)

用法 (P2 模块挂载):

    from CharAgent.hooks import HookPoint, HookRegistry

    registry = HookRegistry()

    def remember_before_turn(*, turn: int, messages: list, **kwargs) -> None:
        messages.insert(0, {"role": "system", "content": "记忆: 用户是 VIP"})

    registry.register(HookPoint.BEFORE_TURN, remember_before_turn)

用法 (业务拦截: 工具执行前拒绝):

    from CharAgent.hooks import Decision, HookPoint, HookRegistry

    def gate(*, tool, **kwargs) -> Decision | None:
        # 注解里的键名与含义由**业务自己**定 (框架只透传不解释): 这里假装业务
        # 给某些工具打了 "locked" 标记, 插件据此拦下
        if tool.annotations.get("locked"):
            return Decision.reject("这个操作需要用户本人确认后才能执行")
        return None  # 不表态 = 放行

    registry.register(HookPoint.BEFORE_TOOL_EXECUTE, gate)

核心零 import P2: 插件方向是 P2 → 核心, 本包不感知任何具体插件.

模块内部 import 走具体模块路径 (hooks.registry, hooks.utils.types), 不绕包
门面; 对外公共 API 统一由本文件 __all__ 导出.
"""

from __future__ import annotations

from CharAgent.hooks.registry import HookRegistry
from CharAgent.hooks.utils.errors import HookConfigError, HookError
from CharAgent.hooks.utils.types import (
    Decision,
    HookFailure,
    HookFn,
    HookPoint,
    ModelCallPhase,
)

__all__ = [
    "Decision",
    "HookConfigError",
    "HookError",
    "HookFailure",
    "HookFn",
    "HookPoint",
    "HookRegistry",
    "ModelCallPhase",
]
