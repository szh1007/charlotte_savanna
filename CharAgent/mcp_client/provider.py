"""消费侧接缝: 一台到多台 MCP server 的工具, 装进框架那个「工具提供者」插座.

一句话理解: `McpToolProvider` 的形状与业务侧那个提供者**一模一样**
(`async def provide(上下文) -> 工具列表`), 于是框架的装配代码不需要知道「这回
的工具是从哪儿来的」—— 本地 `@tool` 与远端 MCP 工具在 `AgentLoop` 眼里没有
区别 (循环零改动, 见 DESIGN ⑨ #50).

三件本模块管、别处不管的事:

1. **连接的生命周期**: `start()` 一次建好全部连接, 之后每次 `provide` 交出的都
   是同一批工具 (远端工具不随运行上下文变); `aclose()` 收尾. 没 start 就
   `provide` **当场报**, 不是交一个空工具集 —— 「工具少了一个」是最难查的一类
   静默失效 (DESIGN #50 点的就是这个).
2. **同名仲裁**: 多 server 场景下两台都叫 `search` 是常态.**默认不合并、不覆盖,
   当场报**, 并在报错里给出两条出路 (改名 / 配 `tool_prefix`). 让后注册的赢是
   最坏的那种: 模型以为它调的是这台, 实际打到那台.
3. **失败不降级**: 任何一台起不来, 整次 `start()` 失败 (已经建好的连接全部收掉).
   「起不来就先跳过它」会把它变成一次「这台今天没有工具」的假象.

用法 (装配处; 与业务提供者并列, 顺序由业务自己排)::

    specs = parse_server_specs(json.loads(os.environ["APP_MCP_SERVERS"]))
    mcp = McpToolProvider(specs)
    await mcp.start()
    try:
        tools = [*await business_provider.provide(context), *await mcp.provide(context)]
    finally:
        await mcp.aclose()
"""

from __future__ import annotations

from collections.abc import Sequence

from CharAgent.agent import RunContext
from CharAgent.mcp_client.client import McpClient
from CharAgent.mcp_client.config import McpServerSpec
from CharAgent.mcp_client.utils.errors import McpConfigError, McpServerError
from CharAgent.tool import Tool


class McpToolProvider:
    """多台 MCP server → 这一个提供者 (形状见 `agent.provider.ToolProvider`).

    Args:
        servers: 每台 server 的启动规格, **顺序即连接顺序**, 也就是工具交出去的
            顺序. 空元组是合法的 (等于「这次没有远端工具」), 但那种情况下别造这个
            对象 —— 装配处直接不调它更清楚.

    attributes:
        无公开属性; `server_names` 与 `tools` 两个只读 property 供排查与用例看.
    """

    def __init__(self, servers: Sequence[McpServerSpec]) -> None:
        self._specs = tuple(servers)
        _check_unique_names(self._specs)
        # **列表而不是元组**: 收尾时"关掉一台就从台账里划掉一台" (`aclose`),
        # 于是取消 (Ctrl-C) 打断收尾之后, 剩下的还在, 再调一次就接着收
        self._clients: list[McpClient] | None = None
        self._tools: tuple[Tool, ...] = ()

    @property
    def server_names(self) -> tuple[str, ...]:
        """配置里的那几台 (顺序与 `__init__` 收到的一致)."""
        return tuple(spec.name for spec in self._specs)

    @property
    def tools(self) -> tuple[Tool, ...]:
        """已发现的工具; 还没 `start()` 时是空的 (见模块 docstring 第 1 条)."""
        return self._tools

    async def start(self) -> None:
        """按顺序连上每一台, 全部成了才算成.

        Raises:
            McpServerError: 某一台起不来 / 握手失败 / 工具重复定义.
            McpConfigError: 已经 start 过.
        """
        if self._clients is not None:
            raise McpConfigError("这个 MCP 提供者已经 start 过, 别重复 start")
        clients: list[McpClient] = []
        succeeded = False
        try:
            for spec in self._specs:
                client = McpClient(spec)
                await client.start()
                clients.append(client)
            tools = arbitrate(clients)
            succeeded = True
        finally:
            # 收尾放在 `finally` 里, **取消也算**: 「连上了两台、第三台炸了」不该留下
            # 两台活着的子进程等进程退出, 而 Ctrl-C / kill switch 恰恰可能落在中途的
            # 某一台握手上 (`CancelledError` 不是 `Exception`, 用 `except Exception`
            # 接不住). 收尾失败不覆盖真因 (`McpClient.aclose` 自己吞).
            if not succeeded:
                for client in reversed(clients):
                    await client.aclose()
        self._clients = clients
        self._tools = tools

    async def aclose(self) -> None:
        """关掉全部连接 (**反序**, 可以重复调, 也可以在 start 失败之后调).

        反序不是讲究: 每台 server 的连接内部是一个 anyio 取消作用域, 而这些作用域
        是**叠在同一个任务上**的 (第 N 台是在第 N-1 台还开着的时候建的). anyio
        要求它们按后进先出退出 —— 正序关第一台的那一下会撞上「里面那个还开着」,
        表现为收尾时抛一个 `CancelledError` (2026-10-05 实测, 两台 server 的用例
        就是这么红的). 同一个任务的取消作用域只能 LIFO 收, 这条纪律在
        `McpClient.aclose` 那一层看不出来, 所以记在这里.

        **取消之后还能接着收** (代码评审抓的): 每关成功一台才从台账里划掉一台,
        于是被打断时剩下的那几台还在 —— 再调一次 `aclose()` 继续. 反过来做
        ("先整体标成关过, 再逐台关") 在取消时会把后面的连接弄丢: 子进程还活着,
        而 `provide()` 已经开始报「还没连上」.
        """
        while self._clients:
            client = self._clients[-1]
            await client.aclose()
            self._clients.pop()
        self._clients = None
        self._tools = ()

    async def provide(self, context: RunContext) -> Sequence[Tool]:
        """交出这台提供者的全部工具 (远端工具不随上下文变, 见模块 docstring).

        Args:
            context: 运行上下文. **不读它**: MCP 那套工具的参数与身份是 server
                自己的事 (它自己认环境变量 / 自己的配置), 框架这侧的运行上下文
                只对**本业务**的工具才有意义 (那是 `agent.provider` 那个插座的
                原设计). 签名照旧收着, 是为了让装配处一行不改地把它与业务提供者
                并列起来.

        Returns:
            Sequence[Tool]: `start()` 时发现并仲裁过的那一批.

        Raises:
            McpConfigError: 还没 start (或已经 aclose) —— 见模块 docstring 第 1 条.
        """
        if self._clients is None:
            raise McpConfigError(
                "MCP 工具还没连上 (start() 没调过, 或者已经 aclose 了): 这时候能"
                "交出去的只有空工具集 —— 与其让这次运行静默地少一套工具, 不如"
                "当场报出来"
            )
        return self._tools


