"""Django 转发层 (BFF) 与客服页面的测试 (CharApp issue 06).

接缝: Django 测试客户端 → BFF 端点 (与 `test_agent_api.py` 同一种打法); 上游
(CharApp 服务) 用 respx 拦下来 (那一手借自框架与 CharApp 侧的测试), 于是不必真起
一个 CharApp 进程 —— 那片归 `CharApp/tests/test_server.py`, 两边各测自己那一段,
中间用**帧的形状**对齐.

四类:
1. 转发契约 —— 身份从 session 取 / 问句与对话编号转发 / 帧逐条透传
2. 失败收口 —— 拿到流之前的失败回真状态码, 流中途的失败在流里补终局事件
3. CSRF —— 有副作用的端点挡得住跨站伪造, 而页面自己那个令牌能过
4. 入参校验 —— 不合法的请求不该打到下游去

帧的形状照抄 05 服务的真实输出 (`CharAgent/server/sse.py` 的 sse_frame):
`id: N` + `event: <类型>` + `data: <JSON>` + 空行, data 里带 type / seq / run_id.

一个写法上的注意: 上游连接是在**视图里**同步打开的, 但响应体仍然是流 —— 断言
「上游收到了什么」的用例要把响应体收干 (这也是真机上的顺序: 浏览器读完整条流).
"""

import json

import httpx
import respx
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from app.minimall.models import Category, Product, Profile
from app.minimall.views_bff import (
    MAX_MESSAGE_LENGTH,
    MAX_THREAD_ID_LENGTH,
    THREAD_ID_PREFIX,
    max_conversation_id_length,
)

# 只在这两条**长度**用例里借框架的校验函数: BFF 卡的那条线必须与助手服务真会走的
# 那条线是同一条, 而「另一条线在哪」只有框架自己说了算 (写死一个 128 恰恰是这次
# 出过的错). 别的用例不依赖框架 —— 两边之间是 HTTP, 不是 import.
from CharAgent.checkpoint import CheckpointConfigError, check_identifier

User = get_user_model()

TOKEN = "test-charapp-token"
AGENT_URL = "http://charapp.test"
RUNS_URL = f"{AGENT_URL}/runs"

CHAT_URL = "/minimall/agent/chat/"
PAGE_URL = "/minimall/agent/"
CANCEL_URL = "/minimall/agent/cancel/"
HISTORY_URL = "/minimall/agent/history/"
CONVERSATIONS_URL = "/minimall/agent/conversations/"

BUYER_NAME = "bff_buyer"
OTHER_NAME = "bff_other"

# 写死两个 UUID 形状的对话编号 (前端每标签页生成一个, 见 agent.html)
TAB_ONE = "6f1c2c1e-1a2b-4c3d-8e9f-0a1b2c3d4e5f"
TAB_TWO = "2b7d9a10-aaaa-4bbb-8ccc-111222333444"

# 一次运行的编号 (框架 `new_run_id()` 发的就是 32 位十六进制), 取消要用它
RUN_ID = "9f3a1c2b4d5e6f7081a2b3c4d5e6f708"
CANCEL_UPSTREAM = f"{AGENT_URL}/runs/{RUN_ID}/cancel"

# 助手服务的历史端点 (框架 `CharAgent/server/history.py` 那条路由)
HISTORY_UPSTREAM = f"{AGENT_URL}/history"

# 一次历史响应 (框架那边的形状: 会话编号 + 一段段 user / assistant 的消息)
HISTORY_BODY = {
    "thread_id": f"minimall:{1}:{TAB_ONE}",
    "messages": [
        {"role": "user", "content": "我最近的订单到哪了"},
        {"role": "assistant", "content": "你的订单已发货。"},
    ],
}

# 助手服务的会话列表端点 (框架 `CharAgent/server/conversations.py` 那条路由)
CONVERSATIONS_UPSTREAM = f"{AGENT_URL}/conversations"

# 一次列表响应 (框架那边的形状: 每段一个三元组, 按最后活动时刻倒序)
CONVERSATIONS_BODY = {
    "conversations": [
        {
            "conversation_id": TAB_ONE,
            "title": "我最近的订单到哪了",
            "updated_at": "2026-09-23T10:00:00+08:00",
        }
    ]
}


def _frame(seq: int, event: str, **payload) -> str:
    """一帧 SSE (形状与 05 服务一致)."""
    data = {"type": event, "seq": seq, **payload, "run_id": RUN_ID}
    body = json.dumps(data, ensure_ascii=False)
    return f"id: {seq}\nevent: {event}\ndata: {body}\n\n"


def _sse(
    *frames: str, status: int = 200, content_type: str = "text/event-stream"
) -> httpx.Response:
    """假 CharApp 的响应 (一段流): 帧 + 那个 `X-Run-Id` 头.

    头一并给上 (真服务也是两样都给): 取消要用的编号就来自这里, 缺了它前端按不动
    停止按钮 —— 那种失败不该由一条"只测帧"的用例替我们瞒过去.
    """
    return httpx.Response(
        status,
        content="".join(frames).encode("utf-8"),
        headers={
            "content-type": f"{content_type}; charset=utf-8",
            "X-Run-Id": RUN_ID,
        },
    )


def _question(message: str = "你好", conversation_id: str = TAB_ONE) -> str:
    """一份请求体 (页面发出来的形状) —— 自己造客户端的用例也用它, 别各抄一份."""
    return json.dumps({"message": message, "conversation_id": conversation_id})


def _cancellation(run_id: str = RUN_ID, conversation_id: str = TAB_ONE) -> str:
    """一份取消请求体 (页面按停止时发出来的形状)."""
    return json.dumps({"run_id": run_id, "conversation_id": conversation_id})


def _body(response) -> str:
    """浏览器实际收到的响应体 (把流式响应收干; 只能收一次)."""
    return b"".join(response.streaming_content).decode("utf-8")


def _events(body: str) -> list[str]:
    """响应体里各帧的 event 名, 按到达顺序."""
    return [
        line[len("event: ") :]
        for block in body.split("\n\n")
        for line in block.splitlines()
        if line.startswith("event: ")
    ]


def _payload(body: str, event: str) -> dict:
    """取某个事件那一帧的 data (解析回对象)."""
    for block in body.split("\n\n"):
        lines = block.splitlines()
        if f"event: {event}" in lines:
            for line in lines:
                if line.startswith("data: "):
                    return json.loads(line[len("data: ") :])
    raise AssertionError(f"响应里没有 {event} 事件: {body!r}")


@override_settings(CHARAPP_INTERNAL_TOKEN=TOKEN, CHARAPP_SERVER_URL=AGENT_URL)
class BffTestBase(TestCase):
    """两个买家 + 一个已登录的客户端 (断言里反复要用)."""

    def setUp(self):
        self.buyer = User.objects.create_user(BUYER_NAME, password="TestPass#2026")
        Profile.objects.create(user=self.buyer, balance=0)
        self.other = User.objects.create_user(OTHER_NAME, password="TestPass#2026")
        Profile.objects.create(user=self.other, balance=0)
        self.client.force_login(self.buyer)

    def mock_agent(self, *frames: str, status: int = 200) -> respx.Route:
        """把 CharApp 服务拦下来, 让它答一段固定的流."""
        return respx.post(RUNS_URL).mock(return_value=_sse(*frames, status=status))

    def post_question(self, payload: dict | None = None, **kwargs):
        """按页面的样子打一次 BFF 端点 (POST + JSON), 返回原始响应.

        默认带上一个标签页的会话编号 —— 页面发出来的请求就是长这样的.
        """
        body = {"message": "你好", "conversation_id": TAB_ONE}
        body.update(payload or {})
        return self.client.post(
            CHAT_URL, data=json.dumps(body), content_type="application/json", **kwargs
        )

    def ask(self, payload: dict | None = None) -> str:
        """打一次 BFF 端点并收干响应体 (顺带把上游请求发出去)."""
        return _body(self.post_question(payload))

    def mock_cancel(self, response: httpx.Response) -> respx.Route:
        """把助手服务的取消端点拦下来, 让它回指定的响应."""
        return respx.post(CANCEL_UPSTREAM).mock(return_value=response)

    def post_cancel(self, payload: dict | None = None, **kwargs):
        """按页面的样子打一次取消端点 (POST + JSON)."""
        body = {"run_id": RUN_ID, "conversation_id": TAB_ONE}
        body.update(payload or {})
        return self.client.post(
            CANCEL_URL, data=json.dumps(body), content_type="application/json", **kwargs
        )

    def mock_history(self, response: httpx.Response) -> respx.Route:
        """把助手服务的历史端点拦下来, 让它回指定的响应."""
        return respx.get(HISTORY_UPSTREAM).mock(return_value=response)

    def read_history(self, conversation_id: str = TAB_ONE, **body):
        """按页面的样子读一次历史 (POST + JSON: 会话编号待在请求体里)."""
        payload = {"conversation_id": conversation_id}
        payload.update(body)
        return self.client.post(
            HISTORY_URL, data=json.dumps(payload), content_type="application/json"
        )

    def mock_conversations(self, response: httpx.Response) -> respx.Route:
        """把助手服务的会话列表端点拦下来, 让它回指定的响应."""
        return respx.get(CONVERSATIONS_UPSTREAM).mock(return_value=response)

    def list_conversations(self, **kwargs):
        """按页面的样子列一次会话 (POST, **不带请求体**).

        与另三条不同: 列表问的是「所有段」, 不针对某一段对话 —— 所以没有编号要发.
        """
        return self.client.post(CONVERSATIONS_URL, **kwargs)

    def page_source(self) -> str:
        """客服页渲染出来的源码, 空白压平成一行.

        页面那一侧的行为大半只能靠**源码断言**守 (它是一段原生 JS, 没有构建也没有
        测试运行器). 断言前压平空白, 守的是「这几个名字在」而不是「它们缩进几格」
        —— 改个格式不该红. 十几个用例都要它, 所以收成一处.
        """
        return " ".join(self.client.get(PAGE_URL).content.decode("utf-8").split())


