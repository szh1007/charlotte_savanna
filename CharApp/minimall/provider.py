"""工具提供者: 把「这一次运行代表谁」翻译成「交出哪 18 个工具」.

一句话理解: 这是插在框架插座 (`CharAgent/agent/provider.py` 的 `ToolProvider`)
上的那个东西。框架只认形状 —— `async def provide(上下文) -> 工具列表`; 本类负责
业务的那一半: 从上下文里取出买家 ID, 再把 18 个工具 (9 只读 + 8 写 + 代付) 装到
那个买家身上。

为什么身份走「上下文 → 闭包」而不是走工具参数 (这是全项目最要紧的一条):
工具的**参数表会被序列化成文本发给模型看**, 身份一旦成了参数, 模型就看得见,
也可能被诱导填别人的值。放进 `RunContext.payload` 之后它从头到尾不露面 ——
「诱导模型去查别人的订单」这条攻击路径因此根本不存在 (PRD §4.2). 有一条测试
专门遍历 18 个工具的 schema 断言里面搜不到身份 (见 `tests/test_provider.py`).

**第二个走这条路的载荷是代付的支付密码** (issue 35, ADR-0015): 用户在自己页面上
输进的那一次密码, 由恢复请求的 `data` 并进同一个载荷, 本模块把它挑出来交给工具
闭包 (`build_tools(..., one_shot=)`) —— 它因此同样不进 schema, 而这一次不只是
"防伪造" (没人能编出别人的密码), 更是"防模型自己编一个": 只要密码成了参数, 模型
就会填一个进去, 而填的值会走 `arguments` 落库. 有一条测试与身份那条同一个判据,
搜的是密码 (见 `tests/test_provider.py`).

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
from CharApp.minimall.tools import ONE_SHOT_FIELDS, build_tools

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


def one_shot_payload(context: RunContext) -> dict[str, str]:
    """从运行上下文里挑出**这一次运行才有的一次性凭据** (今天只有支付密码).

    为什么是"挑"而不是整个载荷照搬: 载荷是业务自己的口袋, 将来会装下别的东西
    (语言 / 页面来源 / 权限), 而那些东西工具一个都用不上 —— 只把凭据交出去,
    闭包里就永远不会多出一份没人管的数据. 挑哪几个由 `tools.ONE_SHOT_FIELDS`
    说了算, 那份清单与护栏声明的 `needs` 是同一批名字 (有用例守着).

    值一律转成字符串: 载荷是从 HTTP 请求体并进来的 (JSON), 而工具的闭包只认
    字符串 —— 在**这一处**转, 别处按一种形态说话. 空值 (缺 / None / 空串) 一律
    不算"拿到了": 空密码撞一次端点只会白烧一条失败路径 (见 `_pay_my_order`).

    Args:
        context: 运行上下文 (恢复那一次的 `data` 已经并进 payload).

    Returns:
        dict[str, str]: 拿到的凭据 (键 → 值); 一个都没有时是空字典 —— 普通提问
        走的就是这一支, 代付工具据此回一句「没有拿到授权」而不是拿空密码去撞.
    """
    return {
        key: str(context.payload[key])
        for key in ONE_SHOT_FIELDS
        if context.payload.get(key)
    }


class MinimallToolProvider:
    """电商客服的工具提供者: 给定上下文, 交出这个买家的 18 个工具。

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
        """按上下文里的买家身份装出这次运行的 18 个工具。

        Args:
            context: 装配代码填好的运行上下文 (payload 里有 `user_id`; 挂起恢复
                那一次还会有一份一次性凭据)。

        Returns:
            Sequence[Tool]: 18 个工具 (9 只读 + 8 写 + 代付), 身份与凭据都已裹进
            各自的闭包。

        Raises:
            MinimallConfigError: 载荷里没有买家身份 (见 `buyer_id`)。
        """
        return build_tools(
            self._client, buyer_id(context), one_shot=one_shot_payload(context)
        )


__all__ = [
    "PAYLOAD_USER_ID",
    "MinimallToolProvider",
    "buyer_id",
    "one_shot_payload",
]
