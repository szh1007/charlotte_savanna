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
- **续跑的记账** (ticket 33 / issue 34): 两种「接着跑」各记在哪一行 —— 命令行
  `--resume` (不传 `run_id`) 开一行新账并收尾它; 传了 `run_id` 的续段沿用那一行,
  并把**这一段的结局**写回去 (挂起那一段写的是 waiting_user, 不是终态, 见 issue 34);
  两种情形落的帧都指着自己那一行 (从前续跑落的帧是孤儿)
- **HITL 的挂起与恢复** (issue 34): 一次要人批的调用让这一段停在 SUSPENDED (那条
  调用一次都没跑), 带上人的结论再续才真的执行
- 续跑不是提问触发的: 失败时只记一句「这一轮没答完」, 不编一句用户提问出来
- 看存档: 历史表逐帧一行; 不留历史的后端 (Redis latest) 给出可执行提示
- aclose 释放模型与存储 (谁建谁关)

被测对象是装配层, 模型与存储全走替身 (MockLLM + InMemoryCheckpointSaver +
FakeRedisClient), 零网络零外部服务.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from doubles import FakeRedisClient
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.agent import Approval, LoopOutcome, TrimAndSummarize
from CharAgent.checkpoint import (
    Checkpoint,
    CheckpointState,
    InMemoryCheckpointSaver,
    RedisCheckpointSaver,
)
from CharAgent.client.session import DEMO_TOOLS, ChatSession
from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.model.utils.types import ModelMessage, ModelResponse
from CharAgent.prompt import PromptError, load_prompt, prompt_ref
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
    hydrate: bool = True,
    recorder: Any | None = None,
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
        hydrate=hydrate,
        recorder=recorder,
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
# 续跑的记账 (ticket 33): 两种「接着跑」各记在哪一行
# ---------------------------------------------------------------------------


class ResumeRecorder:
    """记下续跑那一段交给记录员的东西 (本节三个用例共用).

    记的正好是「记在哪一行」的全部答案: `begin` 调没调 (新不新开账) · 收尾收到哪
    一行 + `finish_run` (结不结账) · `since` 是多少 (记录层按它算消息编号, 必须与
    运行中途落库那一拍同一个下标).

    为什么 `unfinished` 不记 `since`: 那一条路上没有「用户那句话」要认回来
    (提问是 None), 下标用不上.
    """

    def __init__(self) -> None:
        self.begins: list[str] = []
        self.settled: list[tuple[str | None, int, bool]] = []
        self.unfinished: list[tuple[str | None, str | None, bool]] = []

    async def begin(self, *, thread_id: str, title: str = "") -> str | None:
        """开账那一拍: 记下标题候选, 交一个固定编号."""
        self.begins.append(title)
        return "run-new"

    async def record(
        self,
        *,
        thread_id: str,
        result: Any,
        run_id: str | None = None,
        since: int = 0,
        summary: str | None = None,
        model: str | None = None,
        finish_run: bool = True,
    ) -> bool:
        self.settled.append((run_id, since, finish_run))
        return True

    async def record_unfinished(
        self,
        *,
        thread_id: str,
        question: str | None = None,
        status: Any,
        run_id: str | None = None,
        since: int = 0,
        model: str | None = None,
        finish_run: bool = True,
    ) -> bool:
        self.unfinished.append((question, run_id, finish_run))
        return True


async def test_a_cli_resume_opens_its_own_run_and_records_the_segment() -> None:
    """命令行 `--resume` (不传 `run_id`): 这一段**自己开一行新账**, 并收尾它.

    三样一起断: `begin` 调了 (新行) · 收尾 `finish_run=True` (这一行归这一段收) ·
    `since` 就是交给 loop 的那份历史的长度 —— 运行中途落库那一拍 (`flush`) 用的是
    同一个下标, 两边算出来的编号必须对得上, 否则收尾会把已经写过的行再写一遍.
    """
    saver = InMemoryCheckpointSaver()
    first = make_session(
        MockLLM.fixed(text_response("第一段")), saver=saver, thread_id="rec-cli"
    )
    await first.ask("第一问")

    recorder = ResumeRecorder()
    model = MockLLM.fixed(text_response("第二段"))
    second = make_session(model, saver=saver, thread_id="rec-cli", recorder=recorder)

    await second.resume()

    assert recorder.begins == [""], "续跑不是提问触发的: 标题候选为空"
    assert recorder.settled == [("run-new", len(model.calls[0]["messages"]), True)]
    frames = await second.history_frames()
    assert frames is not None and frames[-1].run_id == "run-new", (
        "这一段落的帧指着新开的那一行 —— 从前它们是 None (记录层里的孤儿)"
    )