# ---------------------------------------------------------------------------
# 转发契约
# ---------------------------------------------------------------------------


class BffForwardingTest(BffTestBase):
    """BFF 的三件事: 认证 / 取身份 / 转发 —— 多一件都不做."""

    def test_the_bff_forwards_the_question_and_the_service_headers(self):
        """问题走请求体, 令牌 / 身份 / 对话编号走头 (与 05 的 HTTP 契约对齐)."""
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="好的."))
            self.ask({"message": "我最近的订单到哪了", "conversation_id": TAB_ONE})

        request = route.calls[0].request
        self.assertEqual(json.loads(request.content), {"message": "我最近的订单到哪了"})
        self.assertEqual(request.headers["X-Internal-Token"], TOKEN)
        self.assertEqual(request.headers["X-User-Id"], str(self.buyer.pk))
        self.assertEqual(request.headers["X-Conversation-Id"], TAB_ONE)

    def test_the_identity_comes_from_the_session_not_from_the_body(self):
        """**守卫测试**: 请求体里塞别人的 user_id 字段 → 仍然以自己的身份转发.

        身份的唯一来源是 session (PRD §4.10 的第一层, 也是唯一真正验证「你是谁」
        的地方). 请求体里那个值必须被**无视**, 而不是「优先用 session」—— 任何一条
        「参数能影响身份」的路径都会让用户故事 24 失效.
        """
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="好的."))
            self.ask({"message": "查一下别人的订单", "user_id": str(self.other.pk)})

        forwarded = route.calls[0].request.headers["X-User-Id"]
        self.assertEqual(forwarded, str(self.buyer.pk))

    def test_two_buyers_each_forward_their_own_identity(self):
        """两个买家各自登录 → 各自的身份进各自的那次转发 (用户故事 24)."""
        other_client = self.client_class()
        other_client.force_login(self.other)

        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="好的."))
            self.ask()
            _body(
                other_client.post(
                    CHAT_URL,
                    data=_question(conversation_id=TAB_TWO),
                    content_type="application/json",
                )
            )

        forwarded = [c.request.headers["X-User-Id"] for c in route.calls]
        self.assertEqual(forwarded, [str(self.buyer.pk), str(self.other.pk)])

    def test_the_frames_reach_the_browser_unchanged(self):
        """逐帧透传: 事件名与 data 一个不少 (浏览器按 event 名挑渲染函数)."""
        frames = [
            _frame(1, "thinking", message="让我先查一下订单", turn=1),
            _frame(2, "tool_call", tool_name="list_my_orders", arguments="{}", turn=1),
            _frame(3, "tool_result", tool_name="list_my_orders", status="ok", turn=1),
            _frame(4, "final", content="你最近一笔订单已发货.", tokens=42),
        ]
        with respx.mock:
            self.mock_agent(*frames)
            body = self.ask({"message": "订单到哪了"})

        self.assertEqual(body, "".join(frames), "转发不该改动任何一帧")
        self.assertEqual(
            _events(body), ["thinking", "tool_call", "tool_result", "final"]
        )
        self.assertEqual(_payload(body, "final")["content"], "你最近一笔订单已发货.")

    def test_frames_split_across_chunks_are_kept_whole(self):
        """上游把一帧拆成两个 chunk 送 (TCP 分包) → 浏览器收到的仍是完整帧."""
        frames = [
            _frame(1, "thinking", message="让我先查一下订单", turn=1),
            _frame(2, "final", content="查到了."),
        ]
        raw = "".join(frames).encode("utf-8")
        split = len(raw) // 2  # 一刀切在中间 (多半落在某一帧的正中间)
        chunks = [raw[:split], raw[split:]]

        with respx.mock:
            respx.post(RUNS_URL).mock(
                return_value=httpx.Response(
                    200,
                    stream=iter(chunks),
                    headers={"content-type": "text/event-stream; charset=utf-8"},
                )
            )
            body = self.ask()

        self.assertEqual(body, "".join(frames))
        self.assertEqual(_events(body), ["thinking", "final"])

    def test_the_browser_gets_the_run_id_from_the_upstream(self):
        """响应头里带上这次运行的编号 —— 页面上按「停止」时唯一能指名道姓的东西.

        没有它的后果很具体: 停止按钮无从下手 (取消接口要一个 run_id). 上游**总是**
        带这个头 (框架把它放在响应头而不是第一个事件里, 客户端不必等一个开场事件),
        所以这里断言的是「原样转给了浏览器」这一件事.
        """
        with respx.mock:
            self.mock_agent(_frame(1, "final", content="好的."))
            r = self.post_question()
            _body(r)

        self.assertEqual(r.headers.get("X-Run-Id"), RUN_ID)

    def test_the_browser_gets_an_event_stream(self):
        """响应是 text/event-stream 且不许缓存 (前端与中间层都按它认)."""
        with respx.mock:
            self.mock_agent(_frame(1, "final", content="好的."))
            r = self.post_question()
            _body(r)

        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["content-type"].startswith("text/event-stream"))
        self.assertIn("no-cache", r["cache-control"])

    def test_a_get_is_not_allowed(self):
        """GET 一律 405: 这个端点有副作用, 不能变成一条链接/一张图就能触发的东西.

        这条守的是**语义**: 浏览器、代理、爬虫、预取器都会随手重放 GET, 而重放一次
        就是真跑一次模型、真花一次钱. 405 的响应体也说清了为什么 (不是空的).
        """
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="不该发生"))
            r = self.client.get(
                CHAT_URL, {"message": "你好", "conversation_id": TAB_ONE}
            )

        self.assertEqual(r.status_code, 405)
        self.assertFalse(route.called)
        self.assertEqual(json.loads(r.content)["error"]["code"], "method_not_allowed")


# ---------------------------------------------------------------------------
# 失败收口: 故障各自有用户能看懂的表现 (框架只给事实, 话术归业务侧)
# ---------------------------------------------------------------------------


