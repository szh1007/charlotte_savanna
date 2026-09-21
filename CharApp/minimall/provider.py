"""工具提供者: 把「这一次运行代表谁」翻译成「交出哪 17 个工具」.

一句话理解: 这是插在框架插座 (`CharAgent/agent/provider.py` 的 `ToolProvider`)
上的那个东西。框架只认形状 —— `async def provide(上下文) -> 工具列表`; 本类负责
业务的那一半: 从上下文里取出买家 ID, 再把 17 个工具 (9 只读 + 8 写) 装到那个
买家身上。

为什么身份走「上下文 → 闭包」而不是走工具参数 (这是全项目最要紧的一条):
工具的**参数表会被序列化成文本发给模型看**, 身份一旦成了参数, 模型就看得见,
也可能被诱导填别人的值。放进 `RunContext.payload` 之后它从头到尾不露面 ——
「诱导模型去查别人的订单」这条攻击路径因此根本不存在 (PRD §4.2). 有一条测试
专门遍历 17 个工具的 schema 断言里面搜不到身份 (见 `tests/test_provider.py`).

网页版从哪里接进来: **本文件一行不改** (2026-09-20 起两个入口都在跑). `user_id`
如今由 Django 从 session 里取出、经请求头转发过来, 变的只是「谁往 payload 里放
这个值」, 而那一段是 `service.build_context` 的**参数**: 命令行传 argv, 服务进程
传请求头 (PRD §4.2 的最后一段) —— 两个入口各自取身份, 互不取代。
"""

from __future__ import annotations

from collections.abc import Sequence

from CharAgent.agent import RunContext
from CharAgent.tool import Tool
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import MinimallConfigError
from CharApp.minimall.tools import build_tools

# 运行上下文里放买家身份的那个键. 框架**不认识**它 (payload 是「框架不解释的
# 载荷」), 这个名字只在业务侧有含义 —— 写在这里而不是散在字面量里, 是为了让
# 「身份从哪个键取」只有一个出处。
PAYLOAD_USER_ID = "user_id"


def buyer_id(context: RunContext) -> int:
    """从运行上下文里取出买家 ID。

    Raises:
        MinimallConfigError: 载荷里没有买家身份, 或它不是个整数。这是**装配代码**
            的错误 (入口忘了填 payload), 不是买家的错误 —— 所以当场说清,
            而不是让它变成一个「查不到数据」的假象。
    """
    raw = context.payload.get(PAYLOAD_USER_ID)
    if raw is None:
        raise MinimallConfigError(
            f"运行上下文 {context.thread_id!r} 的载荷里没有 {PAYLOAD_USER_ID}: "
            f"装配时忘了往里放买家身份 "
            f"(见 CharApp/minimall/service.py 的 build_context)"
        )
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise MinimallConfigError(
            f"载荷里的 {PAYLOAD_USER_ID} 应是整数, 实际 {raw!r}"
        ) from exc


class MinimallToolProvider:
    """电商客服的工具提供者: 给定上下文, 交出这个买家的 17 个工具。

    形状上是结构化协议 (有 `provide` 就算), 不继承任何基类 —— 与框架的
    `ChatModel` / `ToolProvider` 同一套做法, 业务不 import 基类。

    Args:
        client: 商城客户端 (连接池与令牌)。**由调用方持有并负责关闭** ——
            提供者只是每次运行时把买家身份接到它上面, 不接管它的生命周期
            (同一个客户端服务同一进程里的所有买家)。

    attributes:
        (无公开属性; 工具集每次现装 —— 装配是纯函数, 不做缓存)
    """

    def __init__(self, client: MinimallClient) -> None:
        self._client = client

    async def provide(self, context: RunContext) -> Sequence[Tool]:
        """按上下文里的买家身份装出这次运行的 17 个工具。

        Args:
            context: 装配代码填好的运行上下文 (payload 里有 `user_id`)。

        Returns:
            Sequence[Tool]: 17 个工具 (9 只读 + 8 写), 身份已裹进各自的闭包。

        Raises:
            MinimallConfigError: 载荷里没有买家身份 (见 `buyer_id`)。
        """
        return build_tools(self._client, buyer_id(context))


__all__ = ["PAYLOAD_USER_ID", "MinimallToolProvider", "buyer_id"]