async def test_a_continuation_resume_keeps_the_run_and_settles_it() -> None:
    """传了 `run_id` (HITL 的第二段): 不新开账, 但**跑到结局就写上去**.

    两半都要断: 那一行**不新建** (它早就存在 —— 这一次运行横跨挂起等待期), 而
    这一段的结局照常写回去 (跑完了就是终态, 又挂起一次就是 `waiting_user`).

    ticket 33 在这里写的是「一笔都不碰」, issue 34 改判: 那一行要写的**不是**「这
    一次运行结没结」, 而是「这一段跑成什么样」—— 挂起那一段自己也要写 (写的是
    `waiting_user`, 不是终态), 否则「它现在在等人」这件事在库里没有落点.
    """
    saver = InMemoryCheckpointSaver()
    first = make_session(
        MockLLM.fixed(text_response("第一段")), saver=saver, thread_id="rec-hitl"
    )
    await first.ask("第一问")

    recorder = ResumeRecorder()
    model = MockLLM.fixed(text_response("第二段"))
    second = make_session(model, saver=saver, thread_id="rec-hitl", recorder=recorder)

    await second.resume(run_id="run-open")

    assert recorder.begins == [], "第二段不新开账: 那一行早就存在"
    assert recorder.settled == [("run-open", len(model.calls[0]["messages"]), True)]
    frames = await second.history_frames()
    assert frames is not None and frames[-1].run_id == "run-open", (
        "这一段落的帧指回**同一次运行**那一行 (不另起一行)"
    )


class OutcomeRecorder:
    """只记「每一段交给记录员的结局与行号」的记录员替身 (HITL 两段用).

    与 `ResumeRecorder` 的分工: 那个看「记在哪一行」(begin 调没调 / finish_run 是
    什么), 这个看「那一段跑成了什么」—— 挂起与恢复的区别正好只在这一处.
    """

    def __init__(self, run_id: str = "run-hitl") -> None:
        self._run_id = run_id
        self.outcomes: list[Any] = []
        self.runs: list[str | None] = []

    async def begin(self, *, thread_id: str, title: str = "") -> str | None:
        """开账那一拍: 交一个固定编号 (第一段自己开的那一行)."""
        return self._run_id

    async def record(
        self,
        *,
        thread_id: str,
        result: Any,
        run_id: str | None = None,
        since: int = 0,
        summary: str | None = None,
        model: str | None = None,
        finish_run: bool = True,
    ) -> bool:
        """收尾那一拍: 记下结局与行号."""
        self.outcomes.append(result.outcome)
        self.runs.append(run_id)
        return True

    async def record_unfinished(self, **kwargs: Any) -> bool:
        """没跑完那一拍 (本用例不涉及): 什么都不记."""
        return True