class BffFailureTest(BffTestBase):
    """跨进程错误传播 —— L1b 比 L1a 多出来的那五个变量里最容易漏的一个."""

    def test_an_unreachable_service_is_one_human_sentence(self):
        """CharApp 服务没起来 → 502 + 一句人话 (不是白屏, 也不是 500)."""
        with respx.mock:
            respx.post(RUNS_URL).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            r = self.post_question()

        self.assertEqual(r.status_code, 502)
        body = json.loads(r.content)
        self.assertEqual(body["error"]["code"], "agent_unavailable")
        self.assertIn("客服暂时联系不上", body["error"]["message"])

    def test_a_rejected_token_does_not_leak_the_upstream_body(self):
        """上游 401 (令牌不对) → 用户看到一句人话, 上游正文不外泄.

        码原样保留 (框架特意给业务留的): `unauthorized` 说明**我们这边**没配对,
        用户做什么都没用, 所以话术走兜底那句; 但码留在响应里, 排查的人一眼看得出
        「不是客服挂了, 是令牌不对」.
        """
        with respx.mock:
            respx.post(RUNS_URL).mock(
                return_value=httpx.Response(
                    401, json={"error": {"code": "unauthorized", "message": "令牌不对"}}
                )
            )
            r = self.post_question()

        self.assertEqual(r.status_code, 502)
        body = json.loads(r.content)
        self.assertEqual(body["error"]["code"], "unauthorized")
        self.assertNotIn("令牌不对", body["error"]["message"])
        self.assertEqual(body["error"]["message"], "客服这边出了点问题, 请稍后再试.")

    def test_a_busy_conversation_says_so_instead_of_blaming_the_service(self):
        """上游 409 thread_busy (同一段对话上一句还没答完) → 说清是「等一等」.

        这条守的是**归因**: 「客服暂时联系不上」对用户是错的 —— 服务好好的, 他
        等这条答完再问就行. 框架按 code 把事实给全了, 业务侧不该把它塌成一句话.
        """
        with respx.mock:
            respx.post(RUNS_URL).mock(
                return_value=httpx.Response(
                    409,
                    json={
                        "error": {
                            "code": "thread_busy",
                            "message": "该会话已有运行在进行中",
                        }
                    },
                )
            )
            r = self.post_question()

        body = json.loads(r.content)
        self.assertEqual(body["error"]["code"], "thread_busy")
        self.assertIn("上一句", body["error"]["message"])
        self.assertNotIn("联系不上", body["error"]["message"])

    def test_an_upstream_error_without_a_usable_body_falls_back(self):
        """上游回了非 200 但正文不是那套 JSON (代理塞了张 HTML 错误页) → 兜底."""
        with respx.mock:
            respx.post(RUNS_URL).mock(
                return_value=httpx.Response(502, text="<html>Bad Gateway</html>")
            )
            r = self.post_question()

        self.assertEqual(r.status_code, 502)
        self.assertEqual(json.loads(r.content)["error"]["code"], "agent_unavailable")

    def test_a_json_body_where_a_stream_was_expected_is_reported(self):
        """上游 200 却不是 SSE (打错了地址 / 中间层返回了 JSON) → 也是一句人话."""
        with respx.mock:
            respx.post(RUNS_URL).mock(
                return_value=httpx.Response(200, json={"detail": "Not Found"})
            )
            r = self.post_question()

        self.assertEqual(r.status_code, 502)
        self.assertEqual(json.loads(r.content)["error"]["code"], "agent_unavailable")

    def test_an_unconfigured_token_fails_before_calling_out(self):
        """本机没配内部令牌 → 连都不连 (配错了要在日志里说清, 不是去挨一次拒)."""
        with override_settings(CHARAPP_INTERNAL_TOKEN=""), respx.mock:
            route = self.mock_agent(_frame(1, "final", content="不该发生"))
            r = self.post_question()

        self.assertFalse(route.called)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(json.loads(r.content)["error"]["code"], "agent_unavailable")

    def test_a_framework_error_becomes_user_facing_copy(self):
        """框架的 error 事件 (事实性文案) → 保留 code, 换成用户话术.

        归因分界: 框架那句「已达最大轮数限制, 未能产出最终答复」是给开发者看的;
        用户该看到的是「换个问法试试」这类能照着做的话.
        """
        with respx.mock:
            self.mock_agent(
                _frame(
                    1,
                    "error",
                    error={
                        "code": "max_turns",
                        "message": "已达最大轮数限制, 未能产出最终答复",
                    },
                )
            )
            body = self.ask()

        payload = _payload(body, "error")
        self.assertEqual(
            payload["error"]["code"], "max_turns", "code 是给程序看的, 原样保留"
        )
        self.assertNotIn("已达最大轮数限制", payload["error"]["message"])
        self.assertIn("换个问法", payload["error"]["message"])
        self.assertEqual(payload["seq"], 1, "帧的其它字段不动")

    def test_an_unknown_error_code_still_gets_a_sentence(self):
        """没见过的 code (框架以后新增的) → 兜底话术, 不是空白."""
        with respx.mock:
            self.mock_agent(
                _frame(1, "error", error={"code": "brand_new", "message": "内部细节"})
            )
            body = self.ask()

        payload = _payload(body, "error")
        self.assertEqual(payload["error"]["code"], "brand_new")
        self.assertNotIn("内部细节", payload["error"]["message"])
        self.assertTrue(payload["error"]["message"])

    def test_a_stream_without_a_terminal_event_still_ends_properly(self):
        """上游没给终局事件就断了 (代理截断 / 进程被杀) → 补一个, 别让前端干等.

        头早就发出去了, 改不了状态码, 所以这一类仍然是流里补一帧 —— 前端拿终局
        事件当「可以收线了」的信号, 少了它就是一直转圈.
        """
        with respx.mock:
            self.mock_agent(_frame(1, "thinking", message="让我想想", turn=1))
            body = self.ask()

        self.assertEqual(
            _events(body), ["thinking", "error"], "前面的事件照发, 末尾补上终局"
        )
        self.assertEqual(_payload(body, "error")["error"]["code"], "stream_interrupted")

    def test_a_mid_stream_break_is_reported_not_hidden(self):
        """上游读到一半炸了 (连接被掐) → 也要收口, 不能静默断在半路."""

        def breaking():
            yield b"id: 1\nevent: thinking\ndata: {}\n\n"
            raise httpx.ReadError("connection lost")

        with respx.mock:
            respx.post(RUNS_URL).mock(
                return_value=httpx.Response(
                    200,
                    stream=breaking(),
                    headers={"content-type": "text/event-stream"},
                )
            )
            body = self.ask()

        self.assertEqual(_events(body), ["thinking", "error"])
        self.assertEqual(_payload(body, "error")["error"]["code"], "stream_interrupted")


# ---------------------------------------------------------------------------
# 入参校验 (BFF 不做业务判断, 但入参是它自己的事)
# ---------------------------------------------------------------------------


