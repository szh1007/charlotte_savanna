"""mcp_client 包: 把外部 MCP server 的工具接进这套循环 (消费侧, DESIGN ⑨ #50).

一句话理解: MCP 是「工具从哪儿来」的一种标准答案 —— 别人把它家的能力包成一台
server (stdio / SSE / Streamable HTTP 三种传输), 我们这边只做一件事: 把它
`tools/list` 出来的每个工具翻成框架自己的 `Tool`, 于是**循环、校验、超时、事件、
错误回填一条都不动**. 这正是 L1a 那条纪律的第七次兑现: 加一层能力不需要动循环核心.

**目录名为什么是 `mcp_client` 而不是 `mcp`** (2026-10-05 实测): 第三方 SDK 的顶包
就叫 `mcp`, 而这个仓库的布局会让 `CharAgent/` 自己进 `sys.path` (pytest 的
rootdir 插入就是一条) —— 那一刻目录名与顶包同名, `import mcp` 解析到的是**我们
自己**, 于是 `client.py` 里那句 `from mcp import ClientSession` 变成循环导入, 报
「cannot import name 'McpClient' from partially initialized module」. 加 `_client`
后缀是把两个名字岔开, 不是口味问题. 同一条坑对**任何**与顶包同名的目录都成立
(`CharAgent/` 下别再造 `httpx/` `pydantic/` 这样的目录).

设计依据 (CharAgent/docs):
- difficulties #50: 生命周期 `initialize → tools/list → tools/call`; 多 server 场景
  要解决工具发现 / 权限 / 路由 / **同名仲裁**; 消费侧与暴露侧是两件事 (本包只做
  前者, 后者在业务侧 —— 那是「我们的能力怎么被别的客户端调」, 与本包无关)
- PRD §4.2 的延长线: 远端工具的参数表就是它自己的 `inputSchema`, 我们**原样**交给
  模型 —— 不翻译、不裁剪、不添字段 (添了就是替别人改契约)

结构总览 (顶层 = 行为模块, 静态零件在 utils/):

- config.py      `McpServerSpec` + `parse_server_specs`: 认 Claude Desktop / Claude
                 Code 那份 `mcpServers` 形状的配置 (可直接抄来抄去)
- client.py      `McpClient`: 一台 server 的连接 (起进程 → 握手 → 列工具 → 换 Tool),
                 以及 `tools/call` 的往返 (文本回填 / `isError` → 可操作错误)
- provider.py    `McpToolProvider`: 形状与业务提供者一样 (`ToolProvider` 协议),
                 多 server 的装配 + **同名仲裁** + 生命周期 (失败不降级)
- utils/         支撑子包: errors (McpError / McpConfigError / McpServerError)

**为什么不在根门面** (`CharAgent/__init__.py`): 与 client / server / eval 同一类 ——
它要一个可选的依赖 (`pip install "charagent[mcp]"`), 而根门面那句「装了这个包就能
用」得留着. 需要的人按完整路径取: ``from CharAgent.mcp_client import McpToolProvider``.
"""

from __future__ import annotations

from CharAgent.mcp_client.client import McpClient
from CharAgent.mcp_client.config import (
    SERVER_TABLE_KEY,
    McpServerSpec,
    parse_server_specs,
)
from CharAgent.mcp_client.provider import McpToolProvider, arbitrate
from CharAgent.mcp_client.utils.errors import McpConfigError, McpError, McpServerError

__all__ = [
    "SERVER_TABLE_KEY",
    "McpClient",
    "McpConfigError",
    "McpError",
    "McpServerError",
    "McpServerSpec",
    "McpToolProvider",
    "arbitrate",
    "parse_server_specs",
]