async def test_a_suspension_stops_the_segment_and_the_decision_completes_it() -> None:
    """会话这一层的 HITL 两段: 挂起停在半路, 带上人的结论才把那条调用做掉.

    这是 issue 34 在**装配层**的面 (loop 那一侧由 `test_loop_suspension.py` 守,
    真库那一侧由 `test_client_resume_db.py` 守, 那一份才断言 `runs` 行落成
    `waiting_user`): 第一段交出的结局是 SUSPENDED, 而那条要人批的调用**一次都没
    跑**; 第二段带着结论续, 它才真的执行, 而这次运行也在第二段收了尾.
    """
    ECHO_CALLS.clear()
    saver = InMemoryCheckpointSaver()
    hooks = HookRegistry()
    hooks.register(
        HookPoint.BEFORE_TOOL_EXECUTE,
        lambda **kw: Decision.requires_approval(
            "要输支付密码", needs=("payment_password",)
        ),
    )
    recorder = OutcomeRecorder()
    gated = MockLLM.scripted(
        [tool_call_response(make_tool_call("echo", '{"text": "hi"}'))]
    )
    first = make_session(
        gated, saver=saver, thread_id="client-hitl", hooks=hooks, recorder=recorder
    )

    suspended = await first.ask("帮我付了这单")

    assert suspended.outcome is LoopOutcome.SUSPENDED
    assert ECHO_CALLS == [], "挂起时那条调用一次都没跑"
    assert recorder.outcomes == [LoopOutcome.SUSPENDED], "第一段自己开的那一行"
    assert recorder.runs == ["run-hitl"]

    # 第二段: 人批了, 同一次运行接着跑 (传回第一段那一行 + 结论)
    second = make_session(
        MockLLM.fixed(text_response("付好了")),
        saver=saver,
        thread_id="client-hitl",
        recorder=recorder,
    )
    result = await second.resume(run_id="run-hitl", approval=Approval.approve())

    assert result is not None and result.outcome is LoopOutcome.FINISHED
    assert ECHO_CALLS == ["hi"], "批了之后那条调用才真的执行"
    assert recorder.outcomes[-1] is LoopOutcome.FINISHED
    assert recorder.runs[-1] == "run-hitl", "第二段记回同一行 (不新开账)"