class BffInputTest(BffTestBase):
    """不合法的请求不该打到下游去."""

    def test_an_empty_question_is_refused(self):
        # 这里刻意不用 post_question(): 它默认会补上 message 与 conversation_id,
        # 那样就测不到「缺字段」这一路了
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="不该发生"))

            for payload in ({}, {"message": ""}, {"message": "   "}, {"message": 42}):
                with self.subTest(payload=payload):
                    r = self.client.post(
                        CHAT_URL,
                        data=json.dumps(payload),
                        content_type="application/json",
                    )
                    self.assertEqual(r.status_code, 400)

        self.assertFalse(route.called)

    def test_a_body_that_is_not_json_is_refused(self):
        """正文不是 JSON 对象 → 400, 而且要说的是用户话术 (页面上会显示这句)."""
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="不该发生"))

            for raw in (b"", b"not json", b"[1, 2, 3]"):
                with self.subTest(raw=raw):
                    r = self.client.post(
                        CHAT_URL, data=raw, content_type="application/json"
                    )
                    self.assertEqual(r.status_code, 400)
                    self.assertEqual(
                        json.loads(r.content)["error"]["code"], "invalid_request"
                    )

        self.assertFalse(route.called)

    def test_a_too_long_question_is_refused(self):
        """问句有长度上限 —— 超长的表现是直接烧 token 或撞上游上下文上限."""
        too_long = "问" * (MAX_MESSAGE_LENGTH + 1)
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="不该发生"))
            r = self.post_question({"message": too_long})

        self.assertEqual(r.status_code, 400)
        self.assertEqual(json.loads(r.content)["error"]["code"], "message_too_long")
        self.assertFalse(route.called)

    def test_a_conversation_id_that_cannot_be_forwarded_is_refused(self):
        """对话编号会进 HTTP 头 → 空白 / 控制字符 / 非 ASCII 一律在这里拦.

        头是按字节传的, 中文与换行在这一层就走不通 (换行更是头注入的形状).
        """
        bad = ["has space", "line\nbreak", "中文对话"]
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="不该发生"))

            for value in bad:
                with self.subTest(value=value):
                    r = self.post_question({"conversation_id": value})
                    self.assertEqual(r.status_code, 400)

        self.assertFalse(route.called)

    def test_the_length_limit_is_measured_on_the_whole_thread_id(self):
        """长度上限卡的是**整串** `minimall:{买家}:{对话}`, 不是单独那一段.

        框架那条 128 是量整串的 (`checkpoint/utils.check_identifier`, 由 AgentLoop
        在构造期调用). 只量第三段的后果很具体: 117 个字符的对话编号从这里过去,
        到助手服务装配会话时才炸 —— 用户看到的是「客服暂时联系不上」, 而真相是
        「你的请求不合法」. 所以边界要按整串算: 刚好占满放行, 多一个字符拒掉.
        """
        budget = max_conversation_id_length(self.buyer.pk)
        thread_id = f"{THREAD_ID_PREFIX}:{self.buyer.pk}:{'x' * budget}"
        self.assertEqual(len(thread_id), MAX_THREAD_ID_LENGTH)

        # 这条线得是**助手服务真会走的那条**: 框架自己的校验函数说它刚好过关,
        # 再多一个字符就该被拒
        self.assertEqual(check_identifier("thread_id", thread_id), thread_id)
        with self.assertRaises(CheckpointConfigError):
            check_identifier("thread_id", thread_id + "x")

        with respx.mock:
            self.mock_agent(_frame(1, "final", content="好的."))
            r = self.post_question({"conversation_id": "x" * budget})
            self.assertEqual(r.status_code, 200)
            _body(r)

            too_long = self.post_question({"conversation_id": "x" * (budget + 1)})

        self.assertEqual(too_long.status_code, 400)

    def test_a_missing_conversation_id_is_refused(self):
        """前端没给对话编号 → 400 (不给默认值).

        助手服务那边有默认值, 但那是给直接调接口的调用方留的; 浏览器这边每开一个
        标签页就生成一个, 缺了只能是前端出了错. 那时塞个默认值, 表现是两个标签页
        悄悄共用同一段对话 —— 正是用户故事 25 要防的串台, 而且不报错.
        """
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="不该发生"))
            body = {"message": "你好"}
            r = self.client.post(
                CHAT_URL, data=json.dumps(body), content_type="application/json"
            )

        self.assertEqual(r.status_code, 400)
        self.assertFalse(route.called)


# ---------------------------------------------------------------------------
# 取消: 转发 + 身份 + 编号的形状 (issue 07)
# ---------------------------------------------------------------------------


class BffCancelTest(BffTestBase):
    """按「停止」的那条短链路 —— 它只转达结果, 收尾仍然发生在 SSE 那条流上."""

    def test_the_cancel_forwards_the_run_and_the_service_headers(self):
        """转发到 `/runs/{id}/cancel`, 三个头一个不少.

        少了 `X-Conversation-Id` 的表现**不是"慢一点"**: 助手服务会按缺省那段会话
        去比对, 于是每一次取消都被当成「不是你的运行」—— 用户点了停止, 页面却什么都
        没发生. 转发层与助手服务之间的这条契约, 由这条用例钉住.
        """
        with respx.mock:
            route = self.mock_cancel(
                httpx.Response(200, json={"run_id": RUN_ID, "status": "cancelling"})
            )
            r = self.post_cancel()

        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r.content)["run_id"], RUN_ID)
        request = route.calls[0].request
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["X-Internal-Token"], TOKEN)
        self.assertEqual(request.headers["X-User-Id"], str(self.buyer.pk))
        self.assertEqual(request.headers["X-Conversation-Id"], TAB_ONE)

    def test_the_cancel_identity_comes_from_the_session_not_from_the_body(self):
        """**守卫测试**: 请求体里塞别人的 user_id → 仍然以自己的身份转发.

        取消是能**让别人花钱**的动作, 所以这条守卫比 chat 那条更要紧: 身份只要能被
        参数影响一次, 「不能取消别人的运行」就只剩助手服务那一层在守了.
        """
        with respx.mock:
            route = self.mock_cancel(httpx.Response(200, json={"run_id": RUN_ID}))
            self.post_cancel({"user_id": str(self.other.pk)})

        self.assertEqual(
            route.calls[0].request.headers["X-User-Id"], str(self.buyer.pk)
        )

    def test_a_malformed_run_id_never_reaches_the_service(self):
        """编号的形状不对 → 400, 而且一个请求都不发出去.

        它要被拼进 URL 的**路径**, 所以形状不对的后果不是"查不到", 而是"这次请求去
        了别的地方" —— 一个带 `/` 或 `..` 的编号能把一个攥着内部令牌与身份头的请求指
        到同一台机器上的另一个端点去. 卡形状 (框架发的就是 32 位十六进制) 比事后转义
        牢, 而且这里拒掉不影响任何人: 合法的编号只可能来自我们自己那条流.
        """
        bad = [
            "",
            "not-a-run-id",
            "../../admin/",  # 想改道
            RUN_ID[:-1],  # 31 位
            RUN_ID + "9",  # 33 位
            RUN_ID.upper(),  # 大写不是框架发的形状
            RUN_ID[:-1] + "/",  # 长度对, 字符不对
        ]
        with respx.mock:
            route = self.mock_cancel(httpx.Response(200, json={}))

            for value in bad:
                with self.subTest(run_id=value):
                    r = self.post_cancel({"run_id": value})
                    self.assertEqual(r.status_code, 400)
                    self.assertEqual(
                        json.loads(r.content)["error"]["code"], "invalid_run_id"
                    )

        self.assertFalse(route.called)

    def test_cancelling_a_run_that_is_already_over_is_not_an_error(self):
        """上游 404 (这次运行已经不在了) → BFF 也回 404 + 一句「已经结束了」.

        这是每次都可能遇到的一次**正常竞争**: 用户按停止的那一瞬, 它刚好答完. 所以
        不能塌成「客服暂时联系不上」—— 那会让用户以为出了故障, 去重试一件根本不需要
        重试的事. 页面据此不打扰用户 (那一轮的终局事件本来也已经到了).
        """
        with respx.mock:
            self.mock_cancel(
                httpx.Response(
                    404, json={"error": {"code": "run_not_found", "message": "不在册"}}
                )
            )
            r = self.post_cancel()

        self.assertEqual(r.status_code, 404)
        body = json.loads(r.content)
        self.assertEqual(body["error"]["code"], "run_not_found")
        self.assertEqual(body["error"]["message"], "这次回答已经结束了.")
        self.assertNotIn("不在册", body["error"]["message"])

    def test_a_404_without_the_expected_code_is_a_failure_not_a_race(self):
        """404 但不是那个码 → **502** (那是对面没有这条路由, 不是"刚好答完了").

        分辨这两者不是吹毛求疵: 页面把所有 404 都当成正常竞争 (静默忽略), 所以一个
        不带 `run_not_found` 的 404 (版本不齐 / 地址打错 / 路由被删) 会被当成"这次
        答完了" —— 停止按钮从此按不动, 而且一句解释都没有. 接线故障就该报出来.
        """
        with respx.mock:
            self.mock_cancel(
                httpx.Response(404, json={"detail": "Not Found"})  # FastAPI 自己的 404
            )
            r = self.post_cancel()

        self.assertEqual(r.status_code, 502)
        body = json.loads(r.content)
        self.assertEqual(body["error"]["code"], "agent_unavailable")
        self.assertNotEqual(body["error"]["code"], "run_not_found")

    def test_an_unreachable_service_is_reported(self):
        """助手服务没起来 → 502 + 一句人话 (与 chat 那条同一个口径)."""
        with respx.mock:
            respx.post(CANCEL_UPSTREAM).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            r = self.post_cancel()

        self.assertEqual(r.status_code, 502)
        self.assertEqual(json.loads(r.content)["error"]["code"], "agent_unavailable")

    def test_an_unconfigured_token_fails_before_calling_out(self):
        """本机没配令牌 (fail closed): 一个请求都不发, 码与 chat 那条一致."""
        with respx.mock:
            route = self.mock_cancel(httpx.Response(200, json={}))
            with override_settings(CHARAPP_INTERNAL_TOKEN=""):
                r = self.post_cancel()

        self.assertEqual(r.status_code, 503)
        self.assertEqual(json.loads(r.content)["error"]["code"], "agent_unavailable")
        self.assertFalse(route.called)

    def test_a_get_cannot_cancel(self):
        """
        GET 一律 405: 取消也有副作用 (它让一次运行停下并作废),
        不做成能随手重放的 GET.
        """
        with respx.mock:
            route = self.mock_cancel(httpx.Response(200, json={}))
            r = self.client.get(
                CANCEL_URL, {"run_id": RUN_ID, "conversation_id": TAB_ONE}
            )

        self.assertEqual(r.status_code, 405)
        self.assertEqual(json.loads(r.content)["error"]["code"], "method_not_allowed")
        self.assertFalse(route.called)


