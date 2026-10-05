"""工具提供者: 把「这一次运行代表谁」翻译成「交出这套工具」.

一句话理解: 这是插在框架插座 (`CharAgent/agent/provider.py` 的 `ToolProvider`)
上的那个东西。框架只认形状 —— `async def provide(上下文) -> 工具列表`; 本类负责
业务的那一半: 从上下文里取出买家 ID, 再把那套工具 (只读的 + 打 `writes` 注解的)
装到那个买家身上。

为什么身份走「上下文 → 闭包」而不是走工具参数 (这是全项目最要紧的一条):
工具的**参数表会被序列化成文本发给模型看**, 身份一旦成了参数, 模型就看得见,
也可能被诱导填别人的值。放进 `RunContext.payload` 之后它从头到尾不露面 ——
「诱导模型去查别人的订单」这条攻击路径因此根本不存在 (PRD §4.2). 有一条测试
专门遍历这套工具的 schema 断言里面搜不到身份 (见 `tests/test_provider.py`).

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

**第三个进闭包的东西不走载荷, 走构造参数** (L5-b): 知识检索器
(`knowledge/retriever.py`)。它既不是买家身份也不是一次性凭据 —— 它是一个轻对象
(配置 + 会话那个模型), 真正重的零件 (向量库连接 / 两个本地模型) 在它内部按惰性
单例拿. 政策对谁都是同一份, 所以它不随运行重建、也不进载荷, 由装配处直接交给
提供者. 于是知识检索那个工具的闭包里装着它, 而它的参数表里只有 `query`
(与身份同一条纪律的两个不同来源: 一个怕被伪造, 一个压根与买家无关).

**第四个进闭包的东西是引用账** (`knowledge/citations.py`, L5-c): 与检索器同一条
通道 (构造参数), 但它记得的是**这段对话**的事 —— 检索工具每次从它那儿领一段
连续编号, 收尾时它再把引用挂到 `final` 上. 装配处造它一次、交给工具与钩子两处
用, 于是「哪几段被引用了」只有一份账.

**第五个进闭包的东西是记忆仓储** (C13): 与检索器同一条通道 (构造参数, 进程级
共享), 但**身份也从上下文取** —— 记忆属于 `(tenant_id, user_id)` 那一对 (与
会话隔离同一对键: 谁能读到谁的记忆, 与谁能看到谁的会话, 是同一条边界; 命令行
调试聊出来的东西也因此不会污染买家在网页上的长期记忆). 于是记忆工具是**唯一**
身份不来自 `payload` 的那一族 —— 框架的 `RunContext` 本来就有那两个字段, 不为
记忆另造一份. 它**不装在 `tools.build_tools` 里** (那个函数的入参是业务身份
`(client, int user_id)`, 装不下这一对键), 由 `provide` 接在最后.
"""

from __future__ import annotations

from collections.abc import Sequence

from CharAgent.agent import RunContext
from CharAgent.db import MemoriesRepository
from CharAgent.tool import Tool, build_memory_tools
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import MinimallConfigError
from CharApp.minimall.knowledge.citations import Citations
from CharApp.minimall.knowledge.retriever import KnowledgeRetriever
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
    """电商客服的工具提供者: 给定上下文, 交出这个买家的那套工具。

    形状上是结构化协议 (有 `provide` 就算), 不继承任何基类 —— 与框架的
    `ChatModel` / `ToolProvider` 同一套做法, 业务不 import 基类。

    Args:
        client: 商城客户端 (连接池与令牌)。**由调用方持有并负责关闭** ——
            提供者只是每次运行时把买家身份接到它上面, 不接管它的生命周期
            (同一个客户端服务同一进程里的所有买家)。
        retriever: 知识库检索器 (L5-b)。由装配处**每个会话现造一个** (对象本身
            很轻: 只拿着配置与会话那个模型), 而它用到的重零件 —— 向量库连接与
            两个本地模型 —— 是 `knowledge/` 里的惰性单例, 一个进程一份。与身份
            不同, 它**不带买家**: 政策对谁都是同一份。
        citations: 这段对话的引用账 (L5-c), 每会话一份.
        memory: 长期记忆仓储 (C13); None 表示**这个进程没有记忆能力** (不记账的
            进程没有那条库入口, 见 `service.MinimallService.database`) —— 装出来
            的工具集因此少 `remember` / `recall` 两个, 而不是装一个会炸的进去.

    attributes:
        (无公开属性; 工具集每次现装 —— 装配是纯函数, 不做缓存)
    """

    def __init__(
        self,
        client: MinimallClient,
        retriever: KnowledgeRetriever,
        citations: Citations,
        memory: MemoriesRepository | None = None,
    ) -> None:
        self._client = client
        self._retriever = retriever
        self._citations = citations
        self._memory = memory

    async def provide(self, context: RunContext) -> Sequence[Tool]:
        """按上下文里的买家身份装出这次运行的那套工具。

        Args:
            context: 装配代码填好的运行上下文 (payload 里有 `user_id`; 挂起恢复
                那一次还会有一份一次性凭据)。

        Returns:
            Sequence[Tool]: 那套工具 (只读的 + 打 `writes` 注解的 + 知识检索;
            配了记忆仓储时还有 `remember` / `recall`), 身份与凭据都已裹进各自的
            闭包。

        Raises:
            MinimallConfigError: 载荷里没有买家身份 (见 `buyer_id`)。
        """
        tools = list(
            build_tools(
                self._client,
                buyer_id(context),
                one_shot=one_shot_payload(context),
                retriever=self._retriever,
                citations=self._citations,
            )
        )
        if self._memory is not None:
            # 记忆那两个工具**接在最后**, 身份取的是上下文里那一对会话隔离键
            # (不是 payload 里那个整数买家号) —— 理由见模块 docstring 第五段;
            # thread_id 一并给 (C30: 写进 source_thread_id, 「这条记忆从哪段对话来」
            # 的线索 —— 只有装配期拿得到, 事后补不回来)
            tools.extend(
                build_memory_tools(
                    self._memory,
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                    thread_id=context.thread_id,
                )
            )
        return tuple(tools)


__all__ = [
    "PAYLOAD_USER_ID",
    "MinimallToolProvider",
    "buyer_id",
    "one_shot_payload",
]
