"""hooks 包支撑子包 (utils/): 静态支撑物, 不含行为类.

对齐 model/utils 与 tool/utils 惯例 —— 顶层 (registry.py) 放行为模块, 静态
支撑按主题收进子包:
- types.py   HookPoint (五个 hook 点枚举) / HookFn (hook 函数形态) /
             HookFailure (被隔离的插件异常记录) / ModelCallPhase
             (ON_MODEL_CALL 的两个 phase: before / after)
- errors.py  异常语义: HookError 基类 + HookConfigError (注册参数错误)

模块内部 import 走具体模块路径 (hooks.registry, hooks.utils.types), 不绕包
门面, 避免隐式循环依赖.

大白话版: 这里放 hooks 包的「静态零件」(插座规格 / 记录类型 / 错误定义),
不含行为逻辑 —— 行为都在上一层的 registry.py 里.
"""

from __future__ import annotations