# ---------------------------------------------------------------------------
# 读历史: 刷新之后对话还在
# ---------------------------------------------------------------------------


class BffHistoryTest(BffTestBase):
    """读历史那条路 —— 页面加载时问一次「这段对话聊到哪儿了」."""

    def test_the_history_forwards_the_conversation_and_the_service_headers(self):
        """转发到 `/history`, 三个头一个不少, 上游的正文原样交给浏览器.

        少 `X-Conversation-Id` 的表现与取消那条一样具体: 助手服务会按缺省那段会话
        去查, 于是**每次都回一段空历史** —— 用户刷新十次都是「没聊过」, 而链路上
        一处报错都没有.

        转发**下游仍是 GET**: 会话编号走头, 地址里不带参数, 要内部令牌才进得来 ——
        上面那条「编号不进 URL」的纪律是给**浏览器**这一侧定的.
        """
        with respx.mock:
            route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            r = self.read_history()

        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r.content), HISTORY_BODY)
        self.assertEqual(r["Content-Type"], "application/json")
        request = route.calls[0].request
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.headers["X-Internal-Token"], TOKEN)
        self.assertEqual(request.headers["X-User-Id"], str(self.buyer.pk))
        self.assertEqual(request.headers["X-Conversation-Id"], TAB_ONE)

    def test_the_conversation_id_stays_out_of_the_url(self):
        """**守卫测试**: 会话编号只走请求体 —— 它不该出现在地址上, 一次都不该.

        这条守的是「会话编号不进 URL」这个决定本身 (理由见 `AgentHistoryView`):
        进了 URL 就等于同时进了访问日志 / 浏览器历史 / Referer. 判据落在**上游收到
        的请求**上: 对下游也不能把它挂进查询串 (它现在走 `X-Conversation-Id` 头).
        """
        with respx.mock:
            route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            r = self.read_history(TAB_TWO)

        self.assertNotIn(TAB_TWO, str(r.request["QUERY_STRING"]))
        self.assertEqual(route.calls[0].request.url.query, b"")

    def test_the_identity_comes_from_the_session_not_from_the_body(self):
        """**守卫测试**: 请求体里塞别人的 user_id → 仍然以自己的身份转发.

        读历史不像取消那样让别人花钱, 但它读到的是**别人的对话内容** —— 身份只要
        能被参数影响一次, 「买家 A 拉不到买家 B 的历史」就只剩助手服务那一层在守.
        """
        with respx.mock:
            route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            self.read_history(user_id=str(self.other.pk))

        request = route.calls[0].request
        self.assertEqual(request.headers["X-User-Id"], str(self.buyer.pk))

    def test_each_tab_asks_for_its_own_conversation(self):
        """会话编号跟着请求体走: 两个标签页问的是两段对话.

        这条是用户故事 25 在读出方向上的那一半 (写方向由 chat 那条守) —— 页面把
        自己那一段的编号发出来, 拿回来的才是自己那一段.
        """
        with respx.mock:
            route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            self.read_history(TAB_TWO)

        self.assertEqual(route.calls[0].request.headers["X-Conversation-Id"], TAB_TWO)

    def test_a_conversation_id_that_cannot_be_forwarded_is_refused(self):
        """编号不合法 → 400, 而且一个请求都不发出去 (与另两条路同一处校验)."""
        with respx.mock:
            route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            r = self.read_history("有中文不行")

        self.assertEqual(r.status_code, 400)
        self.assertEqual(
            json.loads(r.content)["error"]["code"], "invalid_conversation_id"
        )
        self.assertFalse(route.called)

    def test_a_missing_conversation_id_is_refused(self):
        """缺编号 → 400 (不给默认值): 默认值的表现是两个标签页悄悄共用一段历史."""
        with respx.mock:
            route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            r = self.client.post(
                HISTORY_URL, data=json.dumps({}), content_type="application/json"
            )

        self.assertEqual(r.status_code, 400)
        self.assertEqual(
            json.loads(r.content)["error"]["code"], "invalid_conversation_id"
        )
        self.assertFalse(route.called)

    def test_a_body_that_is_not_json_is_refused(self):
        """请求体不是 JSON 对象 → 400, 一个请求都不发出去 (与 chat 那条同款)."""
        with respx.mock:
            route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            r = self.client.post(
                HISTORY_URL, data="不是 JSON", content_type="text/plain"
            )

        self.assertEqual(r.status_code, 400)
        self.assertEqual(json.loads(r.content)["error"]["code"], "invalid_request")
        self.assertFalse(route.called)

    def test_an_unreachable_service_is_reported(self):
        """助手服务没起来 → 502 + 一句人话 (与另两条路同一个口径)."""
        with respx.mock:
            respx.get(HISTORY_UPSTREAM).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            r = self.read_history()

        self.assertEqual(r.status_code, 502)
        self.assertEqual(json.loads(r.content)["error"]["code"], "agent_unavailable")

    def test_an_unconfigured_token_fails_before_calling_out(self):
        """本机没配令牌 (fail closed): 一个请求都不发, 码与另两条路一致."""
        with respx.mock:
            route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            with override_settings(CHARAPP_INTERNAL_TOKEN=""):
                r = self.read_history()

        self.assertEqual(r.status_code, 503)
        self.assertEqual(json.loads(r.content)["error"]["code"], "agent_unavailable")
        self.assertFalse(route.called)

    def test_an_upstream_refusal_keeps_its_code(self):
        """上游拒绝转发时保留它的码 —— 那是对面特意留给业务分辨用的."""
        with respx.mock:
            self.mock_history(
                httpx.Response(503, json={"error": {"code": "not_configured"}})
            )
            r = self.read_history()

        self.assertEqual(r.status_code, 502)
        self.assertEqual(json.loads(r.content)["error"]["code"], "not_configured")

    def test_a_read_by_url_is_not_allowed(self):
        """**守卫测试**: GET 一律 405 —— 会话编号不能从 URL 上读, 也不该被塞进 URL.

        这就是这次改动的全部意义: 一条 `GET /minimall/agent/history/?conversation_id=…`
        会被访问日志、浏览器历史、Referer 记下来, 而跨站页面上一个 `<img src>` 就能
        让别人的浏览器替他去读. 别的写意图同样挡在门外 (同一个出口).
        """
        for method in ("get", "put", "patch", "delete"):
            with self.subTest(method=method), respx.mock:
                route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
                r = getattr(self.client, method)(HISTORY_URL)

                self.assertEqual(r.status_code, 405)
                self.assertEqual(
                    json.loads(r.content)["error"]["code"], "method_not_allowed"
                )
                self.assertFalse(route.called)


# ---------------------------------------------------------------------------
# CSRF: 换 POST 换来的一层显式防护
# ---------------------------------------------------------------------------


