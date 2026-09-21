"""命令行入口: 端到端跑一次真实问答 (PRD §5 的测试接缝一).

被测的是**接线** —— 一条链路从命令行参数一路走到商城的 HTTP 请求:

    参数 → 运行上下文 → 工具 (身份进闭包) → 模型决策 → 工具执行
         → 商城接口 → 数据回填 → 答复

中间任何一处接错, 这里都红。扮演模型的是框架的 MockLLM, 扮演商城的是 respx,
业务代码一行不改 —— 与框架自己的 CLI 测试同一套做法。

为什么这些用例是**同步**函数: `main()` 自己建常驻事件循环 (httpx 的连接池绑定
创建它的那个循环, 每问一句换一个新循环会踩到「连接属于别的循环」), 所以整条
链路跑在 main 自己的循环里, 测试不必也不该插手。
"""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import AGENT_BASE_URL, BUYER_ID, agent_url, mock_all

from CharAgent.checkpoint import config as checkpoint_config
from CharAgent.tests.mock_llm import (
    MockLLM,
    make_tool_call,
    text_response,
    tool_call_response,
)
from CharAgent.tests.trace_assertions import trace_of
from CharApp.minimall import cli
from CharApp.minimall.config import ENV_BASE_URL, ENV_TOKEN


def backfilled(model, turn: int) -> list[str]:
    """第 `turn` 轮模型看到的历史里, 那些**回填给它的工具结果**的正文.

    「工具回了什么」这类断言一律走它 —— 判据是 wire 上的历史 (模型真收到的东西),
    而不是工具函数的返回值: 后者证明不了它有没有被送进模型.
    """
    return [
        str(message.get("content") or "")
        for message in trace_of(model).seen(turn)
        if message.get("role") == "tool"
    ]


@pytest.fixture(autouse=True)
def mall_env(monkeypatch) -> None:
    """把商城的地址、令牌与快照后端都钉成测试值.

    地址必须钉: 不钉的话 `client_from_env` 会用默认的本机 8000 端口, respx 拦不到,
    整个端到端用例就变成了「真的去连本机 Django」—— 那不是单元测试.

    快照后端同理: `main()` 会读根 `.env` (它在仓库里, 不提交), 而开发者完全可能
    按框架文档把后端切成 redis / postgres —— 那时这些「离线」用例会在落快照时去
    连真实存储 (挂了, 或往 `minimall:*` 分区里写脏数据). 钉死 memory 才是它们宣称的
    那个「不依赖外部服务」.
    """
    monkeypatch.setenv(ENV_TOKEN, "test-internal-token")
    monkeypatch.setenv(ENV_BASE_URL, AGENT_BASE_URL)
    monkeypatch.setenv(checkpoint_config.ENV_BACKEND, "memory")


# ---------------------------------------------------------------------------
# 三条验收问答 (问商品 / 问订单 / 问余额)
# ---------------------------------------------------------------------------


def test_asking_for_a_product_gets_real_data(mall, capsys) -> None:
    """「有什么 2000 块以下的手机推荐吗」→ 模型按条件查商品, 答复里带着真数据."""
    routes = mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call(
                    "search_products", '{"keyword": "手机", "max_price": 2000}'
                )
            ),
            text_response("符合条件的有 1 款: 红米 Note 13, 1299.00 元, 现货 12 件."),
        ]
    )

    code = cli.main(
        ["--user-id", str(BUYER_ID), "-q", "有什么 2000 块以下的手机推荐吗"],
        model=model,
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "红米 Note 13" in out
    # 筛选条件真的落到了商城的查询参数上 (模型填对了不等于发对了).
    # `2000` 在这里变成 `2000.0`: 工具的参数声明是 float, pydantic 校验时把模型
    # 给的整数转成了浮点 —— 商城侧的 NumberFilter 两者都收.
    # `page_size` 是工具的默认值, 模型没填也会发出去.
    assert dict(routes["GET products/"].calls[0].request.url.params) == {
        "search": "手机",
        "max_price": "2000.0",
        "page_size": "100",
    }
    # 身份也真的带上了
    assert routes["GET products/"].calls[0].request.headers["X-User-Id"] == str(
        BUYER_ID
    )


def test_asking_about_orders_gets_the_order_list(mall, capsys) -> None:
    """「我最近的订单到哪了」→ 查订单列表, 答复里带着订单号与状态."""
    routes = mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("list_my_orders")),
            text_response("你最近一笔订单是 202609191230450000031234, 已发货."),
        ]
    )

    code = cli.main(
        ["--user-id", str(BUYER_ID), "-q", "我最近的订单到哪了"], model=model
    )

    assert code == 0
    assert "202609191230450000031234" in capsys.readouterr().out
    assert routes["GET orders/"].called


