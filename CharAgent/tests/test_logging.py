"""结构化日志 + 三个 id (difficulties #38): 出口形状 / 打码工序 / 号牌.

三组, 逐条对应票面验收:

| 组 | 钉住的是 |
|----|---------|
| 出口与形状 | 一事件一行 JSON, 三个 id **恒在** (没绑就是 `null`), extra 进得去 |
| 打码在格式化**之前** | 三个落点各一条 (异常栈那条是本片核心) |
| 号牌的绑与还 | 出块要还回去 (不还就是串号), 名字写错当场报, 真实运行里每一行都带号 |

HTTP 那一路 (request_id 从哪来 / 两个并发请求不相同 / 号牌跟进了运行) 在
`test_server_logging.py` —— 那一组要起 app, 与这里的纯单元分开.

「一次真实运行」那条用**同步工具**里打的日志做断言: 同步工具跑在
`asyncio.to_thread` 的线程池里 (`tool/executor.py`), 而 contextvars 会不会跟进去
正是本片最容易做错的地方 (threading.local 就会在那里断掉) —— 拿它当样本, 比在
主协程里 log 一句更能说明问题.
"""

from __future__ import annotations

import io
import json
import logging

import pytest
from conftest import LOG_MASKED_PHONE, LOG_PHONE, LOG_REDACTOR
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.db import PriceTable
from CharAgent.db.recorder import ConversationRecorder
from CharAgent.db.testing import FakeRecordDatabase
from CharAgent.model.utils.types import Usage
from CharAgent.structured_logging import (
    ID_FIELDS,
    LoggingConfigError,
    TraceIds,
    configure_logging,
    current_ids,
    get_logger,
    log_context,
)
from CharAgent.structured_logging.testing import logging_to
from CharAgent.tool import tool

PHONE = LOG_PHONE
MASKED_PHONE = LOG_MASKED_PHONE
REDACTOR = LOG_REDACTOR

# 探针工具打日志用的 logger (取短名 —— 工厂会把它挂到 `charagent` 树下)
PROBE_LOGGER = get_logger("tests.probe")

THREAD_ID = "toy:tests:logger"

# 全天一价那张表 (不用日历): 记录员构造时要一份价目表, 给死免得看环境变量
FLAT_PRICES = PriceTable.from_json(
    '{"models": {"deepseek-flash": {"cache_miss": 2, "cache_hit": 0.5, "output": 8}}}'
)


@tool
def probe() -> str:
    """探针工具: 打一行日志, 再回一句固定的话.

    Returns:
        str: 固定的一句话.
    """
    PROBE_LOGGER.info("工具探针在跑")
    return "ok"


