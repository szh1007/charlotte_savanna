"""工具层异常语义: 可操作错误 vs 配置错误 vs 意外异常 (difficulties #2).

- ToolActionableError: 工具作者主动 raise, 消息面向模型 —— 必须说清「期望什么 /
  实际怎样」, 由 agent loop 错误自纠错回填模型 (不甩 422 让模型猜).
- ToolTimeoutError: 工具调用超时 (#15) —— 框架判的「到点不再等它返回」, 与上一族
  同一条回填路 (文本另有写法, 见 messages.timeout_error_text), 但 loop 会据它
  中断本次运行 (结果未知, ADR-0024).
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


class ToolTimeoutError(ToolActionableError):
    """工具调用超时: 到点不再等它返回, 结果未知 (#15).

    与上一族同走「原文回填」那条路 (所以是它的子类) —— 但这条路到此为止:
    `execute_tool` 见到它会把结果标成 `timed_out`, loop 据此**中断本次运行**
    (不把决定权交回模型, 免得同一动作被执行两遍; 取舍见 ADR-0024). 单独成一个
    类型是为了让执行层 / 日志 / 轨迹分得清**两种失败**: 工具说不行 (业务规则),
    与工具根本没回来 (超时). 后者的危险在于**结果未知** —— 会改数据的工具可能
    已经生效.
    """


class ToolConfigError(ToolError):
    """工具注册期配置错误 (面向开发者, 非模型): 类型注解缺失 / 参数形态不支持等."""
