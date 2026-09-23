"""server 包: HTTP + SSE 服务层 (框架的第十个包, 也是唯一可选的那个).

一句话理解: 把「问一句话 → 流式事件」搬到 HTTP 上. 框架在这里第一次说 HTTP,
但仍然不认识业务 —— 业务通过两个插座接进来 (认证解析 / 会话装配), 除此之外
框架不需要知道任何事.

结构总览 (对齐 model / tool / agent 包惯例):

- app.py       应用工厂 create_app: 三条路 (POST /runs 问一句 / POST /runs/{id}/cancel
               停一次 / GET /history 读这段对话聊过什么) + 一组可选的会话路由
               (GET /conversations 列我聊过哪几段与搜索, 外加 POST
               /conversations/title|pin|delete 三个管理动作, 给了 database 才有)
               + 两条接缝 + 一套收尾
- sse.py       事件流 → SSE: 字段映射 (纯函数) + 响应体生成器 (收尾时叫停没人听的运行)
- sessions.py   会话登记表 + 事件路由: 会话按 thread_id 长驻, 同一会话不并发跑,
               空闲超时的条目被清掉 (真相在快照与记录里, 内存只是缓存)
- runs.py       一次运行的流与在册: 事件队列 + 序号记账 + 可取消的任务句柄
- history.py    会话历史的只读视图: 记录表的行 (或内存那份 wire 历史) → 能给人看的
               那一份对话
- conversations.py 会话列表的读写视图: 会话行 → 前端左栏要的 (对话 ID / 标题 / 时间 /
               置顶时刻), 以及 ticket 20 的三个管理动作 (改名 / 置顶 / 删除) 的契约
- utils/        支撑子包: types (两个插座协议 + wire 契约常量) /
                errors (一族自带状态码的错误)

用法 (业务侧只需要两个类 + 一处装配)::

    from CharAgent.server import (
        RUN_ID_HEADER,       # 响应头: 本次运行的编号
        ServerAuthError,     # 认证失败时抛它 (框架翻成 401)
        create_app,
    )

    class MyContexts:                       # 插座一: 请求 → 运行上下文
        async def provide(self, request):
            if request.headers.get("X-Internal-Token") != token:
                raise ServerAuthError("认证失败")
            user_id = request.headers["X-User-Id"]
            return RunContext(
                thread_id=f"my:{user_id}:1",
                tenant_id="my-app",        # 框架按这两个字符串分区与过滤
                user_id=user_id,
                payload={"locale": "zh"},  # 业务私货 (框架不解释)
            )

    class MySessions:                       # 插座二: 上下文 → 会话
        async def provide(self, context, *, event_sink):
            # 快照后端是 Postgres 时还要给 `database=` (记录层): 帧的 thread_id
            # 指向记录层的 charagent_threads, 那一行由记录员的 begin 建 (ticket 24)
            return ChatSession(model, saver=saver, tools=..., event_sink=event_sink,
                               thread_id=context.thread_id)

    app = create_app(context_provider=MyContexts(), session_provider=MySessions())
    # 怎么跑由业务决定: uvicorn MyModule:app ...

**为什么本包不在根门面里导出** (与 client 一样是刻意排除, 但理由不同):
根门面是「装了这个包就能用」的库 API, 而这一层要 web 栈 (fastapi). 把它做成
可选依赖组之后, 「不装 web 框架也能用这个 agent 框架」是一句能兑现的话 ——
根门面保持零 web 依赖, 要用 HTTP 的自己 `from CharAgent.server import ...`
(装 `charagent[server]`). 这条纪律由用例守着 (`tests/test_root_facade.py`:
`import CharAgent` 不许把 fastapi 拖进 sys.modules).

与其他包的关系: server 消费 stream 的事件流 (事件总线 → SSE), 用 agent 的
RunContext 与 client 的 ChatSession 当装配产物 —— 它不碰 loop / checkpoint 的
内部, 也不改它们任何一行.

与 db 的关系只有一条, 而且只走**仓储** (`db/repositories`, 不写 SQL、不碰实体
之外的内部): `GET /history` 与 `GET /conversations` 这两条只读路在 `database=` 给了
的时候读记录表. 没给就是 None, 那两条路各自退回「没有记录层」的样子 (历史读会话
内存 / 列表那条不注册) —— 框架的「不配不改行为」在这里同样成立.
"""

from __future__ import annotations

from CharAgent.server.app import create_app
from CharAgent.server.conversations import (
    CONVERSATION_ID_FIELD,
    CONVERSATIONS_FIELD,
    CONVERSATIONS_PATH,
    DELETE_PATH,
    DELETED_FIELD,
    LIMIT_QUERY,
    MAX_TITLE_LENGTH,
    PIN_PATH,
    PINNED_AT_FIELD,
    PINNED_FIELD,
    QUERY_QUERY,
    TITLE_FIELD,
    TITLE_PATH,
    UPDATED_AT_FIELD,
)
from CharAgent.server.history import (
    HISTORY_PATH,
    MESSAGES_FIELD,
    THREAD_ID_FIELD,
)
from CharAgent.server.utils.errors import (
    InvalidRequestError,
    RunNotFoundError,
    ServerAuthError,
    ServerConfigError,
    ServerError,
    ThreadBusyError,
    ThreadNotFoundError,
)
from CharAgent.server.utils.types import (
    CANCELLED_CODE,
    MESSAGE_FIELD,
    RUN_FAILED_CODE,
    RUN_ID_HEADER,
    SSE_MEDIA_TYPE,
    ContextProvider,
    SessionProvider,
)

__all__ = [
    "CANCELLED_CODE",
    "CONVERSATIONS_FIELD",
    "CONVERSATIONS_PATH",
    "CONVERSATION_ID_FIELD",
    "DELETED_FIELD",
    "DELETE_PATH",
    "HISTORY_PATH",
    "LIMIT_QUERY",
    "MAX_TITLE_LENGTH",
    "MESSAGES_FIELD",
    "MESSAGE_FIELD",
    "PINNED_AT_FIELD",
    "PINNED_FIELD",
    "PIN_PATH",
    "QUERY_QUERY",
    "RUN_FAILED_CODE",
    "RUN_ID_HEADER",
    "SSE_MEDIA_TYPE",
    "THREAD_ID_FIELD",
    "TITLE_FIELD",
    "TITLE_PATH",
    "UPDATED_AT_FIELD",
    "ContextProvider",
    "InvalidRequestError",
    "RunNotFoundError",
    "ServerAuthError",
    "ServerConfigError",
    "ServerError",
    "SessionProvider",
    "ThreadBusyError",
    "ThreadNotFoundError",
    "create_app",
]
