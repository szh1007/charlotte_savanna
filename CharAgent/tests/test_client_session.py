"""client 会话装配测试: ChatSession 的三个动作 —— 问一句 / 接着跑 / 看存档.

场景 → 断言:
- 问一句: 拿到 LoopResult, 会话历史收成「用户问 + 模型答」
- 多轮连得上: 第二次提问时, 模型收到的 messages 里带着第一次的答案
- **没跑完时把已完成的工作从快照收回历史**: 失败路径上会话历史 = 快照里的
  完整历史 (含工具结果), 用户那句提问也还在 —— 于是下一句「继续」就是一条
  普通提问, 模型看着历史自己接得上 (口语版的「不重复已完成动作」)
- 收回只做加法: 快照还没长出这一轮时不动历史 (用户刚说的那句话不能丢)
- 没有快照时 resume 返回 None (不报错 —— 那是「新会话」的正常情形)
- **中断续跑不重复已完成动作**: 脚本耗尽模拟「跑到一半被打断」, 第一轮的工具
  已经执行并落进快照; 换一个模型从快照接着跑, 那个工具**不会再跑一次**,
  且轮数 / token 按累计口径接续 (P0 验收第 4 条的核心防线)
- 看存档: 历史表逐帧一行; 不留历史的后端 (Redis latest) 给出可执行提示
- aclose 释放模型与存储 (谁建谁关)

被测对象是装配层, 模型与存储全走替身 (MockLLM + InMemoryCheckpointSaver +
FakeRedisClient), 零网络零外部服务.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from doubles import FakeRedisClient
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.agent import TrimAndSummarize
from CharAgent.checkpoint import InMemoryCheckpointSaver, RedisCheckpointSaver
from CharAgent.client.session import DEMO_TOOLS, ChatSession
from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.model.utils.types import ModelMessage, ModelResponse
from CharAgent.prompt import load_prompt
from CharAgent.stream.utils.types import EventType
from CharAgent.tool import Tool, tool

# 工具调用记录 (数「工具到底跑了几次」—— 续跑用例的关键证据)
ECHO_CALLS: list[str] = []


@tool
def echo(text: str) -> str:
    """回显文本. 用户要求回显一段内容时使用; 返回 echo: 前缀的原文.

    Args:
        text: 要回显的文本.
    """
    ECHO_CALLS.append(text)
    return f"echo:{text}"


ECHO_TOOL: Tool = echo  # @tool 装饰器把函数换成了 Tool 对象


def dialogue(messages: list[ModelMessage]) -> list[ModelMessage]:
    """摘掉开头的身份说明, 只看真正的对话部分.

    会话历史的第一条恒为身份说明 (system 消息, 正文来自 `system.prompt`),
    它由 test_history_starts_with_the_identity_prompt 单独盯着; 别的用例关心的是
    「用户问 / 模型答 / 工具结果」长什么样, 带着那一条读起来全是噪音.
    """
    return [message for message in messages if message.get("role") != "system"]


def make_session(
    model: Any,
    *,
    saver: Any | None = None,
    thread_id: str = "client-test",
    model_name: str | None = None,
    tools: list[Tool] | None = None,
    prompt_name: str = "system",
    prompt_dir: Path | None = None,
    hooks: HookRegistry | None = None,
    event_sink: Any | None = None,
    compactor: Any | None = None,
) -> ChatSession:
    """造一个会话 (存储不指定时给内存版; 工具不指定时给回显工具).

    提示词两项不指定 = 框架自己那份 (`system`) —— 与命令行现在的装配完全一样.
    """
    return ChatSession(
        model,
        saver=saver if saver is not None else InMemoryCheckpointSaver(),
        tools=[ECHO_TOOL] if tools is None else tools,
        thread_id=thread_id,
        model_name=model_name,
        prompt_name=prompt_name,
        prompt_dir=prompt_dir,
        hooks=hooks,
        event_sink=event_sink,
        compactor=compactor,
    )


# ---------------------------------------------------------------------------
# 问一句
# ---------------------------------------------------------------------------


async def test_ask_returns_result_and_records_the_exchange() -> None:
    """问一句: 拿到结果; 会话历史收成「用户问 + 模型答」两条."""
    session = make_session(MockLLM.fixed(text_response("订单已发货")))

    result = await session.ask("订单到哪了")

    assert result.content == "订单已发货"
    chat = dialogue(session.history)
    assert [message["role"] for message in chat] == ["user", "assistant"]
    assert chat[0]["content"] == "订单到哪了"


def test_history_starts_with_the_identity_prompt() -> None:
    """会话历史的第一条是身份说明 (system), 且它就是会话发出去的那条.

    为什么钉这条: 模型对自己是什么模型**没有内省能力** —— 不给说明, 它被问到
    「你的底层模型是什么」时只能从语料里续写一句最像的, 于是经常自称 Claude
    (语料里那些对话被大量转载). 身份必须由会话这边给.
    """
    session = make_session(MockLLM.fixed(text_response("好的")))

    assert session.history[0] == {
        "role": "system",
        "content": load_prompt("system", model_name=session.model_name),
    }
    assert load_prompt("system", model_name="any-model").strip(), "身份说明不能是空的"


async def test_the_model_receives_the_identity_prompt() -> None:
    """这条说明真的发给了模型 (不只是躺在会话历史里)."""
    model = MockLLM.fixed(text_response("我是 CharAgent 的演示助手"))
    session = make_session(model)

    await session.ask("你是什么模型?")

    assert model.calls[0]["messages"][0] == {
        "role": "system",
        "content": load_prompt("system", model_name=session.model_name),
    }


def test_identity_prompt_carries_the_configured_model_name() -> None:
    """身份说明里写的是**实际生效**的模型名 (--model 优先于 .env).

    钉住「prompt 说的 = 实际跑的」: 两处若各读一次配置, 迟早各说各话 —— 而这段
    prompt 唯一的职责就是照实说明身份.
    """
    session = make_session(MockLLM.fixed(text_response("好的")), model_name="cli-model")

    assert session.model_name == "cli-model"
    assert "cli-model" in session.history[0]["content"]


def test_a_business_prompt_replaces_the_framework_one(tmp_path: Path) -> None:
    """业务提示词接管身份说明, 且照样能用 `${model_name}` 占位符.

    两个新参数都不给时走框架那份 (`test_history_starts_with_the_identity_prompt`
    盯着默认行为), 这一条管的是给了之后: 读的是业务目录里的那份, 而实际生效的
    模型名仍然填得进去 —— 业务提示词若要自己声明身份, 不能给它一个空值.
    """
    (tmp_path / "cs.prompt").write_text(
        "你是客服, 跑在 ${model_name} 上.\n", encoding="utf-8"
    )
    session = make_session(
        MockLLM.fixed(text_response("好的")),
        model_name="cli-model",
        prompt_name="cs",
        prompt_dir=tmp_path,
    )

    assert session.history[0]["content"] == "你是客服, 跑在 cli-model 上.\n"


async def test_second_ask_carries_the_first_answer_to_the_model() -> None:
    """多轮连得上: 第二次提问时模型看到的 messages 里带着第一次的答案."""
    model = MockLLM.scripted([text_response("第一次答复"), text_response("第二次答复")])
    session = make_session(model)

    await session.ask("第一个问题")
    await session.ask("第二个问题")

    second_request = model.calls[1]["messages"]
    assert [message["content"] for message in dialogue(second_request)] == [
        "第一个问题",
        "第一次答复",
        "第二个问题",
    ]


async def test_failed_run_keeps_the_question_and_reclaims_the_work() -> None:
    """跑失败 (这里用脚本耗尽模拟): 提问留着, 已完成的工作从快照收回.

    第一轮要求调 echo (工具真跑了, 快照落一帧), 第二轮脚本耗尽抛错 —— 相当于
    「跑到一半被打断」. 失败路径上去快照把进度收回来, 于是历史 = 快照那一帧的
    完整内容, 而那句提问 (快照里本来就有) 也还在.

    这条是「说一句『继续』就能接着跑」的地基: 历史里有用户问过什么、也已有什么
    被做完了 —— 模型下一轮自己就接得上, CLI 不需要认任何关键词.
    """
    ECHO_CALLS.clear()
    session = make_session(
        MockLLM.scripted([tool_call_response(make_tool_call("echo", '{"text": "hi"}'))])
    )

    with pytest.raises(AssertionError):
        await session.ask("帮我回显 hi")

    assert ECHO_CALLS == ["hi"]
    chat = dialogue(session.history)
    assert [message["role"] for message in chat] == ["user", "assistant", "tool"]
    assert chat[0]["content"] == "帮我回显 hi"


async def test_reclaim_only_adds_never_drops_what_the_user_just_said() -> None:
    """收回只做加法: 这一轮还没落过盘时, 用户刚说的那句话不能被清掉.

    脚本是空的 (第 1 轮模型调用就失败), 快照里没有本次的任何内容 —— 此时历史
    短于会话历史, 收回这一步什么都不该做. 若它无条件替换, 用户那句提问就人间
    蒸发了 (他会看到自己刚打的字不见了).
    """
    session = make_session(MockLLM.scripted([]))

    with pytest.raises(AssertionError):
        await session.ask("随便问一句")

    assert [message["content"] for message in dialogue(session.history)] == [
        "随便问一句"
    ]


# ---------------------------------------------------------------------------
# 接着跑 (P0 验收第 4 条)
# ---------------------------------------------------------------------------


async def test_resume_returns_none_when_there_is_no_checkpoint() -> None:
    """没有可恢复的快照时返回 None —— 那是「新会话」的正常情形, 不是错误."""
    session = make_session(MockLLM.fixed(text_response("你好")))

    assert await session.resume() is None


async def test_resume_after_interrupt_does_not_rerun_completed_tools() -> None:
    """中断后续跑: 已执行过的工具不再跑第二次, 轮数按累计口径接续.

    第一段只有一条脚本 —— 模型第 1 轮要求调 echo (工具真的执行了, 快照落一帧),
    第 2 轮脚本耗尽抛错, 相当于「跑到一半被打断」. 第二段换一个模型从同一份
    存储的同一会话续跑: 起点是那份快照, 而工具结果早已躺在历史里, 于是
    echo 不会被重跑 —— 这就是「恢复而非重跑」.
    """
    ECHO_CALLS.clear()
    saver = InMemoryCheckpointSaver()
    first = make_session(
        MockLLM.scripted(
            [tool_call_response(make_tool_call("echo", '{"text": "hi"}'))]
        ),
        saver=saver,
        thread_id="client-resume",
    )

    with pytest.raises(AssertionError):
        await first.ask("帮我回显 hi")

    assert ECHO_CALLS == ["hi"]  # 第一段: 工具跑了一次
    assert await first.frame_count() == 1  # 那一次跑完落了一帧快照

    second = make_session(
        MockLLM.fixed(text_response("回显完成")),
        saver=saver,
        thread_id="client-resume",
    )
    result = await second.resume()

    assert result is not None
    assert ECHO_CALLS == ["hi"], "续跑把已完成的那次工具调用重跑了"
    assert result.content == "回显完成"
    # 轮数接续: 快照里已有 1 轮, 本次又跑了 1 轮 —— 但 turns 只含本次那一轮
    assert result.turn_count == 2
    assert len(result.turns) == 1
    # 续跑后本会话在内存里也是接着的 (下一句提问能看到整段历史)
    assert any(message.get("role") == "tool" for message in second.history)


async def test_resume_sends_the_checkpoint_history_to_the_model() -> None:
    """续跑时模型看到的起点是快照里的历史 (含工具结果), 不是空历史."""
    saver = InMemoryCheckpointSaver()
    seed = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("echo", '{"text": "hi"}')),
            text_response("第一段结尾"),
        ]
    )
    first = make_session(seed, saver=saver, thread_id="client-seed")
    await first.ask("帮我回显 hi")

    model = MockLLM.fixed(text_response("接着答"))
    second = make_session(model, saver=saver, thread_id="client-seed")
    await second.resume()

    sent: list[ModelMessage] = model.calls[0]["messages"]
    assert dialogue(sent)[0] == {"role": "user", "content": "帮我回显 hi"}
    assert any(message.get("role") == "tool" for message in sent)
    assert sent[-1]["content"] == "第一段结尾"


# ---------------------------------------------------------------------------
# 看存档
# ---------------------------------------------------------------------------


async def test_frame_count_counts_every_turn_frame() -> None:
    """每 Turn 落一帧: 两轮工具问答过后, 本会话有 2 帧."""
    session = make_session(
        MockLLM.scripted(
            [
                tool_call_response(make_tool_call("echo", '{"text": "a"}')),
                text_response("done"),
            ]
        )
    )

    await session.ask("回显 a")

    assert await session.frame_count() == 2


async def test_history_table_renders_one_row_per_frame() -> None:
    """历史表: 逐帧一行, 带上那一轮调过的工具 (回放调试视图)."""
    session = make_session(
        MockLLM.scripted(
            [
                tool_call_response(make_tool_call("echo", '{"text": "a"}')),
                text_response("done"),
            ]
        )
    )
    await session.ask("回显 a")

    table = await session.history_table()

    assert "轮次" in table and "工具" in table
    assert table.count("\n") == 3  # 表头 + 分隔线 + 2 帧
    assert "echo" in table


async def test_history_table_explains_a_backend_without_history() -> None:
    """不留历史的后端 (Redis latest): 给出可执行的换法, 不是一句报错."""
    saver = RedisCheckpointSaver(client=FakeRedisClient(), mode="latest")
    session = make_session(
        MockLLM.fixed(text_response("你好")), saver=saver, thread_id="client-redis"
    )

    assert await session.frame_count() is None
    table = await session.history_table()
    assert "不留历史" in table
    assert "--backend postgres" in table


async def test_saver_name_and_capabilities_describe_the_backend() -> None:
    """启动横幅要的三项: 存储类名 + 有没有历史 + 会不会过期."""
    saver = InMemoryCheckpointSaver()
    session = make_session(MockLLM.fixed(text_response("你好")), saver=saver)

    assert session.saver_name == "InMemoryCheckpointSaver"
    assert session.saver_capabilities.history is True
    assert session.saver_capabilities.ttl is False


async def test_demo_tools_are_the_five_without_the_manual_twin() -> None:
    """CLI 默认开放五个工具 (manual 引擎的同能力对照件不注册, 免得模型随机挑)."""
    names = [item.name for item in DEMO_TOOLS]

    assert names == [
        "get_current_time",
        "convert_length",
        "batch_convert_lengths",
        "count_text_stats",
        "query_order_status",
    ]


# ---------------------------------------------------------------------------
# 扩展点: 业务挂插件走会话这一条路
# ---------------------------------------------------------------------------


async def test_hooks_reach_the_loop_through_the_session() -> None:
    """注册表透传到 loop: 挂在会话上的插件真的会被叫到.

    这条是业务侧护栏的**唯一入口** —— 不给这个参数, 业务要挂插件就只能自己
    建 `AgentLoop`, 而会话里那几件事 (提示词 / 快照 / 历史) 就得各做一遍.
    """
    turns: list[int] = []

    def record(*, turn: int, **kwargs: Any) -> None:
        turns.append(turn)

    registry = HookRegistry()
    registry.register(HookPoint.BEFORE_TURN, record)
    session = make_session(MockLLM.fixed(text_response("好的")), hooks=registry)

    await session.ask("随便问一句")

    assert turns == [1]


async def test_a_plugin_can_stop_a_tool_at_the_session_level() -> None:
    """拦截插件在**会话**这一层就管用: 说「不许」, 工具一次都不跑.

    比「hook 被叫到了」更进一步 —— 断言的是返回值那半条链路 (loop 里 decide 的
    接线) 也接上了. 时机的活: 会话构造时把注册表带进去, 运行期改注册表不影响
    已经在跑的 loop.
    """
    ECHO_CALLS.clear()
    registry = HookRegistry()
    registry.register(
        HookPoint.BEFORE_TOOL_EXECUTE,
        lambda **kwargs: Decision.reject("这个工具这次不许跑"),
    )
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("echo", '{"text": "hi"}')),
            text_response("那我不回显了"),
        ]
    )
    session = make_session(model, hooks=registry)

    result = await session.ask("帮我回显 hi")

    assert ECHO_CALLS == [], "被拒的工具一次都不该跑"
    assert result.content == "那我不回显了", "模型收到拒绝原因后继续作答"


# ---------------------------------------------------------------------------
# 资源释放
# ---------------------------------------------------------------------------


async def test_aclose_releases_the_model() -> None:
    """谁建谁关: aclose 关掉模型连接池 (会话自己建的那个), 不漏连接.

    存储那边同理 (session.aclose 也调 saver.aclose), 但它「关谁」由存储自己定
    —— checkpoint 的规矩是**谁建谁关**: 自己建的客户端自己关, 注入进来的不碰
    (那是注入方的东西). 这条规矩的用例在 test_checkpoint_redis.py, 这里只验
    会话确实把这一步接上了.
    """
    closed: list[bool] = []

    class RecordingModel:
        """只记「有没有被关过」的模型替身 (协议是薄的, 形状对就行)."""

        async def generate(
            self, messages: list[ModelMessage], tools: Any = None, **kwargs: Any
        ) -> ModelResponse:
            """协议实现 (形状对就行): 恒返回一条正常终止的响应."""
            return text_response("ok")

        async def aclose(self) -> None:
            """记下「被关过」这件事 (断言会话确实做了收尾)."""
            closed.append(True)

    session = make_session(RecordingModel())

    await session.aclose()

    assert closed == [True]


# ---------------------------------------------------------------------------
# 上下文压缩接缝 (#7)

# ---------------------------------------------------------------------------
# 上下文压缩接缝 (#7)
# ---------------------------------------------------------------------------


def _compacting_session(
    model: Any, *, summarizer: Any | None = None, event_sink: Any | None = None
) -> ChatSession:
    """一个配了压缩策略的会话: 阈值调到 1, 于是「有得裁」就会压."""
    return make_session(
        model,
        event_sink=event_sink,
        compactor=TrimAndSummarize(
            threshold_tokens=1,
            keep_recent_questions=1,
            watermark_ratio=0.5,
            tool_result_limit=20,
            summary_max_tokens=64,
            summarizer=summarizer,
        ),
    )


async def test_compaction_is_wired_through_the_session() -> None:
    """会话把压缩策略原样转交给 loop: 配了就压, 事件也经会话的出口透出去.

    第一句没有可裁的东西 (只有一段对话), 第二句才裁得动 —— 于是恰好一条压缩事件.
    """
    events: list[Any] = []
    session = _compacting_session(
        MockLLM.scripted([text_response("第一次答"), text_response("第二次答")]),
        summarizer=MockLLM.scripted([text_response("早前聊的是查订单")]),
        event_sink=events.append,
    )

    await session.ask("第一次提问")
    second = await session.ask("第二次提问")

    assert second.summary == "早前聊的是查订单"
    assert [event.type for event in events].count(EventType.CONTEXT_COMPACTED) == 1


async def test_the_summary_survives_from_one_question_to_the_next() -> None:
    """摘要跟着**会话**走: 第二句问话复用第一句压出来的那份, 不从零再压.

    会话对象横跨很多次提问 (每句一次 run), 所以压缩进度不能只活在单次 run 里 ——
    否则每句问话都要把同一段旧历史重压一遍 (内容不会错, 白花一次摘要调用).
    """
    seen: list[list[ModelMessage]] = []
    summarizer = MockLLM.scripted([text_response("早前聊的是查订单")])

    async def capture(messages: list[ModelMessage]) -> ModelResponse:
        seen.append(list(messages))
        return text_response("答完了")

    session = _compacting_session(
        MockLLM.scripted([capture, capture]), summarizer=summarizer
    )

    await session.ask("第一次提问")
    second = await session.ask("第二次提问")

    assert len(summarizer.calls) == 1  # 只压过一次
    assert second.summary == "早前聊的是查订单"
    # 第二句问话的视图里带着上一条摘要 (会话把它递回给了 loop)
    assert "早前聊的是查订单" in str(seen[-1][1].get("content"))
