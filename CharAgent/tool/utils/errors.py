"""工具层异常语义: 可操作错误 vs 配置错误 vs 意外异常 (difficulties #2).

- ToolActionableError: 工具作者主动 raise, 消息面向模型 —— 必须说清「期望什么 /
  实际怎样」, 由 agent loop 错误自纠错回填模型 (不甩 422 让模型猜).
- ToolConfigError: 工具注册期配置错误 (装饰器 / schema 引擎使用不当), 面向开发者.
- 意外异常 (其他 Exception): 由 executor 包装为失败结果, 返回给模型的是通用文案
  (traceback 不外泄), 根因保存在 ToolExecution.exception 供日志 / 审计.
"""

from __future__ import annotations


class ToolError(Exception):
    """工具模块错误基类."""


class ToolActionableError(ToolError):
    """工具作者主动 raise 的可操作错误: 消息原文回填模型触发自纠错 (#2).

    消息必须让模型能修正重试, 说清期望与实例, 例如::

        raise ToolActionableError("order_no 应为 14 位数字, 实际 'abc123'")
    """


class ToolConfigError(ToolError):
    """工具注册期配置错误 (面向开发者, 非模型): 类型注解缺失 / 参数形态不支持等."""
