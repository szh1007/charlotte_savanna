"""agent 包的业务接入点: 运行上下文 (RunContext) + 工具提供者 (ToolProvider).

被测的是「业务怎么接进框架」这条约定, 以及它**真的够通用**:

1. 运行上下文只装两样东西 —— 会话编号 + 一块**框架不解释**的载荷; 框架从不读
   载荷里的字段, 也不规定它必须有什么.
2. 工具提供者是一个 `async def provide(运行上下文) -> 工具列表` 的形状, 把
   「这次运行该拿哪些工具」从框架里挪出去 —— 框架只认形状, 不认业务.
3. **通用性测试 (本文件的重点)**: 用**同一套装配代码**装两个毫不相干的业务,
   两个都能跑完一次完整问答. 这条用例是「框架不认识业务」这个卖点的防线 ——
   哪天框架里长出一句业务判断, 它就会红.

两个业务为什么都在本文件里写成假实现: 真业务在 `CharApp/`, 而框架**不许**引用
业务 (`CharApp/docs/PLAN.md` §2 的分层约束) —— 测试若去 import 真业务, 这条
规矩当场就破了. 两个假业务的形状刻意拉开距离: 一个纯计算、不需要身份, 另一个
有状态、必须知道「当前用户是谁」; 同一套装配代码要同时伺候这两种.
"""

from __future__ import annotations

import json
from pathlib import Path

from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response
from trace_assertions import trace_of

from CharAgent.agent import LoopResult, RunContext, ToolProvider
from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client.session import ChatSession
from CharAgent.model.protocol import ChatModel
from CharAgent.tool import Tool, tool

# 框架的根目录 (本文件在 CharAgent/tests/ 下, 上一层就是它)
FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]

# 只属于商城业务的词 (见 test_the_framework_never_mentions_the_business).
# 挑词口径: 这些词在本仓的用法**只有**「商城业务」一种含义, 而框架源码里
# 一个都没有 (2026-09-18 实测) —— 像「订单」这种被框架演示工具
# (tools_demo.query_order_status) 用到的词反而不能进词表, 那会让用例一上来就红.
BUSINESS_WORDS = ("minimall", "ecom", "商品", "购物车", "收货地址", "买家", "商城")


# ---------------------------------------------------------------------------
# 两个假业务 (通用性测试的两端)
# ---------------------------------------------------------------------------


# --- 业务 A: 纯计算, 不需要身份 -------------------------------------------


@tool
async def sum_numbers(left: int, right: int) -> str:
    """求两个整数之和.

    Args:
        left: 第一个加数.
        right: 第二个加数.
    """
    return str(left + right)


@tool
async def reverse_text(text: str) -> str:
    """把一段文本倒过来.

    Args:
        text: 要倒置的文本.
    """
    return text[::-1]


TOY_TOOLS = (sum_numbers, reverse_text)


# --- 业务 B: 有状态, 必须知道「当前用户是谁」 -------------------------------


@tool
async def search_products(keyword: str) -> str:
    """按关键词搜索商品.

    Args:
        keyword: 搜索关键词.
    """
    return f"与「{keyword}」有关的商品: 甲 / 乙"


SHOP_TOOLS = (search_products,)


class ToyProvider:
    """业务 A 的工具提供者: 与身份无关, 看一眼上下文就算了."""

    def __init__(self) -> None:
        self.seen: list[RunContext] = []

    async def provide(self, context: RunContext) -> list[Tool]:
        """交出业务 A 的两个玩具工具."""
        self.seen.append(context)
        return list(TOY_TOOLS)


