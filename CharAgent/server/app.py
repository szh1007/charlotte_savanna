"""应用工厂: 三条 HTTP 路 (跑一次 / 停一次 / 读历史) + 一条可选的 (列会话),
两条接缝, 一套收尾.

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

读历史那条路是另一件小事 (同一道门, 只读)::

    GET /history
      ├─ 1. ContextProvider.provide(request)   <- 与上两条**同一道门**
      └─ 2. 查登记表 → 200 {"thread_id", "messages": [{role, content}, ...],
                            "pending_approval": {...} | null}
            没聊过的会话编号 → 空列表 (不是 404: 那是「还没聊过」)

它存在的理由只有一个: 浏览器刷新会把页面那一份渲染丢光, 得有个地方把聊过的话
再取一遍. **取哪儿由装配决定** (见 create_app 的 `database`) —— 给了库就读记录表
(那是给人看的那份**持久**记录, 重启之后照样在, 见 db/recorder.py), 没给就读会话
对象手上那份内存里的历史. 两条来源**不互相兜底** (理由见 history.py): 同一段对话
刷新两次看到不一样的东西, 是最难查的一类 bug.

`pending_approval` 是 issue 34 加的那一块: 有任何**未决挂起**时, 前端靠它把那张
确认卡**重建**出来 (刷新之后卡消失 = 用户永远完不成那次代付). 判据是库里那一行
(`status = needs_approval AND approved_at IS NULL`), 不是本进程的内存 —— 挂起跨得了
重启. 没给库的装配里它恒为 null (那种装配里挂起态本来就没有家).

本路由不建会话、不占会话、不产生任何运行.

列会话那条路与它同源, 只是问的是另一个问题 (「我聊过哪几段」而不是「这段聊了
什么」)::

    GET /conversations[?q=<可选>&limit=<可选>]
      ├─ 1. ContextProvider.provide(request)   <- 与上两条**同一道门**
      └─ 2. 按 (tenant_id, user_id) 查记录表的会话行 → 200 {"conversations": [...]}
            只给「还活着、聊过话、没被删」的, 置顶的在前; 给了 q 就再筛一层

**再加一条路**: 给人对一次挂起的结论, 让那次运行接着跑 (issue 34)::

    POST /runs/{run_id}/resume  {"decision": "approve" | "reject", "data": {...}}
      ├─ 1. ContextProvider.provide(request)   <- 与另两条**同一道门**
      ├─ 2. 读 decision (只认 approve / reject) 与可选的一次性载荷 (400)
      ├─ 3. 查这一次运行有没有未决的挂起 → 没有: 404 (与取消那条同源: 不区分几种
      │      「不在」的情形)
      ├─ 4. **幂等**: 逐条认领 (运行, 消息, 调用) 那把键 —— 已有 409 (在办 / 已办),
      │      双击与重发在这里被挡住 (这不是兜底, 是主线: 恢复动的是钱)
      ├─ 5. 记下「谁在什么时候给的结论」+ 把这次运行的载荷组好
      └─ 6. 占住会话 (resuming=True) + 起任务 + 流成 SSE (与 POST /runs 同一条收尾)

它**与 POST /runs 同构** (同一道身份门、同样的 SSE、同一条收尾路径), 三处不同:

- **不拆成 approve / reject 两个端点**: 拒绝**也要恢复** —— 拒绝原因当作那条工具
  调用的结果回填, 模型据此继续答 (它与「当场拒绝」是两条路, 见 CONTEXT.md 的
  「恢复」词条). 拆成两个端点, 「恢复」这件事就有两处实现, 而它们只差一行.
- **要幂等**: 那一次恢复补做的可能就是「给这一单付款」, 而双击 / 前端重试 / 断线
  重连都会产生第二次请求. 键是 `(运行, 那一条 assistant 消息, 那一次调用)` ——
  三列, 少一列在多轮之间会撞 (上游每轮从 `call_0` 重新编号).
- **`data` 会进本次运行的载荷**: `approve` 时它并进 `RunContext.payload` (与
  `X-User-Id` 完全同一条路, 业务自己取), 于是「一次性载荷」(如支付密码, ADR-0015)
  到得了工具手里; 为此会话会被**重新装配**一次 —— 工具是装配时闭包捕获出来的,
  复用上一段那个会话等于把这份载荷丢掉 (见 `SessionRegistry.acquire` 的 resuming).

另有三条写动作挂在同一段前缀下 (#20), 都是 POST + `X-Conversation-Id` 头::

    POST /conversations/title   {"title": "..."}      改名
    POST /conversations/pin     {"pinned": true|false} 置顶 / 取消置顶
    POST /conversations/delete                         软删

它们的归属判据不在这一层 —— 仓储那三个写方法把 (tenant_id, user_id) 写进了
WHERE, 一行都没命中就是 404 (`ThreadNotFoundError`); 于是「改别人的会话」在这
一层连一条分支都不需要.

**它只在装配时给了库才存在** (没给 = 这条路由不注册, 404): 会话列表没有「内存里
的版本」可言 —— 登记表里只有**这个进程**见过的那几段对话, 拿它当列表等于把
「我聊过哪几段」答成「这个进程见过哪几段」. 与其给一个会骗人的答案, 不如这条路由
压根不存在 (与框架「不配不改行为」那条纪律同源).

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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response, StreamingResponse

from CharAgent.agent import Approval, RunContext
from CharAgent.db.entities import RunStatus, ToolCall, ToolCallStatus
from CharAgent.db.errors import DbError
from CharAgent.db.repositories.base import Database
from CharAgent.db.repositories.idempotency import PgIdempotencyStore
from CharAgent.db.repositories.messages import MessagesRepository
from CharAgent.db.repositories.runs import RunsRepository
from CharAgent.db.repositories.threads import (
    DEFAULT_LIST_LIMIT,
    ThreadsRepository,
)
from CharAgent.db.repositories.tool_calls import ToolCallsRepository
from CharAgent.retry.idempotency import IdempotencyKey, IdempotencyStore
from CharAgent.retry.utils.types import ClaimStatus
from CharAgent.server.conversations import (
    CONVERSATION_ID_FIELD,
    CONVERSATIONS_FIELD,
    CONVERSATIONS_PATH,
    DELETE_PATH,
    DELETED_FIELD,
    LIMIT_QUERY,
    MAX_TITLE_LENGTH,
    PIN_PATH,
    PINNED_FIELD,
    QUERY_QUERY,
    TITLE_FIELD,
    TITLE_PATH,
    conversation_id_of,
    conversation_row,
)
from CharAgent.server.history import (
    HISTORY_PATH,
    MESSAGES_FIELD,
    PENDING_APPROVAL_FIELD,
    THREAD_ID_FIELD,
    conversation_messages,
    conversation_of,
    pending_approval_row,
)
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
    ApprovalAlreadyHandledError,
    InvalidRequestError,
    RunNotFoundError,
    ServerError,
    ThreadNotFoundError,
)
from CharAgent.server.utils.types import (
    MESSAGE_FIELD,
    RUN_ID_HEADER,
    SSE_MEDIA_TYPE,
    ContextProvider,
    SessionProvider,
)

# 恢复那条路 (与 POST /runs 并肩: 一条起新问题, 一条给旧挂起一个结论)
RESUME_PATH = "/runs/{run_id}/resume"

# 恢复请求体里的三个字段: 人的结论 + 可选的说明 + 可选的一次性载荷
DECISION_FIELD = "decision"
DATA_FIELD = "data"
REASON_FIELD = "reason"
APPROVE_DECISION = "approve"
REJECT_DECISION = "reject"

# 事件流不该被任何一层缓存 (中间代理缓存 SSE 会把它变成"跑完才到")
SSE_HEADERS = {"cache-control": "no-cache"}

# 收尾派出去的那几个后台任务 (结幂等键那种): 留着引用, 免得被垃圾回收掉 ——
# 一个被回收的收尾任务会**静默消失** (asyncio 只给一条警告), 而它要写的那笔账
# 就永远结不了. 做完自己从集合里出去.
_pending_tasks: set[asyncio.Task[None]] = set()

# 同一棵日志树 (`runs.py` 那条说明适用): 框架只在自己**没有调用方可以上抛**的
# 那几个点上说话, 这里之所以算一个, 是因为越界的取消请求不该悄悄过去 (见 cancel_run).
logger = logging.getLogger("charagent.server")


def create_app(
    *,
    context_provider: ContextProvider,
    session_provider: SessionProvider,
    database: Database | None = None,
    idempotency: IdempotencyStore | None = None,
) -> FastAPI:
    """装配一个 agent 服务应用 (业务拿到 app 自己决定怎么跑).

    为什么是工厂而不是自己起服务: 什么时候起、跑在哪个端口、单进程还是多进程,
    都是业务 (或部署) 的决定; 框架只负责把 app 装配好 —— 测试里直接喂给 ASGI
    客户端, 生产里交给 uvicorn, 两条路同一个 app.

    Args:
        context_provider: 第一个插座 (HTTP 请求 → RunContext).
        session_provider: 第二个插座 (RunContext → ChatSession).
        database: 记录表那条线的入口 (`db.Database`: 能开一次事务就行). 给了它,
            `GET /history` 就读记录表, 并且多出 `GET /conversations` 这条路由;
            None (默认) 表示这个应用没有记录层 —— `/history` 读会话内存, 会话列表
            那条路由**不注册**. 一次装配选定一个来源, 运行期不互相兜底 (理由见
            history.py). 另外: **快照后端用 Postgres 时它还是写帧的前置**
            (帧的 `thread_id` 指向记录层的 `charagent_threads`, ticket 24) ——
            这种组合下不给它就没人建那一行, 第一帧会以外键失败告终.
            **审批恢复那条路也要它**: 挂起态的家在 `charagent_tool_calls`
            (ADR-0014), 没有库就没有那个家, 于是 `POST /runs/{id}/resume` 这条
            路由**不注册** (与 `GET /conversations` 同一条规矩: 与其给一个骗人的
            答案, 不如这条路由压根不存在).
        idempotency: 恢复那条路的幂等登记簿 (issue 32 的那两个实现之一). None
            (默认) 表示「有库就用 `PgIdempotencyStore`」—— 那次恢复补做的可能是
            「给这一单付款」, 而两次恢复会跨进程 (关掉浏览器隔天再点), 所以默认
            落在库里而不是进程内. 传进来是给**测试**用的 (进程内实现零依赖).

    Returns:
        FastAPI: 装好的应用. 业务可以再往上加自己的路由与中间件 (框架占
        `POST /runs` / `POST /runs/{run_id}/cancel` / `GET /history` 三条路,
        给了 `database` 再也一条 `POST /runs/{run_id}/resume` 与几条会话路由).
    """
    # 挂起那一道闸门的判据: 这段会话还有没有未决的挂起 (要查库, 见 sessions.py)
    calls_repo = None if database is None else ToolCallsRepository(database)
    runs_repo = None if database is None else RunsRepository(database)
    approvals = None if calls_repo is None else _pending_approvals_of(calls_repo)
    registry = SessionRegistry(session_provider, suspended=approvals)
    runs = RunRegistry()
    # 记录表那条线: 没给库就是 None, 于是下面两处各自退回「没有记录层」的行为
    # (历史读会话内存 / 会话列表这条路由不注册) —— 与从前逐字一样
    messages_repo = None if database is None else MessagesRepository(database)
    threads_repo = None if database is None else ThreadsRepository(database)
    replay_guard: IdempotencyStore | None = idempotency
    if replay_guard is None and database is not None:
        replay_guard = PgIdempotencyStore(database)

    app = FastAPI()
    # 两张表挂到 app.state 上: 排查时看得见「现在有哪些会话 / 哪些运行在跑」,
    # 测试也据此断言「登记了 / 放开了」(框架内部状态, 不是给业务改的接口).
    app.state.session_registry = registry
    app.state.run_registry = runs
    # server 层错误一律走这一处翻译 (业务抛的与框架抛的同一套规则)
    app.add_exception_handler(ServerError, _server_error_response)
    if database is not None:
        # 只读那两条路读的是库, 而库连不上是**可用性**故障不是 bug: 不翻译的话它会
        # 以未处理异常的形式冒出去 (客户端拿到一个没有 code 的 500, 转发方只能猜).
        # 只在这条装配线存在时注册: 没给库的 app 里没有一处会碰库
        app.add_exception_handler(DbError, _record_store_error_response)

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
            **挂起中的运行走另一条路** (issue 34): 它没有任务可取消 (那次 HTTP
           请求早就结束了), 要收的是库里那两行 —— 见下面那一步.
        4. **`task.cancel()`** —— 取消就是这一下 (与客户端 Ctrl-C 走的同一套语义,
           见 `client/app.py` 的 KillSwitch). 之后不再等: 运行怎么收尾是它自己的
           事, 客户端等的是**事件流上的终局事件**, 不是这个响应.
           **挂起中**的运行改为: 那条等着人批的调用落成 `cancelled` + 那次运行从
           `waiting_user` 推进到 `cancelled` (状态机允许的那一步). 于是「这次挂起
           被收掉了」在库里成立, 用户之后可以正常接着聊 (新提问不再被 409 拦下).

        Returns:
            JSONResponse: 200 + `{"run_id", "status"}` —— 正在跑的是 `cancelling`
            (只声明「请求收到了」, 不声明「已经停了」: 那是事件流说了算的); 挂起中
            的是 `cancelled` (那一下是**当场**写完的, 没有事件流可等).

        Raises:
            ServerAuthError: 业务那个插座没认下这次请求 (框架翻成 401).
            RunNotFoundError: 不在册 / 已结束 / 没有未决挂起 / 不属于这段会话
                (框架翻成 404).
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
            if await _cancel_suspension(
                run_id,
                context=context,
                calls=calls_repo,
                runs=runs_repo,
                registry=registry,
            ):
                return JSONResponse({"run_id": run_id, "status": "cancelled"})
            raise RunNotFoundError(_not_running_message(run_id))

        handle.task.cancel()
        return JSONResponse({"run_id": run_id, "status": "cancelling"})

    if calls_repo is not None and runs_repo is not None and replay_guard is not None:

        @app.post(RESUME_PATH, response_class=StreamingResponse)
        async def resume_run(run_id: str, request: Request) -> Response:
            """给一次挂起一个结论, 让那次运行接着跑 (SSE, 与 `POST /runs` 同构).

            七步, 顺序有意 (**认领键在前, 占会话在后**):

            1. **先认证** —— 同一道门. 审批是有影响力的动作 (它决定一笔钱付不付),
               不认识的人一个字都不该看到.
            2. **再读请求体** —— `decision` 只认 `approve` / `reject`; 看不懂就
               400, 不替它猜一个 (猜错了就是替人做了一次决定).
            3. **查未决挂起** —— 这一次运行有哪几条调用在等人批; 一条都没有 → 404
               (跑完了 / 压根没挂起过 / 不是这段会话的, 三种**共用同一条回答**, 与
               取消那条同源: 本层分不出来, 也不该分).
            4. **认领幂等键** —— 每个 `(运行, 消息, 调用)` 一把. 抢不到 → 409:
               在办 (`approval_in_progress`) 或已办 (`approval_already_applied`).
               **这一步是主线不是兜底**: 双击的第二下、前端重发、断线重连都会到这儿,
               而那一次恢复补做的可能是「给这一单付款」.
               **为什么认领排在占会话之前**: 「这一次审批是不是有人在办」该由那把键
               回答 —— 它是**跨进程**的判据 (重启之后内存里那个「忙」字早没了, 而键
               还在库里). 反过来说, 会话忙不忙只是本进程的一件事, 拿它当这道闸门
               会把「另一个进程正在处理这次审批」漏过去.
               **不配 TTL 是刻意的**: 一把会过期的键等于「过一会儿可以再点一次」,
               而重放的是一笔付款. 真卡住了 (进程死在恢复的半路) 有另一条明路 ——
               取消这次挂起重新发起 (`POST /runs/{id}/cancel`), 那条路上用户知道
               自己在做什么.
            5. **组载荷** —— `approve` 时 `data` 并进本次运行的上下文 (与 `X-User-Id`
               同一条路: 框架不解释它, 原样交给业务的装配).
            6. **占住会话 (resuming=True)** —— 跳过「有没有未决挂起」那道闸门
               (我们正是在解决它), 并让业务用**这次的上下文**重新装配一次会话
               (一次性载荷要进工具的闭包).
               这一步**可能失败** (会话正忙 / 业务装配报错), 而上面那把键已经到手了
               —— 失败时把它**放回去** (`_release_claims`), 否则这次挂起会被一把
               没人管的键永久锁死.
            7. **起任务 + 流成 SSE** —— 与 `POST /runs` 收尾同一条路 (任务自己的
               收尾回调: 关流 / 解绑 / 放开 / 出册), 另外多出两笔**等运行结束才结**
               的账 (见 `ApprovalBookkeeping`): 幂等键 (跑到头了标完成; 没跑成就释放,
               于是用户能重试这一次恢复) 与「谁批的 / 什么时候批的」那一对列
               (ADR-0014 —— 它是挂起那道闸门的判据, 所以**不能提前写**: 提前写了,
               这次恢复要是死在半路, 用户既恢复不了也取消不了).

            响应头同样带 `X-Run-Id` (就是路径里那个运行编号) —— 前端按它继续读流.

            **路径里那个 `run_id` 是记录层那一行** (前端从 `GET /history` 的
            `pending_approval.run_id` 取), 不是事件流里那个进程内编号 —— 两个编号
            在框架里本来就是两回事 (见 `server/runs.py` 的 `new_run_id`): 前者是
            「哪一次运行」, 后者是「这一次 HTTP 请求推的这条流」. 拿错了会 404
            (`没有等着人确认的调用`), 不会做错事.

            Returns:
                StreamingResponse: 这次恢复的事件流 (终局事件照旧恰好一个).

            Raises:
                ServerAuthError: 业务那个插座没认下这次请求 (框架翻成 401).
                InvalidRequestError: 请求体不成形 / `decision` 不是那两个值 (400).
                RunNotFoundError: 这次运行没有未决的挂起 (框架翻成 404).
                ApprovalAlreadyHandledError: 这一份审批已经有人在办 / 办完了 (409).
                ThreadBusyError: 这段会话正有另一次运行在跑 (框架翻成 409).
            """
            context = await context_provider.provide(request)
            approval, data = await read_approval(request)
            pending = [
                row
                for row in await calls_repo.list_pending_approvals(context.thread_id)
                if row.run_id == run_id
            ]
            if not pending:
                raise RunNotFoundError(_not_pending_message(run_id))
            # 一次性载荷并进本次运行的上下文 (与 X-User-Id 同一条路: 框架不解释它,
            # 原样交给业务的装配). 只并 approve 的 —— 拒绝那一路没有东西要给工具.
            #
            # **只增不覆盖**: `data` 在后写会顶掉上下文里同名的键, 而那份上下文里装着
            # 业务自己的事实 —— "谁在说话"就在里面 (业务侧取身份正是从载荷里读的).
            # 而这一袋 `data` 来自**恢复请求的正文**, 也就是客户端: 谁能覆盖那个键,
            # 谁就能把这一趟换成别人的身份去查数据, 而答复还流回他自己页面上.
            # 一次性的东西只该**补上缺的那些**, 已有的键 (谁 / 哪一段会话) 一律以本次
            # 运行为准.
            if approval.approved and data:
                context = replace(context, payload={**data, **context.payload})
            # 认领在前, 占会话在后: 「这一次审批有人在办」该由**那一把键**回答
            # (它是跨进程的判据), 而不是由「会话忙不忙」顺带答一句. 代价是认领之后
            # 还有可能失败 (会话忙 / 装配出错) —— 那一笔必须**放回去**, 否则这把键
            # 永久卡在「在办」, 用户既恢复不了也不知道为什么 (见下面的 except)
            keys = tuple(_approval_key(row) for row in pending)
            claimed: list[IdempotencyKey] = []
            try:
                for key in keys:
                    await _claim_or_refuse(replay_guard, key)
                    claimed.append(key)
                entry = await registry.acquire(context, resuming=True)
            except BaseException:
                await _release_claims(replay_guard, claimed)
                raise
            thread_id = context.thread_id

            stream = RunStream(run_id)
            entry.router.bind(stream)
            task = asyncio.create_task(
                # 任务名带上 run_id: 排查时能在任务列表里一眼找到是哪次运行
                entry.session.resume(run_id=run_id, approval=approval),
                name=f"charagent-resume:{run_id}",
            )
            runs.register(RunHandle(run_id=run_id, thread_id=thread_id, task=task))
            task.add_done_callback(
                partial(
                    _finish_run,
                    entry=entry,
                    thread_id=thread_id,
                    stream=stream,
                    registry=registry,
                    runs=runs,
                    # 这两笔账都要等运行结束才知道怎么结 (见 ApprovalBookkeeping)
                    bookkeeping=ApprovalBookkeeping(
                        guard=replay_guard,
                        keys=keys,
                        calls=calls_repo,
                        approvals=tuple(pending),
                        decided_by=context.user_id if approval.approved else None,
                    ),
                )
            )

            return StreamingResponse(
                sse_stream(stream, task),
                media_type=SSE_MEDIA_TYPE,
                headers={RUN_ID_HEADER: run_id, **SSE_HEADERS},
            )

    @app.get(HISTORY_PATH)
    async def session_history(request: Request) -> JSONResponse:
        """读一段会话聊过什么 (只读 —— 与 `POST /runs` 是同一道门下的两条路).

        为什么要有它: 会话活在**进程内存**里 (登记表按 thread_id 长驻), 而浏览器
        刷新会把页面那一份渲染丢光 —— 没有这个读口, 用户看到的就是「我刚说的话
        不见了」. 取的是会话对象手上那份历史, 不是快照: 快照是断点续跑用的
        (`resume` 走那条路), 而这里问的是「这段对话现在聊到哪儿了」.

        四件事按顺序说清:

        1. **认证走同一个插座** —— 与 `/runs` 一字不差 (`context_provider.provide`).
           于是「拉谁的对话」这件事只有一条判据: 请求里认出来的 `thread_id`. 请求体
           与查询串都影响不了它 —— 换一个身份就换一段对话, 拿不到别人的.
        2. **只读**: 本路由不建会话, 不占会话, 不产生任何运行. 没聊过的对话编号
           回一个**空的** `messages` —— 那是「还没聊过」, 不是错误 (第一次打开页面
           就是这种情形, 不该让调用方分辨「404 还是空」).
        3. **取数与过滤都在 `history.py` 里** (哪个来源、哪些字段能出去, 那里写明
           了理由) —— 本路由只负责取数与包信封. 来源由装配时的 `database` 定死,
           本路由**不做**「这个来源读不到就换另一个」的兜底 (理由见 history.py).
        4. **方法**: 只登记 GET. 同一个路径上的别的写意图 (POST / DELETE) 由框架的
           路由层回 405 + `Allow: GET`, 不必在这里手写一段拒绝 —— 但**要有用例钉住**
           (看起来像「什么都不做」, 其实是被别处的机制挡下了).
        5. **未决挂起一起带上** (issue 34): 有挂起时多一块 `pending_approval`, 前端
           靠它把确认卡**重建**出来 —— 刷新之后卡消失, 用户就永远完不成那次代付.
           判据在库里 (那一行 `needs_approval` 且还没批), 不在本进程内存.

        Returns:
            JSONResponse: 200 + `{"thread_id": ..., "messages": [...],
            "pending_approval": {...} | null}` (`pending_approval` 恒在, 没有时是
            null —— 字段恒定比「有时多一个」好消费).

        Raises:
            ServerAuthError: 业务那个插座没认下这次请求 (框架翻成 401).
        """
        context = await context_provider.provide(request)
        pending: dict[str, Any] | None = None
        if messages_repo is None:
            # 没有记录层: 读会话内存那份 (与从前一样), 过滤按角色
            entry = await registry.entry(context.thread_id)
            messages = [] if entry is None else conversation_of(entry.session.history)
        else:
            # 有记录层: 只读记录表 (已经按 hidden 过滤过), 读不到也不退回内存
            rows = await messages_repo.list_conversation(context.thread_id)
            messages = conversation_messages(rows)
            # 挂起那一块也来自库 (与「这段会话还在等着人批吗」同一个查询)
            pending = pending_approval_row(
                await calls_repo.list_pending_approvals(context.thread_id)
            )
        return JSONResponse(
            {
                THREAD_ID_FIELD: context.thread_id,
                MESSAGES_FIELD: messages,
                PENDING_APPROVAL_FIELD: pending,
            }
        )

    if threads_repo is not None:

        @app.get(CONVERSATIONS_PATH)
        async def list_conversations(request: Request) -> JSONResponse:
            """列「我聊过哪几段」(只读; 与另三条同一道门).

            判据只有两个, 而且**都只能从认证插座来**: 请求里认出来的 `tenant_id`
            与 `user_id`. 请求体与查询串影响不了它们 (查询串只认 `limit`) ——
            「看不到别人的会话」就是这么兑现的.

            Returns:
                JSONResponse: 200 + `{"conversations": [...]}`. 没聊过 = 空列表
                (与 `/history` 同一条: 「还没有」不是错误).

            Raises:
                ServerAuthError: 业务那个插座没认下这次请求 (框架翻成 401).
                InvalidRequestError: `limit` 不成形 (框架翻成 400).
            """
            context = await context_provider.provide(request)
            limit = read_limit(request)
            rows = await threads_repo.list_active_with_messages(
                context.tenant_id,
                user_id=context.user_id,
                query=read_query(request),
                limit=limit,
            )
            return JSONResponse(
                {CONVERSATIONS_FIELD: [conversation_row(row) for row in rows]}
            )

        async def _conversation_action(
            request: Request, change: Callable[[RunContext], Awaitable[dict | None]]
        ) -> JSONResponse:
            """三个管理动作的共同一半: 认证 → 落库 → 没改到就 404 → 回显.

            顺序是有意的 (与 `start_run` 那条同源): **先认证** (不认识的人不该看见
            任何东西), 再读请求体与落库, 最后才拼响应.

            `change` 由各自的路由给: 它读自己的请求体、调自己的仓储方法, 成功了返回
            「要在响应里回显的那几个字段」, **没改到就返回 None**. 「没改到」= 这段
            会话不存在, 或者不是这次认证出来的那个人的 —— 两种情况**都回 404**,
            不区分 (见 `ThreadNotFoundError`).

            做法收成一处而不是抄三遍: 归属判据 (仓储那张 WHERE) 与「没改到 → 404」
            是**同一件事**的两半, 分头写早晚有一条对不上 —— 而那种错不会报错, 只会
            让某个动作悄悄对别人的会话生效.
            """
            context = await context_provider.provide(request)
            echoed = await change(context)
            if echoed is None:
                raise ThreadNotFoundError(_thread_not_found_message(context.thread_id))
            return JSONResponse(
                {
                    CONVERSATION_ID_FIELD: conversation_id_of(context.thread_id),
                    **echoed,
                }
            )

        @app.post(TITLE_PATH)
        async def rename_conversation(request: Request) -> JSONResponse:
            """给一段会话改标题 (#20; 会话编号走 `X-Conversation-Id` 头).

            三件事按顺序: 认证 → 读标题 → 落库.

            **空标题拒掉** (400): 列表上那一行会变成空白, 看着像坏了. 长度也有上限
            (理由见 `MAX_TITLE_LENGTH`), 两条都在这一层卡 —— 仓储只管把值写进去.

            「没改到」= 这段会话不存在, 或者不是这次认证出来的那个人的: 两种情况
            **都回 404**, 不区分 (见 `ThreadNotFoundError`).

            Returns:
                JSONResponse: 200 + `{"conversation_id", "title"}` —— 回显改成了
                什么, 调用方不必再查一次.

            Raises:
                ServerAuthError: 业务那个插座没认下这次请求 (框架翻成 401).
                InvalidRequestError: 标题缺失 / 空 / 过长 (框架翻成 400).
                ThreadNotFoundError: 这段会话不在册 (框架翻成 404).
            """

            async def change(context: RunContext) -> dict | None:
                title = await read_title(request)
                renamed = await threads_repo.update_title(
                    context.thread_id,
                    title,
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                )
                return {TITLE_FIELD: title} if renamed else None

            return await _conversation_action(request, change)

        @app.post(PIN_PATH)
        async def pin_conversation(request: Request) -> JSONResponse:
            """置顶 / 取消置顶一段会话 (#20; 会话编号走 `X-Conversation-Id` 头).

            `pinned` 必须是**布尔**: 传字符串 `"true"` 会被拒 (400) 而不是被悄悄
            当成真 —— 「严格认类型」与 `read_message` 那条同源, 前端发错了要当场
            看得见, 而不是某天发现取消置顶怎么也取消不掉.

            Returns:
                JSONResponse: 200 + `{"conversation_id", "pinned"}`.

            Raises:
                ServerAuthError: 同上 (401).
                InvalidRequestError: 请求体不成形 / `pinned` 不是布尔 (400).
                ThreadNotFoundError: 这段会话不在册 (404).
            """

            async def change(context: RunContext) -> dict | None:
                pinned = await read_pinned(request)
                changed = await threads_repo.set_pinned(
                    context.thread_id,
                    pinned,
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                )
                return {PINNED_FIELD: pinned} if changed else None

            return await _conversation_action(request, change)

        @app.post(DELETE_PATH)
        async def delete_conversation(request: Request) -> JSONResponse:
            """把一段会话从用户的列表里删掉 (#20) —— **软删**, 行与消息都留着.

            不读请求体: 删哪一段由 `X-Conversation-Id` 头说了算, 没有第二个参数.
            重删**幂等** (再删一次照样 200) —— 判据是「命中了行」而不是「刚删的」
            (见 `ThreadsRepository.soft_delete`), 这里不必额外判断「是不是已经删过了」.

            Returns:
                JSONResponse: 200 + `{"conversation_id", "deleted": true}`.

            Raises:
                ServerAuthError: 同上 (401).
                ThreadNotFoundError: 这段会话不在册 (404).
            """

            async def change(context: RunContext) -> dict | None:
                deleted = await threads_repo.soft_delete(
                    context.thread_id,
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                )
                return {DELETED_FIELD: True} if deleted else None

            return await _conversation_action(request, change)

    return app


@dataclass(frozen=True, slots=True)
class ApprovalBookkeeping:
    """一次恢复**跑完之后**要结的两笔账: 幂等键 + 「谁批的」那一对列.

    为什么打成一个包: 它们同进同出, 而且**都要等运行结束才知道怎么结** —— 那一次
    恢复成了就标完成、记下是谁批的; 没成就把键释放掉 (用户该能重试), 而那一行**一个
    字都不动** (还挂着, 闸门也还拦着).

    为什么不在这之前就把 `approved_at` 写上 (「人已经点过确认了」): 那样一来, 那份
    挂起在库里就不再是「未决」, 而**这次恢复还没跑完** —— 进程要是死在半路, 用户
    既恢复不了 (没有未决的了) 也取消不了 (取消那条路也只认未决的), 那次挂起就成了
    一个谁也碰不到的死结. 现在这样, 跑了半路的恢复留下的是「还挂着 + 键在办」:
    他要么等一下重试 (键被释放之后), 要么走取消那条明路.

    attributes:
        guard: 这次认领幂等键的那个登记簿.
        keys: 认领到的那几把键 (一次挂起一般只有一把, 见 loop 的「一次只挂一条」).
        calls: 工具调用表的读写口 (记「谁批的」用它). **必填**: 这个包只在那条
            带库的恢复路由里造出来 —— 没有库就没有那条路由 (见 `create_app`).
        approvals: 这次要结的那几条挂起 (就是键对应的那几行).
        decided_by: 谁给的结论; **None = 这次是拒绝** —— 拒绝不写 `approved_by`
            (那一列的字面意思是「谁批的」, 而没有人批准过它; 拒绝本身记在状态与
            结果里, 见 `ToolCallsRepository.set_status`).
    """

    guard: IdempotencyStore
    keys: tuple[IdempotencyKey, ...]
    calls: ToolCallsRepository
    approvals: tuple[ToolCall, ...]
    decided_by: str | None = None


def _pending_approvals_of(
    calls: ToolCallsRepository,
) -> Callable[[str], Awaitable[bool]]:
    """造「这段会话有没有未决挂起」那个判据 (交给会话登记表当闸门).

    单独一个函数而不是就地写个 lambda: 判据要有**一个**出处 —— 会话登记表 (拦新
    提问) 与历史接口 (重建确认卡) 问的是同一个问题, 两处各写一遍迟早在某一边漏掉
    `approved_at IS NULL` 那一半.
    """

    async def suspended(thread_id: str) -> bool:
        return bool(await calls.list_pending_approvals(thread_id))

    return suspended


def _approval_key(row: ToolCall) -> IdempotencyKey:
    """一条未决的挂起 → 它那把幂等键.

    键是**三列**: 运行 + 发起它的那条 assistant 消息 + 模型给的那次调用编号.
    少一列在多轮之间会撞 —— 上游每轮都从 `call_0` 重新编号 (见 db/schema.py 那段
    「为什么主键要三列」).
    """
    return IdempotencyKey(f"resume:{row.run_id}:{row.message_id}:{row.tool_call_id}")


async def _release_claims(
    guard: IdempotencyStore, claimed: Sequence[IdempotencyKey]
) -> None:
    """把刚认领到手的键**放回去** (那一次恢复没能跑起来的补救).

    为什么要补这一手: 认领与占会话之间还隔着几步 (会话可能正忙、业务装配可能报错),
    而那几步失败时这一次根本没跑 —— 键却已经认领了. 不放回去的话, 那一把会永久
    卡在「在办」: 用户再点被 409 挡下, 而库里没有任何东西告诉他为什么. 释放
    正是幂等协议里「动作失败就放行」那一手 (见 `retry/idempotency.py`).

    **释放本身失败只记一笔, 不掩盖真正的失败原因**: 调用方是那条 except, 它正要
    把原来的异常抛给上层 —— 这里再抛一个只会让人看不到真正发生了什么. 写不进去的
    后果与上面那个「卡住」相同, 而它还有取消挂起那条明路 (`_cancel_suspension`).
    """
    for key in claimed:
        try:
            await guard.release(key)
        except BaseException as exc:  # 没有调用方可以上抛 (见 docstring)
            logger.warning(
                "审批的幂等键没能放回去 (这把键会卡住): %s", exc, exc_info=True
            )


async def _claim_or_refuse(guard: IdempotencyStore, key: IdempotencyKey) -> None:
    """认领这把键; 抢不到就当场回 409 (两情形用不同的码, 见下面的理由).

    Args:
        guard: 幂等登记簿 (issue 32 的实现之一).
        key: 这一条挂起的键.

    Raises:
        ApprovalAlreadyHandledError: 已经有人在办 (`approval_in_progress`) 或已经
            办完 (`approval_already_applied`). 两个码分开是为了让前端能说实话:
            「提交中, 请稍候」与「这一条已经处理过了」对用户是两件事.
    """
    result = await guard.claim(key)
    if result.status is ClaimStatus.CLAIMED:
        return
    if result.status is ClaimStatus.COMPLETED:
        raise ApprovalAlreadyHandledError(
            f"这次的审批已经处理过了 (键 {key.value!r} 已登记完成): "
            "不必再点一次 —— 刷新页面对一下最新状态",
            code="approval_already_applied",
        )
    raise ApprovalAlreadyHandledError(
        f"这次的审批正在处理中 (键 {key.value!r} 已有人认领): "
        "请等这一次跑完, 不要重复提交",
        code="approval_in_progress",
    )


async def _cancel_suspension(
    run_id: str,
    *,
    context: RunContext,
    calls: ToolCallsRepository | None,
    runs: RunsRepository | None,
    registry: SessionRegistry,
) -> bool:
    """把一次**挂起中**的运行收掉 (给它一个「取消」的结论).

    与「取消一次正在跑的运行」是两件事: 挂起的运行没有任务可取消 (它那次 HTTP 请求
    早就结束了, 会话也放开了), 要收的是库里的两处:

    - 那条等着人批的调用 → `cancelled` (**同时记下是谁收的**: 那一对列正是挂起
      这道闸门的判据, 写完它新提问就放行了)
    - 那次运行 → 从 `waiting_user` 推进到 `cancelled` (状态机允许的那一步)

    两笔都由判据驱动: 只要那条调用行还是未决的, 它就是这次挂起; 一行都没有就说明
    「没有未决挂起」, 于是这里什么都不做 (调用方按 404 处理).

    Returns:
        bool: 真的收到了一次挂起 True; 这次运行没有未决挂起 False.
    """
    if calls is None or runs is None:
        return False
    pending = [
        row
        for row in await calls.list_pending_approvals(context.thread_id)
        if row.run_id == run_id
    ]
    if not pending:
        return False
    for row in pending:
        await calls.set_status(
            row.run_id,
            row.message_id,
            row.tool_call_id,
            ToolCallStatus.CANCELLED,
            approved_by=context.user_id,
        )
    # 推进那一行: 从「等人」到「取消」. 推进不成 (别人先动了手, 比如同时发来的两个
    # 取消) 也算成功 —— 结果是同一个: 这次挂起已经被收掉了
    await runs.try_transition(run_id, RunStatus.WAITING_USER, RunStatus.CANCELLED)
    # 会话内存里那份历史停在「欠着这条调用的结果」的半路上, 而它永远不会执行了 ——
    # 那种形状直接发给模型会被上游拒掉 (真机上就是这样: 取消之后问一句, 模型 API
    # 回 400「assistant 带 tool_calls 后面必须跟上 tool 消息」). 丢掉这份缓存, 下一次
    # 提问会重新装配并从快照水合 —— 那时欠着的那条会被补一条「结果未知」的回填
    registry.forget(context.thread_id)
    return True


def _not_pending_message(run_id: str) -> str:
    """「这次运行没有未决的挂起」那一句事实.

    与 `_not_running_message` 同一个形状 (也同一个理由): 跑到这儿有四条来路 ——
    已经恢复过了 / 被取消了 / 压根没有这个编号 / 不属于这段会话 —— 本层**刻意不
    区分**, 因为区分等于确认「这个编号真实存在过」.
    """
    return (
        f"运行 {run_id!r} 没有等着人确认的调用: 它可能已经恢复过, 可能被取消过, "
        f"也可能压根没有这个编号 —— 本层刻意不区分这几种"
    )


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


def read_query(request: Request) -> str | None:
    """从查询串里取搜索词 —— 会话列表的第二个可选参数 (#20).

    空串 / 只有空白返回 **None** (不搜), 而不是把空串交给仓储: 后者按 `ILIKE '%%'`
    会命中所有会话 —— 结果看着与「没搜」一样, 但语义是错的, 而前端清空输入框时
    发的正是空串.

    不设长度上限: 它只是一个 `ILIKE` 的模式串, 长了也只是扫得慢一点 (会话量级
    本来就小), 而 URL 本身的长度由服务器那道闸管着.
    """
    raw = request.query_params.get(QUERY_QUERY)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def read_limit(request: Request) -> int:
    """从查询串里取「最多几条」—— 会话列表那条路由的第二个可选参数.

    只认非负整数; 不成形就 400 (与 `read_message` 同一条规矩: 看不懂的请求当场
    说清, 不替它猜一个数). **0 是合法的** (「一条都不要」), 仓储会回空列表 ——
    这里不额外管, 「多少条算零条」的规则只有仓储那一处.

    Raises:
        InvalidRequestError: 不是整数 / 是负数.
    """
    raw = request.query_params.get(LIMIT_QUERY)
    if raw is None or not raw.strip():
        return DEFAULT_LIST_LIMIT
    try:
        limit = int(raw)
    except ValueError as exc:
        raise InvalidRequestError(f"{LIMIT_QUERY} 应是整数, 收到 {raw!r}") from exc
    if limit < 0:
        raise InvalidRequestError(f"{LIMIT_QUERY} 不能是负数, 收到 {limit}")
    return limit


async def read_message(request: Request) -> str:
    """从请求体里取「用户问的那句话」(框架 HTTP 契约里唯一认识的字段).

    业务要用请求体里的别的数据 (会话 ID / 页面来源...) 自己读 —— body 只会被
    真正解析一次, 谁先读都一样.

    Raises:
        InvalidRequestError: 不是 JSON 对象 / 缺字段 / 字段不是非空字符串.
    """
    payload = await _read_object(request)
    message = payload.get(MESSAGE_FIELD)
    if not isinstance(message, str) or not message.strip():
        raise InvalidRequestError(f"请求体缺少非空字符串字段 {MESSAGE_FIELD!r}")
    return message


async def _read_object(request: Request) -> dict:
    """请求体 → JSON 对象 (三条读字段的路共用的第一段).

    单拎出来是因为「解析 + 必须是对象」这两步**跟字段无关** —— 三条路 (问句 /
    改标题 / 置顶) 各有各的字段规则, 但读不懂请求体这件事只有一种说法. 抄三份
    的话, 迟早有一条路的报错信息与另两条不一样, 而调用方是按信息排查的.

    Raises:
        InvalidRequestError: 正文不是合法 JSON / 不是一个对象.
    """
    try:
        payload = await request.json()
    except ValueError as exc:  # 覆盖 JSONDecodeError 与编码错误 (都是 ValueError)
        raise InvalidRequestError(f"请求体不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidRequestError(
            f"请求体应为 JSON 对象, 收到 {type(payload).__name__}"
        )
    return payload


async def read_title(request: Request) -> str:
    """从请求体里取新标题 (#20 改标题那条路由).

    空标题**拒掉**而不是放行: 列表上那一行会变成空白, 看着像坏了. 长度那条线画在
    `MAX_TITLE_LENGTH` (接口的规矩, 比列宽窄得多 —— 不该等撞到库那道闸才说太长).

    Raises:
        InvalidRequestError: 不是 JSON 对象 / 缺 `title` / 空 / 过长.
    """
    payload = await _read_object(request)
    title = payload.get(TITLE_FIELD)
    if not isinstance(title, str) or not title.strip():
        raise InvalidRequestError(
            f"请求体缺少非空字符串字段 {TITLE_FIELD!r}", code="invalid_title"
        )
    title = title.strip()
    if len(title) > MAX_TITLE_LENGTH:
        raise InvalidRequestError(
            f"标题最多 {MAX_TITLE_LENGTH} 个字符, 收到 {len(title)}",
            code="title_too_long",
        )
    return title


async def read_approval(request: Request) -> tuple[Approval, dict]:
    """请求体 → (人的结论, 一次性载荷) —— 恢复那条路读的两个字段.

    `decision` **只认** `approve` / `reject` 两个值 (严格认类型与 `read_pinned`
    同源): 拼错的字符串不能被悄悄当成拒绝 —— 那是一次**替人做的决定**, 而用户以为
    自己批准了.

    `data` 是可选的 (要并进本次运行载荷的那些东西, 如支付密码); 给了就必须是 JSON
    对象 —— 拒绝那一路不看它 (没有东西要给工具).

    `data.reason` 是可选的一句拒绝说明 (面向模型): 不给就用框架那句缺省文案
    (见 `Approval.reject`), 它明确劝模型别重试 —— 再试一次就是再弹一张卡.

    Raises:
        InvalidRequestError: 不是 JSON 对象 / `decision` 不是那两个值 / `data` 不是
            对象 / `reason` 不是字符串.
    """
    payload = await _read_object(request)
    decision = payload.get(DECISION_FIELD)
    data = payload.get(DATA_FIELD)
    if data is not None and not isinstance(data, dict):
        raise InvalidRequestError(
            f"{DATA_FIELD!r} 应是 JSON 对象, 收到 {type(data).__name__}",
            code="invalid_approval_data",
        )
    if decision == APPROVE_DECISION:
        return Approval.approve(), data or {}
    if decision == REJECT_DECISION:
        reason = (data or {}).get(REASON_FIELD)
        if reason is not None and not isinstance(reason, str):
            raise InvalidRequestError(
                f"{REASON_FIELD!r} 应是字符串, 收到 {type(reason).__name__}",
                code="invalid_approval_reason",
            )
        return Approval.reject(reason), {}
    raise InvalidRequestError(
        f"{DECISION_FIELD!r} 只认 {APPROVE_DECISION!r} 或 {REJECT_DECISION!r}, "
        f"收到 {decision!r}",
        code="invalid_decision",
    )


async def read_pinned(request: Request) -> bool:
    """从请求体里取「置顶还是取消置顶」(#20 那条路由).

    **必须是布尔**: 传字符串 `"true"` 会被拒而不是被当成真 —— 与 `read_message`
    同一条「严格认类型」. 放水的话, 前端把 true 写成字符串时表现是「置顶始终生效、
    取消置顶怎么点都不动」, 那种半好半坏最难查.

    Raises:
        InvalidRequestError: 不是 JSON 对象 / 缺 `pinned` / 不是布尔.
    """
    payload = await _read_object(request)
    pinned = payload.get(PINNED_FIELD)
    if not isinstance(pinned, bool):
        raise InvalidRequestError(
            f"请求体缺少布尔字段 {PINNED_FIELD!r}, 收到 {type(pinned).__name__}",
            code="invalid_pinned",
        )
    return pinned


def _thread_not_found_message(thread_id: str) -> str:
    """「这段会话不在册」那一句事实 —— 三种情况**共用同一条文本**.

    与 `_not_running_message` 同款 (响应体只有回显的编号不同, 其余逐字相同 ——
    那是契约, 由构造保证比由「记得写一样」保证牢).
    """
    return (
        f"会话 {thread_id!r} 不在册: 它可能已经被删掉, 可能不属于这次请求的那个人, "
        f"也可能压根没有这个编号 —— 本层刻意不区分这三种"
    )


def _record_store_error_response(request: Request, exc: DbError) -> JSONResponse:
    """记录表读不到 → 503 的干净 JSON (与 ServerError 那套同一个信封形状).

    503 而不是 500: 「库这会儿读不了」是可用性, 转发方重试或降级都有意义; 而 500
    意味着「这次请求本身有问题」—— 这两件事对调用方的处置完全不同.

    信封与别的错误一致 (`{"error": {"code", "message"}}`): 同一次失败在响应与
    事件流两条通道上长得一样, message 只陈述事实, 面向用户的话术归业务.
    """
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "record_store_unavailable",
                "message": f"记录表读不到: {type(exc).__name__}: {exc}",
            }
        },
    )


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
    bookkeeping: ApprovalBookkeeping | None = None,
) -> None:
    """任务收尾 (正常 / 失败 / 取消都恰好一次): 关流 + 解绑路由 + 会话放开 + 运行出册
    (+ 恢复那条路还要结两笔账).

    四件登记动作都是同步的, 也都不需要 await —— 收尾路径上多一个 await 就多一处
    可能被再次取消的地方 (**结那两笔账是唯一例外**: 它要写库, 于是派一个任务去做).

    为什么不等响应生成器来收 (两处都在收尾, 只相信一个): 生成器是**消费端**,
    它可能压根没被启动 (客户端在响应头发出去之前就走了), 也可能不被关闭 (硬断连
    时 Starlette 直接抛 ClientDisconnect, 不等生成器收尾). 任务自己的收尾回调
    没有这两个漏洞 —— 运行结束它就响, 连带把事件流关掉 (close_stream).

    三件登记动作包在 `finally` 里: 关流那一步万一炸了 (它在跟异常对象打交道),
    会话也不能被永久占住、登记表里也不能留幽灵条目 —— 这些是**生命周期的保证**,
    优先级高于「把错误原样传上去」. 回调里的异常仍然会冒出来被 asyncio 打出来.

    Args:
        bookkeeping: 恢复那条路要结的两笔账 (幂等键 + 「谁批的」); None = 这不是
            一次恢复 (起新问题那条路没有这些账).
    """
    try:
        close_stream(task, stream)
    finally:
        entry.router.unbind(stream)
        registry.release(thread_id)
        runs.finish(stream.run_id)
        if bookkeeping is not None:
            # 这里**不能直接 await** (本回调是同步的, 而且上面那几行不许被一个
            # await 拖住): 派一个任务去结这两笔账
            # 引用留着并挂到任务集里 (RUF006 的那条理由): 不留引用的话, 这个任务
            # 可能被垃圾回收掉 —— 那时它会**静默消失**, 而那两笔账就永远结不了
            settled = asyncio.create_task(
                _settle_approvals(task, bookkeeping),
                name=f"charagent-approvals:{stream.run_id}",
            )
            _pending_tasks.add(settled)
            settled.add_done_callback(_pending_tasks.discard)


async def _settle_approvals(
    task: asyncio.Task[None], bookkeeping: ApprovalBookkeeping
) -> None:
    """把这次恢复的两笔账结掉: 跑完了标完成 + 记下是谁批的, 没跑成就释放键.

    **不抛**: 它跑在一个独立的收尾任务里, 没有调用方接得住异常 —— 抛出去只会是
    一条没人认领的 asyncio 警告. 写不进去就记一笔 (那条键会卡在「在办」, 用户下次
    点会被 409 挡下 —— 这时他能走的路是取消这次挂起重新发起, 见 `resume_run`).

    `BaseException` 也兜: 收尾任务在**事件循环关停**时会被取消 (进程退出), 那不该
    变成一条 traceback 噪音 —— 而它要写的是一笔「这次审批处理过了」的账, 丢了大
    不了下次点被挡住 (安全的那一侧).
    """
    applied = task.cancelled() is False and task.exception() is None
    try:
        for key in bookkeeping.keys:
            if applied:
                await bookkeeping.guard.complete(key, {"applied": True})
            else:
                await bookkeeping.guard.release(key)
        if applied and bookkeeping.decided_by:
            # 批过了而且真的做完了: 记下「谁在什么时候拍的板」(ADR-0014 的那一对列).
            # 闸门问的正是这一对 —— 于是它到这里才打开, 而**不是**在前一步
            for row in bookkeeping.approvals:
                await bookkeeping.calls.record_decision(
                    row.run_id,
                    row.message_id,
                    row.tool_call_id,
                    decided_by=bookkeeping.decided_by,
                )
    except BaseException as exc:  # 没有调用方可以上抛的收尾路径 (见 docstring)
        logger.warning("审批那两笔账没能结掉 (下次点会被挡住): %s", exc, exc_info=True)
