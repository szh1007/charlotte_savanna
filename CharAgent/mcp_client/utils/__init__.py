"""mcp 包支撑模块: 静态支撑物 (异常语义), 不含连接行为.

对齐 tool/utils 的组织惯例: 顶层只放行为模块 (config / client / provider),
被多个行为模块共享且无编排逻辑的支撑收编于此. 目前只有 errors 一件 ——
`McpServerSpec` 那类**配置数据**跟着它自己的行为模块 (config.py), 不往这里收.
"""

from __future__ import annotations

from CharAgent.mcp_client.utils.errors import McpConfigError, McpError, McpServerError

__all__ = [
    "McpConfigError",
    "McpError",
    "McpServerError",
]
