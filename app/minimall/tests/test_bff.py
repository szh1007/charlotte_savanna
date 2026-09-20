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

BUYER_NAME = "bff_buyer"
OTHER_NAME = "bff_other"

# 写死两个 UUID 形状的对话编号 (前端每标签页生成一个, 见 agent.html)
TAB_ONE = "6f1c2c1e-1a2b-4c3d-8e9f-0a1b2c3d4e5f"
TAB_TWO = "2b7d9a10-aaaa-4bbb-8ccc-111222333444"


def _frame(seq: int, event: str, **payload) -> str:
    """一帧 SSE (形状与 05 服务一致)."""
    data = {"type": event, "seq": seq, **payload, "run_id": "run-20260920"}
    body = json.dumps(data, ensure_ascii=False)
    return f"id: {seq}\nevent: {event}\ndata: {body}\n\n"


def _sse(
    *frames: str, status: int = 200, content_type: str = "text/event-stream"
) -> httpx.Response:
    """假 CharApp 的响应 (一段流)."""
    return httpx.Response(
        status,
        content="".join(frames).encode("utf-8"),
        headers={"content-type": f"{content_type}; charset=utf-8"},
    )


def _question(message: str = "你好", conversation_id: str = TAB_ONE) -> str:
    """一份请求体 (页面发出来的形状) —— 自己造客户端的用例也用它, 别各抄一份."""
    return json.dumps({"message": message, "conversation_id": conversation_id})


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
# CSRF: 换 POST 换来的一层显式防护
# ---------------------------------------------------------------------------


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

    def test_the_pages_own_token_is_accepted(self):
        """页面拿到的那个令牌能过 —— 有防护还不够, 正常路径不能被误伤."""
        client = self.csrf_client()
        client.get(PAGE_URL)  # 页面渲染时种下 csrf cookie
        token = client.cookies["csrftoken"].value

        with respx.mock:
            route = self.mock_agent(_frame(1, "final", content="好的."))
            r = client.post(
                CHAT_URL,
                data=_question(),
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
            )
            _body(r)

        self.assertEqual(r.status_code, 200)
        self.assertTrue(route.called)

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
    """客服页面: 登录可见, 入口全站可达, 六类事件都接了."""

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
        """六类事件一个不少 —— 漏一个的表现是「那种事件静默消失」.

        这是前端能被 Django 测试够到的一半 (另一半是浏览器里真跑一遍): 事件名与
        框架的 `EventType` 全集对齐. 断言前先把空白压平 —— 守的是「这六个名字在」,
        不是「它们缩进几格」 (改个格式不该红).
        """
        compact = " ".join(self.client.get(PAGE_URL).content.decode("utf-8").split())

        for event in (
            "thinking",
            "tool_call",
            "tool_result",
            "reasoning",
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
        compact = " ".join(self.client.get(PAGE_URL).content.decode("utf-8").split())

        self.assertIn("new TextDecoderStream()", compact)
        self.assertIn("new EventSourceParserStream()", compact)
        self.assertNotIn("getReader()", compact)

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
