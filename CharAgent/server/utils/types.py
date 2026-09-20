"""server 包的静态零件: 两个接入协议 (插座) + 事件流与 HTTP 之间的契约常量.

一句话理解: 本文件规定框架的 HTTP 层**怎么跟业务接**, 以及事件流推出去时
「哪一层字段叫什么名字」. 与 agent/provider.py 是同一套做法 —— 框架规定插座
的形状, 业务决定插上去的是什么; 插座本身不带电, 也不认识插上的电器.

两个插座 (业务实现, 框架调用):

    HTTP 请求 ─[ContextProvider.provide]-> RunContext
                 (认证 + 解析: 这是谁, 算哪段会话)
    RunContext ─[SessionProvider.provide]-> ChatSession
                 (装配: 这次用什么工具 / 提示词 / 模型)

- 第一个插座回答「这次运行是谁的、算哪段会话」, 身份与令牌都在这里认; 认不出来
  就抛 ServerAuthError (框架把它翻成状态码, 不翻成大段 traceback).
- 第二个插座回答「这次运行拿什么去跑」, 与 L1a 的 ToolProvider 同一条分工线:
  框架只认「给我一个会话」, 会话里装了什么它一概不问.

**框架不解释 RunContext.payload**: 里面放什么 (用户 ID / 租户 / 语言...) 是业务的
事, 与 ToolProvider 那条纪律完全一致. 框架唯一认识的是 `thread_id` —— 它要拿它
做会话分区.

为什么第二个插座要多收一个 `event_sink`: 事件的出口在**会话构造时**就定死了
(ChatSession 把它交给 AgentLoop), 而一个会话要连续服务很多次运行. 所以框架在
装配时把「这个会话的事件往哪儿送」一并交出去, 运行之间的分流由框架自己做
(见 sessions.EventRouter) —— 业务只管把它转交给 ChatSession, 不必知道它是什么.

契约常量看着琐碎, 但都是**跨进程**的名字 (客户端按它解析), 所以集中放在这里,
不从实现里各写各的.
"""

from __future__ import annotations

from typing import Protocol

from starlette.requests import Request

from CharAgent.agent import RunContext
from CharAgent.client.session import ChatSession
from CharAgent.stream import EventSink

# ---------------------------------------------------------------------------
# 事件流契约 (框架补发的终局事件用哪个 code)
# ---------------------------------------------------------------------------

# 运行被取消时的终局 error code. 触发源有两个 (显式取消请求 / 客户端断连),
# 但事件流里长得一样 —— 消费方不必区分是「谁按的停止」.
CANCELLED_CODE = "cancelled"

# 运行因异常终止时的终局 error code: 模型调用失败 / 快照落盘失败 / 装配失效等.
# loop 自己不发终局事件的那几类失败 (见 agent/loop.py 的 run docstring) 落到这里,
# 由本层补一个 —— 否则前端只能靠超时猜「是不是没了」.
RUN_FAILED_CODE = "run_failed"

# ---------------------------------------------------------------------------
# HTTP 契约 (框架自己的那一小块 wire 形状)
# ---------------------------------------------------------------------------

# 请求体里「用户问的那句话」放这个字段: {"message": "..."}. 框架只认这一个
# 字段 —— 业务要传别的 (会话 ID / 页面来源...) 自己做主, 从 Request 里取,
# 框架不解释也不需要知道.
MESSAGE_FIELD = "message"

# 事件流响应的头: 客户端从它拿到本次运行的编号 (取消与排查都靠它).
# 放在响应头而不是第一个事件里: 头在第一个字节之前就发出去了, 客户端不必
# 等一个「开场事件」 —— 而事件类型是封闭的六类, 不该为本层另开一种.
RUN_ID_HEADER = "X-Run-Id"

# SSE 的 media type (text/event-stream; EventSource 与 curl 都按它认).
SSE_MEDIA_TYPE = "text/event-stream"


# ---------------------------------------------------------------------------
# 两个插座 (SPI: 业务实现, 框架调用)
# ---------------------------------------------------------------------------


class ContextProvider(Protocol):
    """SPI: 业务实现它, 把一次 HTTP 请求认成一次运行 (认证 + 解析).

    形状只有一个方法, 且**是异步的** —— 真实业务在这里除了读头, 常常还要打
    一次内部接口确认身份或拉配置 (与 ToolProvider.provide 同样的理由).
    """

    async def provide(self, request: Request) -> RunContext:
        """认下这次请求: 认证通过就交出这次运行的上下文.

        业务在这一个方法里做三件事 (框架只看第三件的结果):

        1. **认证**: 读令牌之类的凭据, 对不上就抛 `ServerAuthError` ——
           框架按它给的状态码回一个干净的 JSON, 不把异常摊成 500.
           消息请写成粗粒度的说法 (「认证失败」), 别把「令牌错」还是「用户
           不存在」漏给调用方; 细节自己记日志.
        2. **取身份**: 当前是哪个用户 (或哪个租户), 放进 `payload`.
        3. **拼会话编号**: `thread_id` 决定这次运行算哪段对话 —— 会话按它复用,
           多用户隔离也靠它. 框架不替业务拼 (各家的编号规则不一样).

        请求体里框架只认 `MESSAGE_FIELD` 那一个字段, 业务要用的别的数据自己
        从 request 里取 (body 读两次不会重复消耗, 框架那边读的是同一份缓存).

        Args:
            request: 本次 HTTP 请求 (头 / 正文 / 查询串都在).

        Returns:
            RunContext: 这次运行的上下文 (会话编号 + 业务自己的载荷).

        Raises:
            ServerAuthError: 认证 / 解析失败 (框架翻成 401, 或业务指定的状态码).
            ServerConfigError: 业务自己没配好 (例如令牌根本没设), 翻成 503.
        """
        ...


class SessionProvider(Protocol):
    """SPI: 业务实现它, 按运行上下文交出这次运行要用的会话 (装配).

    与 ToolProvider 同款: 结构化协议, 有那个方法就算, 不要求继承基类
    (因而 `isinstance` 不适用 —— 与既有协议保持一致).

    实现要点 (两句话): 会话是**按 thread_id 长驻**的 —— 框架只在第一次碰到某个
    会话编号时调一次本方法, 之后一直复用同一个会话, 所以对话历史自然连得上;
    以及会话里的模型与存储通常是**进程级共享**的 (建一次, 服务该进程里所有
    会话), 每次运行重复建一遍会把连接池建没.
    """

    async def provide(
        self, context: RunContext, *, event_sink: EventSink
    ) -> ChatSession:
        """按上下文建一个会话 (框架只在某个会话编号第一次出现时调用).

        Args:
            context: 业务自己的解析结果 (身份在 payload 里; thread_id 决定
                这个会话属于哪段对话).
            event_sink: 事件出口 —— 请原样转交给 ChatSession (`event_sink=`).
                它的作用域是**这个会话**, 框架内部按运行分流 (见本模块
                docstring); 业务不必保存它, 也不必知道它是什么.

        Returns:
            ChatSession: 装好的会话 (工具 / 提示词 / 模型 / 快照都由业务决定).

        Raises:
            ServerConfigError: 配不起来 (例如模型 Key 没配) —— 框架翻成 503.
            其余异常照常向上走: 那是 bug, 该留 traceback 给自己看.
        """
        ...
