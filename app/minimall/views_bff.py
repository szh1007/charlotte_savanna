"""Django 转发层 (BFF): 浏览器 ↔ CharApp 客服服务之间的那一跳 (CharApp issue 06).

一句话理解: 浏览器只能用 cookie 证明「我是谁」, 而客服服务只认一个内部令牌 +
一个买家 ID —— 本文件就是这两套语言之间的翻译, 顺带把 SSE 帧原样端过去.

一次问答走完长这样 (每一步谁负责)::

    POST /minimall/agent/chat/  {"message": ..., "conversation_id": ...}
      ├─ 1. 认证: Django session + CSRF 令牌 —— 唯一真正验证「你是谁」的地方 (PRD §4.10)
      ├─ 2. 取身份: user_id **只从 session 取** (请求体里的同名字段一律无视)
      ├─ 3. 转发: POST CharApp /runs, 带 X-Internal-Token + X-User-Id
      └─ 4. 透传: 上游的 SSE 帧逐条写回浏览器 (错误帧换成人话)

按「停止」时走的是另一条短链路 (07)::

    POST /minimall/agent/cancel/  {"run_id": ..., "conversation_id": ...}
      ├─ 1. 认证与取身份: 同上 (同一个 session, 同一个 CSRF)
      ├─ 2. 转发: POST CharApp /runs/{run_id}/cancel (同样三个头)
      └─ 3. 回一个状态码 —— 运行停下来的信号仍在那条 SSE 流上 (终局事件), 不在这个
            响应里; 所以这里**只**转达「请求收到了 / 已经被拒了」.

页面加载时走的是第三条路 (不跑模型, 也没有流)::

    POST /minimall/agent/history/  {"conversation_id": ...}
      ├─ 1. 认证与取身份: 同上 (同一个 session, 同一个 CSRF)
      ├─ 2. 转发: GET CharApp /history (同样三个头)
      └─ 3. 上游的响应体**原样**交给浏览器 (聊过的话就在里面)

这条路的产出是「刷新之后对话还在」: 会话活在助手服务的进程内存里, 而浏览器那一份
渲染刷新即丢 —— 所以页面每次加载都先问一次「这段对话聊到哪儿了」. 它不碰商城,
也不产生任何运行.

左栏的会话列表走第四条路 (同样不跑模型, 同样没有流)::

    POST /minimall/agent/conversations/
      ├─ 1. 认证与取身份: 同上 (同一个 session, 同一个 CSRF)
      ├─ 2. 转发: GET CharApp /conversations (同样三个头)
      └─ 3. 上游的响应体**原样**交给浏览器 (我聊过哪几段就在里面)

它与上一条是一对 (一个答「这段聊了什么」, 一个答「我有哪些对话」), 只是连请求体
都不读 —— 列表问的是「所有段」, 不需要指名哪一段. 列表内容由助手服务按**转发过去
的身份**过滤, 所以「换一个买家登录就看不到别人的」在这条路上是免费的.

**它为什么也是 POST** (读操作用 POST 是违反 HTTP 语义的, 所以得说清): 会话编号是
私密数据 —— 它在 URL 里就等于同时进了访问日志 / 浏览器历史 / Referer, 而 GET +
cookie 又是跨站可触发的 (`<img src>` 一行就够). 换成 POST + CSRF 之后, 四条路形状
一致 (都 POST + CSRF, 都从 session 取身份), 页面那边一套写法. 下游那一跳 (读历史与
列会话) 仍是 GET: 同机同信任域, 地址里不带参数, 要令牌才进得来.

`run_id` 从哪来: 上一条 Chat 响应的 `X-Run-Id` 头 (框架给的, 本层原样带给浏览器).
它是页面上按「停止」时唯一能指名道姓的东西 —— 而它**能且只能**取消自己那段会话里
的运行: 判据在助手服务那侧 (会话编号里含买家, 见 `CharApp/minimall/service.py`),
本层只负责把它原样转过去.

**为什么是 POST** (2026-09-21 从 GET 改过来, 见 `CharApp/docs/adr/0002`): 这个端点
**有副作用** —— 它真跑一次模型、真花钱、真往会话里写东西. 按 HTTP 的语义, 有副作用的
操作本来就不该用 GET, 而上一版用 GET 是被前端 `EventSource` 逼的 (它只能发 GET、也
带不了自定义头). 换成 `fetch` 之后, 三件事一起解决了:

1. **能被显式防护**: POST + cookie 认证 ⇒ Django 的 CSRF 中间件接管 ⇒ 跨站页面
   伪造不出来 (上一版只能靠 `SameSite=Lax`, 而它挡不住顶层导航);
2. **问句不再进日志**: 上一版问句在 query 里, 每一句都会落进访问日志 (订单号、
   地址这些都会跟着进去), 现在走请求体 (读历史那条也一样 —— 会话编号从查询串搬进
   了请求体);
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
| 工具行 (工具名 + 一句中文短语) | 助手服务给的那句 `label` (ADR-0003) |

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
CANCEL_PATH = "/runs/{run_id}/cancel"
HISTORY_PATH = "/history"
CONVERSATIONS_PATH = "/conversations"
# 三个管理动作 (#20): 都挂在列表端点下面, 会话编号一律走 `X-Conversation-Id` 头
CONVERSATION_TITLE_PATH = "/conversations/title"
CONVERSATION_PIN_PATH = "/conversations/pin"
CONVERSATION_DELETE_PATH = "/conversations/delete"
MESSAGE_FIELD = "message"
# 搜索词在上游是**查询串**里的 `q` (框架那条列表路由的契约), 而在浏览器这边是
# 请求体里的 `query` —— 两个名字不是笔误: 那是两套 wire 契约 (见模块 docstring
# 「不 import CharApp」那一段), 这一层的工作之一就是把它们对上.
UPSTREAM_QUERY_FIELD = "q"
QUERY_FIELD = "query"

# 运行编号: 响应头上带出去 (`X-Run-Id`), 取消时从请求体里收回来. 形状是框架
# `new_run_id()` 发的那个 (uuid4 的 hex), 这里按**传输契约**再声明一遍 —— 它要进
# URL 的路径, 不合形状的一律挡在门外 (见 `valid_run_id`).
RUN_ID_HEADER = "X-Run-Id"
RUN_ID_FIELD = "run_id"
RUN_ID_LENGTH = 32
_HEX_DIGITS = frozenset("0123456789abcdef")

# 浏览器打进来的那几个字段 (页面按它发, 这里按它认)
CONVERSATION_FIELD = "conversation_id"
TITLE_FIELD = "title"
PINNED_FIELD = "pinned"

# 标题长度上限 (与助手服务那条线同值: `CharAgent/server/conversations.py` 的
# MAX_TITLE_LENGTH). BFF 也卡一道不是重复劳动 —— 这一层卡住了, 用户看到的是
# 「名字太长」; 让它穿到上游再被打回来, 用户看到的是同一句话但要绕一趟网络.
MAX_TITLE_LENGTH = 100

# 列会话那条路带上去的 `X-Conversation-Id`: **空值**, 而且是有意的.
#
# 它问的是「我聊过哪几段」, 不针对某一段对话 —— 但三个头一直是一组, 单独少带一个
# 就变成"另一份拼法" (见 `_service_headers`, 那里正是被漏带害过). 助手服务把空着
# 或缺失的这个头兜成它自己的默认段, 而列会话只按**租户与买家**过滤 —— 这个值是什么
# 都不影响结果 (它连会话编号都不回显).
NO_CONVERSATION_ID = ""

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

# 取消请求的超时 (秒): 比上面短得多, 因为那边**立刻**就答 —— 它只把任务标记成
# 取消 (`task.cancel()`), 不等运行收尾 (权威信号在那条 SSE 流上). 十秒还没回,
# 说明出事的不是「这次运行」而是那条链路, 早点告诉用户比继续等强.
CANCEL_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# 读历史那条路的超时 (与取消同档): 它也是一次普通请求/响应, 而且答得更快 ——
# 那边只是把会话手上那份历史读出来, 不跑模型.
HISTORY_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# 列会话那条路的超时 (与上面两条同档): 同样是一次库读, 不跑模型. 单列一个常量而
# 不是复用 HISTORY_TIMEOUT —— 三条路现在同值只是巧合 (取消那条的注释里写了它自己
# 的理由), 合并之后想单独调其中一条, 就得先把它们拆回来.
CONVERSATIONS_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# 终局事件 (框架的契约: 一条流里恰好一个, 之后流才收线)
TERMINAL_EVENTS = frozenset({"final", "error"})

# 本层自己补的两个错误码 (框架不会发这两个)
UNAVAILABLE_CODE = "agent_unavailable"
INTERRUPTED_CODE = "stream_interrupted"

# 上游「这次运行不在了」那个码 (框架 `RunNotFoundError` 的默认码). 取消那条路上它
# 是唯一一个**能原样转给浏览器**的失败 —— 别的 404 都是接线问题, 见 `forward_cancel`.
RUN_NOT_FOUND_CODE = "run_not_found"

# 上游「这段会话不在了」那个码 (框架 `ThreadNotFoundError` 的默认码, #20). 三个
# 管理动作 (改名 / 置顶 / 删除) 上它能原样转给浏览器 —— 理由与上面那条一模一样:
# 用户可能只是在另一个标签页里删掉了它, 那不是故障. 其余 404 是接线问题.
THREAD_NOT_FOUND_CODE = "thread_not_found"

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
    RUN_NOT_FOUND_CODE: "这次回答已经结束了.",  # 按停止时它正好答完 —— 不是故障
    UNAVAILABLE_CODE: "客服暂时联系不上, 请稍后再试.",
    INTERRUPTED_CODE: "回答中途断开了, 请重新问一次.",
    # 请求本身不合法 (本层判的, 轮不到上游). 这几条理论上只有前端出 bug 才会走到,
    # 但用户看到的仍得是一句能照着做的话 —— 不是什么「参数非法」.
    "invalid_request": "这条消息没能发出去, 重说一遍试试.",
    "invalid_message": "这句话是空的, 说点什么再发吧.",
    "message_too_long": "这句话太长了, 我一次读不完 —— 拆短一点再问吧.",
    "invalid_conversation_id": "这次对话的连接坏了, 刷新页面再问一次.",
    "invalid_run_id": "这次没能停下来, 刷新页面再看看.",
    "method_not_allowed": "这条请求的方式不对, 刷新页面再问一次.",
    # 三个管理动作 (#20) 自己的码. 上游 (框架) 那边的码是事实描述, 这里是照着
    # 用户能做什么写的 —— 与上面那一批同一条规矩.
    "invalid_title": "给这段对话起个名字吧, 空名字在列表上会是一行空白.",
    "title_too_long": "名字太长了, 短一点再试.",
    "invalid_pinned": "这次置顶没能生效, 刷新页面再试一次.",
    "thread_not_found": "这段对话已经不在列表里了, 刷新看看.",
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

    只动 error 一类的理由: 那条 message 是**写给开发者的**, 摆给用户看既看不懂也不
    可操作 —— 而且它**不一定**是框架那句事实说明: 有的码 (真机上 `run_failed` 就是)
    直接带异常文本, 里面可能有模型上游回的正文 (issue 29 在真机上见过那一串). 换成
    人话既对用户友好, 也顺手把那段正文挡在浏览器之外. 别的事件要么是给人看的正文
    (final), 要么是助手服务已经写好的人话 (tool_* 那一句 `label` / thinking /
    reasoning), 改它们等于替别人写话.

    解析失败 (帧不合契约) 就原样转发: 让用户看到一句难看的话, 好过把终局事件
    吞掉 —— 后者会让前端一直等下去. (这一条**真的**把原文放给了浏览器, 所以上面
    那条路径只适用于「能解析」的那一大类; 日志那边也因此不该再存一份, 见下面那行
    `logger.warning`.)
    """
    if _event_name(frame) != "error":
        return frame
    raw = _field(frame, b"data")
    try:
        payload = json.loads(raw) if raw else None
        error = payload["error"]
        code = error["code"]
    except (ValueError, TypeError, KeyError) as exc:
        # 记「哪条帧、多少字节、解析为什么失败」, **不记帧的原文** (issue 29): 这一帧
        # 本来就会**原样转发给浏览器** (下面那行 return), 要查它看页面收到的就行 ——
        # 日志不必再存一份 (那一份会进检索与备份, 而这条帧的 message 里可能带着上游
        # 异常文本, 见 `_with_user_copy` 的说明).
        logger.warning(
            "上游的 error 帧解析不了 (%s), 原样转发: %d 字节",
            type(exc).__name__,
            len(frame),
        )
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