class BffConversationsTest(BffTestBase):
    """列会话那条路 —— 左侧列表 (issue 19) 要的「我聊过哪几段」.

    与读历史是一对 (一个答「这段聊了什么」, 一个答「我有哪些对话」), 断的东西也
    照着那一页来: 转发契约 / 身份只从 session 取 / 失败收口 / 动词。差别只在一处
    —— 这条**没有请求体**, 也不看哪一段对话.
    """

    def test_the_list_forwards_the_identity_and_hands_back_the_body(self):
        """转发到 `/conversations`: 三个头一个不少, 上游的正文原样交给浏览器.

        `X-Conversation-Id` 这条路上是**空值** (列表不针对某一段) —— 但头仍然要带:
        三个头是一组, 少带一个就是"另一份拼法", 而这一处正是被漏带害过的地方
        (见 `_service_headers`). 下游那一跳仍是 GET.
        """
        with respx.mock:
            route = self.mock_conversations(
                httpx.Response(200, json=CONVERSATIONS_BODY)
            )
            r = self.list_conversations()

        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r.content), CONVERSATIONS_BODY)
        self.assertEqual(r["Content-Type"], "application/json")
        request = route.calls[0].request
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.headers["X-Internal-Token"], TOKEN)
        self.assertEqual(request.headers["X-User-Id"], str(self.buyer.pk))
        self.assertEqual(request.headers["X-Conversation-Id"], "")
        self.assertEqual(request.url.query, b"", "列表不带查询串 (不分页)")

    def test_two_buyers_each_ask_for_their_own_list(self):
        """两个买家各自登录 → 各自的身份进各自的那次转发.

        「换一个买家登录看不到别人的」判据在助手服务那侧 (它按 `X-User-Id` 查);
        本层能保证的是**转过去的一定是 session 里那个人** —— 这条就是那一半.
        """
        other_client = self.client_class()
        other_client.force_login(self.other)

        with respx.mock:
            route = self.mock_conversations(
                httpx.Response(200, json=CONVERSATIONS_BODY)
            )
            self.list_conversations()
            other_client.post(CONVERSATIONS_URL)

        forwarded = [call.request.headers["X-User-Id"] for call in route.calls]
        self.assertEqual(forwarded, [str(self.buyer.pk), str(self.other.pk)])

    def test_the_identity_comes_from_the_session_not_from_the_body(self):
        """**守卫测试**: 请求体里塞别人的 user_id → 仍然以自己的身份转发.

        这条路的请求体**根本不读** (列表不需要指名哪一段), 但正因为如此更要说清:
        万一以后有人"顺手"在这儿加一个 `user_id` 字段, 它也只能被无视.
        """
        with respx.mock:
            route = self.mock_conversations(
                httpx.Response(200, json=CONVERSATIONS_BODY)
            )
            self.client.post(
                CONVERSATIONS_URL,
                data=json.dumps({"user_id": str(self.other.pk)}),
                content_type="application/json",
            )

        self.assertEqual(
            route.calls[0].request.headers["X-User-Id"], str(self.buyer.pk)
        )

    def test_an_unreachable_service_is_reported(self):
        """助手服务没起来 → 502 + 一句人话 (与另三条路同一个口径)."""
        with respx.mock:
            respx.get(CONVERSATIONS_UPSTREAM).mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            r = self.list_conversations()

        self.assertEqual(r.status_code, 502)
        self.assertEqual(json.loads(r.content)["error"]["code"], "agent_unavailable")
        self.assertIn("客服暂时联系不上", json.loads(r.content)["error"]["message"])

    def test_an_unconfigured_token_fails_before_calling_out(self):
        """本机没配令牌 (fail closed): 一个请求都不发, 码与另三条路一致."""
        with respx.mock:
            route = self.mock_conversations(
                httpx.Response(200, json=CONVERSATIONS_BODY)
            )
            with override_settings(CHARAPP_INTERNAL_TOKEN=""):
                r = self.list_conversations()

        self.assertEqual(r.status_code, 503)
        self.assertEqual(json.loads(r.content)["error"]["code"], "agent_unavailable")
        self.assertFalse(route.called)

    def test_an_upstream_refusal_keeps_its_code(self):
        """上游拒绝转发时保留它的码 —— 那是对面特意留给业务分辨用的."""
        with respx.mock:
            self.mock_conversations(
                httpx.Response(503, json={"error": {"code": "not_configured"}})
            )
            r = self.list_conversations()

        self.assertEqual(r.status_code, 502)
        self.assertEqual(json.loads(r.content)["error"]["code"], "not_configured")

    def test_a_read_by_url_is_not_allowed(self):
        """**守卫测试**: GET 一律 405 —— 四条路一个形状, 页面才不必按动词分两套.

        这条路上没有会话编号要保护, 但它读的是**私人数据** (我聊过哪几段): GET +
        cookie 是跨站可触发的, 一条 `<img src=".../minimall/agent/conversations/">`
        就能让别人的浏览器替他发出这条请求. POST + CSRF 之后由中间件接管.
        """
        for method in ("get", "put", "patch", "delete"):
            with self.subTest(method=method), respx.mock:
                route = self.mock_conversations(
                    httpx.Response(200, json=CONVERSATIONS_BODY)
                )
                r = getattr(self.client, method)(CONVERSATIONS_URL)

                self.assertEqual(r.status_code, 405)
                self.assertEqual(
                    json.loads(r.content)["error"]["code"], "method_not_allowed"
                )
                self.assertFalse(route.called)


class BffCsrfTest(BffTestBase):
    """有副作用的端点必须挡住跨站伪造 —— 现在是 Django 的 CSRF 中间件在做这件事.

    上一版用 GET 时它做不了: GET 压根不过 CSRF 检查, 只能靠 `SameSite=Lax`, 而
    Lax 挡不住**顶层导航** —— 攻击者页面上一句
    `location = '.../minimall/agent/chat/?message=...'` 就能让登录用户真跑一次
    模型 (真花钱). POST + 令牌之后这条路走不通了.
    """

    def csrf_client(self):
        """打开 CSRF 检查的客户端 (默认的测试客户端会跳过它, 那样就测不到真东西)."""
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.buyer)
        return client

    def test_a_post_without_the_csrf_token_is_refused(self):
        client = self.csrf_client()
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="不该发生"))
            r = client.post(
                CHAT_URL,
                data=_question(),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 403)
        self.assertFalse(route.called, "被 CSRF 拦下的请求绝不该打到下游")

    def test_a_cancel_without_the_csrf_token_is_refused(self):
        """取消端点同样受 CSRF 保护 (它是 POST, 中间件才管得着).

        一条伪造的取消请求的代价是「把别人的回答掐掉」—— 比伪造一次提问更便宜, 也
        更隐蔽 (页面上只会显示"已取消"). 同一个令牌, 同一层防护.
        """
        client = self.csrf_client()
        with respx.mock:
            route = self.mock_cancel(httpx.Response(200, json={}))
            r = client.post(
                CANCEL_URL,
                data=_cancellation(),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 403)
        self.assertFalse(route.called, "被 CSRF 拦下的请求绝不该打到下游")

    def test_reading_the_history_without_the_csrf_token_is_refused(self):
        """**读历史也受 CSRF 保护** —— 这正是它从 GET 改成 POST 的原因.

        改之前这条请求不过 CSRF 检查: 跨站页面上一行 `<img src>` 就能让登录用户替
        他去读历史 (响应那头读不到, 但请求真的发出去了 —— 而「会话编号进 URL」本身
        已经把编号泄给了日志与 Referer). 现在它与提问、取消同一层防护.

        顺带守住那条「编号不进 URL」的决定: 伪造者连**怎么发**这条请求都得先拿到
        页面上的 CSRF 令牌, 光知道编号没用.
        """
        client = self.csrf_client()
        with respx.mock:
            route = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            r = client.post(
                HISTORY_URL,
                data=json.dumps({"conversation_id": TAB_ONE}),
                content_type="application/json",
            )

        self.assertEqual(r.status_code, 403)
        self.assertFalse(route.called, "被 CSRF 拦下的请求绝不该打到下游")

    def test_listing_the_conversations_without_the_csrf_token_is_refused(self):
        """**列会话也受 CSRF 保护** (它同样是 POST).

        这条路上没有会话编号要保护, 但列表本身就是私人数据 (我聊过哪几段、每段叫
        什么名字) —— 一条 GET 就能被跨站页面上的 `<img src>` 触发, 而 POST + 令牌
        之后伪造者得先拿到页面上的那一个.
        """
        client = self.csrf_client()
        with respx.mock:
            route = self.mock_conversations(
                httpx.Response(200, json=CONVERSATIONS_BODY)
            )
            r = client.post(CONVERSATIONS_URL)

        self.assertEqual(r.status_code, 403)
        self.assertFalse(route.called, "被 CSRF 拦下的请求绝不该打到下游")

    def test_the_pages_own_token_is_accepted(self):
        """页面拿到的那个令牌能过 —— 有防护还不够, 正常路径不能被误伤.

        四条 POST 一起过一遍 (提问 / 取消 / 读历史 / 列会话): 页面用的是同一个
        `csrfToken()`, 「哪一条能用」分家就等于某一条在真机上永远是 403, 而用例全绿.
        """
        client = self.csrf_client()
        client.get(PAGE_URL)  # 页面渲染时种下 csrf cookie
        token = client.cookies["csrftoken"].value

        with respx.mock:
            chat = self.mock_agent(_frame(1, "final", content="好的."))
            cancel = self.mock_cancel(httpx.Response(200, json={}))
            history = self.mock_history(httpx.Response(200, json=HISTORY_BODY))
            conversations = self.mock_conversations(
                httpx.Response(200, json=CONVERSATIONS_BODY)
            )
            r = client.post(
                CHAT_URL,
                data=_question(),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            )
            _body(r)
            client.post(
                CANCEL_URL,
                data=_cancellation(),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            )
            read = client.post(
                HISTORY_URL,
                data=json.dumps({"conversation_id": TAB_ONE}),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            )
            listed = client.post(CONVERSATIONS_URL, HTTP_X_CSRFTOKEN=token)

        self.assertEqual(r.status_code, 200)
        self.assertTrue(chat.called)
        self.assertTrue(cancel.called)
        self.assertEqual(read.status_code, 200)
        self.assertTrue(history.called)
        self.assertEqual(listed.status_code, 200)
        self.assertTrue(conversations.called)

    def test_a_client_generated_conversation_id_is_passed_through(self):
        """两个标签页各自的编号 → 各自成段 (用户故事 25 的前端那一半)."""
        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="好的."))
            for tab in (TAB_ONE, TAB_TWO):
                self.ask({"conversation_id": tab})

        sent = [c.request.headers["X-Conversation-Id"] for c in route.calls]
        self.assertEqual(sent, [TAB_ONE, TAB_TWO])


