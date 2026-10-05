"""暴露侧契约: 只暴露只读的那几个, 只代表配置里那一个账户, schema 逐字段原样.

对端是**真协议往返**: 用例把一个 `ClientSession` 接到 `build_server(...)` 装出来的
server 上 (`mcp.shared.memory` 那对内存流, SDK 自带的测试支撑), 于是 `tools/list`
与 `tools/call` 走的都是真 JSON-RPC —— 只不过商城那一侧仍然是 respx 拦下来的假商城,
整套用例离线可跑.

三条性质各由一个方向的断言守着, 它们合起来才是 ADR-0030 说的那件事:

| 性质 | 怎么断 |
|------|--------|
| **只读** | 暴露的名字集 = 本地工具集里不带 `writes` 注解的那些; 写工具的名单逐个不在 |
| **一个账户** | 身份来自装配参数, schema 里搜不到身份, 客户端想塞身份也会被挡在本地 |
| **机械映射** | 每个工具的 `inputSchema` / `description` 与本地那份**逐字相等** |

「不暴露写工具」是**否定断言**, 所以它断两遍: 名字不在 (结果), 与本地那个工具确实
带 `writes` 注解 (机制). 只断前者的话, 哪天写工具被改名或注解漏打, 这条会假绿.
"""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import (
    BUYER_ID,
    ORDER_LIST,
    PAYMENT_PASSWORD,
    PROFILE,
    TOOL_NAMES,
    WRITE_TOOL_NAMES,
    agent_url,
    citations_for_tests,
    retriever_for_tests,
)
from mcp.shared.memory import create_connected_server_and_client_session

from CharAgent.tool import Tool
from CharApp.minimall.client import HEADER_USER_ID, MinimallClient
from CharApp.minimall.config import (
    ENV_MCP_USER_ID,
    MinimallConfigError,
    mcp_user_id_from_env,
)
from CharApp.minimall.mcp_server import (
    INSTRUCTIONS,
    SERVER_NAME,
    build_server,
    read_only_tools,
)
from CharApp.minimall.tools import WRITE_ANNOTATION_KEY, build_tools

# 记忆那三个工具 (remember / recall / forget) 不在 `build_tools` 里 —— 它们由提供者
# 接在最后一位 (`provider.provide`), 而暴露侧走的是 `build_tools` 那一层. 于是它们
# 本来就不会出现在工具列表里; 这里写下来是为了让「应该出现的集合」有一个完整出处.
MEMORY_TOOL_NAMES = ("remember", "recall", "forget")

# 期望暴露的那一份: 整套工具减去写工具, 再减去记忆那三个
EXPOSED_NAMES = tuple(
    name
    for name in TOOL_NAMES
    if name not in WRITE_TOOL_NAMES and name not in MEMORY_TOOL_NAMES
)


def local_tools(client: MinimallClient) -> dict[str, Tool]:
    """本地那份工具集 (暴露侧的**来源**; 逐字比对用)."""
    return {
        tool.name: tool
        for tool in build_tools(
            client,
            BUYER_ID,
            retriever=retriever_for_tests(),
            citations=citations_for_tests(),
        )
    }


# ---------------------------------------------------------------------------
# 只读
# ---------------------------------------------------------------------------


async def test_the_exposed_set_is_exactly_the_read_only_tools(
    client: MinimallClient,
) -> None:
    """上线的工具就是那 11 个只读的 (名字与顺序都与本地那份对得上)."""
    server = build_server(client, BUYER_ID)

    async with create_connected_server_and_client_session(server) as session:
        listed = (await session.list_tools()).tools

    assert [tool.name for tool in listed] == list(EXPOSED_NAMES)


