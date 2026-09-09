"""工具层支撑模块: 静态支撑物 (异常语义 / 模型文案生成), 不含执行行为.

对齐 model/utils 的组织惯例 (model 包 docstring): 顶层只放行为模块
(decorator / executor / schema), 被多个行为模块共享且无编排逻辑的支撑
收编于此 —— errors (异常类) + messages (面向模型的可操作文案).

模块分工:
- errors.py    异常语义: ToolError / ToolActionableError (#2) / ToolConfigError
- messages.py  ValidationError → 可操作中文文案 (executor 拆出, 独立纯函数,
               供 loop 错误自纠错与未来结构化输出校验复用)
"""

from __future__ import annotations

from CharAgent.tool.utils.errors import (
    ToolActionableError,
    ToolConfigError,
    ToolError,
)

__all__ = [
    "ToolActionableError",
    "ToolConfigError",
    "ToolError",
]
