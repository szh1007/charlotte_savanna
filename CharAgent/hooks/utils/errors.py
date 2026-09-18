"""hooks 包异常语义 (扩展点).

对齐 model/utils/errors.py 与 tool/utils/errors.py 的错误族组织 (纯 Exception
基类, 与 agent/utils/errors.py 的 ValueError 基类形成对照 —— 后者是用户可传错
的构造参数, 本包是框架接线期的编程错误):
- HookError: 本包错误基类.
- HookConfigError: hook 注册参数错误 (非 callable 等), 尽早暴露拼错的注册.

大白话版: 定义「插头插错了」这种错误 —— 比如把不是函数的东西当插头往插座上
插. 在登记时立刻报错, 而不是等到运行时才发现根本没被叫到.
"""

from __future__ import annotations


class HookError(Exception):
    """hooks 包错误基类 (纯 Exception: 本包错误均为框架接线期的编程错误)."""


class HookConfigError(HookError):
    """HookRegistry 注册参数错误 (hook 非 callable 等, 面向开发者)."""