async def test_no_write_tool_is_exposed(client: MinimallClient) -> None:
    """**否定断言**: 会改数据的 8 个一个都不在线上.

    MCP 客户端没有本业务那套确认机制 (护栏 + 挂起恢复), 暴露写工具等于「绕过人按的
    那一下直接下单」—— 这条断言就是那道闸的证据 (理由见 ADR-0030).
    """
    server = build_server(client, BUYER_ID)

    async with create_connected_server_and_client_session(server) as session:
        names = [tool.name for tool in (await session.list_tools()).tools]

    exposed_writes = set(names) & set(WRITE_TOOL_NAMES)
    assert exposed_writes == set(), f"写工具不该上线: {sorted(exposed_writes)}"
    # 机制那一半: 那 8 个本地工具确实都打了注解 (名字变了 / 注解漏了都会让上面那条
    # 假绿 —— 「不在线上」到底是没暴露, 还是压根没被当写工具)
    local = local_tools(client)
    for name in WRITE_TOOL_NAMES:
        assert local[name].annotations.get(WRITE_ANNOTATION_KEY), (
            f"{name} 是写工具, 本地那份却没打 {WRITE_ANNOTATION_KEY} 注解"
        )


def test_the_filter_reads_the_annotation_not_a_hand_written_list() -> None:
    """过滤的判据是**注解**, 不是手抄名单 —— 以后加写工具也不会漏.

    造两个玩具工具 (一个打注解一个不打) 直接过筛: 这就是「新加的写工具天然进不了
    线上」那句话的可执行形式.
    """

    async def noop() -> str:
        """什么也不做."""
        return "ok"

    read = Tool(name="read_one", description="只读", fn=noop, parameters={})
    write = Tool(
        name="write_one",
        description="会改数据",
        fn=noop,
        parameters={},
        annotations={WRITE_ANNOTATION_KEY: True},
    )

    assert [tool.name for tool in read_only_tools([read, write])] == ["read_one"]


# ---------------------------------------------------------------------------
# 一个账户 (ADR-0030 的边界)
# ---------------------------------------------------------------------------


def test_the_account_comes_from_the_environment_and_nowhere_else() -> None:
    """绑定哪个买家**只**由环境变量决定; 没配就起不来 (fail closed)."""
    assert mcp_user_id_from_env({ENV_MCP_USER_ID: " 7 "}) == 7
    for missing in ({}, {ENV_MCP_USER_ID: "  "}):
        with pytest.raises(MinimallConfigError) as caught:
            mcp_user_id_from_env(missing)
        assert ENV_MCP_USER_ID in str(caught.value)
    for bad in ("buyer3", "0", "-1"):
        with pytest.raises(MinimallConfigError):
            mcp_user_id_from_env({ENV_MCP_USER_ID: bad})


async def test_no_identity_parameter_in_any_exposed_schema(
    client: MinimallClient,
) -> None:
    """**否定断言**: 上线的 schema 里搜不到任何身份 (与助手那边同一条守卫).

    客户端改不了「代表谁」: 它连一个能填身份的参数都找不到.
    """
    server = build_server(client, BUYER_ID)

    async with create_connected_server_and_client_session(server) as session:
        listed = (await session.list_tools()).tools

    for tool in listed:
        schema = json.dumps(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            },
            ensure_ascii=False,
        )
        for forbidden in ("user_id", "buyer", "buyer_id"):
            assert forbidden not in schema, (
                f"{tool.name} 的 schema 里有 {forbidden}: {schema}"
            )


async def test_a_client_cannot_ask_for_another_account(
    client: MinimallClient, mall
) -> None:
    """客户端在参数里塞一个身份 → 当场被挡, 而且**一个请求都没发出去**.

    这条把边界的两半一起钉住: 参数收不下 (schema 里没有它), 而且拒绝发生在本地
    (不是「发出去了但商城不理」—— 那种「先发再忽略」的写法等于把身份问题交给了
    下游, 而下游正是那个不该被信任的地方).
    """
    route = mall.get(agent_url("profile/")).mock(
        return_value=httpx.Response(200, json=PROFILE)
    )
    server = build_server(client, BUYER_ID)

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("get_my_profile", {"user_id": 999})

    assert result.isError is True
    assert route.calls == [], "被拒绝的那一次不该打到商城"