def _refusal_code(response: httpx.Response) -> str:
    """上游拒绝转发时它给的错误码 (拿不到就退回「服务不可用」).

    框架的拒绝响应是 `{"error": {"code", "message"}}` (与事件流同一个形状), 那个
    code 是**框架特意留给业务用的**: 按它决定用户文案与要不要重试
    (`CharAgent/server/utils/errors.py` 开头就写着这条分工). 所以这里原样接下来,
    别把它塌成一句「反正是失败」—— `thread_busy` (上一句还没答完) 与
    `unauthorized` (令牌配错了) 对用户是两回事: 前者等一等就好, 后者他做什么
    都没用.

    Note:
        **这也是日志里该记的那一半** (issue 29): 拒绝时记「状态码 + 这个码」就够定位,
        上游的响应正文**不进日志** —— 那是**第三方返回的正文**, 本层没有它的字段知识
        (不知道第 137 个字符是订单号还是商品名), 拿它去猜着脱敏正是 #26 说的碰运气.
        正文本来就只在这里被读一次 (为了取这个码), 别的地方也不必读它.
    """
    try:
        response.read()
        return response.json()["error"]["code"]
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return UNAVAILABLE_CODE


def _log_refusal(
    who: str, action: str, response: httpx.Response, *, level: int = logging.WARNING
) -> str:
    """上游拒绝时记一条日志: **状态码 + 错误码**, 把那个码返回给调用方 (issue 29).

    三条路 (流式转发 / `_call_upstream` 那六条 / 取消) 共用这一处, 因为「拒绝时记
    什么」是一条**纪律**, 不是三处巧合: 有状态码与错误码就够定位, 上游的响应**正文
    一个字都不记** (那是第三方返回的正文, 本层没有按字段脱敏它的知识 —— 见
    `_refusal_code` 的 Note). 收在一处之后, 想把正文加回来就得改这一个函数,
    而不是在三条路里各塞一行.

    Args:
        who: 谁 (买家 + 会话编号), 只进日志.
        action: 要做什么 (转发 / 读历史 / 列会话 / 取消...), 只进日志.
        response: 上游的非 200 响应.
        level: 流式那条路用 ERROR (用户当场拿不到答复), 其余用 WARNING.

    Returns:
        str: 上游给的错误码 (`_refusal_code` 取的那个; 拿不到就是「服务不可用」).
    """
    code = _refusal_code(response)
    logger.log(
        level,
        "%s: 客服服务拒绝了这次%s: HTTP %d (%s)",
        who,
        action,
        response.status_code,
        code,
    )
    return code


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