async def test_an_interrupted_resume_records_no_question() -> None:
    """续跑被打断: 记一句「这一轮没答完」, 但**不编**一句用户提问出来.

    这一段本来就不是提问触发的 —— 编一条 `user` 行会让记录撒谎 (「只有真由用户
    输入产生的消息才是 user」是这一层立着的硬规矩). 新开的那一行照样收尾 (它是
    这一段自己建的); 传进来的那一行不动.
    """
    saver = InMemoryCheckpointSaver()
    first = make_session(
        MockLLM.fixed(text_response("第一段")), saver=saver, thread_id="rec-broken"
    )
    await first.ask("第一问")

    recorder = ResumeRecorder()
    # 脚本为空 = 段落一开口就失败 (与真机上的模型报错同一条路)
    broken = MockLLM.scripted([])
    for run_id in (None, "run-open"):
        session = make_session(
            broken, saver=saver, thread_id="rec-broken", recorder=recorder
        )
        with pytest.raises(AssertionError):
            await session.resume(run_id=run_id)

    assert recorder.unfinished == [
        # 自己开的那一行归自己收尾; 传进来的那一行留给收尾的那一段
        (None, "run-new", True),
        (None, "run-open", False),
    ]


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
    model: Any,
    *,
    summarizer: Any | None = None,
    event_sink: Any | None = None,
    recorder: Any | None = None,
) -> ChatSession:
    """一个配了压缩策略的会话: 阈值调到 1, 于是「有得裁」就会压."""
    return make_session(
        model,
        event_sink=event_sink,
        recorder=recorder,
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

    # 首轮要够长: 压完视图里会多出摘要那一条 (前缀本身就有二三十字), 被裁的那段
    # 省不过它就等于「压了反而更大」—— 那属于另一回事, 与这条验的接线无关
    await session.ask("第一次提问" + "。" * 60)
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

    await session.ask("第一次提问" + "。" * 60)  # 够长才压得动, 见上一条用例
    second = await session.ask("第二次提问")

    assert len(summarizer.calls) == 1  # 只压过一次
    assert second.summary == "早前聊的是查订单"
    # 第二句问话的视图里带着上一条摘要 (会话把它递回给了 loop)
    assert "早前聊的是查订单" in str(seen[-1][1].get("content"))


# ---------------------------------------------------------------------------
# 水合: 重启之后还认得上一段对话 (ticket 17)
# ---------------------------------------------------------------------------


def requests(model: Any) -> list[list[ModelMessage]]:
    """每次调用模型时它看到的消息 (逐次请求, 来自 MockLLM 的轨迹记录)."""
    return [record["messages"] for record in model.calls]


def contents(messages: list[ModelMessage]) -> list[str]:
    """一份消息列表里的正文 (断言「模型看到了哪几句话」用)."""
    return [str(message.get("content")) for message in messages]


async def test_a_restarted_session_picks_up_the_previous_conversation() -> None:
    """换了进程 (新建会话对象) 之后, 第一次提问就把上一段历史读回来.

    这正是 L2.5 那条验收的机制: 光把快照换成 Postgres 不够 —— 还得有人在第一次
    提问前**去读它**, 否则模型看不到上一段进程里聊过什么.
    """
    saver = InMemoryCheckpointSaver()
    first = make_session(
        MockLLM.fixed(text_response("订单已发货")), saver=saver, thread_id="restart-1"
    )
    await first.ask("订单到哪了")

    # 新进程: 同一个 saver、同一个会话编号, 但会话对象是全新的 (内存历史为空)
    model = MockLLM.fixed(text_response("预计明天到"))
    restarted = make_session(model, saver=saver, thread_id="restart-1")
    await restarted.ask("那什么时候能到")

    seen = contents(requests(model)[0])
    assert "订单到哪了" in seen, "上一段对话的问题要读回来"
    assert "订单已发货" in seen, "上一段的答复也要读回来"
    assert seen[-1] == "那什么时候能到", "新问的那句接在最后"


async def test_hydration_can_be_turned_off() -> None:
    """`hydrate=False`: 每段新会话从零开始 (不认旧账).

    想彻底开一段全新对话, 换个 thread_id 更直白; 这个开关留给「同一编号、不要
    旧上下文」的调试场景.
    """
    saver = InMemoryCheckpointSaver()
    first = make_session(
        MockLLM.fixed(text_response("订单已发货")), saver=saver, thread_id="restart-2"
    )
    await first.ask("订单到哪了")

    model = MockLLM.fixed(text_response("你好"))
    fresh = make_session(model, saver=saver, thread_id="restart-2", hydrate=False)
    await fresh.ask("那什么时候能到")

    seen = contents(requests(model)[0])
    assert "订单到哪了" not in seen
    assert seen[-1] == "那什么时候能到"


async def test_hydration_seals_a_tool_call_that_never_got_its_result() -> None:
    """快照停在「工具调用还没有结果」的半路: 补一条「结果未知」, **不重放**那个工具.

    场景是真实会发生的 (进程死在工具执行中间 / 人工审批挂起点): 那种历史直接喂给
    模型既不合 wire 规矩 (tool_calls 与 tool 消息必须配对), 也会把模型推向重发 ——
    而重发写操作就是重复下单.
    """
    saver = InMemoryCheckpointSaver()
    thread_id = "restart-pending"
    await saver.save(
        Checkpoint.create(
            thread_id=thread_id,
            loop_id="loop-1",
            turn_number=1,
            state=CheckpointState(
                messages=[
                    {"role": "system", "content": "你是助手."},
                    {"role": "user", "content": "回显 hello"},
                    {
                        "role": "assistant",
                        "content": "我调用一下",
                        "tool_calls": [
                            {
                                "id": "call_0",
                                "type": "function",
                                "function": {
                                    "name": "echo",
                                    "arguments": '{"text": "hello"}',
                                },
                            }
                        ],
                    },
                ]
            ),
        )
    )
    ECHO_CALLS.clear()
    model = MockLLM.fixed(text_response("这一轮结束了"))
    session = make_session(model, saver=saver, thread_id=thread_id)

    await session.ask("继续")

    # 那条欠着的调用有了一条结果 (正文说「未知」, 不说「没执行」—— 说后者是撒谎)
    filled = [
        message
        for message in dialogue(session.history)
        if message.get("role") == "tool"
    ]
    assert [message["tool_call_id"] for message in filled] == ["call_0"]
    assert "结果未知" in str(filled[0]["content"])
    # 而工具**一次都没跑**: 服务重启不该自动重放写操作
    assert ECHO_CALLS == []
    assert "回显 hello" in contents(requests(model)[0])


async def test_hydration_only_happens_on_the_first_question() -> None:
    """水合只认第一次提问: 第二句问话不会再读一遍快照 (否则历史会长出重复)."""
    model = MockLLM.fixed(text_response("答好了"))
    session = make_session(model, thread_id="restart-3")

    await session.ask("第一问")
    await session.ask("第二问")

    second = contents(requests(model)[1])
    assert second.count("第一问") == 1
    assert second.count("答好了") == 1


async def test_a_hydrated_session_hangs_its_new_frames_on_the_old_chain() -> None:
    """水合之后落的帧接着**原来那棵树**长 (不是又起一条新根).

    不接着写的话, 同一段对话在快照存储里会变成好几条互不相干的线 —— 内容不丢,
    但「这段对话的快照」看起来就是散的 (`history_table` 里一眼可见).
    """
    saver = InMemoryCheckpointSaver()
    thread_id = "restart-chain"
    first = make_session(
        MockLLM.fixed(text_response("第一次答")), saver=saver, thread_id=thread_id
    )
    await first.ask("第一问")
    frames_before = await saver.list_history(thread_id)
    last_of_first = frames_before[-1]

    restarted = make_session(
        MockLLM.fixed(text_response("第二次答")), saver=saver, thread_id=thread_id
    )
    await restarted.ask("第二问")

    frames = await saver.list_history(thread_id)
    added = frames[len(frames_before) :]
    assert added, "第二段提问应该落了新帧"
    assert added[0].parent_id == last_of_first.checkpoint_id, (
        "新帧挂在水合读到的那一帧下面, 而不是当新根"
    )


async def test_the_session_hands_every_run_to_the_recorder() -> None:
    """记录: 跑完把这一轮交给记录员, 并告诉它「新增从第几条开始」.

    为什么 `since` 要算准: 记录员按它切出**本次新增**的那一段 (老的那一段上一次
    已经写过了), 算错就会写出重复行或漏行.
    """
    calls: list[tuple[str, int, str | None]] = []

    class Recorder:
        """记下每次被调用的形状 (与 db/recorder.py 的协议同款)."""

        async def begin(self, *, thread_id: str, title: str = "") -> str | None:
            """开账那一拍 (ticket 22): 交一个固定编号.

            不记进 `calls` —— 各用例断的是收尾那一拍.
            """
            return "run-1"

        async def record(
            self,
            *,
            thread_id: str,
            result: Any,
            run_id: str | None = None,
            since: int = 0,
            summary: str | None = None,
            model: str | None = None,
            finish_run: bool = True,
        ) -> bool:
            calls.append((thread_id, since, result.content))
            return True

        async def record_unfinished(
            self,
            *,
            thread_id: str,
            question: str,
            status: Any,
            run_id: str | None = None,
            since: int = 0,
            model: str | None = None,
            finish_run: bool = True,
        ) -> bool:
            calls.append((thread_id, -1, question))
            return True

    session = make_session(
        MockLLM.fixed(text_response("答好了")), thread_id="rec-1", recorder=Recorder()
    )

    await session.ask("第一问")
    await session.ask("第二问")

    assert calls == [("rec-1", 1, "答好了"), ("rec-1", 3, "答好了")], (
        "第 0 条是身份说明, 所以第一问的下标是 1; 第二问接在「问+答」后面, 下标是 3"
    )


async def test_the_session_tells_the_recorder_which_model_ran() -> None:
    """记录: 会话把**实际生效**的模型名交给记录员 (#40 版本归因).

    为什么由会话交: `LoopResult` 里没有模型名 —— loop 手上是个薄协议的模型对象
    (`ChatModel` 上就没有「我叫什么」这一项), 名字只有装配处知道. 与身份说明同
    一条规矩: 记录里写的必须是**真跑的那个** (--model 优先于 .env).
    """
    seen: list[str | None] = []

    class Recorder:
        """只关心 model 那一个参数的记录员."""

        async def begin(self, *, thread_id: str, title: str = "") -> str | None:
            """开账那一拍 (ticket 22): 交一个固定编号.

            不记进 `calls` —— 各用例断的是收尾那一拍.
            """
            return "run-1"

        async def record(
            self,
            *,
            thread_id: str,
            result: Any,
            run_id: str | None = None,
            since: int = 0,
            summary: str | None = None,
            model: str | None = None,
            finish_run: bool = True,
        ) -> bool:
            seen.append(model)
            return True

        async def record_unfinished(
            self,
            *,
            thread_id: str,
            question: str,
            status: Any,
            run_id: str | None = None,
            since: int = 0,
            model: str | None = None,
            finish_run: bool = True,
        ) -> bool:
            seen.append(model)
            return True

    session = make_session(
        MockLLM.fixed(text_response("答好了")),
        thread_id="rec-model",
        model_name="cli-model",
        recorder=Recorder(),
    )

    await session.ask("第一问")

    assert seen == ["cli-model"], "交的必须与会话实际生效的那个名字一致"


async def test_an_interrupted_run_is_recorded_as_unfinished() -> None:
    """取消 / 失败那一轮也要记: 用户确实说过那句话 (记录里不该少一轮)."""
    calls: list[tuple[str, Any]] = []

    class Recorder:
        """只关心「没答完」那条路 (record 这一轮走不到)."""

        async def begin(self, *, thread_id: str, title: str = "") -> str | None:
            """开账那一拍 (ticket 22): 交一个固定编号.

            不记进 `calls` —— 各用例断的是收尾那一拍.
            """
            return "run-1"

        async def record(
            self,
            *,
            thread_id: str,
            result: Any,
            run_id: str | None = None,
            since: int = 0,
            summary: str | None = None,
            model: str | None = None,
            finish_run: bool = True,
        ) -> bool:
            calls.append(("finished", result.outcome))
            return True

        async def record_unfinished(
            self,
            *,
            thread_id: str,
            question: str,
            status: Any,
            run_id: str | None = None,
            since: int = 0,
            model: str | None = None,
            finish_run: bool = True,
        ) -> bool:
            calls.append((question, status))
            return True

    class Exploding:
        """一调用就炸的模型 (模拟上游挂了)."""

        async def generate(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("上游挂了")

        async def aclose(self) -> None:
            """协议要求 (假模型没有资源)."""

    session = make_session(Exploding(), thread_id="rec-2", recorder=Recorder())

    with pytest.raises(RuntimeError):
        await session.ask("这一句会失败")

    assert calls, "失败那一轮也要记一笔"
    assert calls[0][0] == "这一句会失败"
    assert str(calls[0][1]) == "failed", "失败那一轮的状态应当翻成 failed"


async def test_only_a_fresh_summary_is_handed_to_the_recorder() -> None:
    """压缩出来的摘要交给记录员, 但**只在它是新的时候** (否则每轮写一条重复行).

    判「新不新」只有会话判得了: 比较基准是它手上那份上一轮的摘要, 而记录员拿不到
    那个基准 (它只看见这一轮的结果). 阈值调到 1 的会话每问一句都会压一遍, 于是
    第三次提问正好验「压出来的与手上那份一样 -> 不算新的」.
    """
    summaries: list[str | None] = []

    class Recorder:
        """只关心 summary 那一个参数的记录员."""

        async def begin(self, *, thread_id: str, title: str = "") -> str | None:
            """开账那一拍 (ticket 22): 交一个固定编号.

            不记进 `calls` —— 各用例断的是收尾那一拍.
            """
            return "run-1"

        async def record(
            self,
            *,
            thread_id: str,
            result: Any,
            run_id: str | None = None,
            since: int = 0,
            summary: str | None = None,
            model: str | None = None,
            finish_run: bool = True,
        ) -> bool:
            summaries.append(summary)
            return True

        async def record_unfinished(
            self,
            *,
            thread_id: str,
            question: str,
            status: Any,
            run_id: str | None = None,
            since: int = 0,
            model: str | None = None,
            finish_run: bool = True,
        ) -> bool:
            return True

    session = _compacting_session(
        MockLLM.scripted(
            [
                text_response("第一次答"),
                text_response("第二次答"),
                text_response("第三次答"),
            ]
        ),
        # 两次压缩给同一段文本
        summarizer=MockLLM.scripted(
            [text_response("早前聊的是查订单"), text_response("早前聊的是查订单")]
        ),
        recorder=Recorder(),
    )

    await session.ask("第一次提问" + "。" * 60)  # 还没裁得动 -> 没有新摘要
    await session.ask("第二次提问")  # 这一轮压出了摘要
    await session.ask("第三次提问")  # 压出来的与手上那份一样

    assert summaries == [None, "早前聊的是查订单", None]


# ---------------------------------------------------------------------------
# 帧 v5: 身份说明只存引用, 正文由会话按引用补回来
# ---------------------------------------------------------------------------


async def test_frames_store_a_reference_instead_of_the_identity_text(
    tmp_path: Path,
) -> None:
    """落盘的帧里**没有**那几千字正文, 只有一个引用 (名字 + 渲染后正文的哈希)."""
    (tmp_path / "cs.prompt").write_text("你是客服 A.", encoding="utf-8")
    saver = InMemoryCheckpointSaver()
    session = make_session(
        MockLLM.fixed(text_response("好的")),
        saver=saver,
        prompt_name="cs",
        prompt_dir=tmp_path,
    )

    await session.ask("在吗")

    frames = await saver.list_history(session.thread_id)
    state = frames[-1].state
    assert state.prompt_ref == prompt_ref("cs", "你是客服 A.")
    roles = [message.get("role") for message in state.messages]
    assert "system" not in roles, "帧里不该再有那条身份说明"
    # 会话自己那份历史里当然还在 —— 请求照旧带着它发出去
    assert session.history[0] == {"role": "system", "content": "你是客服 A."}


async def test_a_restarted_session_gets_the_identity_back(tmp_path: Path) -> None:
    """重启之后: 新会话从帧里读回历史, 身份说明按引用补回来, 模型仍看得到它.

    这一条盯的是「剥离」的另一半 —— 少了它, 重启后的第一次提问就是一次**没有身份**
    的对话: 不报错, 只是模型答得不像那个人.
    """
    (tmp_path / "cs.prompt").write_text("你是客服 A.", encoding="utf-8")
    saver = InMemoryCheckpointSaver()
    thread_id = "restart-thread"
    first = make_session(
        MockLLM.fixed(text_response("好的")),
        saver=saver,
        thread_id=thread_id,
        prompt_name="cs",
        prompt_dir=tmp_path,
    )
    await first.ask("在吗")

    model = MockLLM.fixed(text_response("还在的"))
    reborn = make_session(
        model,
        saver=saver,
        thread_id=thread_id,
        prompt_name="cs",
        prompt_dir=tmp_path,
    )
    await reborn.ask("接着说")

    # 补回来的身份说明 + 上一段的历史, 真的发给了模型
    sent = requests(model)[0]
    assert sent[0] == {"role": "system", "content": "你是客服 A."}
    assert contents(sent)[1:3] == ["在吗", "好的"], "上一轮的问与答都读回来了"
    # 新落的帧照样只存引用 (这一段历史用的还是同一份提示词)
    frames = await saver.list_history(thread_id)
    assert frames[-1].state.prompt_ref == prompt_ref("cs", "你是客服 A.")


async def test_resume_restores_the_identity_before_handing_it_to_the_loop(
    tmp_path: Path,
) -> None:
    """`resume()` 那条路同样要补: loop 拿到的历史必须带身份说明.

    不补的话 loop 会当场拒绝 (它有护栏) —— 但会话层本就该在这一步补好, 于是这里
    断言「补好了, 没抛」.
    """
    (tmp_path / "cs.prompt").write_text("你是客服 A.", encoding="utf-8")
    saver = InMemoryCheckpointSaver()
    thread_id = "resume-thread"
    first = make_session(
        MockLLM.fixed(text_response("好的")),
        saver=saver,
        thread_id=thread_id,
        prompt_name="cs",
        prompt_dir=tmp_path,
    )
    await first.ask("在吗")

    model = MockLLM.fixed(text_response("接着说"))
    reborn = make_session(
        model, saver=saver, thread_id=thread_id, prompt_name="cs", prompt_dir=tmp_path
    )
    result = await reborn.resume()

    assert result is not None
    assert requests(model)[0][0] == {"role": "system", "content": "你是客服 A."}


async def test_the_question_is_handed_over_the_moment_it_is_asked() -> None:
    """提问那一行**当场**交给记录员 (ticket 27 的「产生即落库」).

    断的是「交了什么、从第几条开始」: 内容是那句提问, 下标是它在本次 run 历史里的
    位置 (身份说明占第 0 条) —— 记录层按那个下标算编号, 收尾那一拍据此认出**同一
    行**, 而不是再写一条 (那样会变成「用户说过两遍」的假象).
    """
    flushes: list[tuple[int, list[Any]]] = []

    class Recorder:
        """实现了 `flush` 的记录员 (那是**中途**出口, 与收尾那三个方法并列)."""

        async def begin(self, *, thread_id: str, title: str = "") -> str | None:
            return "run-1"

        async def flush(
            self,
            *,
            thread_id: str,
            run_id: str,
            start: int,
            messages: Sequence[Any],
            calls: Any = (),
        ) -> None:
            flushes.append((start, list(messages)))

        async def record(self, **kwargs: Any) -> bool:
            return True

        async def record_unfinished(self, **kwargs: Any) -> bool:
            return True

    session = make_session(
        MockLLM.fixed(text_response("答好了")),
        thread_id="rec-early",
        recorder=Recorder(),
    )

    await session.ask("第一问")

    assert flushes[0] == (1, [{"role": "user", "content": "第一问"}]), (
        "提问行第一个交出去 (在 loop 之前), 下标 1 = 身份说明之后那一条"
    )


async def test_an_interrupted_run_knows_where_its_question_was_written() -> None:
    """取消 / 失败那一轮: 提问行在提问时就写过了, 收尾那一拍认的是**同一行**.

    断的是 `since` 传下去了: 它错的话记录里会多出一条重复的提问行 —— 而看记录的人
    会以为用户把同一句话说了一遍.
    """
    unfinished: list[tuple[str, int]] = []

    class Recorder:
        """只关心「没答完」那一条路的记录员."""

        async def begin(self, *, thread_id: str, title: str = "") -> str | None:
            return "run-1"

        async def record(self, **kwargs: Any) -> bool:
            return True

        async def record_unfinished(
            self,
            *,
            thread_id: str,
            question: str,
            status: Any,
            run_id: str | None = None,
            since: int = 0,
            model: str | None = None,
            finish_run: bool = True,
        ) -> bool:
            unfinished.append((question, since))
            return True

    class Exploding:
        """一调用就炸的模型 (模拟上游挂了)."""

        async def generate(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("上游挂了")

        async def aclose(self) -> None:
            """协议要求 (假模型没有资源)."""

    session = make_session(Exploding(), thread_id="rec-2", recorder=Recorder())

    with pytest.raises(RuntimeError):
        await session.ask("这一句会失败")

    assert unfinished == [("这一句会失败", 1)], "下标就是提问那一行 (身份说明之后)"


async def test_a_missing_prompt_version_stops_the_restart_loudly(
    tmp_path: Path,
) -> None:
    """那段会话用的那版提示词被删了: 报错, 不猜也不退回别的版本.

    「读不到就不静默退回上一版」是 `resolve_prompt_version` 立下的纪律; 这里同一条
    —— 编不出正文还继续, 等于让这段会话带着别人的身份往下聊.
    """
    (tmp_path / "cs.prompt").write_text("你是客服 A.", encoding="utf-8")
    saver = InMemoryCheckpointSaver()
    thread_id = "gone-prompt-thread"
    first = make_session(
        MockLLM.fixed(text_response("好的")),
        saver=saver,
        thread_id=thread_id,
        prompt_name="cs",
        prompt_dir=tmp_path,
    )
    await first.ask("在吗")

    # 新会话先建起来 (构造期它会读一次提示词), 随后那份提示词没了 (换目录 / 误删)
    reborn = make_session(
        MockLLM.fixed(text_response("接着说")),
        saver=saver,
        thread_id=thread_id,
        prompt_name="cs",
        prompt_dir=tmp_path,
    )
    (tmp_path / "cs.prompt").unlink()

    with pytest.raises(PromptError):
        await reborn.ask("接着说")