# ---------------------------------------------------------------------------
# 页面与入口
# ---------------------------------------------------------------------------


class AgentPageTest(BffTestBase):
    """客服页面: 登录可见, 入口全站可达, **七类事件**都接了, 左栏是会话列表."""

    def test_the_page_and_the_stream_are_for_logged_in_buyers_only(self):
        """未登录访问 → 跳登录页 (不是 500, 也不是空流)."""
        self.client.logout()

        page = self.client.get(PAGE_URL)
        stream = self.client.post(
            CHAT_URL,
            data=_question(),
            content_type="application/json",
        )

        self.assertEqual(page.status_code, 302)
        self.assertTrue(page["Location"].startswith("/minimall/login/"))
        self.assertEqual(stream.status_code, 302)
        self.assertTrue(stream["Location"].startswith("/minimall/login/"))

    def test_the_page_renders_the_chat(self):
        r = self.client.get(PAGE_URL)

        self.assertEqual(r.status_code, 200)
        body = r.content.decode("utf-8")
        # 发问走 fetch + POST (不是 EventSource), 并且带着 CSRF 令牌
        self.assertIn("method: 'POST'", body)
        self.assertIn("'X-CSRFToken': csrfToken()", body)
        self.assertNotIn("new EventSource(", body)

    def test_every_event_type_has_a_handler(self):
        """七类事件一个不少 —— 漏一个的表现是「那种事件静默消失」.

        这是前端能被 Django 测试够到的一半 (另一半是浏览器里真跑一遍): 事件名与
        框架的 `EventType` 全集对齐. 断言前先把空白压平 —— 守的是「这七个名字在」,
        不是「它们缩进几格」 (改个格式不该红).

        `context_compacted` 是第七类 (框架 issue 16 加的, 本片才接上): 它以前进
        这个表就会红, 正是这条用例存在的意义 —— 框架新增一类事件时, 页面这边
        必须有人做一次决定 (接上, 还是有意忽略).
        """
        compact = self.page_source()

        for event in (
            "thinking",
            "tool_call",
            "tool_result",
            "reasoning",
            "context_compacted",
            "final",
            "error",
        ):
            with self.subTest(event=event):
                self.assertIn(f"{event}: function (data) {{", compact)

    def test_the_page_borrows_the_sse_parser_instead_of_writing_one(self):
        """帧解析用库, 不自己写 —— 一个汉字被 TCP 切成两半那种细节不该由页面负责.

        这条守的是**那个决定本身** (理由见 `adr/0002`): 手写版本要三十多行, 还得记住
        `{stream: true}` 之类的坑; 库版本两行管道. 断言钉住那两件「别人已经做对的活」,
        同时挡住「又回去自己读字节流」这条路.
        """
        compact = self.page_source()

        self.assertIn("new TextDecoderStream()", compact)
        self.assertIn("new EventSourceParserStream()", compact)
        self.assertNotIn("getReader()", compact)

    def test_the_stop_button_is_wired_to_the_cancel_endpoint(self):
        """停止按钮: 只在跑的时候露出来, 按下去打的是取消端点, 并且带上那个编号.

        前端能被 Django 测试够到的那一半就是这些接线 (另一半在浏览器里真按一次).
        三条一起守, 因为缺哪一条这个按钮都只是块装饰:
        - 页面上有它, 而且默认藏着 (没在跑的时候不该有个能按的"停止");
        - 它发的是 POST + CSRF (与提问同一条纪律);
        - 请求体里带 `run_id` 与 `conversation_id` —— 少一个都取消不掉.
        """
        compact = self.page_source()

        self.assertIn('id="ask-stop"', compact)
        self.assertIn("stop.hidden = !runId", compact)
        self.assertIn("'/minimall/agent/cancel/'", compact)
        self.assertIn(
            "JSON.stringify({ run_id: runId, conversation_id: conversationId })",
            compact,
        )
        self.assertIn("stop.addEventListener('click', stopAsking)", compact)

    def test_the_run_id_only_shows_the_button_it_does_not_fake_it(self):
        """拿不到运行编号时不给停止按钮 —— 宁可不显示, 也不放一个按不动的按钮.

        编号是响应头给的 (`X-Run-Id`); 它要是被哪一层吞了, 按下去只会发一个空
        `run_id` 出去、被前端自己的校验挡下 —— 用户看到的是"点了没反应". 与其演这一下,
        不如一开始就别给.
        """
        compact = self.page_source()

        self.assertIn("runId = response.headers.get('X-Run-Id')", compact)
        self.assertIn("if (finished || !runId || stopping) return", compact)

    def test_the_page_restores_the_conversation_on_load(self):
        """页面加载时拉一次历史, 并用它渲染出聊过的那几轮.

        前端能被 Django 测试够到的那一半 (另一半在浏览器里真刷新一次): 拉的地址、
        带的编号、以及**渲染走的是同一套 DOM** —— 恢复的那一轮与直播那一轮共用
        `startTurn`, 没有第二套渲染代码. 两套迟早会漂, 而漂了只在一个方向上看得出来.
        """
        compact = self.page_source()

        self.assertIn("'/minimall/agent/history/'", compact)
        self.assertIn("loadHistory();", compact)
        self.assertIn("renderHistory(payload.messages || [])", compact)
        self.assertIn("restoreTurn(message.content)", compact)

    def test_the_history_is_asked_for_with_post_not_get(self):
        """会话编号**不当 URL 参数发出去** —— 页面这一侧也得守这条纪律.

        改这条的理由是「编号进 URL = 同时进日志 / 浏览器历史 / Referer, 而 GET 带
        cookie 跨站可触发」(见 `AgentHistoryView`). 断言落在**页面发的那个请求**上:
        方法是 POST、编号在请求体里、且带着 CSRF 令牌 —— 三样缺一, 这条纪律就漏了.

        列会话那条路 (本片接上的第四条) 同样吃这条纪律, 所以计数是 4.
        """
        compact = self.page_source()

        self.assertIn("fetch('/minimall/agent/history/', {", compact)
        self.assertIn(
            "body: JSON.stringify({ conversation_id: conversationId })", compact
        )
        self.assertNotIn("/history/?conversation_id=", compact)
        # 提问 / 取消 / 读历史 / 列会话: 四条路一套写法 (都 POST + 都带 CSRF)
        self.assertEqual(compact.count("'X-CSRFToken': csrfToken()"), 4)

    def test_the_conversation_id_survives_a_reload_but_not_a_new_tab(self):
        """会话编号存 `sessionStorage`: 刷新还在, 新标签页是新的一段.

        换成 `localStorage` 会让两个标签页共用一段对话 (历史串台 —— 用户故事 25 要
        防的正是这个), 换成一个内存变量则刷新即丢 (那是 L1b 的旧边界). 这条把那个
        选择钉在页面上, 不让它随手被改掉.
        """
        compact = self.page_source()

        self.assertIn("window.sessionStorage.getItem(STORAGE_KEY)", compact)
        self.assertIn("window.sessionStorage.setItem(STORAGE_KEY, id)", compact)
        # 查的是那个 API 有没有被用 (注释里提到了它是被否掉的那条路, 不算)
        self.assertNotIn("window.localStorage", compact)

    def test_the_tool_rows_take_their_words_from_the_backend(self):
        """工具那两行读的是 `label` (服务端给的人话), 不再读 `summary`.

        事件载荷换过形状了 (ADR-0003): 还按老字段渲染的话, 页面上那两行会变成空 ——
        这正是「后端改了字段、前端没跟上」那类静默失效. 反过来也要守住: 页面**不该**
        再引用那个已经不存在的字段, 否则以后有人补一个 `data.summary` 就又漏回来了.
        """
        compact = self.page_source()

        self.assertIn("data.label || '正在处理'", compact)
        self.assertIn(
            "data.label || (data.status === 'ok' ? '完成了' : '没成功')", compact
        )
        self.assertNotIn("data.summary", compact, "工具结果不再带正文了")

    def test_the_entry_is_in_the_navigation_on_a_product_page(self):
        """用户故事 23: 一边看商品页一边问 —— 入口在导航栏, 不在某一页里."""
        category = Category.objects.create(name="手机", slug="phones-bff")
        Product.objects.create(
            name="红米 Note 13",
            slug="redmi-note-13-bff",
            category=category,
            price=1299,
            stock=12,
        )

        body = self.client.get("/minimall/products/redmi-note-13-bff/").content.decode(
            "utf-8"
        )

        self.assertIn('href="/minimall/agent/"', body)
        # 顺带守一件事: 导航里不该漏出模板语法的残渣 —— Django 的 `{# #}` 只吃
        # 单行, 写成多行就不再是注释, 会被原样渲染到页面上 (这条在本片踩过一次,
        # 页面上真的出现过 `{# 智能客服入口... #}` 这行字).
        self.assertNotIn("{#", body)
        self.assertNotIn("{%", body)

    # ------------------------------------------------------------------
    # 左栏: 会话列表 (issue 19)
    # ------------------------------------------------------------------

    def test_the_sidebar_lists_the_conversations_of_this_buyer(self):
        """左栏的数据源只有一条 (`/conversations`), 而且拉的是**这个买家**的.

        页面自己不造行 —— 「没聊过的会话不显示」是后端按「有没有可见消息」过滤的
        结果 (见 `CharAgent/server/conversations.py`), 前端补一行就是另一种真相;
        「换一个买家看不到别人的」同理, 判据在助手服务那侧按转发过去的身份查.
        这里守的是接线: 打哪个地址, 用哪个动词, 拿回来画什么.
        """
        compact = self.page_source()

        self.assertIn("fetch('/minimall/agent/conversations/', {", compact)
        self.assertIn("loadConversations();", compact)
        self.assertIn("renderList(payload.conversations || [])", compact)
        # 当前那一段高亮: 比的是列表行给的编号与页面上正在用的那个
        self.assertIn("row.conversation_id === conversationId", compact)

    def test_an_empty_sidebar_says_so_instead_of_staying_blank(self):
        """一个会话都没有时说一句话, 不留一片空白.

        空列表是**正常状态** (新买家第一回来), 不是故障 —— 所以它既不该弹错, 也不
        该是一片看不出所以然的空白.

        注意它**只管「后端回了空列表」这一种**: 列表压根没拉回来 (上游没起来 / 没配
        记录层) 时左栏是留白的 —— 那时候说「还没有历史对话」就是撒谎, 而首屏的
        辅助件失败本来就不该打扰用户 (与读历史同一条收口).
        """
        compact = self.page_source()

        self.assertIn("还没有历史对话", compact)

    def test_a_blank_title_and_a_stale_timestamp_still_read_as_words(self):
        """标题空着要有兜底文案, 时间要读得懂 —— 两个都不是装饰.

        标题来自首条用户消息, 它可能还没写上 (会话行先建, 标题后补, 见
        `recorder._ensure_thread`), 空标题会让列表出现一行空白; 而把
        `2026-09-23T10:00:00+08:00` 原样摆出来对买家没有意义, 所以摆的是相对时间.
        """
        compact = self.page_source()

        self.assertIn("未命名对话", compact)
        self.assertIn("function relativeTime(", compact)
        self.assertIn("'刚刚'", compact)
        self.assertIn("分钟前", compact)
        self.assertIn("小时前", compact)

    def test_a_new_conversation_only_swaps_the_id_and_clears_the_room(self):
        """「新对话」**不落库**: 它只换一个会话编号 + 清空主区.

        会话行是「第一条消息发出去」时由记录器创建的 (懒创建), 所以点了新对话又
        不用, 列表里不会多出一条空壳 —— 这条正是不落库换来的. 编号来源也不许有
        第二份: 走的是同一个 `newConversationId()`.

        真机上核过这一条: 一整轮问答里点过两次新对话, 库里只多出**一行**会话
        (就是真聊过的那个), 另外两个编号谁也没建行.
        """
        compact = self.page_source()

        self.assertIn("switchConversation(newConversationId())", compact)
        self.assertIn("chat.replaceChildren()", compact)

    def test_switching_a_conversation_never_touches_the_url(self):
        """会话编号**不进地址栏** —— 切会话只动内存与 sessionStorage (ADR-0002).

        做成 `/minimall/agent/?c=…` 会顺带把它写进访问日志 / 浏览器历史 / Referer,
        那正是 issue 13 复核时把读历史从 GET 改成 POST 的同一条理由. 这条守的是
        页面这一侧: 换个会话不许碰 URL (pushState / replaceState / 查询串一个都
        不许出现), 于是"当前是哪一段"这件事只活在页面自己的状态里.
        """
        compact = self.page_source()

        self.assertNotIn("/history/?conversation_id=", compact)
        for banned in ("pushState", "replaceState", "searchParams"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, compact)

    def test_the_list_is_not_switchable_while_an_answer_is_running(self):
        """正在流式回答时切不动 —— 否则半截事件会落进另一段对话里.

        两道一起上: 列表项置灰且点不动 (视觉 + `pointer-events`), 以及切换函数
        自己那道 `finished` 判断 (键盘 / 程序化触发也拦得住). 回答结束 (终局事件
        之后走 `end()`) 恢复.
        """
        compact = self.page_source()

        self.assertIn("if (!finished || !id || id === conversationId) return", compact)
        self.assertIn("lockSwitching(true)", compact)
        self.assertIn("lockSwitching(false)", compact)
        self.assertIn("agent-list-busy", compact)

    def test_the_compaction_line_is_drawn_apart_from_the_tool_rows(self):
        """压缩那一行灰字与工具行**分开画** —— 它们说的是两件事.

        工具行说的是「做了什么」(查了订单 / 加进购物车了), 这一行说的是「上下文
        治理」(框架刚替我省了一笔). 同一块过程区里放两种版式, 是为了让"压缩"这件
        事看得见, 又不至于和一次工具调用混成一样.

        载荷沿用脱敏后的那一份 (`redact` 只动工具事件): 页面读到的是 dropped /
        truncated / saved_tokens 这些计数, 拼成人话再画 —— 不把字段名吐给用户,
        也不在页面这边重算一遍.
        """
        compact = self.page_source()

        self.assertIn("context_compacted: function (data) {", compact)
        self.assertIn("addNote(compactionNote(data))", compact)
        self.assertIn("'agent-compact'", compact)
        # 画出来的是一句话 (含压缩条数), 而不是 `dropped=2` 这种字段照抄
        self.assertIn("已压缩更早的", compact)
        self.assertIn("条消息", compact)
        # 与上一条灰字一样就不画第二遍. 比的是**上一条灰字**而不是最后一个孩子:
        # 两轮之间常常刚好插进一条工具行 (压缩 → 调工具 → 又压缩, 真机上的帧序
        # 就是这样), 比最后一个孩子会全漏 —— 这条第一版就写错了, 真机上抓到.
        self.assertIn("if (text === lastNote) return;", compact)