def test_asking_about_the_balance_gets_the_profile(mall, capsys) -> None:
    """「我余额还有多少」→ 查账户, 余额照读 (2 位小数字符串)."""
    routes = mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("get_my_profile")),
            text_response("你的余额是 9500.00 元."),
        ]
    )

    code = cli.main(["--user-id", str(BUYER_ID), "-q", "我余额还有多少"], model=model)

    assert code == 0
    assert "9500.00" in capsys.readouterr().out
    assert routes["GET profile/"].called


# ---------------------------------------------------------------------------
# 多轮连贯 (框架已有能力, 这里验证接线正确)
# ---------------------------------------------------------------------------


def test_two_questions_stay_connected(mall, capsys) -> None:
    """问完推荐后追问「第二个多少钱」—— 第二轮看得到第一轮说过的商品.

    验的是接线而不是框架: 会话历史必须真的接上去, 而不是每次从零开始。做法是
    直接看模型**第二轮**收到了什么 (MockLLM 记下了每次请求的 messages).
    """
    mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("search_products", '{"keyword": "手机"}')
            ),
            text_response("有两款: 红米 Note 13 (1299.00 元) 和 小米 14 (3999.00 元)."),
            tool_call_response(
                make_tool_call("get_product_detail", '{"slug": "redmi-note-13"}')
            ),
            text_response("第二个是小米 14, 3999.00 元."),
        ]
    )

    code = cli.main(
        [
            "--user-id",
            str(BUYER_ID),
            "-q",
            "有什么 2000 块以下的手机推荐吗",
            "-q",
            "第二个多少钱",
        ],
        model=model,
    )

    assert code == 0
    trace = trace_of(model)
    # 两次工具调用按模型决策的顺序发生 (轨迹断言: 不只看答得对不对)
    trace.assert_tool_calls(
        [
            ("search_products", {"keyword": "手机"}),
            ("get_product_detail", {"slug": "redmi-note-13"}),
        ]
    )
    # 第 3 轮 = 第二个问题的第一次决策: 它看到的历史里必须有第一轮说过的话
    seen = [str(message.get("content") or "") for message in trace.seen(3)]
    assert any("红米 Note 13" in text for text in seen), (
        "第二个问题的请求里没有第一轮的答复 —— 多轮接续断了"
    )
    assert any(message.get("role") == "tool" for message in trace.seen(3)), (
        "第一轮的工具结果也没在历史里"
    )


# ---------------------------------------------------------------------------
# 身份: 从命令行参数来 (同一处装配在 HTTP 入口那边改成读转发头, 见 test_server.py)
# ---------------------------------------------------------------------------


def test_the_buyer_identity_comes_from_the_command_line(mall) -> None:
    """`--user-id 7` → 每个请求都带 `X-User-Id: 7`, 快照分区也跟着换."""
    routes = mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("get_my_profile")),
            text_response("余额 9500.00 元."),
        ]
    )

    code = cli.main(["--user-id", "7", "-q", "我余额还有多少"], model=model)

    assert code == 0
    assert routes["GET profile/"].calls[0].request.headers["X-User-Id"] == "7"


def test_the_session_id_carries_the_business_and_the_buyer() -> None:
    """会话编号 = `业务:买家:对话` —— 多用户隔离靠的就是它 (快照按它分区)."""
    assert cli.parse_argv(["--user-id", "7"]).thread_id == "minimall:7:cli"

    per_device = cli.parse_argv(["--user-id", "7", "--conversation-id", "phone"])

    assert per_device.thread_id == "minimall:7:phone", (
        "换一段对话就该换一个分区键 —— 否则两台设备的历史会串台"
    )


# ---------------------------------------------------------------------------
# 交互模式
# ---------------------------------------------------------------------------