def _check_unique_names(specs: Sequence[McpServerSpec]) -> None:
    """server 名不许重复 —— 名字是「哪一台」的全部指代 (报错与仲裁都靠它).

    配置那份 (JSON / 映射) 里重复不了 (键唯一), 但直接构造 `McpServerSpec` 列表
    可以 —— 重名的两台在报错里长得一模一样, 排查时无从下手. 当场报比事后猜强.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    for spec in specs:
        if spec.name in seen:
            duplicates.append(spec.name)
        seen.add(spec.name)
    if duplicates:
        raise McpConfigError(
            f"server 名重复: {sorted(set(duplicates))} —— "
            f"名字要能指认出是哪一台, 两台同名在报错里分不清"
        )


def arbitrate(clients: Sequence[McpClient]) -> tuple[Tool, ...]:
    """把各台 server 的工具合成一串, **重名当场报** (不让后一个覆盖前一个).

    两种重名分开报, 因为改法不一样:

    - 同一个 server 里两次出现同名工具: 那是 server 侧的 bug (MCP 规范要求一台
      之内唯一), 我们能做的只有把它说清楚.
    - 跨 server 重名: 配置层面的事, 给出两条出路 —— 换前缀, 或者干脆别连那一台.

    Args:
        clients: 已连上的 server (顺序即优先级, 但这个函数**不用**优先级 —
            重名不裁决, 直接报).

    Returns:
        tuple[Tool, ...]: 全部工具, 按「server 顺序 + 各自发现顺序」排.

    Raises:
        McpServerError: 同一台 server 里工具重名 (那是它的 bug).
        McpConfigError: 跨 server 重名 (配置可改).
    """
    owners: dict[str, McpClient] = {}
    tools: list[Tool] = []
    for client in clients:
        for tool in client.tools:
            owner = owners.get(tool.name)
            # 判据是**连接对象本身**而不是它的名字: 两个 client 同名的场景 (直接构造
            # 时就可能出现) 若按名字判, 会把「两台撞名」误报成「一台之内重复」
            if owner is client:
                raise McpServerError(
                    f"MCP server {client.name!r} 里有两个同名工具 {tool.name!r} —— "
                    f"远端工具名在**一台之内**必须唯一, 这是 server 侧的问题"
                )
            if owner is not None:
                raise McpConfigError(
                    f"工具名 {tool.name!r} 撞了: {owner.name!r} 与 {client.name!r} "
                    f"各有一个 —— 不合并也不覆盖 (那会让模型以为调的是这台, 实际"
                    f"打到那台); 给其中一台配 tool_prefix 前缀, 或者别连它"
                )
            owners[tool.name] = client
            tools.append(tool)
    return tuple(tools)


__all__ = ["McpToolProvider", "arbitrate"]