class ShopProvider:
    """业务 B 的工具提供者: 从载荷取用户 ID, 用闭包包进工具里.

    这就是「身份绕开参数表」那条设计的完整形态 —— 模型看到的工具参数表里只有
    `keyword`, 用户是谁早在装配时裹进闭包了. 真业务 (issue 03) 用的是同一个
    形状, 只是工具多几个、背后打的是商城接口.
    """

    def __init__(self) -> None:
        self.seen: list[RunContext] = []

    async def provide(self, context: RunContext) -> list[Tool]:
        """按上下文里的用户身份造工具."""
        self.seen.append(context)
        user_id = context.payload["user_id"]

        @tool
        async def my_balance() -> str:
            """查询当前用户自己的账户余额."""
            return f"用户 {user_id} 的余额: 100.00"

        return [*SHOP_TOOLS, my_balance]


# ---------------------------------------------------------------------------
# 装配 (两个业务共用的那一套)
# ---------------------------------------------------------------------------


async def assemble(
    model: ChatModel,
    provider: ToolProvider,
    context: RunContext,
    *,
    prompt_name: str,
    prompt_dir: Path,
    question: str,
) -> tuple[ChatSession, LoopResult]:
    """**同一套装配代码**: 上下文 → 工具 → 会话 → 问一句 (两个业务共用).

    真实业务的命令行入口走的就是这条线; 这里压成几行, 好让「换一个业务只换
    参数、不换流程」在测试里看得见. 顺序也如实反映依赖: 先拿上下文换工具
    (提供者是异步的), 再把工具交给会话.
    """
    session = ChatSession(
        model,
        saver=InMemoryCheckpointSaver(),
        tools=list(await provider.provide(context)),
        thread_id=context.thread_id,
        prompt_name=prompt_name,
        prompt_dir=prompt_dir,
    )
    return session, await session.ask(question)