def test_interactive_mode_answers_then_quits(mall, capsys) -> None:
    """交互模式: 读一行 → 答一句 → `/quit` 退出, 退出码 0."""
    routes = mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("get_my_profile")),
            text_response("你的余额是 9500.00 元."),
        ]
    )
    lines = iter(["我余额还有多少", "/quit"])

    code = cli.main(
        ["--user-id", str(BUYER_ID)], model=model, reader=lambda _prompt: next(lines)
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "minimall 电商客服" in out, "启动横幅要摆清现在以谁的身份在跟谁说话"
    assert "9500.00" in out
    assert routes["GET profile/"].called


def test_an_unknown_command_is_not_sent_to_the_model(mall, capsys) -> None:
    """敲错的命令只提示, **不**发给模型 —— 既费 token 又让人一头雾水."""
    mock_all(mall)
    model = MockLLM.scripted([text_response("不该被用到")])
    lines = iter(["/nope 3", "/quit"])

    code = cli.main(
        ["--user-id", str(BUYER_ID)], model=model, reader=lambda _prompt: next(lines)
    )

    assert code == 0
    assert "不认识这条命令" in capsys.readouterr().out
    assert model.calls == [], "敲错的命令不该变成一次模型调用"


def test_ctrl_c_at_the_prompt_ends_the_session_cleanly(mall, capsys) -> None:
    """在提示符处按 Ctrl-C 是「退出」的常规写法 —— 不当错误处理, 也不打 traceback."""
    mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("get_my_profile")),
            text_response("你的余额是 9500.00 元."),
        ]
    )
    reads = 0

    def reader(_prompt: str) -> str:
        nonlocal reads
        reads += 1
        if reads == 1:
            return "我余额还有多少"
        raise KeyboardInterrupt

    code = cli.main(["--user-id", str(BUYER_ID)], model=model, reader=reader)

    assert code == 0
    assert "9500.00" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 启动期错误: 报一句人话, 不打 traceback
# ---------------------------------------------------------------------------


def test_a_missing_token_fails_at_startup(monkeypatch, capsys) -> None:
    """没配内部令牌时**启动就报错** —— 而不是等第一个工具执行才说「内部错误」.

    这条守的是可排查性: 令牌没配的表现本来是「商城的每个请求都被拒」, 助手只会
    说「工具执行时发生内部错误」, 排查起来从最远的地方开始找. 在这里拦住, 报错
    信息才有地方说清「该去 .env 里配哪个变量」.
    """
    monkeypatch.setenv(ENV_TOKEN, "")

    code = cli.main(["--user-id", str(BUYER_ID), "-q", "我余额还有多少"])

    assert code == 1
    out = capsys.readouterr().out
    assert "启动失败" in out
    assert ENV_TOKEN in out


def test_a_tool_failure_is_reported_to_the_model_not_crashed(mall, capsys) -> None:
    """商城 5xx 时工具失败, 但整次运行不崩 —— 模型收到的是一句可操作的错误.

    框架的既有约定: 工具异常永不外泄, 一律变成模型看得懂的文字回填, 模型据此向
    买家说明情况, 而不是抛一个 500 到命令行上.
    """
    # 正文刻意不含下面断言的那句话: 这样「框架的回填文案」与「工具异常原文漏进
    # 模型」两条路就区分得开 (后者会把响应摘要一起带过去)
    mall.get(agent_url("profile/")).mock(
        return_value=httpx.Response(500, text="数据库连接失败")
    )
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("get_my_profile")),
            text_response("商城好像出问题了, 稍后再试试."),
        ]
    )

    code = cli.main(["--user-id", str(BUYER_ID), "-q", "我余额还有多少"], model=model)

    assert code == 0, "工具失败不该让整次运行失败 —— 模型还能把话说圆"
    # 判据取框架内部错误文案的独有片段 (`tool/utils/messages.INTERNAL_ERROR_TEXT`),
    # 而不是「内部错误」这四个字 —— 后者在异常原文漏出去时也成立, 那样这条用例
    # 就守不住它自己声称的那件事了
    seen = backfilled(model, 2)
    assert any("请勿使用相同参数重试" in text for text in seen), (
        f"工具失败要以框架的可操作错误文案回填给模型, 实际: {seen}"
    )
    assert "稍后再试" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 写操作端到端: 真的改了数据, 而且真的被护栏拦得住
# ---------------------------------------------------------------------------


def test_asking_to_add_to_cart_really_adds_it(mall, capsys) -> None:
    """「把红米加两件到购物车」→ 商城真的收到加购请求, 答复里是加完之后的整车.

    断在**商城侧收到的那条请求**上 (方法 / 路径 / 请求体), 不是断在「工具被调用
    了」—— 工具调了但参数拼错, 在只读那边只是"查不到", 在这里是**改错东西**.
    紧接着再查一次车, 于是「写后立刻读得到」也被钉住了.
    """
    routes = mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call(
                    "add_to_cart", '{"slug": "redmi-note-13", "quantity": 2}'
                )
            ),
            tool_call_response(make_tool_call("get_my_cart")),
            text_response("加好了, 购物车里现在 2 件, 一共 2598.00 元."),
        ]
    )

    code = cli.main(
        ["--user-id", str(BUYER_ID), "-q", "把红米 Note 13 加两件到购物车"],
        model=model,
    )

    assert code == 0
    added = routes["POST cart/items/"].calls[0].request
    assert json.loads(added.content) == {"slug": "redmi-note-13", "quantity": 2}
    assert added.headers["X-User-Id"] == str(BUYER_ID), "写操作也要带身份"
    assert routes["GET cart/"].called, "加完之后再查一次车要读得到"
    assert "2598.00" in capsys.readouterr().out