def conversation_id_from(value: object, user_id: int) -> str:
    """从请求里的某个值取会话编号; 不合法就抛 `RefusedError` (400).

    三个端点都要它 (chat / cancel 从请求体的字段取, history 从查询串取), 所以收成
    一处: 校验规则 (`valid_conversation_id`) 与那句日志只该有一份 —— 三条路上
    「怎么算不合法」分家了, 表现是某一条路悄悄放行了另两条拦下的东西.

    Args:
        value: 请求里那个原始值 (可能压根不是字符串 —— 请求体里什么都能塞).
        user_id: 这次请求的身份 (长度上限按整串 thread_id 算, 里面含它).

    Raises:
        RefusedError: 空 / 超长 / 含空白或非 ASCII (400 + `invalid_conversation_id`).
    """
    conversation_id = value.strip() if isinstance(value, str) else ""
    if not valid_conversation_id(conversation_id, user_id):
        logger.warning("买家 %s: 会话编号不合法: %r", user_id, value)
        raise RefusedError(400, "invalid_conversation_id")
    return conversation_id


def _payload_from_request(request, user_id: int, action: str) -> dict:
    """请求体 → JSON 对象 (五条读字段的路共用的第一段).

    `action` 只说这次要做什么 (提问 / 取消 / 读历史 / 改名 / 置顶), 用来把日志写得
    像人话. 抽成一处是因为「解析 + 必须是对象」跟字段无关 —— 抄五遍的话, 迟早有
    一条路的报错与另几条不一样, 而排查的人是按日志找的.

    Raises:
        RefusedError: 正文不是合法 JSON 对象 (400 + `invalid_request`).
    """
    try:
        payload = json.loads(request.body)
    except ValueError as exc:
        logger.warning("买家 %s: %s请求体不是合法 JSON: %s", user_id, action, exc)
        raise RefusedError(400, "invalid_request") from exc
    if not isinstance(payload, dict):
        logger.warning("买家 %s: %s请求体应为 JSON 对象", user_id, action)
        raise RefusedError(400, "invalid_request")
    return payload


def question_from_request(request, user_id: int) -> Question:
    """请求体 → 一次问句; 哪里不合契约就抛 `RefusedError` (400).

    Raises:
        Refused: 正文不是 JSON 对象 / 缺字段 / 问句为空或过长 / 会话编号不合法.
            给用户的文案取自 `ERROR_COPY`, 给开发者的那份理由 (哪个字段坏了) 走日志.
    """
    payload = _payload_from_request(request, user_id, "提问")
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

    conversation_id = conversation_id_from(payload.get(CONVERSATION_FIELD), user_id)

    return Question(message=message, conversation_id=conversation_id)


def valid_run_id(value: str) -> bool:
    """这个运行编号能不能原样拼进 URL 的路径.

    为什么**必须**卡: 它要去的地方是 `{基地址}/runs/{run_id}/cancel` —— 一个带
    `/`、`?`、`#` 或者 `..` 的编号能把这次请求指到同一台机器上的**另一个端点**去
    (它手上还攥着内部令牌与 X-User-Id). 卡形状比事后转义牢: 框架发的编号就是
    32 位十六进制 (`new_run_id` 的 uuid4.hex), 别的形状只可能是伪造的.
    """
    return len(value) == RUN_ID_LENGTH and all(char in _HEX_DIGITS for char in value)


@dataclass(frozen=True, slots=True)
class Cancellation:
    """浏览器要停的那次运行 (BFF 从请求体里只认这两样)."""

    run_id: str
    conversation_id: str


