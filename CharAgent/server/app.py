"""应用工厂: 一个 HTTP 端点, 两条接缝, 一套收尾.

一句话理解: 客户端问一句话 (`POST /runs`), 服务把这次问答的流式事件推回去
(SSE); 中间两处属于业务 (认证解析 / 会话装配), 框架只认它们的结果. 框架仍然
不知道业务的存在 —— 这一页里没有一个字提到具体业务.

请求 → 响应 (每一步谁负责, 一眼看清)::

    POST /runs  {"message": "..."}
      │
      ├─ 1. ContextProvider.provide(request)      <- 业务: 认证 + 解析
      │      认证失败抛 ServerAuthError → 401/403 的 JSON (不是 traceback)
      │
      ├─ 2. 读 MESSAGE_FIELD                      <- 框架: HTTP 契约
      │      不成形 → 400 (缺字段 / 不是 JSON)
      │
      ├─ 3. SessionRegistry.acquire(context)      <- 框架: 会话登记
      │      同一 thread_id 已有运行 → 409 (不并发写)
      │      第一次见 → SessionProvider.provide(context, event_sink=...) <- 业务: 装配
      │
      └─ 4. 起任务 + 把事件流成 SSE               <- 框架
             StreamingResponse (media_type=text/event-stream)
             响应头 X-Run-Id: 本次运行的编号 (取消与排查靠它)

响应体是 SSE 帧 (见 sse.py 的字段映射). 响应**不会**中途静默断掉: 正常结束有
final, 失败与取消有本层补的 error (见 runs.py), 之后流才收线.

取消的契约: 一次运行被取消时, 本层补一个 `error` 事件, 载荷为
`{"error": {"code": "cancelled", "message": <事实性说明>}}` —— 形状与 loop 自己发的
终局 error 完全一致, 客户端一套解析吃两边; 之后不再有任何事件 (终局事件恰好一个).
触发源有两个 (显式取消请求 / 客户端断连), 事件流里长得一样.

触发取消的端点长这样::

    POST /runs/{run_id}/cancel
      ├─ 1. ContextProvider.provide(request)   <- 与 POST /runs **同一道门**
      ├─ 2. 查在册: 跑完了 / 没这个编号 / 不是这段会话 → 一律 404 (见 RunNotFoundError)
      └─ 3. task.cancel() → 200 {"run_id", "status": "cancelling"}

三件事在这里定死, 客户端按它写:

- **身份由业务认, 判据是「这次运行算不算他那段会话」.** 取消端点复用第一个插座
  (同一个 ContextProvider), 于是令牌 / 身份 / 会话编号的规则与 `/runs` 一字不差;
  比对的是登记时记下的 `RunHandle.thread_id`, 请求里**没有任何参数**能影响它 ——
  这就是「不能取消别人的运行」的全部实现.
- **200 只说「请求收到了」.** 取消是协作式的 (落在下一个 await), 真正停下来的权威
  信号是那条事件流上的终局事件 —— 客户端等的是它, 而不是这个响应.
- **三种「不在册」共用一个回答** (404 + `run_not_found`): 本层分不出来 (跑完即
  出册), 也不该分 (403 会确认「这个编号真实存在过」). 理由与那条纪律同源, 见
  RunNotFoundError.

三条边界:

- **框架不写用户文案**: 补发的 error 只陈述事实 (错误码 + 异常说明), 面向用户的
  话术由业务按 code 决定 (与 stream 层同一条规矩).
- **框架不开 CORS**: 本层面向服务端 (上游转发层), 不直接面向浏览器; 业务要直连
  浏览器, 自己加中间件.
- **框架只管这次运行的登记与收尾**: 会话里的模型与存储是业务建的, 由业务在进程
  退出时自己关 (见 sessions.py「谁建谁关」).
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response, StreamingResponse

from CharAgent.server.runs import (
    RunHandle,
    RunRegistry,
    RunStream,
    close_stream,
    new_run_id,
)
from CharAgent.server.sessions import SessionEntry, SessionRegistry
from CharAgent.server.sse import sse_stream
from CharAgent.server.utils.errors import (
    InvalidRequestError,
    RunNotFoundError,
    ServerError,
)
from CharAgent.server.utils.types import (
    MESSAGE_FIELD,
    RUN_ID_HEADER,
    SSE_MEDIA_TYPE,
    ContextProvider,
    SessionProvider,
)

# 事件流不该被任何一层缓存 (中间代理缓存 SSE 会把它变成"跑完才到")
SSE_HEADERS = {"cache-control": "no-cache"}

# 同一棵日志树 (`runs.py` 那条说明适用): 框架只在自己**没有调用方可以上抛**的
# 那几个点上说话, 这里之所以算一个, 是因为越界的取消请求不该悄悄过去 (见 cancel_run).
logger = logging.getLogger("charagent.server")


def create_app(
    *,
    context_provider: ContextProvider,
    session_provider: SessionProvider,
) -> FastAPI:
    """装配一个 agent 服务应用 (业务拿到 app 自己决定怎么跑).

    为什么是工厂而不是自己起服务: 什么时候起、跑在哪个端口、单进程还是多进程,
    都是业务 (或部署) 的决定; 框架只负责把 app 装配好 —— 测试里直接喂给 ASGI
    客户端, 生产里交给 uvicorn, 两条路同一个 app.

    Args:
        context_provider: 第一个插座 (HTTP 请求 → RunContext).
        session_provider: 第二个插座 (RunContext → ChatSession).

    Returns:
        FastAPI: 装好的应用. 业务可以再往上加自己的路由与中间件 (框架只占
        `POST /runs` 与 `POST /runs/{run_id}/cancel` 两个端点).
    """
    registry = SessionRegistry(session_provider)
    runs = RunRegistry()

    app = FastAPI()
    # 两张表挂到 app.state 上: 排查时看得见「现在有哪些会话 / 哪些运行在跑」,
    # 测试也据此断言「登记了 / 放开了」(框架内部状态, 不是给业务改的接口).
    app.state.session_registry = registry
    app.state.run_registry = runs
    # server 层错误一律走这一处翻译 (业务抛的与框架抛的同一套规则)
    app.add_exception_handler(ServerError, _server_error_response)

    @app.post("/runs", response_class=StreamingResponse)
    async def start_run(request: Request) -> Response:
        """起一次运行, 把事件流推回去.

        每一步的顺序都是有意的: 先认证 (不认识的人不该看见任何业务信息), 再读
        问句, 最后才占会话 —— 占会话是个有副作用的动作 (可能触发建会话), 不该
        在读不懂的请求上发生.
        """
        context = await context_provider.provide(request)
        message = await read_message(request)
        entry = await registry.acquire(context)
        # 放开时用的键要与占住时**同一个**: 会话是业务建的, 它的 thread_id 未必
        # 与这次运行的上下文一致 (业务写错了也不该把这段会话永久占住)
        thread_id = context.thread_id

        run_id = new_run_id()
        stream = RunStream(run_id)
        entry.router.bind(stream)
        task = asyncio.create_task(
            # 任务名带上 run_id: 排查时能在任务列表里一眼找到是哪次运行
            entry.session.ask(message),
            name=f"charagent-run:{run_id}",
        )
        runs.register(RunHandle(run_id=run_id, thread_id=thread_id, task=task))
        # 收尾挂在任务自己的收尾回调上, 而不是响应生成器上: 无论这次运行是正常
        # 结束 / 失败 / 被取消 (含"任务还没开跑就被取消"这种边角), 回调都恰好跑
        # 一次 —— 会话不会被永久占住, 登记表里也不会留下幽灵条目.
        task.add_done_callback(
            partial(
                _finish_run,
                entry=entry,
                thread_id=thread_id,
                stream=stream,
                registry=registry,
                runs=runs,
            )
        )

        return StreamingResponse(
            sse_stream(stream, task),
            media_type=SSE_MEDIA_TYPE,
            headers={RUN_ID_HEADER: run_id, **SSE_HEADERS},
        )

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request) -> JSONResponse:
        """停一次正在跑的运行 (显式取消 —— 与「客户端断连」那条路并肩的另一条).

        为什么要有这个端点 (断连不是已经能停了吗): 用户点「停止」是一个**明确的
        意图**, 得有一个明确的地方接住它. 断连是「人走了」的副作用 (浏览器关标签页
        也会触发), 拿它当唯一入口等于让停止变成一件要靠猜的事 (见 ticket 07 备注).

        四步, 顺序有意:

        1. **先认证** —— 与 `POST /runs` 同一个插座. 取消是有影响力的动作 (它能
           让别人的钱白花), 不认识的人一个字都不该看到.
        2. **再查在册** —— 不在 / 已结束 → 404; 这里**不做**任何「猜一个让他取消」
           的好心兜底.
        3. **对身份** —— 这次运行登记时算的是哪段会话 (`RunHandle.thread_id`) 与
           这次请求认出来的那段会话必须是同一个. 判据只有这一个, 请求体里没有
           任何字段能影响它 —— 「不能取消别人的运行」就是这么兑现的.
        4. **`task.cancel()`** —— 取消就是这一下 (与客户端 Ctrl-C 走的同一套语义,
           见 `client/app.py` 的 KillSwitch). 之后不再等: 运行怎么收尾是它自己的
           事, 客户端等的是**事件流上的终局事件**, 不是这个响应.

        Returns:
            JSONResponse: 200 + `{"run_id", "status": "cancelling"}` —— 只声明
            「请求收到了」, 不声明「已经停了」(那是事件流说了算的).

        Raises:
            ServerAuthError: 业务那个插座没认下这次请求 (框架翻成 401).
            RunNotFoundError: 不在册 / 已结束 / 不属于这段会话 (框架翻成 404).
        """
        context = await context_provider.provide(request)
        handle = runs.get(run_id)
        if handle is not None and handle.thread_id != context.thread_id:
            # 这里**认得出**是越界 (另外两种「不在册」认不出, 见 _not_running_message):
            # 一次跨会话的取消尝试值得留一笔 —— 它是这一层唯一一条安全信号, 而
            # 401 (令牌不对) 与它完全不是一回事, 不该混在一条日志里.
            logger.warning(
                "取消被拒: 运行 %s 属于会话 %r, 而这次请求的身份是 %r",
                run_id,
                handle.thread_id,
                context.thread_id,
            )
            raise RunNotFoundError(_not_running_message(run_id))
        if handle is None or handle.task.done():
            # 任务的收尾回调是「稍后」跑的 (call_soon), 所以出册比任务结束晚一瞬 ——
            # 这一小段窗口里表里还有它, 却已经停稳了: 只认「还在跑的」, 别去 cancel
            # 一个已经结束的任务 (那一下什么也不会发生, 但回一个 200 就成了假话).
            raise RunNotFoundError(_not_running_message(run_id))

        handle.task.cancel()
        return JSONResponse({"run_id": run_id, "status": "cancelling"})

    return app


def _not_running_message(run_id: str) -> str:
    """「这次运行不在册」那一句事实 —— 三种情况**共用同一条文本**.

    刻意合成一个函数 (而不是各处写一句差不多的话): 三种情况的响应体只有回显的编号
    不同, 其余逐字相同 —— 这是**契约** (见 RunNotFoundError), 而契约由构造保证比
    由「记得写一样」保证牢.
    """
    return (
        f"运行 {run_id!r} 不在册: 它可能已经结束, 可能不属于这次请求的那段会话, "
        f"也可能压根没有这个编号 —— 本层刻意不区分这三种"
    )


async def read_message(request: Request) -> str:
    """从请求体里取「用户问的那句话」(框架 HTTP 契约里唯一认识的字段).

    业务要用请求体里的别的数据 (会话 ID / 页面来源...) 自己读 —— body 只会被
    真正解析一次, 谁先读都一样.

    Raises:
        InvalidRequestError: 不是 JSON 对象 / 缺字段 / 字段不是非空字符串.
    """
    try:
        payload = await request.json()
    except ValueError as exc:  # 覆盖 JSONDecodeError 与编码错误 (都是 ValueError)
        raise InvalidRequestError(f"请求体不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidRequestError(
            f"请求体应为 JSON 对象, 收到 {type(payload).__name__}"
        )
    message = payload.get(MESSAGE_FIELD)
    if not isinstance(message, str) or not message.strip():
        raise InvalidRequestError(f"请求体缺少非空字符串字段 {MESSAGE_FIELD!r}")
    return message


def _server_error_response(request: Request, exc: ServerError) -> JSONResponse:
    """ServerError → 干净的 JSON 响应 (状态码与错误码都是错误自己带的).

    响应体形状与事件流的 error 载荷一致 (`{"error": {"code", "message"}}`):
    同一次失败在两条通道上长得一样, 客户端一套解析吃两边.

    只注册给 ServerError 一族 (`add_exception_handler(ServerError, ...)`),
    所以这里收到的异常一定是这一类; 别的一律照旧上抛 —— 那是 bug, 该留 traceback.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


