"""契约测试用的 demo MCP server: 真协议 / 真子进程, 但**零网络零依赖**.

它是 `test_mcp_provider.py` 的对端 —— 用例用 `sys.executable` 把它起成子进程
(stdio 传输), 于是「消费侧接缝」这条链路上除了被测代码以外全是真货: 真的
`initialize` 握手、真的 `tools/list`、真的 `tools/call` 往返、真的进程边界.

为什么不用现成的官方参考 server (filesystem 之类): 默认用例**不触网**是这套测试
的纪律 (见 conftest 开头), 而那几个参考实现要么走 npx (要 node + 首次下载)、要么
要真实的文件系统布局. 外部参考 server 的验证另有一条手动脚本
(`tests/try_mcp_reference_server.py`), 不进默认套件.

刻意留了五个工具, 每个对应一条要验的性质:

| 工具 | 验什么 |
|------|--------|
| `add_numbers` | 正常往返: 必填参数 / 文本结果回填 |
| `echo` | **省略的选填参数不带出去** (返回的就是它收到的原始 arguments) |
| `fail_softly` | `isError=true` → 我们这边翻成「可操作错误」 |
| `crash` | server 半路没了 → 下一次调用必须**响亮地失败**, 不是空结果 |
| `bare` | 没有 description 的工具 (消费侧用工具名兜底) + 带一组远端注解 |

三个开关 (都是**不设就不出现**, 免得干扰别条用例断工具列表):

| 环境变量 | 效果 |
|---|---|
| `MCP_DEMO_PAGE_SIZE` | 每页几个工具 (游标翻页是规范的一部分, 只读第一页会静默少几个) |
| `MCP_DEMO_BAD_TOOL_NAME` | 多一个名字不合模型侧规范的工具 (带点) |
| `MCP_DEMO_CWD_TOOL` | 多一个 `where_am_i` (回工作目录; 验 `cwd` 传到了子进程) |

本文件不是测试模块 (pytest 只收 `test_*.py`), 也不会被 import —— 用例把它的
路径交给子进程.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

# initialize 握手时自报的名字 (用例断它等于配置里那个 server 名, 说明「连上的
# 就是这台」)
SERVER_NAME = "charagent-demo"

# 每页几个工具; 不设 = 一次列完
PAGE_SIZE_ENV = "MCP_DEMO_PAGE_SIZE"

# 多一个名字带点的工具 (模型侧收不下这种名字); 不设 = 不出现
BAD_NAME_ENV = "MCP_DEMO_BAD_TOOL_NAME"

# 多一个 where_am_i (回工作目录); 不设 = 不出现
CWD_TOOL_ENV = "MCP_DEMO_CWD_TOOL"


def _tool(name: str, description: str | None, schema: dict[str, Any]) -> types.Tool:
    """造一个工具定义 (description=None 表示服务端没给说明, 那是合法的)."""
    return types.Tool(name=name, description=description, inputSchema=schema)


_OBJECT: dict[str, Any] = {"type": "object", "properties": {}}

TOOLS: tuple[types.Tool, ...] = (
    _tool(
        "add_numbers",
        "把两个整数加起来.",
        {
            "type": "object",
            "properties": {"left": {"type": "integer"}, "right": {"type": "integer"}},
            "required": ["left", "right"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "echo",
        "把收到的参数原样回显 (JSON 文本), 用来观察客户端到底发了什么.",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}, "note": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    ),
    _tool(
        "fail_softly",
        "用 isError 报一次业务失败 (正文是给模型看的说明).",
        _OBJECT,
    ),
    _tool(
        "crash",
        "当场把本进程杀掉 (模拟 server 半路没了).",
        _OBJECT,
    ),
    # 带一组 MCP 自己的注解: 消费侧**故意不把它搬进 `Tool.annotations`**
    # (那是业务自己的词汇表, 见 mcp_client/client.py 的模块 docstring)
    types.Tool(
        name="bare",
        description=None,
        inputSchema=_OBJECT,
        annotations=types.ToolAnnotations(readOnlyHint=True),
    ),
)

# 名字不合模型侧规范的那个 (带点) —— 只在开关打开时出现
BAD_NAME_TOOL = _tool("bad.name", "名字带点的工具.", _OBJECT)

# 回自己工作目录的那个 —— 只在开关打开时出现 (验 cwd 传到了子进程)
CWD_TOOL = _tool("where_am_i", "回自己的工作目录.", _OBJECT)


def build_server() -> Server:
    """装出这个 demo server (list_tools / call_tool 两个处理器)."""
    server: Server = Server(SERVER_NAME)
    tools = list(TOOLS)
    if os.environ.get(BAD_NAME_ENV):
        tools.append(BAD_NAME_TOOL)
    if os.environ.get(CWD_TOOL_ENV):
        tools.append(CWD_TOOL)

    @server.list_tools()
    async def _list_tools(req: types.ListToolsRequest) -> types.ListToolsResult:
        """列工具; 配了分页就把它们切成每页 N 个 (游标是页序号)."""
        size = _page_size()
        if size is None:
            return types.ListToolsResult(tools=tools)
        cursor = req.params.cursor if req.params else None
        start = int(cursor) if cursor else 0
        page = tools[start : start + size]
        end = start + size
        return types.ListToolsResult(
            tools=page,
            nextCursor=str(end) if end < len(tools) else None,
        )

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any]):
        """执行一个工具 (返回文本内容, 或一条 isError 结果)."""
        if name == "add_numbers":
            total = int(arguments["left"]) + int(arguments["right"])
            return [types.TextContent(type="text", text=str(total))]
        if name == "echo":
            # 原样回显它收到的参数 (键与值都不动) —— 用例据此断言「省略的选填项
            # 真的没有被发出来」, 那是客户端保真度唯一看得见的证据
            return [
                types.TextContent(
                    type="text",
                    text=json.dumps(arguments, ensure_ascii=False, sort_keys=True),
                )
            ]
        if name == "fail_softly":
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="配额用完了, 换个法子")],
                isError=True,
            )
        if name == "crash":
            # 应答都来不及发就没了: 调用方那边应当是一次失败的调用, 而不是空结果
            sys.stdout.flush()
            os._exit(1)
        if name == "where_am_i":
            return [types.TextContent(type="text", text=os.getcwd())]
        if name == "bare":
            return [types.TextContent(type="text", text="bare 被调用了")]
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"没有这个工具: {name}")],
            isError=True,
        )

    return server


def _page_size() -> int | None:
    """读分页开关 (空 / 非正数 = 不分页)."""
    raw = (os.environ.get(PAGE_SIZE_ENV) or "").strip()
    if not raw:
        return None
    size = int(raw)
    return size if size > 0 else None


async def serve() -> None:
    """stdio 主循环 (握手、收请求、应答, 直到对端关掉 stdin)."""
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


if __name__ == "__main__":  # pragma: no cover - 由测试起的子进程
    anyio.run(serve)