def cancellation_from_request(request, user_id: int) -> Cancellation:
    """请求体 → 一次取消; 哪里不合契约就抛 `RefusedError` (400).

    与 `question_from_request` 同一套做法 (同一份"给开发者的理由进日志"的规矩),
    只是认的字段不同: 取消没有问句, 只有一个"哪一次运行".

    Raises:
        RefusedError: 正文不是 JSON 对象 / 缺字段 / 编号形状不对 / 会话编号不合法.
    """
    payload = _payload_from_request(request, user_id, "取消")
    raw = payload.get(RUN_ID_FIELD)
    run_id = raw.strip() if isinstance(raw, str) else ""
    if not valid_run_id(run_id):
        # 日志里只留**长度与前 40 个字符**: 这个值可能是伪造的 (比如塞了一整条路径
        # 或一大坨垃圾), 整段照抄进日志既没用又脏 (与"问句不进日志"同一个道理);
        # 而长度本身就是排查要看的第一样东西 (笔误 vs 塞了一整页)
        logger.warning(
            "买家 %s: 运行编号形状不对 (长度 %d): %r",
            user_id,
            len(run_id),
            run_id[:40],
        )
        raise RefusedError(400, "invalid_run_id")

    conversation_id = conversation_id_from(payload.get(CONVERSATION_FIELD), user_id)

    return Cancellation(run_id=run_id, conversation_id=conversation_id)


@dataclass(frozen=True, slots=True)
class Rename:
    """浏览器要给哪段对话改成什么名字 (BFF 从请求体里只认这两样)."""

    conversation_id: str
    title: str


def rename_from_request(request, user_id: int) -> Rename:
    """请求体 → 一次改名; 哪里不合契约就抛 `RefusedError` (400).

    空标题**拒掉**: 列表上那一行会变成空白, 看着像坏了 (与上游同一条判据, 这里先
    卡一道 —— 见 `MAX_TITLE_LENGTH` 那段的理由).

    Raises:
        RefusedError: 正文不成形 / 缺 `title` / 空 / 过长 / 会话编号不合法.
    """
    payload = _payload_from_request(request, user_id, "改名")
    raw = payload.get(TITLE_FIELD)
    title = raw.strip() if isinstance(raw, str) else ""
    if not title:
        logger.warning(
            "买家 %s: 改名的请求体缺少非空字符串字段 %s", user_id, TITLE_FIELD
        )
        raise RefusedError(400, "invalid_title")
    if len(title) > MAX_TITLE_LENGTH:
        logger.warning(
            "买家 %s: 标题过长 (%d 字符, 上限 %d)",
            user_id,
            len(title),
            MAX_TITLE_LENGTH,
        )
        raise RefusedError(400, "title_too_long")
    conversation_id = conversation_id_from(payload.get(CONVERSATION_FIELD), user_id)
    return Rename(conversation_id=conversation_id, title=title)


@dataclass(frozen=True, slots=True)
class Pin:
    """浏览器要把哪段对话置顶还是取消置顶 (BFF 从请求体里只认这两样)."""

    conversation_id: str
    pinned: bool


def pin_from_request(request, user_id: int) -> Pin:
    """请求体 → 一次置顶/取消; 哪里不合契约就抛 `RefusedError` (400).

    `pinned` **必须是真布尔**: 传 `"true"` / `1` / 缺字段都拒. 放水的话表现是
    「置顶一直生效、取消置顶怎么点都不动」—— 半好半坏最难查, 与上游同一条判据.

    Raises:
        RefusedError: 正文不成形 / `pinned` 不是布尔 / 会话编号不合法.
    """
    payload = _payload_from_request(request, user_id, "置顶")
    pinned = payload.get(PINNED_FIELD)
    if not isinstance(pinned, bool):
        logger.warning(
            "买家 %s: 置顶的请求体缺少布尔字段 %s, 收到 %s",
            user_id,
            PINNED_FIELD,
            type(pinned).__name__,
        )
        raise RefusedError(400, "invalid_pinned")
    conversation_id = conversation_id_from(payload.get(CONVERSATION_FIELD), user_id)
    return Pin(conversation_id=conversation_id, pinned=pinned)


def conversation_to_delete_from_request(request, user_id: int) -> str:
    """请求体 → **要删的那段**会话编号; 不合法就抛 `RefusedError` (400).

    名字写成「要删的那段」而不是「已删除的」: 它返回的是**待办**里的那个编号,
    不是删完之后的什么东西.

    只认这一个字段 (删除没有第二个参数), 所以不另造一个 dataclass ——
    `Rename` / `Pin` 那种容器是为了把**两个**字段捆着传, 一个字段装不下任何别的
    东西, 包一层只是多一个名字要记.

    Raises:
        RefusedError: 正文不成形 / 会话编号不合法.
    """
    payload = _payload_from_request(request, user_id, "删除")
    return conversation_id_from(payload.get(CONVERSATION_FIELD), user_id)


def search_query_from_request(request, user_id: int) -> str | None:
    """请求体 → 搜索词 (没给 / 空串 / **压根没发体** = 不搜).

    与读历史一样, 列表那条也是 POST + 请求体: 浏览器这一侧**不把任何东西放进
    URL** —— 搜索词里完全可能有订单号.

    「没发请求体」是合法的, 而且是有意保留的:**不搜的时候本来就没有参数要传**.
    列会话是唯一一条请求体可选的写形状 (另几条都得有个字段才能干活), 所以空体在
    这里等于「一个筛选条件都不给」, 而不是「请求不合法」—— 逼着前端每次都发一个
    `{}` 只是形式主义.
    """
    if not request.body:
        return None
    payload = _payload_from_request(request, user_id, "列会话")
    raw = payload.get(QUERY_FIELD)
    query = raw.strip() if isinstance(raw, str) else ""
    return query or None


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


def _url(path: str) -> str:
    """上游某个端点的地址 (基地址写在 settings 里, 部署期可换).

    **只有这一处拼基地址** —— 五条转发路各自拼一遍的话, 换部署地址就得记得改五处,
    而漏掉的那一条表现是「只有某个功能连不上」, 排查时最难想到的就是地址本身.
    """
    return f"{settings.CHARAPP_SERVER_URL.rstrip('/')}{path}"


