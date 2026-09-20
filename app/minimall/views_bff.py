"""Django 转发层 (BFF): 浏览器 ↔ CharApp 客服服务之间的那一跳 (CharApp issue 06).

一句话理解: 浏览器只能用 cookie 证明「我是谁」, 而客服服务只认一个内部令牌 +
一个买家 ID —— 本文件就是这两套语言之间的翻译, 顺带把 SSE 帧原样端过去.

一次问答走完长这样 (每一步谁负责)::

    POST /minimall/agent/chat/  {"message": ..., "conversation_id": ...}
      ├─ 1. 认证: Django session + CSRF 令牌 —— 唯一真正验证「你是谁」的地方 (PRD §4.10)
      ├─ 2. 取身份: user_id **只从 session 取** (请求体里的同名字段一律无视)
      ├─ 3. 转发: POST CharApp /runs, 带 X-Internal-Token + X-User-Id
      └─ 4. 透传: 上游的 SSE 帧逐条写回浏览器 (错误帧换成人话)

**为什么是 POST** (2026-09-21 从 GET 改过来, 见 `CharApp/docs/adr/0002`): 这个端点
**有副作用** —— 它真跑一次模型、真花钱、真往会话里写东西. 按 HTTP 的语义, 有副作用的
操作本来就不该用 GET, 而上一版用 GET 是被前端 `EventSource` 逼的 (它只能发 GET、也
带不了自定义头). 换成 `fetch` 之后, 三件事一起解决了:

1. **能被显式防护**: POST + cookie 认证 ⇒ Django 的 CSRF 中间件接管 ⇒ 跨站页面
   伪造不出来 (上一版只能靠 `SameSite=Lax`, 而它挡不住顶层导航);
2. **问句不再进日志**: 上一版问句在 query 里, 每一句都会落进访问日志 (订单号、
   地址这些都会跟着进去), 现在走请求体;
3. **错误能回真状态码**: 浏览器读得到非 200 的响应体, 于是「连不上 / 上游拒绝」
   可以回 502/503 + `{"error": {...}}`, 不必再假装 200 往流里塞错误帧.

代价写在明处: 前端不再能白拿 `EventSource` 的帧解析, 得自己读流 —— 这一半交给现成
的库 (`eventsource-parser`, 见 `agent.html`), 页面只剩胶水. 换来的还有一件事: 没有
自动重连 ⇒ 上一版那套「重连守卫」(Last-Event-ID → 409) 连同它的测试一起删掉了,
不存在「一句话跑两遍」.

**BFF 不做业务判断**: 不判断「这句话该不该问」, 不改写问题, 不缓存回答. 它只做
认证 / 取身份 / 转发三件事, 唯一一处「加工」是错误帧的文案 (见下).

**用户面文案归这里** —— 这是框架 `docs/DESIGN.md` 那句「降级话术归 server 层」的
落地: 框架只发事实 (错误码 + 说明), 那句话是给开发者看的; 用户看到的话由
`ERROR_COPY` 决定. 分成三处各管一摊, 才不至于到处都在编用户文案:

| 用户看到的 | 谁写的 |
|-----------|--------|
| 答复正文 (含「暂时查不到」这类降级说法) | 模型 —— 它拿得到工具失败的回填 |
| 错误提示 (超轮数 / 服务不可用...) | 本文件的 `ERROR_COPY` |
| 工具调用轨迹 (参数与结果) | 原样透传 —— 它是给人看的排查窗, 不翻译 |

由此有一条**硬规矩**: 凡是回到浏览器的 `error.message`, 都必须是用户话术 (取自
`ERROR_COPY`); 给开发者的那份理由 (哪个字段不合法 / 上游正文说了什么) 一律进日志.
上一版能偷懒是因为 4xx 只有 curl 看得见, 现在页面上会显示, 就不能再混着了.

**不 import CharApp**: 两个进程之间只有 HTTP 契约 (三个头名 + 一个请求体字段),
本文件因此把那几个名字自己声明一遍. Django 侧不该依赖助手服务的 Python 包 ——
依赖方向只有一条 (业务 → 框架), 而 Django 与服务之间是 HTTP, 不是 import.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import httpx
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.generic import TemplateView, View

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 与 CharApp 服务之间的 wire 契约
# ---------------------------------------------------------------------------

# 三个头 (对面在 CharApp/minimall/client.py 与 server.py 里定义; 名字必须逐字对上)
HEADER_TOKEN = "X-Internal-Token"
HEADER_USER_ID = "X-User-Id"
HEADER_CONVERSATION_ID = "X-Conversation-Id"

# 助手服务占的端点与它认的请求体字段 (框架 `CharAgent/server/` 的 HTTP 契约)
RUNS_PATH = "/runs"
MESSAGE_FIELD = "message"

# 浏览器打进来的那两个字段 (页面按它发, 这里按它认)
CONVERSATION_FIELD = "conversation_id"

# 问句长度上限 (字符): 客服问答没有理由更长. 超长的表现很具体 —— 直接烧 token,
# 或者撞上模型侧的上下文上限换回一句没用的报错, 不如在这里说清楚.
MAX_MESSAGE_LENGTH = 2000

# 会话编号的拼法: `minimall:{买家}:{对话}` (PRD §4.11). BFF 只发最后一段, 但**长度
# 要按整串算** —— 框架卡的是整串 (`checkpoint/utils` 的 check_identifier, 128),
# 只量第三段会让 117 字符的对话编号从这里过去, 到助手服务装配会话时才炸 (用户看到
# 的是「客服暂时联系不上」而不是「你的请求不合法」). 这两条拼法上的事实属于
# wire 契约, 与三个头名一样在这里声明一遍.
THREAD_ID_PREFIX = "minimall"
MAX_THREAD_ID_LENGTH = 128

# 上游超时 (秒): 连接要快失败 (服务没起来时立刻说人话), 读要耐心 —— 模型思考时
# 可能半天没有事件, 那是正常的安静, 不是故障.
UPSTREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)

# 错误日志里最多带多少字符的上游正文 (够定位, 不把整页 HTML 塞进日志)
_DETAIL_LIMIT = 200

# 终局事件 (框架的契约: 一条流里恰好一个, 之后流才收线)
TERMINAL_EVENTS = frozenset({"final", "error"})

# 本层自己补的两个错误码 (框架不会发这两个)
UNAVAILABLE_CODE = "agent_unavailable"
INTERRUPTED_CODE = "stream_interrupted"

# 错误码 → 用户看得懂的一句话.
#
# 左边是**事实** (框架的 LoopOutcome 值, 加本层补的两个), 右边是**话术**: 用户
# 能照着做的话 (「换个问法」「稍后再试」), 而不是把错误码翻译成中文.
# 想知道每个码在框架里的确切含义, 见 `CharAgent/agent/utils/events.py`.
ERROR_COPY: dict[str, str] = {
    "max_turns": "这个问题我查了几轮还没查明白, 换个问法或者问得更具体一点试试?",
    "token_budget": "这次要查的东西太多了, 超出了单次额度, 把问题拆小一点再问吧.",
    "time_limit": "这次查得太久超时了, 稍后再试或把问题问得具体一些.",
    "truncation_limit": "回答被打断了好几次, 没能答完, 请重新问一次.",
    "server_interrupted": "模型服务中断了这次回答, 请再问一次.",
    "content_filter": "这个问题被安全策略拦下了, 换个说法试试.",
    "run_failed": "客服这边出了点问题, 请稍后再试.",
    "cancelled": "这次回答已取消.",
    # 上游拒绝转发时给的码 (框架 `server/utils/errors.py` 定的那一套):
    # 只有 thread_busy 是用户自己能处理的一种 —— 别的 (unauthorized /
    # not_configured / invalid_request) 都说明**我们这边**没配对, 用户无从下手,
    # 所以走兜底话术, 码保留给日志与 devtools 看.
    "thread_busy": "上一句我还在答呢, 等这条答完再问下一句吧.",
    UNAVAILABLE_CODE: "客服暂时联系不上, 请稍后再试.",
    INTERRUPTED_CODE: "回答中途断开了, 请重新问一次.",
    # 请求本身不合法 (本层判的, 轮不到上游). 这几条理论上只有前端出 bug 才会走到,
    # 但用户看到的仍得是一句能照着做的话 —— 不是什么「参数非法」.
    "invalid_request": "这条消息没能发出去, 重说一遍试试.",
    "invalid_message": "这句话是空的, 说点什么再发吧.",
    "message_too_long": "这句话太长了, 我一次读不完 —— 拆短一点再问吧.",
    "invalid_conversation_id": "这次对话的连接坏了, 刷新页面再问一次.",
    "method_not_allowed": "这条请求的方式不对, 刷新页面再问一次.",
}

# 没见过的码 (框架以后新增的) 用的兜底话术
ERROR_COPY_FALLBACK = "客服这边出了点问题, 请稍后再试."


# ---------------------------------------------------------------------------
# SSE 帧: 解析 (只为了让错误帧换成人话) 与生成
# ---------------------------------------------------------------------------


def _field(frame: bytes, name: bytes) -> bytes | None:
    """取一帧里某个字段的值 (SSE 的字段就是 `名字: 值` 一行).

    只认框架那一边的写法 (行尾 `\\n`), 不做完整 SSE 解析 —— 对面是自家服务,
    帧结构由 `CharAgent/server/sse.py` 定死; 这里的解析只服务于「错误帧换文案」
    这一件事, 认不出来就当普通帧原样转发 (见 `_with_user_copy`).
    """
    prefix = name + b": "
    for line in frame.split(b"\n"):
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def _event_name(frame: bytes) -> str | None:
    """一帧的 event 名 (框架的事件类型就是 SSE 的 event 字段)."""
    raw = _field(frame, b"event")
    return raw.decode("ascii", "replace") if raw else None


def user_copy_for(code: str) -> str:
    """错误码 → 用户话术 (没见过的码走兜底)."""
    return ERROR_COPY.get(code, ERROR_COPY_FALLBACK)


def error_frame(code: str, *, seq: int = 1) -> bytes:
    """本层补的一帧 error —— 帧形状与框架那帧同形, 前端一套解析吃两边.

    载荷刻意只有 `{"error": {"code", "message"}}`: 前端对 error 事件只读这两样,
    而 `type` / `run_id` 是本帧没有的东西 (这一次运行可能压根没起来), 硬填一个假的
    反而会让排查的人当真. 两边真正共用的是帧的三个字段 (`id` / `event` / `data`).

    Args:
        code: 错误码 (原样保留给程序看; 用户话术由 `user_copy_for` 翻).
        seq: 事件序号; 补在别人的事件后面时接着往下编 (id 单调, 前端排序靠它).
    """
    error = {"code": code, "message": user_copy_for(code)}
    return _frame_bytes("error", seq, {"error": error})


def _frame_bytes(event: str, seq: int | bytes, data: dict) -> bytes:
    """一个事件 → 一帧字节 (id / event / data + 空行; 本文件里**唯一**拼帧的地方).

    `seq` 收 int 也收字节: 换文案那条路是照着上游那一帧的样子重拼的, 它的 id
    字段原样拿来 (自己再解一遍数字没有意义).
    """
    head = seq if isinstance(seq, bytes) else str(seq).encode()
    payload = json.dumps(data, ensure_ascii=False).encode()
    lines = (b"id: " + head, b"event: " + event.encode(), b"data: " + payload, b"")
    return b"\n".join(lines) + b"\n"


def _with_user_copy(frame: bytes) -> bytes:
    """错误帧换成人话, 其余原样返回 —— 唯一一处改动上游内容的地方.

    只动 error 一类的理由: 终局 error 的 message 是**框架写给开发者的**事实说明
    (「已达最大轮数限制...」), 直接摆给用户看既看不懂也不可操作; 别的事件要么是
    给人看的正文 (final), 要么是排查窗里的轨迹 (tool_* / thinking / reasoning),
    改它们等于替模型写话.

    解析失败 (帧不合契约) 就原样转发: 让用户看到一句难看的话, 好过把终局事件
    吞掉 —— 后者会让前端一直等下去.
    """
    if _event_name(frame) != "error":
        return frame
    raw = _field(frame, b"data")
    try:
        payload = json.loads(raw) if raw else None
        error = payload["error"]
        code = error["code"]
    except (ValueError, TypeError, KeyError):
        logger.warning("上游的 error 帧解析不了, 原样转发: %r", frame[:_DETAIL_LIMIT])
        return frame

    error["message"] = user_copy_for(code)
    return _frame_bytes("error", _field(frame, b"id") or b"1", payload)


def iter_frames(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """字节流 → 一帧一帧 (SSE 的帧分隔符是一个空行).

    为什么要攒: 一次 `iter_bytes()` 拿到的不是「一帧」, 而是「这段时间到的字节」
    —— 一帧可能被 TCP 切成两半送来 (真机上常有, 本地回环上也一样会发生). 按帧
    边界攒齐再放行, 下游看到的才永远是完整的帧.
    """
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        while (end := buffer.find(b"\n\n")) != -1:
            yield buffer[: end + 2]
            buffer = buffer[end + 2 :]
    if buffer.strip():
        # 上游没按帧收尾 (缺最后一个空行) —— 别把已经收到的内容丢掉
        yield buffer


def _detail(response: httpx.Response, limit: int = _DETAIL_LIMIT) -> str:
    """上游响应正文的一段摘要 (**只进日志**, 不进浏览器).

    读正文可能自己就失败 (连接已经断了), 那就退回状态码 —— 排查要的是「哪儿
    断的」, 日志里留一句「读不到」也够用.
    """
    try:
        response.read()
    except httpx.HTTPError as exc:
        return f"<读不到正文: {type(exc).__name__}>"
    text = response.text.strip()
    return text[:limit] if text else "(空响应体)"


def _refusal_code(response: httpx.Response) -> str:
    """上游拒绝转发时它给的错误码 (拿不到就退回「服务不可用」).

    框架的拒绝响应是 `{"error": {"code", "message"}}` (与事件流同一个形状), 那个
    code 是**框架特意留给业务用的**: 按它决定用户文案与要不要重试
    (`CharAgent/server/utils/errors.py` 开头就写着这条分工). 所以这里原样接下来,
    别把它塌成一句「反正是失败」—— `thread_busy` (上一句还没答完) 与
    `unauthorized` (令牌配错了) 对用户是两回事: 前者等一等就好, 后者他做什么
    都没用.
    """
    try:
        response.read()
        return response.json()["error"]["code"]
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return UNAVAILABLE_CODE


def _is_event_stream(response: httpx.Response) -> bool:
    """上游回的确实是 SSE 吗 (`text/event-stream`, 可能带 charset)."""
    content_type = response.headers.get("content-type", "")
    return content_type.split(";")[0].strip() == "text/event-stream"


def max_conversation_id_length(user_id: int) -> int:
    """这个买家的对话编号最长能有多少字符 (整串 `thread_id` 不超上限).

    与 `MAX_THREAD_ID_LENGTH` 一起构成「前端生成的编号天然合法」这条保证: 页面发的
    是 UUID (36 字符), 离上限还远; 但规则写在这里, 越界就是一次 400 + 日志, 而不是
    让助手服务在装配时报一个配置错.
    """
    head = f"{THREAD_ID_PREFIX}:{user_id}:"
    return MAX_THREAD_ID_LENGTH - len(head)


def valid_conversation_id(value: str, user_id: int) -> bool:
    """这个会话编号能不能原样转发出去.

    两条都是**传输契约**上的事:

    1. 它马上就要**进 HTTP 头**, 而头是按字节传的 —— 空白, 换行 (头注入的形状),
       非 ASCII (中文在这一层走不通) 都得在出门前挡住.
    2. 它会被拼进 `thread_id`, 而那个整串有长度上限 (见 `max_conversation_id_length`).

    Note:
        这一条**必填**: 助手服务那边有默认值 (`server.DEFAULT_CONVERSATION_ID`),
        但那是给直接调接口的调用方留的; BFF 面向的浏览器每开一个标签页就生成一个,
        缺了只能是前端出了错 —— 那时给个默认值, 表现是两个标签页悄悄共用了同一段
        对话 (用户故事 25 要防的正是这个), 不如直接说「请求不合法」.
    """
    if not value or len(value) > max_conversation_id_length(user_id):
        return False
    return value.isascii() and all(
        char.isprintable() and not char.isspace() for char in value
    )


# ---------------------------------------------------------------------------
# 请求 → 一次问句 (POST 的这一半)
# ---------------------------------------------------------------------------


class RefusedError(Exception):
    """这次请求没法继续 —— 带够信息去回一个真状态码.

    与框架的 `ServerError` 同款思路 (状态码与错误码都挂在异常上, 由出口统一翻成
    响应). 为什么用异常而不是一路 `return`: 读请求体与打开上游都在同一个 try 里,
    谁先失败都该走同一条出口, 而且**打开上游失败时手里还攥着一个 httpx 客户端**
    —— 异常能把栈带回出口, 那里统一收尾.

    Note: 只带事实 (状态码 + 错误码), **不带用户话术** —— 文案由回响应那一处
    (`_refusal`) 统一从码推, 免得两个地方各存一份、哪天改岔了.
    """

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class Question:
    """浏览器问的一件事 (BFF 从请求体里只认这两样)."""

    message: str
    conversation_id: str


def question_from_request(request, user_id: int) -> Question:
    """请求体 → 一次问句; 哪里不合契约就抛 `RefusedError` (400).

    Raises:
        Refused: 正文不是 JSON 对象 / 缺字段 / 问句为空或过长 / 会话编号不合法.
            给用户的文案取自 `ERROR_COPY`, 给开发者的那份理由 (哪个字段坏了) 走日志.
    """
    try:
        payload = json.loads(request.body)
    except ValueError as exc:
        logger.warning("买家 %s: 请求体不是合法 JSON: %s", user_id, exc)
        raise RefusedError(400, "invalid_request") from exc
    if not isinstance(payload, dict):
        logger.warning("买家 %s: 请求体应为 JSON 对象", user_id)
        raise RefusedError(400, "invalid_request")

    message = payload.get(MESSAGE_FIELD)
    if not isinstance(message, str) or not message.strip():
        logger.warning("买家 %s: 请求体缺少非空字符串字段 %s", user_id, MESSAGE_FIELD)
        raise RefusedError(400, "invalid_message")
    message = message.strip()
    if len(message) > MAX_MESSAGE_LENGTH:
        logger.warning(
            "买家 %s: 问句过长 (%d 字符, 上限 %d)",
            user_id,
            len(message),
            MAX_MESSAGE_LENGTH,
        )
        raise RefusedError(400, "message_too_long")

    raw = payload.get(CONVERSATION_FIELD)
    conversation_id = raw.strip() if isinstance(raw, str) else ""
    if not valid_conversation_id(conversation_id, user_id):
        logger.warning("买家 %s: 会话编号不合法: %r", user_id, raw)
        raise RefusedError(400, "invalid_conversation_id")

    return Question(message=message, conversation_id=conversation_id)


def _refusal(status: int, code: str) -> JsonResponse:
    """回一个错误响应 (形状与框架的 error 响应一致: `{"error": {...}}`).

    Note: `message` **是用户话术** (由码推出来, 与 `RefusedError` 同一个来源) ——
    页面上会把它显示出来, 因为 fetch 读得到响应体 (这是换 POST 换来的一件好事);
    给开发者的细节只进日志, 别往这里塞.
    """
    return JsonResponse(
        {"error": {"code": code, "message": user_copy_for(code)}}, status=status
    )


# ---------------------------------------------------------------------------
# 转发
# ---------------------------------------------------------------------------


def _runs_url() -> str:
    """上游端点地址 (基地址写在 settings 里, 部署期可换)."""
    return f"{settings.CHARAPP_SERVER_URL.rstrip('/')}{RUNS_PATH}"


def _who_for(user_id: int, conversation_id: str) -> str:
    """日志前缀: 跨进程的故障要能按**买家**追溯 (系统级规范 §6.3).

    翻日志的人手上通常只有一个「谁反映的」, 没有别的线索.
    """
    return f"买家 {user_id} / 会话 {conversation_id}"


@dataclass(slots=True)
class Upstream:
    """已打开的上游流: **可迭代** (吐出来的是字节帧) 且 **可关闭**; 谁开谁关.

    交给 `StreamingHttpResponse` 的就是它本身 —— 为什么不是裸生成器: Django 只在
    生成器**被迭代过**之后才会跑它的 `finally`, 而"响应还没开始写、客户端就走了"这
    一小段窗口里, 上游那次运行已经真跑起来了 —— 那样连接没人关, 模型那边还在烧
    token. 带 `close()` 的对象会被 Django 登记进响应的 `_resource_closers`
    (`response.py` 的 `_set_streaming_content`), 响应一关就一定会调到它
    (`FileResponse` 用的也是这个机制), 两条收尾路径就都堵上了.

    为什么用 `ExitStack` 而不是两个嵌套的 `with`: 资源的**生命周期跨越了两个作用域**
    —— 连接要在视图里开 (好在返回响应之前知道成功失败), 却要活到流跑完. 拿一个
    stack 把两份资源攒起来, 收尾时一次关掉 (顺序也对: 先关流再关客户端).
    """

    stack: contextlib.ExitStack
    response: httpx.Response
    who: str

    def __iter__(self) -> Iterator[bytes]:
        return relay(self)

    def close(self) -> None:
        """关流 + 关客户端 (重复调用无副作用: `ExitStack` 只关一次).

        关不掉也要留一行日志 —— 收尾不该盖住真正的失败原因, 但也不该什么都不说
        (系统级规范 §6.3: 不静默吞异常).
        """
        try:
            self.stack.close()
        except Exception as exc:
            logger.warning(
                "%s: 关不掉上游连接: %s: %s", self.who, type(exc).__name__, exc
            )


def open_upstream(user_id: int, question: Question) -> Upstream:
    """打上游并按契约检查它; 任何问题都抛 `RefusedError` (状态码给浏览器用).

    为什么要在这里**同步地**把连接开好 (上一版是在生成器里懒打开): 「服务没起来」
    这类失败现在要回一个真状态码 (502/503), 而状态码必须在响应头出门之前定下来
    —— 生成器跑到的时候头早就发出去了. 代价是第一帧之前多等一次上游握手 (本机
    一两毫秒).

    状态码的分法 (给浏览器看的语义):

    | 情况 | 状态 | 码 |
    |------|------|----|
    | 本机就没配令牌 (我们自己的问题) | 503 | `agent_unavailable` |
    | 连不上 / 超时 | 502 | `agent_unavailable` |
    | 上游回非 200 | 502 | **上游给的那个码** (`thread_busy` / `unauthorized`...) |
    | 上游回 200 但不是 SSE (打错地址?) | 502 | `agent_unavailable` |

    Note:
        用**每次请求一个** `httpx.Client`: Django 没有可靠的进程退出钩子 (WSGI
        进程是被 kill 的), 挂一个全局客户端就得指望没人来关; 而一次问答只连一次
        本机服务, 建连接的开销可以忽略.
    """
    who = _who_for(user_id, question.conversation_id)
    token = settings.CHARAPP_INTERNAL_TOKEN
    if not token:
        # 与商城侧同一条纪律 (fail closed): 没配就一个请求都不发. 说清是哪个变量
        # 没配, 而不是让人对着「客服暂时联系不上」去猜 CharApp 为什么没起来.
        logger.error(
            "%s: CHARAPP_INTERNAL_TOKEN 未配置, 转发无处可去 (与商城侧 settings 同值)",
            who,
        )
        raise RefusedError(503, UNAVAILABLE_CODE)

    headers = {
        HEADER_TOKEN: token,
        HEADER_USER_ID: str(user_id),
        HEADER_CONVERSATION_ID: question.conversation_id,
    }
    stack = contextlib.ExitStack()
    try:
        client = stack.enter_context(httpx.Client(timeout=UPSTREAM_TIMEOUT))
        try:
            response = stack.enter_context(
                client.stream(
                    "POST",
                    _runs_url(),
                    json={MESSAGE_FIELD: question.message},
                    headers=headers,
                )
            )
        except httpx.HTTPError as exc:
            # 连接被拒 / 握手超时: 上游根本没答上来
            logger.error("%s: 连不上客服服务: %s: %s", who, type(exc).__name__, exc)
            raise RefusedError(502, UNAVAILABLE_CODE) from exc

        if response.status_code != 200:
            code = _refusal_code(response)
            logger.error(
                "%s: 客服服务拒绝了这次转发: HTTP %d %s",
                who,
                response.status_code,
                _detail(response),
            )
            raise RefusedError(502, code)
        if not _is_event_stream(response):
            logger.error(
                "%s: 客服服务回的不是 SSE: content-type=%s",
                who,
                response.headers.get("content-type"),
            )
            raise RefusedError(502, UNAVAILABLE_CODE)
    except BaseException:
        # 任何一条路没走通都要把已经开的连接放掉, 再把异常原样抛出去; 只有**成功**
        # 那条路才把 stack 交给 Upstream 保管. 用 BaseException 而不是 HTTPError:
        # 配置写错时抛的 `httpx.InvalidURL` 不是 HTTPError (基地址端口写错就够),
        # 漏出去的话表现是 500 + 连接没人关.
        with contextlib.suppress(Exception):
            stack.close()
        raise

    return Upstream(stack=stack, response=response, who=who)


def relay(upstream: Upstream) -> Iterator[bytes]:
    """把上游的帧逐条交给浏览器 (中途出事就补一个终局事件).

    拿到流**之前**的失败已经由 `open_upstream` 回成真状态码了, 这里只剩两种
    「头已经发出去了、改不了状态码」的情况, 它们仍然是流里补一帧 error:

    | 上游 | 用户看到 |
    |------|---------|
    | 流断在半路 (连接被掐) | 回答中途断开了 |
    | 流正常收线但**没有终局事件** | 同上 —— 前端靠终局事件收线, 少了它就得干等 |

    第二条是**本层自己的守卫**: 框架保证「每条流恰好一个终局事件」, 但中间隔着
    uvicorn / 代理 / 操作系统, 万一那条保证在路上丢了, 补一个比让前端一直转圈强.
    """
    who = upstream.who
    last_seq = 0
    seen_terminal = False
    try:
        for frame in iter_frames(upstream.response.iter_bytes()):
            if (seq := _field(frame, b"id")) is not None:
                last_seq = int(seq) if seq.isdigit() else last_seq
            if _event_name(frame) in TERMINAL_EVENTS:
                seen_terminal = True
            yield _with_user_copy(frame)
    except httpx.HTTPError as exc:
        logger.error("%s: 转发中断: %s: %s", who, type(exc).__name__, exc)
        if not seen_terminal:
            yield error_frame(INTERRUPTED_CODE, seq=last_seq + 1)
        return  # 断在半路: 收尾那一帧已经在上面补过, 别掉进下面那条"流读完了"的路
    finally:
        # 走到这儿有三种情形: 流读完 / 上游断了 / 浏览器先走了 (生成器被关闭).
        # 三种都该把这两份资源关掉 —— 特别是最后一种: 用户关掉页面就该立刻
        # 松开与助手服务的连接.
        upstream.close()

    if not seen_terminal:
        logger.error("%s: 客服服务的流结束了却没有终局事件 (代理截断 / 进程被杀?)", who)
        yield error_frame(INTERRUPTED_CODE, seq=last_seq + 1)


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------


class AgentPageView(LoginRequiredMixin, TemplateView):
    """客服页面 (原生 JS + `fetch` + `eventsource-parser`; 与商城前端同一套零构建风格).

    Note:
        页面上不渲染历史消息: L1b 不做历史接口 (框架的会话历史是给模型看的).
        刷新页面就是一段新对话 —— 页面上的 `conversation_id` 也随之换新, 这是
        本片的已知边界, 不是 bug.
    """

    template_name = "minimall/agent.html"


class AgentChatView(LoginRequiredMixin, View):
    """BFF 端点: 一次问答 → 一条 SSE 流 (POST).

    Note:
        只认 POST: 这个端点有副作用 (真跑模型 / 真花钱 / 真写会话), 那是 GET 不该
        做的事; 而且 POST + cookie 认证才受 Django 的 CSRF 中间件保护 —— 跨站页面
        伪造不出这条请求 (见模块 docstring 与 `CharApp/docs/adr/0002`)。
    """

    def post(self, request):
        # 身份只从 session 取: 请求体里就算带了 user_id 也不看 (PRD §4.2 的
        # 「身份不进模型能碰到的地方」在传输层的第一步 —— 用户的参数就是模型碰不到的)
        buyer_id = request.user.pk
        try:
            question = question_from_request(request, buyer_id)
            upstream = open_upstream(buyer_id, question)
        except RefusedError as refused:
            # 走到这儿的一定是"还没开始流"的失败 (入参不合法 / 连不上上游):
            # 回真状态码 + 用户话术, 浏览器读得到
            return _refusal(refused.status, refused.code)

        return StreamingHttpResponse(
            upstream,
            content_type="text/event-stream",
            headers={
                # 缓存 SSE 的中间层会把它变成「跑完才到」; 后者是 nginx 的对应开关
                "cache-control": "no-cache",
                "x-accel-buffering": "no",
            },
        )

    def http_method_not_allowed(self, request, *args, **kwargs) -> HttpResponse:
        """GET 之类一律拒掉, 并说清为什么 (而不是默认那句干巴巴的 405).

        默认响应体是空的 —— 而这条 405 是**有意的设计**(别把有副作用的动作做成
        一个链接/一张图片就能触发的 GET), 值得写明白, 免得以后有人"顺手"补一个
        `def get` 上去.
        """
        logger.warning("BFF 端点收到 %s (只认 POST): %s", request.method, request.path)
        return _refusal(405, "method_not_allowed")