def test_the_write_budget_stops_the_ninth_write(mall, capsys) -> None:
    """连环加购到第 9 次被护栏拦下: 商城只收到 8 次请求, 模型收到理由后继续作答.

    这是 L2 验收里那句「护栏生效且可轨迹断言」的落地 —— 三条证据缺一不可:

    1. 商城侧**只收到 8 次** (第 9 次真的没打出去, 不是打完了才发现);
    2. 被拒的那条 tool 消息里是护栏给的理由 (模型据此对买家说明);
    3. 整次运行照常收尾 (拒绝不是崩溃).
    """
    routes = mock_all(mall)
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("add_to_cart", f'{{"slug": "p{n}"}}', call_id=f"c{n}")
            )
            for n in range(1, 10)  # 9 次写操作, 预算 8
        ]
        + [text_response("这件我先不动了, 你到页面上操作吧.")]
    )

    code = cli.main(["--user-id", str(BUYER_ID), "-q", "把这些都加上"], model=model)

    assert code == 0, "被拒的那一次不该让整次运行失败"
    assert routes["POST cart/items/"].call_count == 8, "第 9 次不该打出去"
    # 看**最后一轮**模型看到的历史 (第 10 轮: 9 次调用都出结果之后才轮到它总结),
    # 而不是第 9 轮 —— 那一轮模型看到的还只有前 8 条结果
    seen = backfilled(model, 10)
    assert len(seen) == 9
    assert "已经用完" in seen[-1] and "页面上完成" in seen[-1]
    assert "你到页面上操作吧" in capsys.readouterr().out


def test_an_order_over_the_limit_never_reaches_the_mall(mall, capsys) -> None:
    """购物车合计 9900 元时, 那一单**商城侧一次都没收到** —— 护栏在下单前拦下.

    这里不用 `mock_all` 的现成样本, 而是自己铺一份「车里很贵」的: 该收到的只有
    那条查购物车的请求. `assert_all_mocked` 是开着的, 所以「下单打出去了」会以
    未预期请求的形式当场炸出来, 不必另外断言.
    """
    routes = {
        "cart": mall.get(agent_url("cart/")).mock(
            return_value=httpx.Response(
                200, json={"items": [], "total_count": 100, "total_amount": "9900.00"}
            )
        ),
        "order": mall.request("POST", agent_url("orders/")).mock(
            return_value=httpx.Response(200, json={})
        ),
    }
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("place_order")),
            text_response("这单金额有点大, 你自己在结算页下吧."),
        ]
    )

    code = cli.main(["--user-id", str(BUYER_ID), "-q", "下单"], model=model)

    assert code == 0
    assert routes["cart"].called, "判金额要真去问一次车 (不是猜的)"
    assert routes["order"].call_count == 0, "超限的那一单不该打出去"
    seen = backfilled(model, 2)
    assert "9900.00" in seen[0] and "5000.00" in seen[0]
    assert "结算页" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 开关真的生效 (解析了、也传下去了)
# ---------------------------------------------------------------------------


def test_no_thinking_actually_reaches_the_model(mall) -> None:
    """`--no-thinking` 真的关掉了思考模式 —— 不是「解析完就丢」.

    这条盯的是一类静默失效: 命令行开关解析了、存进 options 了、装配时却没往下传.
    表现为用户付了推理 token 却以为关了, 而且**测试全绿**——判据因此取模型请求里的
    `thinking` (MockLLM 记下了每次调用的采样参数), 而不是 options 里的值: 后者在
    漏传时照样是对的, 断言它等于没断言. 这个 bug 真的发生过, 由代码评审抓出来.
    """
    mock_all(mall)
    model = MockLLM.scripted([text_response("好的.")])

    code = cli.main(
        ["--user-id", str(BUYER_ID), "--no-thinking", "-q", "你好"], model=model
    )

    assert code == 0
    assert model.calls[0]["thinking"] is False


def test_thinking_follows_the_upstream_default_when_not_asked(mall) -> None:
    """不给开关时不表态 (None) —— 思考模式走上游默认, 与框架自己的 CLI 一致."""
    mock_all(mall)
    model = MockLLM.scripted([text_response("好的.")])

    code = cli.main(["--user-id", str(BUYER_ID), "-q", "你好"], model=model)

    assert code == 0
    assert model.calls[0]["thinking"] is None
