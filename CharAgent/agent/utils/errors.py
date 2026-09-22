"""agent 运行时异常语义 (difficulties #3/#10 配置防护 + 面向开发者错误).

对齐 model/utils/errors.py 与 tool/utils/errors.py 的错误族组织:
- 基类 AgentError 继承 ValueError —— agent 层的错误全部是「构造/使用参数
  错误」, 保持 Python 内置的非法参数语义 (调用方 except ValueError 即可
  捕获, 无需逐类感知), 与 model/tool 的纯 Exception 基类形成对照注释.
- LoopConfigError: AgentLoop 构造参数错误 (工具名重复 / max_truncations
  非法等).
- GuardConfigError: LoopGuard 构造参数错误 (上限非法等).
- CompactionConfigError: TrimAndSummarize 构造参数错误 (阈值 / 水位线 /
  保留轮数非法等).
"""

from __future__ import annotations


class AgentError(ValueError):
    """agent 运行时错误基类 (继承 ValueError: 全部为参数类错误, 见模块 docstring)."""


class LoopConfigError(AgentError):
    """AgentLoop 构造参数错误 (工具名重复 / max_truncations 非法, 面向开发者)."""


class GuardConfigError(AgentError):
    """LoopGuard 构造参数错误 (上限非法, 面向开发者)."""


class CompactionConfigError(AgentError):
    """上下文压缩策略的构造参数错误 (阈值 / 水位线 / 保留轮数非法, 面向开发者)."""