def _internal_token(who: str, action: str) -> str:
    """取内部令牌; 没配就**一个请求都不发** (与商城侧同一条纪律: fail closed).

    说清是哪个变量没配, 而不是让人对着「客服暂时联系不上」去猜 CharApp 为什么没起来.
    `action` 只说这次要做什么 (转发 / 取消), 用来把那行日志写得像人话.

    Raises:
        RefusedError: 令牌没配 (503 + `agent_unavailable`).
    """
    token = settings.CHARAPP_INTERNAL_TOKEN
    if not token:
        logger.error(
            "%s: CHARAPP_INTERNAL_TOKEN 未配置, %s无处可去 (与商城侧 settings 同值)",
            who,
            action,
        )
        raise RefusedError(503, UNAVAILABLE_CODE)
    return token


def _service_headers(token: str, user_id: int, conversation_id: str) -> dict[str, str]:
    """打助手服务时那三个头 (**一个 wire 契约, 只有这一处拼**).

    分开写两份的代价很具体: 少带 `X-Conversation-Id` 时, 助手服务会按缺省的那一段
    会话去比对 —— 于是「取消自己的运行」这件事**每次都失败**, 而且报的是「不是你的
    运行」. 这种错在代码里看不出来, 只能靠"只写一份"来防.
    """
    return {
        HEADER_TOKEN: token,
        HEADER_USER_ID: str(user_id),
        HEADER_CONVERSATION_ID: conversation_id,
    }


def _who_for(user_id: int, conversation_id: str | None = None) -> str:
    """日志前缀: 跨进程的故障要能按**买家**追溯 (系统级规范 §6.3).

    翻日志的人手上通常只有一个「谁反映的」, 没有别的线索.

    `conversation_id` 可以不给 (或给空): 列会话那条路问的是「我聊过哪几段」, 本来就
    没有哪一段对话可言 —— 那时前缀只报到买家 (拼一个空段进去, 日志看着像缺了一块).
    空串与 None 同等对待, 是因为列会话那条路上游要的正是 `NO_CONVERSATION_ID`.
    """
    if not conversation_id:
        return f"买家 {user_id}"
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
    headers = _service_headers(
        _internal_token(who, "转发"), user_id, question.conversation_id
    )
    stack = contextlib.ExitStack()
    try:
        client = stack.enter_context(httpx.Client(timeout=UPSTREAM_TIMEOUT))
        try:
            response = stack.enter_context(
                client.stream(
                    "POST",
                    _url(RUNS_PATH),
                    json={MESSAGE_FIELD: question.message},
                    headers=headers,
                )
            )
        except httpx.HTTPError as exc:
            # 连接被拒 / 握手超时: 上游根本没答上来
            logger.error("%s: 连不上客服服务: %s: %s", who, type(exc).__name__, exc)
            raise RefusedError(502, UNAVAILABLE_CODE) from exc

        if response.status_code != 200:
            code = _log_refusal(who, "转发", response, level=logging.ERROR)
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


def _cancel_url(run_id: str) -> str:
    """取消端点地址 (编号已经过 `valid_run_id`, 拼进路径是安全的)."""
    return _url(CANCEL_PATH.format(run_id=run_id))


def _call_upstream(
    *,
    method: str,
    path: str,
    user_id: int,
    conversation_id: str,
    timeout: httpx.Timeout,
    action: str,
    params: dict[str, str] | None = None,
    payload: dict | None = None,
    not_found_code: str | None = None,
) -> bytes:
    """打一次「普通请求/响应」型的上游调用; 失败就抛 `RefusedError`.

    | 情况 | 状态 | 码 |
    |------|------|----|
    | 本机没配令牌 (我们自己的问题) | 503 | `agent_unavailable` |
    | 连不上 / 超时 / 基地址写错 | 502 | `agent_unavailable` |
    | 上游回非 200 | 502 | **上游给的那个码** |

    五条路 (读历史 / 列会话 / 改名 / 置顶 / 删除) 都走它 —— 分头写五遍的代价很
    具体: 其中一条忘了把 `httpx.InvalidURL` 收进 except (基地址端口写错就够),
    表现是那条路冒成 500 而别的路回 502, 排查的人得先猜是哪一条.

    **三个头也在这里拼** (`_service_headers`): 五个调用方各拼一遍, 就等于把
    「身份从哪儿来」这条纪律抄了五份 —— 而它出过的错 (某个头漏带) 正是因为抄了
    第二份.

    与 `relay()` 的分工: 那条路搬的是**流**(逐帧透传), 这条搬的是一次普通响应的
    正文 (整段字节). 两边都不解析上游的内容 —— 响应体里有哪些字段是助手服务那侧
    的契约, 本层照着转; 它要是改了形状, 该跟着改的是页面, 不是这一层.

    Args:
        method: GET (两条读路) 或 POST (三条写动作).
        path: 上游端点 (本文件顶上那几个 `*_PATH`).
        user_id: 这次请求的买家 —— 只可能来自 `request.user.pk`.
        conversation_id: 哪一段对话; 列会话那条给 `NO_CONVERSATION_ID` (空值).
        timeout: 这条路的耐心 (三条读路同档, 三条写路也同档).
        action: 要做什么 (读历史 / 列会话 / 改名...), 只进日志.
        params: 查询串 (只有列会话那条的搜索词会用到).
        payload: 请求体 (只有三条写动作会用到).
        not_found_code: 上游回的 404 **带着这个码**时, 把 404 原样转给浏览器;
            见下.

    Returns:
        bytes: 上游的响应体 (原样, 不重新编码).

    Raises:
        RefusedError: 见上表 (令牌没配是 503, 其余是 502); 给了 `not_found_code`
            且上游正好回那个码时, 是 **404**.
    """
    who = _who_for(user_id, conversation_id)
    headers = _service_headers(_internal_token(who, action), user_id, conversation_id)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(
                method, _url(path), headers=headers, params=params, json=payload
            )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # `InvalidURL` 不是 `HTTPError` (基地址写错就够), 但它同样属于「这条链路
        # 根本没走通」, 该回同一句话而不是冒成 500
        logger.error("%s: %s请求发不出去: %s: %s", who, action, type(exc).__name__, exc)
        raise RefusedError(502, UNAVAILABLE_CODE) from exc

    if response.status_code == 200:
        return response.content
    code = _log_refusal(who, action, response)
    # 「这段会话不在了」是唯一一个对用户有意义的答案 (两个标签页里删掉了、别人替它
    # 删了) —— 那不是故障, 页面该照实说「它已经从列表里消失」. 别的 404 一律当接线
    # 故障 (对面根本没有这条路由 = 版本不齐 / 地址打错), 塌成 502.
    #
    # 判据必须带上**那个码**: 一个不带 `thread_not_found` 的 404 说明路由不在,
    # 放过去的话页面会把它当成"已删除"静默吞掉, 而实际是接线坏了 —— 与取消那条
    # 路同一个坑, 见 `forward_cancel`.
    if response.status_code == 404 and code == not_found_code:
        raise RefusedError(404, code)
    raise RefusedError(502, code)


