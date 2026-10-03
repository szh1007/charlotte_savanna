"""HTTP 层的一小块横切: 给每次请求发一个 request_id, 并让它贯穿这一程 (#38).

一句话理解: 请求进来时先看头里带没带 `X-Request-Id` —— 带了就用它 (上游转发方
可以从浏览器那一次点击一路带下来, 于是「用户说的那一次」与「进程日志里那一次」
串得上), 没带就自己发一个; 这一个号在整个请求处理期间挂在上下文上 (见
`CharAgent.structured_logging`), 请求结束时还回去.

**为什么是裸 ASGI 中间件, 不是 `BaseHTTPMiddleware`**: 后者把下游应用放进**另一个
任务**里跑, 而 contextvars 按任务隔离 —— 在那里绑的号传不传得下去, 取决于 Starlette
在哪一步派生任务、又有没有复制上下文. 裸 ASGI 中间件与下游在同一条调用链上
(它只是 `await self.app(...)`), 绑一次全程同源 —— 本功能要的正是这件事, 所以不
去赌另一条路的实现细节.

**号必须还回去** (`log_context` 出块时还原): 同一个连接上的下一个请求不该带着上
一个号的残影. 串号不报错, 只是让日志说假话 —— 那比没有号更糟.

**响应头里回一个 `X-Request-Id`**: 客户端 (或转发方) 拿着它才能在日志里指名道姓
地找这一次. 报错截图上写着的那一串, 是唯一能把「他说的那一次」与「进程日志里的
那一次」对上的东西.

**认不出的头自己发一个**: 号是**我们自己**用来关联的东西, 不是客户端往日志里塞
东西的地方 —— 空 / 过长 / 带怪字符的值一律不用, 换一个我们生成的 (它照样出现在
响应头里, 所以客户端没有「我给的号丢了」这种困惑).

大白话版: 每个请求发一个流水号, 头里带了我就不发; 这个号跟着这一程的每一行日志
走, 也在响应头里还给客户端.
"""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from CharAgent.server.utils.types import REQUEST_ID_HEADER
from CharAgent.structured_logging import log_context

# 认得出的号长这样: 字母数字加四个分隔符 (`-` / `_` / `.` / `:` —— traceparent、
# UUID、`业务:用户:对话` 那类拼法都在里面), 最长 64.
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")

# 头名在 ASGI scope 里是小写字节 (uvicorn 如此, 规范也如此) —— 比较前一律降成
# 小写, 免得换个服务器 (或中间件) 就认不出来
_REQUEST_ID_KEY = REQUEST_ID_HEADER.lower().encode("ascii")


class RequestIdMiddleware:
    """给每个 HTTP 请求定一个 request_id, 并让它贯穿这一程打的每一行日志.

    形状与 ASGI 中间件一致 (`__init__(app)` + `__call__(scope, receive, send)`),
    于是 `app.add_middleware(RequestIdMiddleware)` 直接可用.

    Args:
        app: 下游 ASGI 应用 (Starlette 装配时递进来).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # 非 HTTP 的 scope (lifespan / websocket) 直接放过去: 它们没有「一次请求」
        # 这个单位, 发号没有意义
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _request_id_of(scope)
        with log_context(request_id=request_id):
            await self.app(scope, receive, _echoing_request_id(send, request_id))


def _request_id_of(scope: Scope) -> str:
    """这次请求的号: 头里那个 (认得出的话), 否则现发一个."""
    for key, value in scope.get("headers") or ():
        if key.lower() != _REQUEST_ID_KEY:
            continue
        candidate = value.decode("latin-1").strip()
        if _REQUEST_ID.fullmatch(candidate):
            return candidate
        # 头里那个用不了 (空 / 太长 / 有怪字符): 不发就用, 也不报错 —— 号丢了
        # 只是「这次排查少了根线头」, 不值得把一次正常的请求变成 400
        break
    return uuid4().hex


def _echoing_request_id(send: Send, request_id: str) -> Send:
    """把响应包一层: `http.response.start` 上补一个请求编号头, 其余原样放行.

    只动响应头那一帧 (它在正文之前发出, 于是 SSE 那种长流也来得及带上) ——
    正文一帧都不碰.
    """

    async def wrapper(message: Message) -> None:
        if message["type"] == "http.response.start":
            MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
        await send(message)

    return wrapper


__all__ = ["RequestIdMiddleware"]