def write_prompt(directory: Path, name: str, body: str) -> Path:
    """往业务自己的提示词目录里放一个 `.prompt` 文件 (返回该目录)."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.prompt").write_text(body, encoding="utf-8")
    return directory


# ---------------------------------------------------------------------------
# 运行上下文
# ---------------------------------------------------------------------------


def test_run_context_carries_a_thread_id_and_an_opaque_payload() -> None:
    """上下文只有两样东西: 会话编号 + 载荷; 载荷原样躺着, 框架不碰它."""
    payload = {"user_id": "u-9f3a", "tenant": "acme", "nested": {"roles": ["buyer"]}}

    context = RunContext(thread_id="shop:u-9f3a:chat-1", payload=payload)

    assert context.thread_id == "shop:u-9f3a:chat-1"
    assert context.payload == payload
    assert context.payload is payload, "载荷要原样透传, 不是拷一份出来"


def test_run_context_payload_defaults_to_empty() -> None:
    """不装业务数据时不必写 payload (纯聊天场景)."""
    assert RunContext(thread_id="t-1").payload == {}


async def test_the_framework_never_reads_inside_the_payload(tmp_path: Path) -> None:
    """框架只认「有一块载荷」, 不认里面有什么 —— 换个业务的字段名不用改框架.

    做法: 用一批**框架没听说过**的字段名造上下文, 走完一次完整问答. 框架若在
    哪儿偷读过某个约定字段 (比如假定必有 user_id), 这里就会炸.
    """
    toy_dir = write_prompt(tmp_path / "toy", "toy", "你是玩具助手 A.")
    opaque = RunContext(thread_id="t-opaque", payload={"租户": "甲", "轮次": 3})
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("reverse_text", '{"text": "abc"}')),
            text_response("倒过来是 cba"),
        ]
    )

    _, result = await assemble(
        model,
        ToyProvider(),
        opaque,
        prompt_name="toy",
        prompt_dir=toy_dir,
        question="把 abc 倒过来",
    )

    assert result.content == "倒过来是 cba"


# ---------------------------------------------------------------------------
# 工具提供者
# ---------------------------------------------------------------------------


async def test_a_provider_is_anything_with_the_right_shape() -> None:
    """提供者是结构化协议: 有那个方法就算, 不要求继承谁 (与 ChatModel 同款)."""
    provider = ToyProvider()
    context = RunContext(thread_id="t-1")

    tools = await provider.provide(context)

    assert [item.name for item in tools] == ["sum_numbers", "reverse_text"]
    assert provider.seen == [context], "提供者应当收到装配时给的那个上下文"


async def test_the_identity_stays_out_of_every_tool_schema() -> None:
    """身份在装配时裹进闭包: 提供者拿得到用户 ID, 工具的 schema 里一个字没有.

    这条是 4.2 那条安全设计的守卫 —— 模型的视角就是工具的 JSON schema
    (名字 + 说明 + 参数表), 那里面没有的东西, 它看不见、也无从填别人的值.
    """
    provider = ShopProvider()
    context = RunContext(thread_id="shop:u-9f3a:chat-1", payload={"user_id": "u-9f3a"})

    tools = await provider.provide(context)

    assert provider.seen[0].payload["user_id"] == "u-9f3a"
    balance = next(item for item in tools if item.name == "my_balance")
    assert balance.parameters["properties"] == {}, "余额工具的入参必须是空的"
    for item in tools:
        schema = json.dumps(
            {
                "name": item.name,
                "description": item.description,
                "parameters": item.parameters,
            },
            ensure_ascii=False,
        )
        assert "u-9f3a" not in schema, f"工具 {item.name} 的 schema 漏了身份: {schema}"


# ---------------------------------------------------------------------------
# 通用性: 同一套装配, 两个不同的业务
# ---------------------------------------------------------------------------


async def test_one_assembly_serves_two_unrelated_businesses(tmp_path: Path) -> None:
    """**同一套装配代码**装两个毫不相干的业务, 两个都跑完一次完整问答.

    业务 A (纯计算、无身份) 与业务 B (有状态、要身份) 的唯一差别是传给装配函数
    的参数 —— 装配流程一行没变. 这条用例就是「框架不认识业务」这个卖点的防线.
    """
    toy_dir = write_prompt(tmp_path / "toy", "toy", "你是玩具助手 A.")
    shop_dir = write_prompt(tmp_path / "shop", "shop", "你是商城助手 B.")
    toy_model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("sum_numbers", '{"left": 2, "right": 3}')
            ),
            text_response("2 加 3 等于 5"),
        ]
    )
    shop_model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("search_products", '{"keyword": "手机"}')
            ),
            text_response("找到两款手机: 甲 / 乙"),
        ]
    )

    toy_session, toy_result = await assemble(
        toy_model,
        ToyProvider(),
        RunContext(thread_id="toy:chat-1"),
        prompt_name="toy",
        prompt_dir=toy_dir,
        question="2 加 3 是多少",
    )
    shop_session, shop_result = await assemble(
        shop_model,
        ShopProvider(),
        RunContext(thread_id="shop:u-9f3a:chat-1", payload={"user_id": "u-9f3a"}),
        prompt_name="shop",
        prompt_dir=shop_dir,
        question="有什么手机推荐吗",
    )

    # 两个业务都真的跑通了: 工具被调、结果回填、答复里带着业务数据
    toy_trace = trace_of(toy_model)
    toy_trace.assert_tool_calls([("sum_numbers", {"left": 2, "right": 3})])
    toy_trace.assert_tool_result_backfilled("sum_numbers", contains="5")
    assert toy_result.content == "2 加 3 等于 5"

    shop_trace = trace_of(shop_model)
    shop_trace.assert_tool_calls([("search_products", {"keyword": "手机"})])
    shop_trace.assert_tool_result_backfilled("search_products", contains="甲")
    assert shop_result.content == "找到两款手机: 甲 / 乙"

    # 各自的提示词是各自的 (目录参数真的生效, 不是读的框架那一份)
    assert toy_session.history[0]["content"] == "你是玩具助手 A."
    assert shop_session.history[0]["content"] == "你是商城助手 B."


async def test_the_identity_actually_reaches_the_tool(tmp_path: Path) -> None:
    """反过来也要成立: 身份虽然不进参数表, 但工具真的用上了它.

    与上一条合起来才是完整的主张 —— 「模型看不见」不等于「谁都没看见」.
    """
    shop_dir = write_prompt(tmp_path / "shop", "shop", "你是商城助手 B.")
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("my_balance")),
            text_response("余额 100.00"),
        ]
    )

    _, result = await assemble(
        model,
        ShopProvider(),
        RunContext(thread_id="shop:u-9f3a:chat-1", payload={"user_id": "u-9f3a"}),
        prompt_name="shop",
        prompt_dir=shop_dir,
        question="我余额还有多少",
    )

    trace_of(model).assert_tool_result_backfilled("my_balance", contains="u-9f3a")
    assert result.content == "余额 100.00"


async def test_a_provider_driven_session_still_writes_checkpoints(
    tmp_path: Path,
) -> None:
    """接进来的业务照常落快照 (断点续跑这些既有能力不为业务让路)."""
    toy_dir = write_prompt(tmp_path / "toy", "toy", "你是玩具助手 A.")

    session, _ = await assemble(
        MockLLM.fixed(text_response("好的")),
        ToyProvider(),
        RunContext(thread_id="toy:checkpoint-1"),
        prompt_name="toy",
        prompt_dir=toy_dir,
        question="随便说点什么",
    )

    assert await session.frame_count() == 1


# ---------------------------------------------------------------------------
# 框架不认识业务
# ---------------------------------------------------------------------------


# 扫描时要跳过的目录 (三个都不是框架源码):
# - `tests`: 测试与两个假业务都在那儿, 业务词表正不该扫它
# - `build`: setuptools 的构建中间产物 —— `pip wheel` 会在包目录下留一份自己的
#   副本, 扫它等于把框架源码扫两遍, 且扫到的是**上一次构建**的旧代码 (改了这里却
#   因为旧副本报错, 或反过来漏报, 是最费解的一类失败; 见下面那条元测试)
# - `__pycache__`: 字节码缓存
_SKIPPED_PARTS = frozenset({"tests", "build", "__pycache__"})


def _framework_source_files() -> list[Path]:
    """框架自己的源码文件 (排除测试 / 构建产物 / 字节码缓存)."""
    return sorted(
        path
        for path in FRAMEWORK_ROOT.rglob("*")
        if path.suffix in {".py", ".prompt"} and not _SKIPPED_PARTS & set(path.parts)
    )


def test_the_framework_never_mentions_the_business() -> None:
    """框架的源码与提示词里不出现业务词 —— 「框架不认识业务」这句话的证据.

    为什么值得一条用例: 这条规矩只靠自觉守不住 —— 顺手写一句
    `if business == "商城"` 就能省不少事, 而且当下跑得通. 扫源码是最便宜的
    防线: 假业务都在 tests/ 里, 真业务在 CharApp/, 两边都不该在框架的源码里
    留下名字.
    """
    files = _framework_source_files()
    assert files, "一个框架源码文件都没扫到, 说明路径找错了 (用例本身失效)"

    offenders: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(FRAMEWORK_ROOT)} 里的 {word!r}"
            for word in BUSINESS_WORDS
            if word in text
        )

    assert offenders == [], f"框架源码里出现了业务词: {offenders}"


def test_the_business_scan_covers_the_framework_packages() -> None:
    """扫描范围本身要被钉住 —— 扫漏了比扫出错更危险 (用例会假绿).

    上面那条用例的说服力全在「扫到了该扫的东西」上: 路径写错成空列表、
    或者只扫到一个文件, 它都会静默通过.
    """
    scanned = {
        path.relative_to(FRAMEWORK_ROOT).parts[0] for path in _framework_source_files()
    }

    assert {
        "agent",
        "checkpoint",
        "client",
        "db",
        "hooks",
        "model",
        "prompt",
        "retry",
        "stream",
        "tool",
    } <= scanned, f"扫描漏了这些包: {scanned}"
    assert "tests" not in scanned, "业务词表不该拿去扫测试目录 (假业务就在那儿)"
    assert "build" not in scanned, (
        "构建产物不该进扫描范围: 那里面是上一次构建的旧副本, 扫它等于拿旧代码判新代码"
    )