def lines_of(stream: io.StringIO) -> list[dict]:
    """缓冲里的一行行 JSON (每一行都必须能解析 —— 这本身就是要验的一条)."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line]


# ---------------------------------------------------------------------------
# 一、出口与形状
# ---------------------------------------------------------------------------


def test_one_event_is_one_json_line(log_stream: io.StringIO) -> None:
    """一事件一行, 且是标准的 JSON (能被 jq 直接消费)."""
    get_logger("tests.shape").warning("干了一件事", extra={"tool": "probe", "ms": 12})

    written = lines_of(log_stream)

    assert len(written) == 1, "一次调用写了一行以上 (或者一行都没写)"
    assert written[0]["level"] == "WARNING"
    assert written[0]["logger"] == "charagent.tests.shape"
    assert written[0]["msg"] == "干了一件事"
    # extra 放的结构化字段原样进 JSON —— 这是「结构化」与「拼字符串」的分野
    assert written[0]["tool"] == "probe"
    assert written[0]["ms"] == 12
    assert written[0]["ts"].startswith("20"), "ts 该是 ISO-8601 的时刻"


def test_the_three_ids_are_always_present(log_stream: io.StringIO) -> None:
    """三个键**恒在** —— 没绑就是 null (形状固定, 筛的人才不用先判键在不在)."""
    get_logger("tests.shape").info("没有号的一条")

    written = lines_of(log_stream)[0]

    assert set(ID_FIELDS) <= set(written)
    assert all(written[name] is None for name in ID_FIELDS)


def test_the_bound_ids_ride_along(log_stream: io.StringIO) -> None:
    """块内打的日志带着三个号, 且值就是绑上去的那几个."""
    with log_context(thread_id="t-1", run_id="r-1", request_id="q-1"):
        get_logger("tests.shape").info("带号的一条")

    written = lines_of(log_stream)[0]

    assert written["thread_id"] == "t-1"
    assert written["run_id"] == "r-1"
    assert written["request_id"] == "q-1"


def test_the_ids_are_released_when_the_block_ends(log_stream: io.StringIO) -> None:
    """出块要**还回去** —— 不还就是串号: 同一处下一条日志带着上一条的号."""
    with log_context(thread_id="t-1", run_id="r-1"):
        pass

    assert current_ids().thread_id is None
    assert current_ids().run_id is None

    get_logger("tests.shape").info("块外的一条")

    assert lines_of(log_stream)[0]["run_id"] is None


def test_binding_twice_keeps_one_token_per_name(log_stream: io.StringIO) -> None:
    """同一个名字绑第二次 = 换一个值, 且出块时不会留下没还的那一笔."""
    with log_context(thread_id="t-1") as trace:
        trace.bind(run_id="r-1")
        trace.bind(run_id="r-2")
        get_logger("tests.shape").info("换过号的一条")

    assert lines_of(log_stream)[0]["run_id"] == "r-2"
    assert current_ids().run_id is None, "里面那层没还回去"


def test_a_misspelled_id_is_refused_loudly() -> None:
    """名字写错当场报 —— 静默的表现是「那个号没绑上」, 而日志照写."""
    with (
        pytest.raises(LoggingConfigError) as ctx,
        log_context(thread="t-1"),  # type: ignore[call-arg]
    ):
        pass

    assert "thread" in str(ctx.value)
    assert "thread_id" in str(ctx.value), "报错要列出认识的那几个名字"


def test_an_unknown_level_is_refused_loudly() -> None:
    """级别名认不出的表现是「那一档日志全不见了」, 所以要在装配这一刻拦住."""
    with pytest.raises(LoggingConfigError) as ctx:
        configure_logging(level="VERBOSE", redactor=REDACTOR, stream=io.StringIO())

    assert "VERBOSE" in str(ctx.value)


def test_configuring_twice_does_not_write_twice(log_stream: io.StringIO) -> None:
    """再配一次先摘旧的 —— 每行写两遍会变成「是不是有两处都在打这一条」的假线索.

    同一个出口配两次 (启动脚本跑了两遍的样子), 只该留下一个 handler.
    """
    configure_logging(level=logging.INFO, redactor=REDACTOR, stream=log_stream)

    get_logger("tests.shape").info("只该出现一次")

    assert len(lines_of(log_stream)) == 1


def test_get_logger_puts_the_name_under_the_framework_tree() -> None:
    """短名与全名落到同一个 logger 上 (名字是选择器, 层级不许飘)."""
    assert get_logger("db").name == "charagent.db"
    assert get_logger("charagent.db") is get_logger("db")
    assert get_logger("server.app").name == "charagent.server.app"


def test_an_empty_logger_name_is_refused_loudly() -> None:
    """空名会建出**根** logger —— 那是个别的东西, 不该悄悄给."""
    with pytest.raises(LoggingConfigError):
        get_logger("  ")


def test_the_plain_tone_keeps_the_same_fields() -> None:
    """`json=False` 换的是体裁, 不是内容: 三个号与 extra 一个都不少."""
    stream = io.StringIO()
    with (
        logging_to(stream, redactor=REDACTOR, json=False),
        log_context(run_id="r-1"),
    ):
        get_logger("tests.plain").info("人念的一条", extra={"tool": "probe"})

    written = stream.getvalue()

    assert "r-1" in written
    assert "probe" in written
    assert "人念的一条" in written
    assert not written.lstrip().startswith("{"), "这一档不该是 JSON"


# ---------------------------------------------------------------------------
# 二、打码在格式化之前 (三个落点)
# ---------------------------------------------------------------------------


def test_a_phone_number_in_the_message_is_masked(log_stream: io.StringIO) -> None:
    """落点一: 拼好那句话 (参数里带的也算)."""
    get_logger("tests.redact").warning("买家电话 %s 打不通", PHONE)

    written = lines_of(log_stream)[0]

    assert written["msg"] == f"买家电话 {MASKED_PHONE} 打不通"
    assert PHONE not in log_stream.getvalue()


def test_uvicorns_color_message_never_becomes_a_field(log_stream: io.StringIO) -> None:
    """uvicorn 塞的 `color_message` 不进结构化字段 (两边都不进).

    它是 uvicorn 给自己那行**彩色**输出用的模板串, 正文与我们写出去那句逐字同义 ——
    留着的唯一后果是启动时满屏一个重复字段 (2026-10-05 用户报的). 排它的地方只有一处
    (`record.IGNORED_EXTRAS`: 「哪些键算字段」的权威), 于是 JSON 与纯文本两档一起干净.
    """
    get_logger("tests.noise").info(
        "Uvicorn running on http://127.0.0.1:8011",
        extra={"color_message": "Uvicorn running on \u001b[1m%s\u001b[0m"},
    )

    written = lines_of(log_stream)[0]

    assert "color_message" not in written
    assert written["msg"].startswith("Uvicorn running on")


def test_the_same_record_stays_clean_in_plain_text() -> None:
    """纯文本那一档同样不带噪音键 (两档是「看的方式不同」, 不是「记的内容不同」)."""
    stream = io.StringIO()
    with logging_to(stream, redactor=REDACTOR, json=False):
        get_logger("tests.noise").info("起服务了", extra={"color_message": "x"})

    written = stream.getvalue()

    assert "color_message" not in written
    assert "起服务了" in written


def test_a_declared_extra_field_is_masked(log_stream: io.StringIO) -> None:
    """落点二: `extra=` 传进来的结构字段**按声明**打 (主手段)."""
    get_logger("tests.redact").info(
        "账户状态", extra={"profile": {"phone": PHONE, "balance": "9500.00"}}
    )

    written = lines_of(log_stream)[0]

    assert written["profile"]["phone"] == MASKED_PHONE
    assert written["profile"]["balance"] == "[已抹除]"
    assert PHONE not in log_stream.getvalue()


def test_a_framework_traceback_never_reaches_the_log(log_stream: io.StringIO) -> None:
    """落点三 (**本片核心**): 框架自己打的异常栈也过出口.

    issue 29 真机验过的那条: 同一段供应商正文, 重试提示那一行是
    `138****0003`, 而框架打的「运行异常终止 + traceback」里还是原文 —— 因为那条
    日志不经过业务递给框架的 writer. 现在它在同一个出口上, 于是 `msg` 与 `exc`
    两处都该是打过码的.
    """
    get_logger("tests.redact").info("开始")

    def refuse() -> None:
        raise RuntimeError(f"上游拒绝: 输入里有手机号 {PHONE}, 请检查")

    try:
        refuse()
    except RuntimeError as exc:
        get_logger("tests.redact").error(
            "运行异常终止: %s", type(exc).__name__, exc_info=exc
        )

    raw = log_stream.getvalue()
    written = lines_of(log_stream)[-1]

    assert PHONE not in raw, "异常栈里的手机号原文落进了日志"
    assert written["msg"] == "运行异常终止: RuntimeError"
    assert "exc" in written, "异常栈该单独占一个字段 (它不是正文的一部分)"
    assert MASKED_PHONE in written["exc"]
    # 栈还得是栈: 打码不该把「哪一行抛的」一起打没
    assert "Traceback (most recent call last)" in written["exc"]
    assert "refuse" in written["exc"]


# ---------------------------------------------------------------------------
# 三、一次真实运行: 号牌跟着工具执行一起走
# ---------------------------------------------------------------------------


def _recorder() -> ConversationRecorder:
    """一个绑在玩具属主上的记录员 (给死价目表, 免得看环境变量).

    运行编号是**记录层**那一行 (`charagent_runs.run_id`) —— 没有记录层就没有号,
    所以「一次运行的每一行都带 run_id」这条只有在配了记录员时才成立.
    """
    return ConversationRecorder(
        database=FakeRecordDatabase(),
        tenant_id="t-logging",
        user_id="u-1",
        prices=FLAT_PRICES,
    )


async def test_a_run_carries_thread_and_run_id_into_the_tool_thread(
    log_stream: io.StringIO,
) -> None:
    """一次真实运行: 连**同步工具**里打的日志都带着号 (它跑在线程池里).

    这一条同时把两件事钉住: 会话层在开跑前后绑了号, 以及 contextvars 跟得进
    `asyncio.to_thread` (换成 `threading.local` 就会在这里断 —— 那正是本片最容易
    做错的地方).
    """
    model = MockLLM.scripted(
        [tool_call_response(make_tool_call("probe")), text_response("好了")]
    )
    session = ChatSession(
        model,
        saver=InMemoryCheckpointSaver(),
        tools=[probe],
        thread_id=THREAD_ID,
        model_name="deepseek-flash",
        recorder=_recorder(),
    )

    await session.ask("跑一下探针")

    probe_lines = [
        line for line in lines_of(log_stream) if line["logger"] == PROBE_LOGGER.name
    ]

    assert probe_lines, "探针那一行没写出来 (工具真的跑了吗)"
    for line in probe_lines:
        assert line["thread_id"] == THREAD_ID
        assert line["run_id"] == session.last_run_id, "日志里的号该是记录层那一行"
    assert session.last_run_id is not None, "配了记录员就该有运行编号"


async def test_a_finished_run_leaves_exactly_one_line(log_stream: io.StringIO) -> None:
    """跑完一段就留一行 —— 「跑顺的时候日志里空空如也」是这一行补上的那个洞.

    为什么值得一条: 别的日志都是**出了事**才响的, 于是「正常长什么样」这份基准
    根本不存在; 而这次问答花了多少 token / 几轮 / 多久, 只有这一行记着. 它也是
    「结构化」那一半真正在用的证据 (走 `extra=`, 能被筛而不只是能读).
    """
    model = MockLLM.scripted(
        [text_response("你好", usage=Usage(input_tokens=120, total_tokens=140))]
    )
    session = ChatSession(
        model,
        saver=InMemoryCheckpointSaver(),
        tools=[],
        thread_id=THREAD_ID,
        model_name="deepseek-flash",
        recorder=_recorder(),
    )

    await session.ask("你好")

    written = [line for line in lines_of(log_stream) if line["level"] == "INFO"]
    assert len(written) == 1, "一次运行该只留一行 (失败那条路另有落点)"
    line = written[0]
    # 这一次运行自己的事实
    assert line["outcome"] == "finished"
    assert line["turns"] == 1
    assert line["tokens"] == 140
    assert line["model"] == "deepseek-flash"
    assert "tokens" in line["msg"]
    # 三个号仍然由上下文自动带上 (这一行不需要自己记)
    assert line["thread_id"] == THREAD_ID
    assert line["run_id"] == session.last_run_id


async def test_the_ids_do_not_leak_into_the_next_run(log_stream: io.StringIO) -> None:
    """两句连着问: 第二句不会带着第一句的运行号 (出块还原的那一条)."""
    model = MockLLM.scripted([text_response("一"), text_response("二")])
    session = ChatSession(
        model,
        saver=InMemoryCheckpointSaver(),
        tools=[],
        thread_id=THREAD_ID,
        model_name="deepseek-flash",
        recorder=_recorder(),
    )

    await session.ask("第一句")
    first = session.last_run_id
    await session.ask("第二句")

    assert session.last_run_id != first
    # 会话结束后上下文是干净的 —— 下一次运行从零绑起
    assert current_ids() == TraceIds()


def test_the_logger_names_are_still_the_documented_ones() -> None:
    """六个模块换工厂之后, logger 名**一个字都没变** (老用例按名字筛日志)."""
    import CharAgent.agent.compaction as compaction
    import CharAgent.client.session as session
    import CharAgent.db.recorder as recorder
    import CharAgent.prompt.ref as ref
    import CharAgent.server.app as server_app
    import CharAgent.server.runs as server_runs

    assert compaction.logger.name == "charagent.agent"
    assert session.logger.name == "charagent.client"
    assert recorder.logger.name == "charagent.db"
    assert ref.logger.name == "charagent.prompt"
    assert server_app.logger.name == "charagent.server"
    assert server_runs.logger is server_app.logger
