"""暴露侧: `python -m CharApp.minimall.mcp_server` —— 把商城的只读工具包成 MCP server.

一句话理解: 同一个工具集, 换一个客户端来调. 助手那边 (本仓的 CharAgent) 是**我们
自己**的循环在调工具; 这个进程把同一批 `Tool` 挂到 MCP 协议上, 于是 Claude Code /
Claude Desktop 这类**任何** MCP 客户端, 配一行 command 就能用自然语言问出商城的
真实数据 (「我的订单到哪了」→ 工具真打到 Django)。

三件事按顺序发生 (每一件都只有一个出处, 因此不会漂):

1. **工具集照抄助手那一套** —— `tools.build_tools` 现装一遍, 于是说明 / schema /
   闭包身份与网页版**逐字相同**; 这里不重写任何一个工具.
2. **只留只读的** —— 按 `writes` 注解剔掉会改数据的 8 个 (下单 / 付款 / 退款…).
   为什么: MCP 客户端**没有确认卡那套机制** (那是本业务护栏 + 挂起恢复的活),
   把写工具暴露出去等于「绕过人按的那一下直接下单」. 判据是注解而不是手抄名单 ——
   以后加一个写工具, 它天然进不了这张表 (理由与边界记在 ADR-0030).
3. **绑定一个账户** —— 买家 ID 从 `CHARAPP_MCP_USER_ID` 读, 只此一处 (**客户端
   说什么都不算数**). 为什么不能像服务端那样认请求头: 服务端敢信 `X-User-Id` 的
   前提是「同一个信任域内转发, 浏览器 session 是唯一认证点」(ADR-0001) —— 而 MCP
   客户端不在那个域里, 它没有 cookie. 处置与生产化路径见 ADR-0030.

协议那一层用的是**低层 `Server`** 而不是 FastMCP, 理由只有一条: 我们的工具 schema
**已经是** wire JSON Schema (`Tool.parameters`), 低层处理器把它原样递给客户端,
中间没有第二次推断; FastMCP 会从函数签名重新生成一遍 schema, 那是把已有的答案再猜
一次 (还会给每个工具配一个它自己生成的 outputSchema).

**调用走框架同一条执行链** (`tool.executor.execute_tool`): 参数校验 / 超时 / 错误
文案与助手那边逐字一样 —— 于是「暴露出去的工具」与「助手手里的工具」不可能是两种
脾气. 失败按 MCP 的 `isError` 回给对方 (正文就是给模型看的那句可操作的话).

**stdout 是协议通道**, 所以本进程一条日志都不许往那儿写: 日志走 `configure_logging`
(默认 stderr, 且带打码, ADR-0019), 启动失败那句人话也走 stderr. 传输层自己不操心
编码 —— SDK 把 stdin / stdout 重新包了一层 UTF-8 (实测 `mcp/server/stdio.py`),
所以这里不调 `use_utf8_stdio`.

怎么接上 (前置: 商城 Django 在跑 + 根 `.env` 里有 `CHARAPP_INTERNAL_TOKEN` 与
`CHARAPP_MCP_USER_ID`) —— **仓库根的 `.mcp.json` 已经配好这一份**, Claude Code
在仓库目录里打开时会认到它 (第一次用要本人在会话里批准一次, 那是它的安全默认)::

    // Claude Code: 仓库根 .mcp.json (已经在了)
    {"mcpServers": {"minimall": {
        "command": "<仓库根>/.venv/Scripts/python.exe",
        "args": ["-m", "CharApp.minimall.mcp_server"],
        // **必须给 PYTHONPATH**: `python -m 包名` 靠 cwd 或者 PYTHONPATH 才找得到
        // `CharApp`; 客户端从哪个目录起这个进程不由我们决定
        "env": {"PYTHONPATH": "<仓库根>"}}}}

    // Claude Desktop / 任何认 mcpServers 的客户端: 同一个形状. 它的启动目录同样
    // 不保证, 所以要么给 env (上面那条路), 要么给 "cwd": "<仓库根>"

接上之后问一句「我的订单到哪了」, 工具会真打到商城 —— 而「我的」指的是
`CHARAPP_MCP_USER_ID` 里那一个账户 (边界与取舍见 ADR-0030).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import Sequence

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from CharAgent.client import load_root_env
from CharAgent.structured_logging import configure_logging
from CharAgent.tool import Tool, execute_tool
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import (
    KnowledgeConfig,
    client_from_env,
    knowledge_config_from_env,
    mcp_user_id_from_env,
)
from CharApp.minimall.knowledge.citations import Citations
from CharApp.minimall.knowledge.prewarm import prewarm
from CharApp.minimall.knowledge.retriever import KnowledgeRetriever
from CharApp.minimall.log_redaction import build_redactor
from CharApp.minimall.service import STARTUP_ERRORS
from CharApp.minimall.tools import WRITE_ANNOTATION_KEY, build_tools

logger = logging.getLogger(__name__)

# 握手时自报的名字 (客户端界面上显示的就是它)
SERVER_NAME = "minimall"

# 版本号: 显式给, 不让 SDK 拿 mcp 库自己的版本顶替 (`create_initialization_options`
# 在 version=None 时会填 `mcp` 包的版本号 —— 那会让人以为这个 server 是 1.29.0)
SERVER_VERSION = "0.1.0"

# initialize 时交给客户端的一句说明. 它是**信任边界唯一对外声明的地方**: 接上来的
# 模型看得见这句话, 于是它不会以为「这个 server 能代表任意用户」或者「能下单」.
INSTRUCTIONS = (
    "这是 minimall 商城的只读工具集, 代表**配置里指定的那一个买家账户**"
    "(不是通用网关, 也不认识你带来的任何用户身份)。只读: 下单 / 付款 / 退款这类"
    "会改数据的操作不在这里。"
)


def read_only_tools(tools: Sequence[Tool]) -> tuple[Tool, ...]:
    """按 `writes` 注解剔掉会改数据的工具 (注解是唯一判据, 不是手抄名单).

    Args:
        tools: `tools.build_tools` 装出来的整套 (含写工具).

    Returns:
        tuple[Tool, ...]: 只读的那些, 顺序不变.
    """
    return tuple(
        tool for tool in tools if not tool.annotations.get(WRITE_ANNOTATION_KEY)
    )


def build_server(
    client: MinimallClient,
    user_id: int,
    *,
    knowledge: KnowledgeConfig | None = None,
) -> Server:
    """装出这个 MCP server: 工具集 + 两个处理器.

    Args:
        client: 商城客户端 (连接池与令牌); 生命周期归调用方.
        user_id: 这个进程代表哪个买家 (从 `CHARAPP_MCP_USER_ID` 来).
        knowledge: 知识库配置; None = 用业务那一份默认值.

    Returns:
        Server: 挂好 `tools/list` 与 `tools/call` 的 MCP server (还没起传输).

    Note:
        **引用账是进程级一份** (`Citations()`): 助手那边每段对话一份, 而 MCP 这一侧
        没有「对话」这个概念 (一次提问就是一次独立的调用) —— 于是编号在进程中一路
        累加, 而不是每次从 [1] 重数. 这是已知的形态差别 (编排出来的正文里编号仍然
        唯一且一致), 换来的是不必改检索工具那一层.
    """
    # 检索器很轻 (只拿配置; 向量库与两个本地模型是 `knowledge/` 里的惰性单例), 而
    # **不传模型**: 查询改写是可选的 (model=None 时跳过), 这个进程不该再去连一个
    # 上游模型 —— 提问的那一方才是模型.
    retriever = KnowledgeRetriever(
        knowledge if knowledge is not None else KnowledgeConfig()
    )
    exposed = read_only_tools(
        build_tools(client, user_id, retriever=retriever, citations=Citations())
    )
    by_name = {tool.name: tool for tool in exposed}
    # 开机记一行: 暴露了哪几个工具 (stderr) —— 事后问「它到底开没开写工具」时,
    # 这一行就是答案, 不必去读代码
    logger.info("暴露 %d 个只读工具: %s", len(exposed), ", ".join(by_name))

    server: Server = Server(
        SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS
    )

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        """把工具集报给客户端 (名字 / 说明 / schema 逐字段搬)."""
        return [
            types.Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.parameters,
            )
            for tool in exposed
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, object]):
        """执行一个工具: 走框架那条执行链, 失败翻成 MCP 的错误结果.

        Args:
            name: 工具名 (客户端给的).
            arguments: 参数对象 (客户端给的). **两道校验, 先过的那道是 SDK 的**:
                低层 Server 拿 `inputSchema` 跑一遍 jsonschema (2026-10-05 实测),
                不通过就直接回一条英文的 `Input validation error: ...`, **到不了
                这里** —— 于是我们那套中文的「缺少必填参数 X」只在 SDK 那道放行之后
                才可能说话. 想要中文那两句就得把 `validate_input` 关掉, 但那等于
                放弃 SDK 自带的一道闸, 不值.

        Returns:
            list[types.TextContent]: 成功时的正文 (与工具回填给模型的那份逐字相同).
            types.CallToolResult: 失败时 `isError=True`, 正文是可操作的那句话.
        """
        tool = by_name.get(name)
        if tool is None:
            # 正常客户端不会走到这里 (它照着 list_tools 的结果调); 手写的客户端可能
            return _error_result(
                f"没有这个工具: {name!r} —— 可用的只读工具有: {', '.join(by_name)}"
            )
        execution = await execute_tool(tool, arguments=json.dumps(arguments or {}))
        if execution.ok:
            return [types.TextContent(type="text", text=execution.content)]
        return _error_result(execution.error or "工具执行失败, 但没有给出说明")

    return server


def _error_result(text: str) -> types.CallToolResult:
    """一条失败的调用结果 (MCP 的 `isError`: 正文是给模型看的说明)."""
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], isError=True
    )


async def serve(
    client: MinimallClient,
    user_id: int,
    *,
    knowledge: KnowledgeConfig | None = None,
) -> None:
    """起 stdio 传输并跑到底 (谁建谁关: 收尾时把客户端关掉).

    对端关掉 stdin 就是正常停机 (SDK 的读循环到头), 不是异常.

    停机时预热任务被取消: 正在加载模型的那个线程没法被打断 (`to_thread` 取消不了
    已经在跑的函数), 但它跟着进程一起结束 —— 反正是最后一步了 (与 HTTP 入口那份
    `_serve` 同一套做法与同一句说明).
    """
    config = KnowledgeConfig() if knowledge is None else knowledge
    server = build_server(client, user_id, knowledge=config)
    logger.info("minimall MCP server 起来了 (stdio, 代表买家 #%d)", user_id)
    # 知识库那两个模型在后台预热 (L5-D3): 不预热的话第一次搜政策会现加载, 而
    # **这一次调用的时钟也在走** (框架那道 30 秒的闸包住的正是整条协程) —— 冷加载
    # 实测 7~8 秒, 本机够用, 但慢一档的机器或更大的 retrieve_top_k 上就会变成一次
    # 超时 (超时=结果未知, 框架会中断整次运行). HTTP 入口早就在做这件事
    # (`server.py` 的 `prewarming`), 这边照做.
    prewarming = asyncio.create_task(prewarm(config), name="knowledge-prewarm")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )
    finally:
        prewarming.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await prewarming
        await client.aclose()


def main() -> int:
    """进程入口: 读环境 → 建客户端 → 起 server; 配置错报一句人话就退出.

    Returns:
        int: 0 正常退出 (对端关掉 stdin 或 Ctrl-C); 1 启动配置错.

    Note:
        启动失败那句话走 **stderr**: stdout 是协议通道, 往里写一个字节都会让对端的
        解析器炸在一行不是 JSON 的文本上.
    """
    # 日志出口: 框架那一个 + 本业务的打码名单 (与 HTTP 服务进程同一套, ADR-0019)
    configure_logging(level=logging.INFO, redactor=build_redactor())
    load_root_env()
    try:
        client = client_from_env()
        user_id = mcp_user_id_from_env()
        knowledge = knowledge_config_from_env()
    except STARTUP_ERRORS as exc:
        print(f"启动失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    try:
        asyncio.run(serve(client, user_id, knowledge=knowledge))
    except KeyboardInterrupt:
        # 手动 Ctrl-C 是这个进程的另一种正常停机方式 (客户端那边是关 stdin)
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main())
