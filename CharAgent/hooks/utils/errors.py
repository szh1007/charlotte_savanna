"""hooks 包异常语义 (扩展点).

对齐 model/utils/errors.py 与 tool/utils/errors.py 的错误族组织 (纯 Exception
基类, 与 agent/utils/errors.py 的 ValueError 基类形成对照 —— 后者是用户可传错
的构造参数, 本包是**插件作者写错了**这类编程错误):
- HookError: 本包错误基类.
- HookConfigError: 注册 / 构造参数错误 (hook 非 callable, 拒绝不给原因等),
  尽早暴露写错的注册与写错的裁决.

大白话版: 定义「插头插错了」这种错误 —— 比如把不是函数的东西当插头往插座上
插. 在登记时立刻报错, 而不是等到运行时才发现根本没被叫到.
"""

from __future__ import annotations


class HookError(Exception):
    """hooks 包错误基类 (纯 Exception: 本包错误均为插件侧的编程错误).

    也用来记「插件在裁决点上返回了认不出的值」—— 同样是插件写错了, 只是发现
    在运行期 (由 HookRegistry 记入 failures 并按拒绝处理, 不向 run 外抛).
    """


class HookConfigError(HookError):
    """注册 / 构造参数错误 (hook 非 callable, 拒绝不给原因等, 面向开发者)."""
