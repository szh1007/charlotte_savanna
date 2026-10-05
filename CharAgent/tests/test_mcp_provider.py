"""消费侧接缝: 起一台**真**的 MCP server, 把它的工具接进这套循环.

对端是 `fixtures/mcp/demo_server.py` —— 它由**当前解释器**起成一个子进程, 走真的
stdio 传输与真的 JSON-RPC 往返. 于是本文件里除了被测代码, 全链条都是真货:
`initialize` 握手、`tools/list`(含游标翻页)、`tools/call`、进程边界、连接断开.

四五条性质分开测, 因为它们的失败方式完全不同:

- **装得下**: 一台真 server 的工具能被列全、原样翻成 `Tool`、经 `AgentLoop` 跑通
  (DESIGN ⑨ #50 那句话的可执行证据 —— 循环零改动)
- **保真**: 远端 `isError` 与「没给的选填参数」都不该在中间被改成别的意思
- **不静默**: 起不来 / 重名 / 连接断了 / 还没连就要工具 —— 一律当场报
- **多 server**: 同名仲裁 (默认报错, 前缀是出路) 与路由真的没串台

用例起的每一台 server 都在收尾时关掉 (SDK 会走「关 stdin → 等它退出 → 超时才
SIGTERM / SIGKILL」那条路, 见 `mcp_client/client.py`), 不留孤儿进程.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response
from trace_assertions import trace_of

from CharAgent.agent import RunContext
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.mcp_client import (
    McpConfigError,
    McpServerError,
    McpServerSpec,
    McpToolProvider,
)
from CharAgent.tool import Tool, execute_tool

# demo server 的路径 (用例把它交给子进程; 本文件不 import 它 —— 那会变成同进程)
DEMO_SERVER = Path(__file__).resolve().parent / "fixtures" / "mcp" / "demo_server.py"

# fixture server 声明的五个工具 (顺序即它 list 的顺序)
DEMO_TOOLS = ("add_numbers", "echo", "fail_softly", "crash", "bare")

# fixture server 的两个开关 (见它模块 docstring): 打开就多一个工具
BAD_NAME_ENV = "MCP_DEMO_BAD_TOOL_NAME"
CWD_TOOL_ENV = "MCP_DEMO_CWD_TOOL"

# `add_numbers` 那一份 schema: **远端给什么就是什么**, 中间不加不减 (逐字比对)
ADD_SCHEMA = {
    "type": "object",
    "properties": {"left": {"type": "integer"}, "right": {"type": "integer"}},
    "required": ["left", "right"],
    "additionalProperties": False,
}


def demo_spec(name: str = "demo", **overrides: object) -> McpServerSpec:
    """起 fixture server 的规格 (当前解释器 + 那个脚本; 要改哪个字段就传哪个)."""
    fields: dict[str, object] = {
        "name": name,
        "command": sys.executable,
        "args": (str(DEMO_SERVER),),
    }
    fields.update(overrides)
    return McpServerSpec(**fields)  # type: ignore[arg-type]


@asynccontextmanager
async def started(*specs: McpServerSpec) -> AsyncIterator[McpToolProvider]:
    """起一个提供者, 用完收掉 (用例只关心中间那一段)."""
    provider = McpToolProvider(list(specs))
    await provider.start()
    try:
        yield provider
    finally:
        await provider.aclose()


def context() -> RunContext:
    """一个运行上下文 (MCP 工具不读它, 但 `provide` 的签名收着)."""
    return RunContext(thread_id="mcp:demo:1", tenant_id="mcp-demo", user_id="u-1")


def by_name(tools: list[Tool] | tuple[Tool, ...]) -> dict[str, Tool]:
    """工具集 → 名字索引 (用例里按名字取的那几处)."""
    return {tool.name: tool for tool in tools}


# ---------------------------------------------------------------------------
# 装得下: 一台真 server → Tool 集 → AgentLoop 跑通
# ---------------------------------------------------------------------------


async def test_a_real_server_over_stdio_is_swallowed_by_the_loop() -> None:
    """契约用例: 起真 server → 列全工具 → 翻成 `Tool` → 循环跑通一次调用.

    这一条就是「框架抽象装得下生态标准」的可执行证据 —— 远端工具的 schema 与说明
    逐字到达模型, 而 `AgentLoop` 那一边一行代码都没有为它改过.
    """
    async with started(demo_spec()) as provider:
        tools = await provider.provide(context())

        assert [tool.name for tool in tools] == list(DEMO_TOOLS)
        # 原样搬: schema 与说明都是从远端那儿搬过来的, 中间没有翻译层
        assert by_name(tools)["add_numbers"].parameters == ADD_SCHEMA
        assert by_name(tools)["add_numbers"].description == "把两个整数加起来."
        # 远端没给说明时用工具名兜底 (空说明会让模型无从判断这个工具干什么)
        assert by_name(tools)["bare"].description == "bare: MCP 工具 (服务端未提供说明)"

        # 交给循环: 一次工具调用 + 一次收尾答复 (装配与本地工具逐字一样)
        model = MockLLM.scripted(
            [
                tool_call_response(
                    make_tool_call("add_numbers", '{"left": 2, "right": 3}')
                ),
                text_response("2 加 3 等于 5"),
            ]
        )
        session = ChatSession(
            model,
            saver=InMemoryCheckpointSaver(),
            tools=list(tools),
            thread_id="mcp:demo:1",
        )
        result = await session.ask("2 加 3 是多少")

        trace = trace_of(model)
        trace.assert_tool_calls([("add_numbers", {"left": 2, "right": 3})])
        trace.assert_tool_result_backfilled("add_numbers", contains="5")
        assert result.content == "2 加 3 等于 5"


async def test_a_remote_failure_reaches_the_model_as_an_actionable_text() -> None:
    """远端 `isError=true` → 模型的下一轮看得见那句说明 (框架记成一次失败执行).

    远端那条 `isError` 的正文本来就是写给模型看的, 而本地 `ToolActionableError`
    的语义一个字不差 —— 所以映射就是「把它抛出去」, 而不是当成一次正常返回.
    """
    async with started(demo_spec()) as provider:
        tools = await provider.provide(context())
        model = MockLLM.scripted(
            [
                tool_call_response(make_tool_call("fail_softly")),
                text_response("好, 我换个法子"),
            ]
        )
        session = ChatSession(
            model,
            saver=InMemoryCheckpointSaver(),
            tools=list(tools),
            thread_id="mcp:demo:fail",
        )

        result = await session.ask("随便问一句")

        trace_of(model).assert_tool_result_backfilled(
            "fail_softly", contains="配额用完了"
        )
        assert result.content == "好, 我换个法子"


async def test_an_omitted_optional_argument_is_not_sent_as_null() -> None:
    """没给的选填参数**不发出去** —— 「没有这个键」与「键是 null」不是一回事.

    `echo` 回显的是它收到的原始 arguments, 所以这里看得见客户端到底发了什么.
    """
    async with started(demo_spec()) as provider:
        tools = by_name(await provider.provide(context()))

        omitted = await execute_tool(tools["echo"], arguments='{"text": "hi"}')
        explicit = await execute_tool(
            tools["echo"], arguments='{"text": "hi", "note": "x"}'
        )

        assert omitted.ok, omitted.error
        assert json.loads(omitted.content) == {"text": "hi"}, "选填项被当成 null 发走了"
        assert json.loads(explicit.content) == {"text": "hi", "note": "x"}


async def test_the_parameter_shape_is_checked_here_too() -> None:
    """参数填错在本地就被拦下 (与本地工具同一套文案), 不必等一个协议往返.

    值域不归我们管 (那是 server 的事), 但**键的形状**归框架的 executor ——
    「多给了一个键」「少给了一个必填键」正是模型最常犯的两种错.
    """
    async with started(demo_spec()) as provider:
        tools = by_name(await provider.provide(context()))

        extra = await execute_tool(
            tools["add_numbers"], arguments='{"left": 1, "right": 2, "oops": 3}'
        )
        missing = await execute_tool(tools["add_numbers"], arguments='{"left": 1}')

        assert extra.ok is False and "oops" in (extra.error or "")
        assert missing.ok is False and "right" in (missing.error or "")


# ---------------------------------------------------------------------------
# 多 server: 同名仲裁与路由
# ---------------------------------------------------------------------------


async def test_the_same_tool_name_from_two_servers_is_refused() -> None:
    """两台 server 出同名工具 → **当场报**, 不让后一台悄悄盖掉前一台.

    报错要给出两条出路 (改名 / 配前缀), 因为这是**配置**层面的事, 使用者照着
    报错当场就能改对.
    """
    provider = McpToolProvider([demo_spec("one"), demo_spec("two")])

    with pytest.raises(McpConfigError) as caught:
        await provider.start()

    message = str(caught.value)
    assert "add_numbers" in message
    assert "'one'" in message and "'two'" in message
    assert "tool_prefix" in message
    # 仲裁在 start 里做 (启动期就报), 于是 provide 不会晚一步才炸
    assert provider.tools == ()
    await provider.aclose()  # 失败之后照样收得干净


async def test_a_prefix_lets_two_servers_coexist_and_route_correctly() -> None:
    """配了前缀之后两台并存, 而且**各调各的** (前缀只改名, 不改打到哪儿).

    路由这件事只有真发一次请求才验得出来: 同一份 fixture 起两份, 两边算出来的
    结果一样 —— 所以这里靠「名字对得上, 且都答得出来」这两条一起断.
    """
    async with started(
        demo_spec("one", tool_prefix="a_"), demo_spec("two", tool_prefix="b_")
    ) as provider:
        tools = by_name(await provider.provide(context()))

        # 前面四个有名字的都被加上了前缀, 一个不落
        for name in DEMO_TOOLS:
            assert f"a_{name}" in tools
            assert f"b_{name}" in tools

        result = await execute_tool(
            tools["b_add_numbers"], arguments='{"left": 40, "right": 2}'
        )

        assert result.ok, result.error
        assert result.content == "42"


# ---------------------------------------------------------------------------
# 不静默: 起不来 / 断了 / 还没连
# ---------------------------------------------------------------------------


async def test_a_server_that_cannot_start_fails_the_whole_thing() -> None:
    """其中一台起不来 → 整次 start 失败, **不是**「这台今天没有工具」.

    半开的连接也要收干净: 前一台已经连上了, 它不该留在那儿等着进程退出.
    """
    provider = McpToolProvider(
        [demo_spec("good"), McpServerSpec(name="ghost", command="no-such-program-xyz")]
    )

    with pytest.raises(McpServerError) as caught:
        await provider.start()

    message = str(caught.value)
    assert "'ghost'" in message and "no-such-program-xyz" in message
    assert provider.tools == ()
    # 没连成就不该交出工具 (空工具集比报错难查得多)
    with pytest.raises(McpConfigError):
        await provider.provide(context())
    await provider.aclose()


async def test_a_dead_server_does_not_turn_into_an_empty_answer() -> None:
    """server 半路没了: 当次与之后每一次调用都**响亮地失败**, 不是空结果.

    「工具返回空」与「这次调用根本没打出去」在模型眼里长得一模一样 —— 而后者
    会让它照着一个不存在的结论往下答.
    """
    async with started(demo_spec(call_timeout=5.0)) as provider:
        tools = by_name(await provider.provide(context()))

        killed = await execute_tool(tools["crash"], arguments="{}")
        assert killed.ok is False, "把 server 杀掉的那一次调用不可能有正常结果"

        after = await execute_tool(
            tools["add_numbers"], arguments='{"left": 1, "right": 2}'
        )
        assert after.ok is False, "server 已经死了, 之后这次调用不该有结果"
        assert isinstance(after.exception, McpServerError), (
            f"失败根因应当是「这台 server 打不通」, 实际: {after.exception!r}"
        )


async def test_giving_tools_before_starting_is_loud() -> None:
    """没 start (或已经 aclose) 就 provide → 当场报, 不交空工具集."""
    provider = McpToolProvider([demo_spec()])

    with pytest.raises(McpConfigError):
        await provider.provide(context())

    await provider.start()
    assert provider.tools, "start 之后应当已经有工具了"
    await provider.aclose()

    with pytest.raises(McpConfigError):
        await provider.provide(context())
    assert provider.tools == ()

    await provider.aclose()  # 重复收尾无害 (失败路径上会调两次)


def _live_mute_children(psutil: Any) -> int:
    """还活着的那个"哑进程" (按命令行认, 免得误伤别的子进程)."""
    found = 0
    for child in psutil.Process().children(recursive=True):
        try:
            if any("time.sleep(60)" in part for part in (child.cmdline() or [])):
                found += 1
        except psutil.Error:  # 这一台在遍历途中退出了 —— 正是我们想要的结果
            continue
    return found


async def test_a_cancelled_start_leaves_no_half_open_state() -> None:
    """`start()` 被取消 (Ctrl-C / kill switch) 时: 连接收掉, 状态不留半开.

    对端是一个**起了但不说话**的进程 (`python -c "import time; time.sleep(60)"`),
    于是取消一定落在握手那一步 —— 那正是本仓真实会遇到的场景 (进程刚起、握手还没
    回来时按 Ctrl-C). 断三件事: 取消真的抛出来了; 工具集还是空的 (没留下"半开"的
    假象); 再 start 一次不会被「已经 start 过」挡住 (`CancelledError` 不是
    `Exception`, 只写 `except Exception` 的话它会从两条支路中间漏过去 —— 代码评审
    实测过); **那台子进程真的被收掉了** (不是留给 GC 在别的任务里慢慢退作用域).
    """
    mute = McpServerSpec(
        name="mute",
        command=sys.executable,
        args=("-c", "import time; time.sleep(60)"),
    )
    provider = McpToolProvider([mute])

    with pytest.raises(TimeoutError):  # 握手等不来, 到点取消
        async with asyncio.timeout(2):
            await provider.start()

    # 子进程真的没了: 收尾在取消那条路上跑了 (没 psutil 就跳过这一条 —— 框架的
    # 测试不该多一个硬依赖, 而本仓的 venv 里正好有)
    psutil = pytest.importorskip("psutil")
    assert _live_mute_children(psutil) == 0, "取消之后那台子进程还活着, 没被收掉"

    assert provider.tools == ()
    # 状态没卡在「已 start」—— 再起一次照旧走到握手, 而不是被自己的守卫挡住
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(2):
            await provider.start()

    await provider.aclose()  # 收尾可以重复调 (两次 start 各留下一个半开的栈)


async def test_starting_twice_is_refused() -> None:
    """重复 start 是装配代码的错 —— 报出来, 不是默默再连一台."""
    async with started(demo_spec()) as provider:
        with pytest.raises(McpConfigError, match="已经 start 过"):
            await provider.start()


async def test_a_remote_tool_name_that_the_wire_cannot_carry_is_refused() -> None:
    """远端工具名不合模型侧规范 → **本地当场报**, 不是一路带到上游去炸.

    本地工具的名字在 `@tool` 那层校验过, 而远端工具是直接构造出来的: 少了这道检查,
    一个带点的名字会原样进 wire, 最后变成上游一句看不懂的 400 (报错里一个字都不提
    MCP). 报错要说清是哪台 server 的哪个工具, 以及该怎么改.
    """
    provider = McpToolProvider([demo_spec(env={BAD_NAME_ENV: "1"})])

    with pytest.raises(McpServerError) as caught:
        await provider.start()

    message = str(caught.value)
    assert "bad.name" in message and "'demo'" in message
    assert "工具名" in message
    await provider.aclose()


async def test_the_configured_cwd_reaches_the_child_process(
    tmp_path: Path,
) -> None:
    """配置里的 `cwd` 真的传给了子进程 (不是收下就扔).

    实测过一次它的缺席有多贵: `python -m 某个包` 靠 cwd 才找得到那个包, 客户端从
    别的目录起它时少这一项就是一句 `ModuleNotFoundError`, 而报在客户端界面上只是
    「server 起不来」. 这里让 fixture server 自报工作目录来钉住这条.
    """
    workspace = tmp_path / "elsewhere"
    workspace.mkdir()
    async with started(
        demo_spec(cwd=str(workspace), env={CWD_TOOL_ENV: "1"})
    ) as provider:
        tools = by_name(await provider.provide(context()))

        result = await execute_tool(tools["where_am_i"], arguments="{}")

        assert result.ok, result.error
        assert Path(result.content).resolve() == workspace.resolve()


async def test_the_remote_hints_do_not_become_our_annotations() -> None:
    """远端那组提示 (`readOnlyHint` 等) **不进** `Tool.annotations` —— 故意的.

    那层注解是**业务自己的词汇表** (框架只透传不解释). 把远端的键搬进来, 「这个键
    是谁写的、谁在读」就说不清了; 要搬得在 `_to_tool` 里显式加一行. 这条用例钉的是
    「现在没搬」这个事实 —— 哪天搬了, 它会红, 于是那件事必须是个有意的决定.
    """
    async with started(demo_spec()) as provider:
        tools = by_name(await provider.provide(context()))

        assert tools["bare"].annotations == {}


def test_two_servers_with_the_same_name_are_refused() -> None:
    """server 名重复**在构造期**就报 (连 start 都不用等).

    配置那份 (JSON / 映射) 里重复不了 (键唯一), 但直接构造列表可以 —— 重名的两台
    在报错里长得一模一样, 排查时无从下手.
    """
    with pytest.raises(McpConfigError) as caught:
        McpToolProvider([demo_spec("same"), demo_spec("same")])

    assert "same" in str(caught.value)


# ---------------------------------------------------------------------------
# 翻页: 「只读第一页」等于静默少几个工具
# ---------------------------------------------------------------------------


async def test_tools_are_listed_until_the_cursor_runs_out() -> None:
    """远端分页时要把每一页都翻完 (fixture server 靠环境变量切开页).

    这条顺带验了另一件事: 配置里的 `env` 真的传到子进程了 —— 没有它, server 不
    会分页, 这条用例就退化成「名字列表碰巧一样」.
    """
    async with started(demo_spec(env={"MCP_DEMO_PAGE_SIZE": "2"})) as provider:
        tools = await provider.provide(context())

        assert [tool.name for tool in tools] == list(DEMO_TOOLS), (
            "只翻第一页的话这里会少几个 —— 那正是「工具少了一个」的静默失效"
        )
