"""server 包: HTTP + SSE 服务层 (框架的第十个包, 也是唯一可选的那个).

一句话理解: 把「问一句话 → 流式事件」搬到 HTTP 上. 框架在这里第一次说 HTTP,
但仍然不认识业务 —— 业务通过两个插座接进来 (认证解析 / 会话装配), 除此之外
框架不需要知道任何事.

结构总览 (对齐 model / tool / agent 包惯例):

- app.py       应用工厂 create_app: 一个端点 (POST /runs) + 两条接缝 + 一套收尾
- sse.py       事件流 → SSE: 字段映射 (纯函数) + 响应体生成器 (收尾时叫停没人听的运行)
- sessions.py   会话登记表 + 事件路由: 会话按 thread_id 长驻, 同一会话不并发跑
- runs.py       一次运行的流与在册: 事件队列 + 序号记账 + 可取消的任务句柄
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
            return RunContext(thread_id=f"my:{user_id}:1", payload={"user_id": user_id})

    class MySessions:                       # 插座二: 上下文 → 会话
        async def provide(self, context, *, event_sink):
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
RunContext 与 client 的 ChatSession 当装配产物 —— 它不碰 loop / checkpoint /
db 的内部, 也不改它们任何一行.
"""

from __future__ import annotations

from CharAgent.server.app import create_app
from CharAgent.server.utils.errors import (
    InvalidRequestError,
    ServerAuthError,
    ServerConfigError,
    ServerError,
    ThreadBusyError,
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
    "MESSAGE_FIELD",
    "RUN_FAILED_CODE",
    "RUN_ID_HEADER",
    "SSE_MEDIA_TYPE",
    "ContextProvider",
    "InvalidRequestError",
    "ServerAuthError",
    "ServerConfigError",
    "ServerError",
    "SessionProvider",
    "ThreadBusyError",
    "create_app",
]