def forward_history(user_id: int, conversation_id: str) -> bytes:
    """读一段对话聊过什么 (上路, 见 `_call_upstream` 的失败分法).

    用途只有一个: 浏览器刷新会把页面那一份渲染丢光, 得有个地方把聊过的话再取一遍.
    """
    return _call_upstream(
        method="GET",
        path=HISTORY_PATH,
        user_id=user_id,
        conversation_id=conversation_id,
        timeout=HISTORY_TIMEOUT,
        action="读历史",
    )


def forward_conversations(user_id: int, *, query: str | None = None) -> bytes:
    """列「这个买家聊过哪几段」(上路); 给了 `query` 就只回命中的那些.

    **「换一个买家看不到别人的」不在这层**: 判据在助手服务那侧 (按转发过去的
    `X-User-Id` 过滤, 见 `CharAgent/server/conversations.py`); 本层能保证的是
    「转过去的一定是 session 里那个人」—— 也就是 `_service_headers` 里的身份只可能
    来自 `request.user.pk`.

    Args:
        user_id: 这次请求的买家 (BFF 只从 session 取).
        query: 搜索词; None 表示不搜 (浏览器那一侧给的就是"没填"或空串).

    Returns:
        bytes: 上游的响应体 (原样, 不重新编码).
    """
    return _call_upstream(
        method="GET",
        path=CONVERSATIONS_PATH,
        user_id=user_id,
        conversation_id=NO_CONVERSATION_ID,
        timeout=CONVERSATIONS_TIMEOUT,
        action="列会话",
        # 搜索词走**上游的查询串** (框架那条路的契约就是 `?q=`), 而它在浏览器那侧
        # 走的是请求体 —— 两套 wire 契约各按各的形状, 这一层负责对上.
        # 会话编号仍然只走头 (上面那条纪律), 不进地址.
        params=(None if not query else {UPSTREAM_QUERY_FIELD: query}),
    )


def forward_rename(user_id: int, rename: Rename) -> bytes:
    """把「改标题」转给助手服务 (上路); 会话编号走头, 新名字走请求体."""
    return _call_upstream(
        method="POST",
        path=CONVERSATION_TITLE_PATH,
        user_id=user_id,
        conversation_id=rename.conversation_id,
        timeout=CONVERSATIONS_TIMEOUT,
        action="改标题",
        not_found_code=THREAD_NOT_FOUND_CODE,
        payload={TITLE_FIELD: rename.title},
    )


def forward_pin(user_id: int, pin: Pin) -> bytes:
    """把「置顶 / 取消置顶」转给助手服务 (上路)."""
    return _call_upstream(
        method="POST",
        path=CONVERSATION_PIN_PATH,
        user_id=user_id,
        conversation_id=pin.conversation_id,
        timeout=CONVERSATIONS_TIMEOUT,
        action="置顶",
        not_found_code=THREAD_NOT_FOUND_CODE,
        payload={PINNED_FIELD: pin.pinned},
    )


def forward_delete(user_id: int, conversation_id: str) -> bytes:
    """把「删除」转给助手服务 (上路) —— 不带请求体, 删哪段由头说了算."""
    return _call_upstream(
        method="POST",
        path=CONVERSATION_DELETE_PATH,
        user_id=user_id,
        conversation_id=conversation_id,
        timeout=CONVERSATIONS_TIMEOUT,
        action="删除会话",
        not_found_code=THREAD_NOT_FOUND_CODE,
    )


def forward_cancel(user_id: int, cancellation: Cancellation) -> None:
    """把取消请求转给助手服务; 它拒绝就抛 `RefusedError` (原样带上它的码).

    与 `open_upstream` 同一套分法, 但简单得多 —— 这里没有流要透传, 就是一次普通的
    请求/响应:

    | 情况 | 状态 | 码 |
    |------|------|----|
    | 本机没配令牌 (我们自己的问题) | 503 | `agent_unavailable` |
    | 连不上 / 超时 | 502 | `agent_unavailable` |
    | 上游说这次运行不在了 | **404** | `run_not_found` |
    | 别的非 200 (含**不带那个码**的 404) | 502 | 上游给的那个码 |

    上游的 404 之所以**照原样转给浏览器** (而不是也塌成 502): 它是唯一一个对用户
    有意义的答案 —— 「你按停止的时候它刚好答完了」不是故障, 而是每次都可能遇到的一次
    正常竞争; 页面据此**不打扰用户** (那一轮的终局事件本来也到了). 别的一律当故障,
    因为那些 (401 / 503) 都说明我们这边没配好, 用户做什么都没用.

    Note:
        「404 才转 404」这一条要求那个 404 带的是**那个码** (`run_not_found`): 一个
        不带它的 404 说明对面根本没有这条路由 (版本不齐 / 地址打错), 那是接线故障.
        不这么分的话, 这种故障会被页面当成"正常竞争"静默吞掉 —— 停止按钮从此按不动,
        而且一句解释都没有, 比报错还难查.
    """
    who = _who_for(user_id, cancellation.conversation_id)
    headers = _service_headers(
        _internal_token(who, "取消"), user_id, cancellation.conversation_id
    )
    try:
        with httpx.Client(timeout=CANCEL_TIMEOUT) as client:
            response = client.post(_cancel_url(cancellation.run_id), headers=headers)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # `InvalidURL` 不是 `HTTPError` (基地址写错就够), 但它同样属于"这条链路根本
        # 没走通", 该回同一句话, 而不是让它冒成一个 500
        logger.error("%s: 取消请求发不出去: %s: %s", who, type(exc).__name__, exc)
        raise RefusedError(502, UNAVAILABLE_CODE) from exc

    if response.status_code == 200:
        return
    code = _log_refusal(who, f"取消 (运行 {cancellation.run_id})", response)
    if response.status_code == 404 and code == RUN_NOT_FOUND_CODE:
        raise RefusedError(404, code)
    raise RefusedError(502, code)


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------


