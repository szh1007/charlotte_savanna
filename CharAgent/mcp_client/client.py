"""一台 MCP server 的连接: 起进程 → 握手 → 列工具 → 换成我们的 Tool.

一句话理解: MCP 的生命周期是 `initialize → tools/list → tools/call`
(DESIGN #50), 而框架只认 `Tool` 这一个形状 —— 本模块就是那两者之间的**唯一**
一段胶水: 连上之后把远端的每个工具原样翻成一个 `Tool` 对象, 之后框架那一侧
再也看不见「MCP」这个词 (循环 / 校验 / 超时 / 事件 / 错误回填全部照旧).

三条映射是**逐字段搬**, 中间没有翻译层 (所以也不该有信息损耗):

| MCP 那一侧 | 我们这一侧 |
|---|---|
| `name` | `Tool.name` (可加配置里的前缀, 见 `config.McpServerSpec`) |
| `description` | `Tool.description` |
| `inputSchema` | `Tool.parameters` —— **两边都是 wire JSON Schema, 直接搬** |
| `tools/call` 的文本内容 | `Tool.fn` 的返回值 (契约就是 `str`) |
| `tools/call` 的 `isError=true` | 抛 `ToolActionableError` (与本地工具同一条语义) |

表里**故意没有**的一行: MCP 关于「这个工具会不会改数据」的提示 (`annotations`
里的 `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`) **不搬**
进 `Tool.annotations`. 那层注解是**业务自己的词汇表**, 框架只透传不解释
(见 `tool/decorator.py`) —— 把远端那四个键塞进去, 「这个键是谁写的、谁在读」就说不清
了; 要搬就在 `_to_tool` 里加一行 (远端原始定义只在这一层拿得到). 在那之前有一条要
记住的后果: **按注解做判断的消费者会把远端工具一律当成「没打注解的」** —— 对
CharApp 那套写护栏来说, 「没打注解」正是「只读」的意思, 于是接远端工具进那个业务
时必须先想清楚这一层 (本票没接, 见 C14 的「押后」).

**为什么 `isError` 要抛而不是返回**: MCP 那条 `isError` 说的正是「这次执行失败了,
正文是给模型看的说明」—— 与 `ToolActionableError` 一个字都不差 (executor 认出它
就照原文回填, 而这次执行记成 failed). 返回的话框架会当成正常结果, 页面上显示
"成功", 而远端明明报了错.

**参数校验怎么接上的**: 工具层 executor 的校验读的是「函数的签名」—— pydantic 那条
要一个 `parameter_model`, manual 那条读 `inspect.signature(fn)`. 而远端工具的函数
是 `**kwargs` 形态 (它只负责把参数字典转给 server), 直接交给 manual 那条会把
**一切**参数当成「未知参数」全拒掉. 于是这里按 schema 的 `properties` / `required`
给这个闭包**装一份合成签名** (`__signature__`) —— 框架那道校验因此照常工作
(多给的键 / 少给的必填键都在本地被拦下, 文案与本地工具同一套), 而**类型与值域仍归
server 自己** (我们不收紧、也不替它判).

这条路上试过并否掉的两种做法 (都输在「保真」上): 造一个 pydantic 模型当
`parameter_model` —— 它会**静默丢掉**下划线开头的字段名 (MCP 里 `_id` 这种很常见),
于是那个参数永远传不进去; 拿 `**kwargs` 直接调 server 又少了本地那道校验. 合成签名
还白捡一条: 模型给的是哪几个键就转发哪几个, **省略的选填参数不会被补成 `null`**
(`{"tail": null}` 与「没有 tail 这个键」对有些 server 不是一件事).

连接本身是**长活**的 (一次 run 里模型可能调十次工具), 所以本模块不做事后回收:
`start()` 之后连接一直开着, 直到 `aclose()` —— 谁建谁关, 与框架其余部分同一条
纪律 (`PgDatabase` / `CheckpointSaver` / `MinimallClient` 都是这个形状).
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client

from CharAgent.mcp_client.config import McpServerSpec
from CharAgent.mcp_client.utils.errors import McpConfigError, McpError, McpServerError
from CharAgent.structured_logging import get_logger
from CharAgent.tool import Tool, ToolActionableError

logger = get_logger("mcp")

# 交给模型的那个名字必须长的形状 (与 `tool/decorator.py` 的 `_NAME_PATTERN` 同一条
# 规矩, 见 `_checked_name` 的 Note: 那边没导出这个正则, 为它改公共 API 不划算)
TOOL_NAME_PATTERN = re.compile(r"[a-zA-Z0-9_-]{1,64}")


class McpClient:
    """到一台 MCP server 的连接 (一台一个; 生命周期由调用方显式管).

    Args:
        spec: 这台 server 的启动规格 (`config.McpServerSpec`).

    attributes:
        (无公开属性; 名字与工具由两个只读 property 给出)

    Note:
        与框架其余部分一样, **不在 `__init__` 里做 I/O**: 建对象是纯的, 连上去
        要显式 `await start()` —— 于是「什么时候会起进程」在代码里看得见.
    """

    def __init__(self, spec: McpServerSpec) -> None:
        self._spec = spec
        self._stack: AsyncExitStack | None = None
        self._tools: tuple[Tool, ...] = ()

    @property
    def name(self) -> str:
        """配置里那个名字 (报错与仲裁用)."""
        return self._spec.name

    @property
    def tools(self) -> tuple[Tool, ...]:
        """已经发现的工具 (还没 `start()` 时是空的 —— 见 `McpToolProvider.provide`)."""
        return self._tools

    async def start(self) -> None:
        """起进程 → 握手 → 列出全部工具 → 换成 `Tool`; 任何一步失败都当场报.

        Raises:
            McpServerError: 进程起不来 / 握手失败 / 列工具失败 / 工具定义转不过来.
                **不降级成「这台 server 没有工具」** —— 少一个工具不会让任何既有
                用例变红, 只会让模型在一次问答里少一条路, 而且没人知道为什么.
            McpConfigError: 这条连接已经建过 (重复 start 是装配代码的错).
        """
        if self._stack is not None:
            raise McpConfigError(
                f"server {self._spec.name!r} 的连接已经建过, 别重复 start"
            )
        stack = AsyncExitStack()
        connected = False
        try:
            # 两段分开报: 「连不上」与「连上了但工具定义转不过来」是两种毛病, 修法也
            # 不一样 (前者去看 command / 环境, 后者去看那台 server 的工具定义) ——
            # 合成一句「连接失败」会把第二种人指到错的地方 (代码评审抓的)
            try:
                read, write = await stack.enter_async_context(
                    stdio_client(self._params())
                )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                remote_tools = await self._list_all_tools(session)
            except McpError:
                raise  # 我们自己抛的那几条 (游标打转) 原样出去, 不再包一层
            except Exception as exc:
                raise McpServerError(
                    f"MCP server {self._spec.name!r} 连不上或握手失败 "
                    f"(command={self._spec.command!r}): {type(exc).__name__}: {exc}"
                ) from exc
            try:
                tools = tuple(
                    _to_tool(self._spec, session, remote) for remote in remote_tools
                )
            except McpError:
                raise
            except Exception as exc:
                raise McpServerError(
                    f"MCP server {self._spec.name!r} 列出来的 {len(remote_tools)} 个"
                    f"工具里有一个转不过来 (连接本身是好的): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            connected = True
        finally:
            # 收尾放在 `finally` 里, **取消也算** (代码评审抓的): `CancelledError`
            # 不是 `Exception`, 用 `except Exception` 接不住它 —— 而 Ctrl-C / kill
            # switch 恰恰就发生在「进程刚起、握手还没回来」那几秒. 不收的话子进程
            # 只能等 GC 在**别的任务**里退取消作用域 (实测会从终结器里冒一句
            # "Attempted to exit cancel scope in a different task"), 而且
            # `aclose()` 会变成一次静默的 no-op.
            if not connected:
                await _close_quietly(stack)
        self._stack = stack
        self._tools = tools

    async def aclose(self) -> None:
        """关连接: 收掉 session 与那台子进程 (可以重复调).

        SDK 的收尾顺序是「关 stdin → 等它自己退出 → 超时才 SIGTERM / SIGKILL」
        (实测 `mcp/client/stdio` 的 finally 那一段), 所以正常情况不会留下孤儿
        进程 —— 本模块不再自己补一刀.

        Note:
            多条连接同在一个任务里时, **必须反着建的反序关** (anyio 的取消作用域
            只能后进先出) —— 这条纪律由 `McpToolProvider.aclose` 落实, 单独用
            本类的人自己守.

            收尾被取消 (`CancelledError` 从 `_close_quietly` 里透出来) 时, 这条
            连接**还回原位**: 下一次 `aclose()` 接着收. 反过来做 (先标成"关过"再
            关) 会让子进程变成没人管的孤儿 —— 而 Windows 上父进程退出不会连坐杀
            子进程.
        """
        stack, self._stack = self._stack, None
        self._tools = ()
        if stack is None:
            return
        try:
            await _close_quietly(stack)
        except BaseException:
            self._stack = stack
            raise

    def _params(self) -> StdioServerParameters:
        """规格 → SDK 的启动参数 (env 的语义见 `McpServerSpec.env`)."""
        spec = self._spec
        return StdioServerParameters(
            command=spec.command,
            args=list(spec.args),
            env=dict(spec.env) if spec.env else None,
            cwd=spec.cwd,
        )

    async def _list_all_tools(self, session: ClientSession) -> list[types.Tool]:
        """列工具, **翻完为止** (远端可以分页, 只读第一页就会静默少几个工具).

        Raises:
            McpServerError: 游标回到走过的那一个 —— 那是 server 侧的 bug, 照它
                往下翻会无限循环; 当场说清比挂在那儿强.
        """
        tools: list[types.Tool] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = await session.list_tools(cursor)
            tools.extend(page.tools)
            cursor = page.nextCursor
            if cursor is None:
                return tools
            if cursor in seen:
                raise McpServerError(
                    f"MCP server {self._spec.name!r} 的工具分页游标回到了 "
                    f"{cursor!r} —— 照它翻下去永远翻不完"
                )
            seen.add(cursor)


async def _close_quietly(stack: AsyncExitStack) -> None:
    """收掉半开的连接, 收尾自身的异常只记账不外抛.

    为什么吞: 调用它的时候手上已经有一个**更要紧**的异常 (起不来 / 握手失败),
    收尾再抛一个只会把真因盖掉. 正常那一支 (`aclose()`) 走的是同一条路 ——
    关连接失败没有任何可做的补救.

    **`CancelledError` 不吞** (它不属于 `Exception`): 这个位置上出现它多半不是
    「有人在取消我们」, 而是**取消作用域退错了顺序**这类真问题的信号 —— 实测过
    一次 (两台 server 正序收尾, 见 `provider.aclose`), 吞掉它等于把「收尾没收干
    净」变成一次静默通过.
    """
    try:
        await stack.aclose()
    except Exception as exc:
        # 「只记账不外抛」里的**记账**就是这一行 (全局规范: 不静默吞异常). 记的是
        # 收尾没收干净, 而真因 (起不来 / 握手失败) 由调用方那条路照常报出去
        logger.warning(
            "关闭 MCP 连接时出错 (收尾没走干净): %s: %s",
            type(exc).__name__,
            exc,
        )


def _to_tool(spec: McpServerSpec, session: ClientSession, remote: types.Tool) -> Tool:
    """远端工具定义 → 我们的 `Tool` (逐字段搬, 见模块 docstring 那张表).

    Raises:
        McpServerError: 名字不符合模型侧规范 (见 `_checked_name`), 或参数名不是合法
            的标识符 (见 `_signature_for`).
    """
    schema: Mapping[str, Any] = (
        remote.inputSchema if isinstance(remote.inputSchema, Mapping) else {}
    )
    wire_name = remote.name

    async def _call(**kwargs: Any) -> str:
        """把模型给的那组参数原样转发给远端 (给了哪几个就发哪几个)."""
        return await _call_remote(session, spec.name, wire_name, dict(kwargs))

    # 合成签名挂在**这个闭包自己**身上 (不是某个共享对象上): `inspect.signature`
    # 先看 `__signature__` 再去看真实的参数表, 而 executor 那道校验读的正是它
    _call.__signature__ = _signature_for(schema, spec.name, wire_name)  # type: ignore[attr-defined]

    return Tool(
        name=_checked_name(spec, wire_name),
        description=remote.description or f"{wire_name}: MCP 工具 (服务端未提供说明)",
        fn=_call,
        parameters=dict(schema),
        timeout=spec.call_timeout,
    )


def _signature_for(
    schema: Mapping[str, Any], server_name: str, tool_name: str
) -> inspect.Signature:
    """按 schema 造一份**只认键**的签名 (executor 那道校验照着它拦).

    `properties` → 关键字参数, `required` → 「没有默认值」. 于是两种最常犯的错
    (多给的键 / 少给的必填键) 在本地就被拦下, 文案与本地工具同一套; 类型与值域
    不在这里管 —— 那是 server 自己的事 (`inputSchema` 已经原样交给它).

    Raises:
        McpServerError: 某个参数名在 Python 里当不了关键字参数 (带点 / 带空格 / 是
            关键字…) —— 那种名字连 `inspect.Parameter` 都造不出来, 更没法进
            `**kwargs` 的转发链. 这属于**远端定义的问题**, 报清楚比让它晚一步在上游
            炸掉强.
    """
    properties = schema.get("properties")
    required = schema.get("required")
    names = list(properties) if isinstance(properties, Mapping) else []
    required_names = (
        set(required)
        if isinstance(required, Sequence) and not isinstance(required, str)
        else set()
    )
    try:
        return inspect.Signature(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=(inspect.Parameter.empty if name in required_names else None),
            )
            for name in names
        )
    except ValueError as exc:
        raise McpServerError(
            f"MCP server {server_name!r} 的工具 {tool_name!r} 有个参数名当不了"
            f"关键字参数: {exc} —— 远端那颗参数没法用, 请那台 server 换个名字"
        ) from exc


def _checked_name(spec: McpServerSpec, wire_name: str) -> str:
    """`前缀 + 远端原名` → 交给模型的那个名字 (形状不对当场报).

    为什么这里非有一道检查不可: 本地工具的 `Tool` 是 `@tool` 造出来的, 名字在那里
    就按规范校验过 (`TOOL_NAME_PATTERN`); 而远端工具的这个对象是**直接构造**的,
    绕开了那道闸 —— 不加检查, 一个带 `.` 的空名 / 超长的名字会一路带到 wire 上
    (`Tool.to_spec` 原样透出), 最后在上游变成一个看不懂的 400 (`ModelStatusError`),
    而报错里一个字都不会提到 MCP.

    Note:
        规范与 `tool/decorator.py` 的 `_NAME_PATTERN` 是同一条 (字母 / 数字 / 下划线
        / 连字符, 1~64 字符, 见 OpenAI 的 function name 口径). 那边没有导出这个正则,
        为它把一条内部规矩变成公共 API 是更大的改动, 所以这里重写一份并留这条注释.
    """
    name = f"{spec.prefix}{wire_name}"
    if TOOL_NAME_PATTERN.fullmatch(name):
        return name
    raise McpServerError(
        f"MCP server {spec.name!r} 的工具名 {wire_name!r} (加上前缀后是 {name!r}) "
        f"不符合模型侧的工具名规范: 只能由字母 / 数字 / 下划线 / 连字符组成, "
        f"1~64 字符 —— 换个前缀缩短它, 或者别连这台 server"
    )


async def _call_remote(
    session: ClientSession,
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """`tools/call` 一次: 结果转文本, 失败转「可操作错误」.

    Raises:
        ToolActionableError: 远端报了 `isError` —— 正文照原样交给模型 (与本地工具
            那条路同一条语义: 消息要能让模型修正或换法子).
        McpServerError: 连接断了 / 协议出错 —— 带上是哪台 server 的哪个工具, 而
            原来那个异常挂在 `__cause__` 上 (日志里看得到真因).
    """
    try:
        result = await session.call_tool(tool_name, arguments)
    except Exception as exc:
        raise McpServerError(
            f"MCP server {server_name!r} 的工具 {tool_name!r} 调用失败: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    text = _content_text(result)
    if result.isError:
        raise ToolActionableError(
            text or f"MCP 工具 {tool_name} 报了失败, 但没有给出说明文本"
        )
    return text


def _content_text(result: types.CallToolResult) -> str:
    """`CallToolResult` → 回填给模型的一段文本.

    文本块按顺序拼接; 非文本块 (图片 / 音频 / 资源) **不丢**, 但也没有别的去处
    —— 我们的 `Tool` 契约是返回 `str`, 所以各处一段说明它在哪儿. 全空时退回
    `structuredContent` 的 JSON (带 outputSchema 的工具可能只给结构化那份).
    """
    parts: list[str] = []
    for block in result.content or ():
        if isinstance(block, types.TextContent):
            parts.append(block.text)
        else:
            parts.append(f"[{block.type}: 这段内容不是文本, 未回填]")
    text = "\n".join(part for part in parts if part)
    if text:
        return text
    if result.structuredContent is not None:
        return json.dumps(result.structuredContent, ensure_ascii=False)
    return ""


__all__ = ["McpClient"]