def _finish_run(
    task: asyncio.Task[None],
    *,
    entry: SessionEntry,
    thread_id: str,
    stream: RunStream,
    registry: SessionRegistry,
    runs: RunRegistry,
) -> None:
    """任务收尾 (正常 / 失败 / 取消都恰好一次): 关流 + 解绑路由 + 会话放开 + 运行出册.

    四件事都是同步的, 也都不需要 await —— 收尾路径上多一个 await 就多一处可能
    被再次取消的地方.

    为什么不等响应生成器来收 (两处都在收尾, 只相信一个): 生成器是**消费端**,
    它可能压根没被启动 (客户端在响应头发出去之前就走了), 也可能不被关闭 (硬断连
    时 Starlette 直接抛 ClientDisconnect, 不等生成器收尾). 任务自己的收尾回调
    没有这两个漏洞 —— 运行结束它就响, 连带把事件流关掉 (close_stream).

    三件登记动作包在 `finally` 里: 关流那一步万一炸了 (它在跟异常对象打交道),
    会话也不能被永久占住、登记表里也不能留幽灵条目 —— 这些是**生命周期的保证**,
    优先级高于「把错误原样传上去」. 回调里的异常仍然会冒出来被 asyncio 打出来.
    """
    try:
        close_stream(task, stream)
    finally:
        entry.router.unbind(stream)
        registry.release(thread_id)
        runs.finish(stream.run_id)