class AgentPageView(LoginRequiredMixin, TemplateView):
    """客服页面 (原生 JS + `fetch` + `eventsource-parser`; 与商城前端同一套零构建风格).

    Note:
        页面加载时拉一次历史 (`/minimall/agent/history/`) 并把聊过的话渲染出来 ——
        会话编号存在 `sessionStorage` 里, 所以刷新之后还是同一段对话 (见 agent.html).
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

        headers = {
            # 缓存 SSE 的中间层会把它变成「跑完才到」; 后者是 nginx 的对应开关
            "cache-control": "no-cache",
            "x-accel-buffering": "no",
        }
        # 本次运行的编号带给浏览器: 它是页面上按「停止」时唯一能指名道姓的东西.
        # 上游**总是**带这个头 (框架 `create_app`), 所以"没有"只可能是中间层把它吞了
        # —— 那时不给这个头 (页面上就不显示停止按钮), 而不是塞个空值骗前端.
        if run_id := upstream.response.headers.get(RUN_ID_HEADER):
            headers[RUN_ID_HEADER] = run_id

        return StreamingHttpResponse(
            upstream, content_type="text/event-stream", headers=headers
        )

    def http_method_not_allowed(self, request, *args, **kwargs) -> HttpResponse:
        """GET 之类一律拒掉, 并说清为什么 (而不是默认那句干巴巴的 405).

        默认响应体是空的 —— 而这条 405 是**有意的设计**(别把有副作用的动作做成
        一个链接/一张图片就能触发的 GET), 值得写明白, 免得以后有人"顺手"补一个
        `def get` 上去.
        """
        logger.warning("BFF 端点收到 %s (只认 POST): %s", request.method, request.path)
        return _refusal(405, "method_not_allowed")


class AgentCancelView(LoginRequiredMixin, View):
    """BFF 端点: 停一次正在跑的回答 (POST) —— 与 `AgentChatView` 同一套前置.

    Note:
        「停好了」这件事**不在这里**: 那一轮的 SSE 流仍由 `AgentChatView` 那条路读,
        服务端补的终局事件 (`error` / `cancelled`) 会顺着它回到页面. 所以本端点的
        响应只是「取消请求收到了 / 已经被拒了」, 页面不该拿它当收尾信号.

        **取消是协作式的, 而且只对只读的这一步安全.** 它打断的是那次运行**正在等的
        那个 await**, 不是已经发出去的东西 —— 一个已经打到商城服务器的请求不会因为
        我们这边不等了而回滚. L1b 的工具全是只读查询, 所以怎么取消都不会留下半截
        状态; 到了能改数据的阶段 (加购 / 下单 / 支付), 这里要按
        `CharAgent/docs/DESIGN.md` 的 #17/#18 重想: 副作用要带幂等键, 取消时要走
        补偿, 而不是「不等了就算停」. 这条不是待办, 是**前提变了就得回来改**的标记.
    """

    def post(self, request):
        # 身份仍然只从 session 取 (与 chat 同一行代码同一个理由): 请求体里塞别人的
        # user_id 也改不了这次转发带的是谁 —— 而"能不能取消别人的运行"正是在助手
        # 服务那侧按这个身份判的 (会话编号里含买家)
        buyer_id = request.user.pk
        try:
            cancellation = cancellation_from_request(request, buyer_id)
            forward_cancel(buyer_id, cancellation)
        except RefusedError as refused:
            return _refusal(refused.status, refused.code)
        # 这个 body 是**本层自己的话**, 不是转发上游的: 它说的是「请求收到了」, 而
        # 上游那个 `status` 字段只在它自己那侧有意义 (页面只看状态码, 不看这里)
        return JsonResponse({"run_id": cancellation.run_id, "status": "cancelling"})

    def http_method_not_allowed(self, request, *args, **kwargs) -> HttpResponse:
        """同 `AgentChatView`: 取消是一个有副作用的动作, 不做成 GET."""
        logger.warning("BFF 端点收到 %s (只认 POST): %s", request.method, request.path)
        return _refusal(405, "method_not_allowed")


class AgentHistoryView(LoginRequiredMixin, View):
    """BFF 端点: 读这段对话聊过什么 (POST) —— 刷新页面之后把聊过的话拿回来.

    Note:
        **这条读操作也用 POST** (2026-09-22 从 GET 改过来, 与 `adr/0002` 同一条思路):
        会话编号是**私密数据** —— 一旦进 URL, 它就跟着进访问日志 / 浏览器历史 /
        Referer, 而「读谁的对话」正是由它决定的. 更直接的一条: GET + cookie 是
        **跨站可触发**的 (`<img src=".../history/?conversation_id=…">` 一行就能让
        别人的浏览器发出这条请求), 换 POST 之后由 Django 的 CSRF 中间件接管.

        改完的另一个好处是四条路**形状一致**: 都 POST + CSRF, 都从
        `request.user.pk` 取身份, 页面那边一套写法 (三条带 JSON 请求体, 列会话
        那条连请求体都不读). 代价是「读操作用了 POST」这点违反 HTTP 语义 —— 这里
        认了: 它面向的是**浏览器**, 而浏览器这一侧的威胁模型比动词的语义更重要.

        **身份仍然只从 session 取** (与 chat / cancel 同一条纪律): 请求体里塞别人的
        `user_id` 改不了这次转发带的是谁 —— 而「读谁的对话」在助手服务那侧按这个
        身份判 (会话编号里含买家).

        **下游那一跳仍然是 GET** (`forward_history`): 它打的是同机同信任域的助手
        服务, 地址里不带任何参数 (会话编号走头), 要内部令牌才进得来 —— 上面那些
        泄漏面一条都不成立, 而那里本来就是一次纯读.
    """

    def post(self, request) -> HttpResponse:
        # 身份只从 session 取 (与 chat / cancel 同一行代码同一个理由)
        buyer_id = request.user.pk
        try:
            conversation_id = conversation_id_from(
                _payload_from_request(request, buyer_id, "读历史").get(
                    CONVERSATION_FIELD
                ),
                buyer_id,
            )
            body = forward_history(buyer_id, conversation_id)
        except RefusedError as refused:
            return _refusal(refused.status, refused.code)
        # 正文原样转给浏览器 (不重新编码, 也不重新拼 JSON): 本层不认识它的内部形状
        return HttpResponse(body, content_type="application/json")

    def http_method_not_allowed(self, request, *args, **kwargs) -> HttpResponse:
        """GET 之类一律拒掉: 会话编号要待在请求体里, 不往 URL 上挂 (见类说明)."""
        logger.warning(
            "BFF 历史端点收到 %s (只认 POST): %s", request.method, request.path
        )
        return _refusal(405, "method_not_allowed")


class AgentConversationsView(LoginRequiredMixin, View):
    """BFF 端点: 列「我聊过哪几段」(POST) —— 左侧列表要的东西 (issue 19 用).

    Note:
        与 `AgentHistoryView` 那一对关系: 那条答「这段对话聊了什么」, 这条答「我
        有哪些对话」. 两条的形状**刻意一模一样** (POST + CSRF + 身份只从 session
        取 + 正文原样透传), 页面那边一套写法.

        请求体里只认 `query` 一个字段 (#20 的搜索), 而且**可以不给** —— 不搜就是
        列全部. 它**要不到会话编号**: 列表本来就是「所有段」, 指名某一段没有意义.

        **为什么读操作用 POST** (与 `AgentHistoryView` 同一条纪律, 见 `adr/0002`):
        它读的是私人数据 (谁的对话列表), 而 GET + cookie 是跨站可触发的 —— 一条
        `<img src=".../minimall/agent/conversations/">` 就能让别人的浏览器替他发出
        这条请求. POST 之后由 Django 的 CSRF 中间件接管; 顺带把**搜索词**也从
        地址栏挪进了请求体 (它里面完全可能有订单号).

        「换一个买家看不到别人的」判据**不在本层**: 转发过去的身份只可能来自
        `request.user.pk` (下面那一行), 而列表在助手服务那侧按它查.
    """

    def post(self, request) -> HttpResponse:
        # 身份只从 session 取 (与另几条同一行代码同一个理由): 请求体里塞谁的 ID
        # 都改不了这次转发带的是谁 —— 而「列哪些会话」在助手服务那侧正是按它查的
        buyer_id = request.user.pk
        try:
            body = forward_conversations(
                buyer_id, query=search_query_from_request(request, buyer_id)
            )
        except RefusedError as refused:
            return _refusal(refused.status, refused.code)
        # 正文原样转给浏览器 (与读历史一致): 本层不认识它的内部形状
        return HttpResponse(body, content_type="application/json")

    def http_method_not_allowed(self, request, *args, **kwargs) -> HttpResponse:
        """GET 之类一律拒掉: 四条路一个形状, 页面才不必按动词分两套写法."""
        logger.warning(
            "BFF 会话列表端点收到 %s (只认 POST): %s", request.method, request.path
        )
        return _refusal(405, "method_not_allowed")


class _AgentConversationActionView(LoginRequiredMixin, View):
    """三个管理动作 (改名 / 置顶 / 删除) 的共同一半: 认证 → 取身份 → 转发 → 透传.

    抽出一个基类而不是把三份视图抄三遍: 它们**除了"把请求体翻成哪种请求"之外逐字
    相同** —— 同一个前缀、同一套 CSRF、同一条「身份只从 session 取」、同一个
    「上游正文原样交回」. 抄三份的话, 将来加一条纪律 (比如限流 / 审计日志) 就得
    记得改三处, 而漏掉的那一处不会有任何报错.

    子类只实现 `_forward`: 把请求与买家 ID 变成一次上游调用 (`RefusedError` 照抛,
    由这里统一翻成响应).
    """

    def _forward(self, request, buyer_id: int) -> bytes:  # pragma: no cover - 见子类
        raise NotImplementedError

    def post(self, request) -> HttpResponse:
        buyer_id = request.user.pk
        try:
            body = self._forward(request, buyer_id)
        except RefusedError as refused:
            return _refusal(refused.status, refused.code)
        # 正文原样转给浏览器 (与另几条一致): 本层不认识它的内部形状
        return HttpResponse(body, content_type="application/json")

    def http_method_not_allowed(self, request, *args, **kwargs) -> HttpResponse:
        """GET 之类一律拒掉: 这几个动作都改数据, 不做成一条链接就能触发的 GET."""
        logger.warning(
            "BFF 会话管理端点收到 %s (只认 POST): %s", request.method, request.path
        )
        return _refusal(405, "method_not_allowed")


class AgentConversationTitleView(_AgentConversationActionView):
    """BFF 端点: 给一段会话改名 (POST) —— `{"conversation_id", "title"}`.

    Note:
        网址里**没有会话编号** (它在请求体里, 由 `_service_headers` 那头带下去),
        与 `/history` 同一条纪律: 编号进 URL 就等于同时进了访问日志 / 浏览器历史 /
        Referer.
    """

    def _forward(self, request, buyer_id: int) -> bytes:
        return forward_rename(buyer_id, rename_from_request(request, buyer_id))


class AgentConversationPinView(_AgentConversationActionView):
    """BFF 端点: 置顶 / 取消置顶 (POST) —— `{"conversation_id", "pinned"}`.

    Note:
        置顶与取消是**同一个端点**: 它们是同一个字段的两个取值, 拆成两条路只会让
        「置顶」这件事有两个地址 (前端也要跟着写两个分支). 形状与改名一致.
    """

    def _forward(self, request, buyer_id: int) -> bytes:
        return forward_pin(buyer_id, pin_from_request(request, buyer_id))


class AgentConversationDeleteView(_AgentConversationActionView):
    """BFF 端点: 删除一段会话 (POST) —— `{"conversation_id"}`.

    Note:
        它在上游是**软删** (行与消息都留着, 见 `ThreadsRepository.soft_delete`),
        所以"删了之后历史还读得到"是有意的: 列表里没了, 但那段记录还在库里.
        页面据此把删掉的当前会话换成一段新的空对话.
    """

    def _forward(self, request, buyer_id: int) -> bytes:
        return forward_delete(
            buyer_id, conversation_to_delete_from_request(request, buyer_id)
        )