async def test_a_read_only_call_reaches_the_mall_as_the_bound_account(
    client: MinimallClient, mall
) -> None:
    """真往返: 「我的订单到哪了」那条路上的一个工具真的打到了商城, 拿回真数据.

    这是暴露侧的**验收路径** (把 Claude 换成用例里的 `ClientSession`): 客户端只给
    参数, 身份由装配参数补上, 商城看到的就是配置里那个买家.
    """
    route = mall.get(agent_url("orders/")).mock(
        return_value=httpx.Response(200, json=ORDER_LIST)
    )
    server = build_server(client, BUYER_ID)

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("list_my_orders", {})

    assert result.isError is False, result.content
    text = result.content[0].text
    assert ORDER_LIST["results"][0]["order_no"] in text
    assert route.calls[0].request.headers[HEADER_USER_ID] == str(BUYER_ID)


async def test_a_mall_failure_comes_back_as_an_mcp_error(
    client: MinimallClient, mall
) -> None:
    """商城故障 → 客户端收到 `isError`, 而不是一段编出来的"没有数据"."""
    mall.get(agent_url("profile/")).mock(return_value=httpx.Response(500, text="boom"))
    server = build_server(client, BUYER_ID)

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("get_my_profile", {})

    assert result.isError is True
    assert result.content, "失败也要给一句能看的说明"


async def test_an_unknown_tool_comes_back_as_an_error(client: MinimallClient) -> None:
    """不认识的工具名 → 报错里把可用的那几个列出来 (手写客户端最容易撞的就是这个)."""
    server = build_server(client, BUYER_ID)

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("place_order", {})

    assert result.isError is True
    assert "get_my_order" in result.content[0].text


# ---------------------------------------------------------------------------
# 机械映射: 线上那一份就是本地那一份
# ---------------------------------------------------------------------------


async def test_the_exposed_definition_is_the_local_one_verbatim(
    client: MinimallClient,
) -> None:
    """每个工具的名字 / 说明 / schema 都与本地那份逐字相同 —— 中间没有第二个来源.

    这条是「不必用 FastMCP」那个决定的守卫: 一旦有人改成从函数签名重新生成 schema,
    线上那份就会与本地那份开始漂 (而漂了之后, 模型看到的参数表就不再是工具真正
    校验的那一份).
    """
    local = local_tools(client)
    server = build_server(client, BUYER_ID)

    async with create_connected_server_and_client_session(server) as session:
        listed = (await session.list_tools()).tools

    assert listed, "一个工具都没报出来, 这条用例自己就失效了"
    for tool in listed:
        source = local[tool.name]
        assert tool.description == source.description
        assert tool.inputSchema == source.parameters


def test_the_handshake_declares_the_boundary(client: MinimallClient) -> None:
    """握手时就把边界说给客户端听 (只读 + 代表配置里那一个账户).

    它是**对外**那一半的声明 (对内那一半是代码与 ADR): 接上来的模型看得见这句话,
    于是不会以为这里能下单、也不会以为它能代表别人.
    """
    server = build_server(client, BUYER_ID)

    assert server.name == SERVER_NAME
    assert server.instructions == INSTRUCTIONS
    assert "只读" in INSTRUCTIONS and "一个买家" in INSTRUCTIONS


def test_the_one_shot_credential_does_not_ride_along_in_the_wire(
    client: MinimallClient,
) -> None:
    """暴露出去的那份定义里没有一次性凭据 (身份之外的第二条守卫).

    代付的支付密码走的是「挂起恢复那一刻的一次性载荷」, 而 MCP 这一侧**没有挂起
    恢复** —— 它连一个能收密码的地方都不该有. 代付工具本来就被只读那道筛子挡掉了,
    这里连它的名字与那个字段名 (以及那个值) 一起搜一遍: 「挡掉了」要是靠运气
    (比如哪天有人给它改个名), 这条会红.
    """
    wire = json.dumps(
        [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,
            }
            for tool in read_only_tools(
                build_tools(
                    client,
                    BUYER_ID,
                    retriever=retriever_for_tests(),
                    citations=citations_for_tests(),
                )
            )
        ],
        ensure_ascii=False,
    )

    for forbidden in ("payment_password", PAYMENT_PASSWORD, "pay_my_order"):
        assert forbidden not in wire, f"暴露出去的 schema 里有 {forbidden}"
