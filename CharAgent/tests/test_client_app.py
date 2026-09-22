"""client 进程入口测试: 参数解析 / 装配 / 两条运行路径 / Ctrl-C 打断.

场景 → 断言:
- parse_argv: 默认值, 多次 -q 收集成元组, --no-thinking / --no-retry 的映射,
  非 TTY 自动不上色 (pytest 里 stdout 被采集, 正好是「重定向」那种情形)
- 一次性提问 (-q): 打答复 + 一行账目, 退出码 0; 模型没答完 (guard 刹车) 退出码 1
- --history: 打印快照历史表 (空会话也给一句人话, 不是空白)
- 交互模式: 问一句 -> 答一句 -> /quit 退出; 四条命令各走各的; 不认识 /resume 的
  拼错时提示命令表而不把它发给模型; 多写的参数被点出来
- 启动配置错 (缺 Key / 后端名不认识): 一行人话 + 退出码 1, 不出 traceback
- **重试真的会在这条路上发生**: 只换掉 `chat_model_from_env` (最外层的生产工厂),
  让模型先抛两次瞬态错 —— `build_model` / `RetryPolicy` / `on_retry` 全真跑,
  断言跑完答上了 + `[retry]` 打了两行; 反面 `--no-retry` 一次就失败
- KillSwitch: SIGINT 到达时取消正在跑的任务并等它收尾 (kill switch, #3),
  之后事件循环仍可用 —— 这正是「打断后还能 /resume」的前提

装配注入用两处既有缝: 模型走 `main(model=...)` (ChatModel 薄协议, 与全仓测试
同一套替身做法), 输入走 `main(reader=...)`; 存储用 monkeypatch 换掉
`build_saver_for` (本测试只在验收「接线」, 存储本身的行为归 checkpoint 的用例).
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response

from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.client import app
from CharAgent.client.app import KillSwitch, build_saver_for, main, parse_argv
from CharAgent.client.utils.types import DEFAULT_THREAD_ID, CliOptions
from CharAgent.model import HttpXChatModel, ModelConfigError, ModelConnectionError
from CharAgent.model.utils.types import FinishReason, ModelMessage, ModelResponse
from CharAgent.retry import RetryingChatModel

# 仓库根 (起子进程时当 cwd, 子进程才 import 得到 CharAgent)
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _memory_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认把快照后端钉成内存版 (不跟着本机 `.env` 走).

    为什么必须钉: 这几个用例的前提是「一个全新的会话**没有**存档」, 而
    `CHARAGENT_CHECKPOINT_BACKEND` 在本机 .env 里是 postgres —— 跑过一次之后
    `client-main` 就有帧了, 于是「没有可恢复的快照」这条断言会在**第二次**跑时
    红 (2026-09-22 撞上, 排查花的时间比修它长). 这是用例的环境依赖, 不是被测代码
    的行为: 真的「听环境变量」那一条 (test_build_saver_for_falls_back_to_the_
    environment) 自己 setenv, 不受这里影响.
    """
    monkeypatch.setenv("CHARAGENT_CHECKPOINT_BACKEND", "memory")


class ScriptedReader:
    """按剧本吐行的假输入 (用尽后抛 EOFError, 等价于 Ctrl-Z / 管道读完)."""

    def __init__(self, *lines: str) -> None:
        self._lines = list(lines)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def test_defaults_are_a_single_question_free_session() -> None:
    """不给参数: 内存语义的默认配置 (后端听环境变量, 会话编号固定)."""
    options = parse_argv([])

    assert options.backend is None
    assert options.thread_id == DEFAULT_THREAD_ID
    assert options.questions == ()
    assert options.resume is False
    assert options.show_history is False
    assert options.thinking is None
    assert options.max_turns == 10
    assert options.max_tokens is None
    assert options.use_retry is True
    assert options.interactive is True


def test_questions_accumulate_in_order() -> None:
    """-q 可重复给, 按给的顺序收集 (一次性问几句)."""
    options = parse_argv(["-q", "第一句", "--question", "第二句"])

    assert options.questions == ("第一句", "第二句")
    assert options.interactive is False


def test_flag_mapping_for_thinking_and_retry() -> None:
    """--no-thinking -> thinking=False (显式关); --no-retry -> 不套重试包装."""
    options = parse_argv(["--no-thinking", "--no-retry", "--backend", "redis"])

    assert options.thinking is False
    assert options.use_retry is False
    assert options.backend == "redis"


def test_max_tokens_is_carried_into_the_options() -> None:
    """`--max-tokens` 进 CliOptions (透传给 generate; 设小可稳定造出 length 截断)."""
    assert parse_argv(["--max-tokens", "10"]).max_tokens == 10


def test_max_turns_is_carried_into_the_options() -> None:
    """`--max-turns` 进 CliOptions (透传给 LoopGuard)."""
    assert parse_argv(["--max-turns", "3"]).max_turns == 3


def test_color_is_off_when_stdout_is_not_a_tty() -> None:
    """非 TTY (pytest 采集 / 重定向进文件) 时自动不上色, 免得文件里全是转义符."""
    assert parse_argv([]).color is False
    assert parse_argv(["--plain"]).color is False


def test_unknown_backend_is_rejected_by_the_parser() -> None:
    """后端名不在三选一时, argparse 直接拦下 (退出码 2, 不进装配)."""
    with pytest.raises(SystemExit) as excinfo:
        parse_argv(["--backend", "postgresql"])

    assert excinfo.value.code == 2


def test_build_saver_follows_the_explicit_backend() -> None:
    """显式给 --backend 时按它挑存储 (这里只验类型, 不连真服务)."""
    saver = build_saver_for(CliOptions(backend="memory"))

    assert isinstance(saver, InMemoryCheckpointSaver)


# ---------------------------------------------------------------------------
# 一次性提问
# ---------------------------------------------------------------------------


def test_question_mode_prints_answer_and_summary(capsys: pytest.CaptureFixture) -> None:
    """-q: 打事件行 + 答复正文 + 一行账目; 退出码 0."""
    model = MockLLM.fixed(text_response("订单 20260701123456 已发货"))

    code = main(["-q", "订单到哪了"], model=model)
    out = capsys.readouterr().out

    assert code == 0
    assert "[final]" in out
    assert "订单 20260701123456 已发货" in out
    assert "[完成] 答完了" in out


def test_question_mode_with_tools_runs_the_full_path(
    capsys: pytest.CaptureFixture,
) -> None:
    """带工具问答: thinking / tool_call / tool_result / final 四类事件都出现."""
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("get_current_time", '{"fmt": "iso", "timezone": "utc"}'),
                content="我先看一下时间",
            ),
            text_response("现在时间是 2026-09-15。"),
        ]
    )

    code = main(["-q", "现在几点", "--max-turns", "4"], model=model)
    out = capsys.readouterr().out

    assert code == 0
    for tag in ("[thinking]", "[tool_call]", "[tool_result]", "[final]"):
        assert tag in out
    assert "get_current_time" in out
    assert "2026-09-15" in out


def test_question_mode_returns_one_when_the_model_never_answers(
    capsys: pytest.CaptureFixture,
) -> None:
    """模型一直要调工具被 guard 拦下: 退出码 1, 且终局是 error 不是 final."""
    model = MockLLM.fixed(tool_call_response(make_tool_call("get_current_time", "{}")))

    code = main(["-q", "现在几点", "--max-turns", "1"], model=model)
    out = capsys.readouterr().out

    assert code == 1
    assert "[error] max_turns" in out
    assert "没有产出正文" in out


def test_truncation_continuation_is_stitched_into_the_answer(
    capsys: pytest.CaptureFixture,
) -> None:
    """length 截断走续写路径: 两段被拼成一个完整答复 (--max-tokens 的用途).

    `--help` 明说这个选项「设小可以稳定造出 length 截断, 用来看续写路径」——
    这条就是那句话的防线.
    """
    model = MockLLM.scripted(
        [
            text_response("前半段", finish_reason=FinishReason.LENGTH),
            text_response("后半段"),
        ]
    )

    code = main(["-q", "写篇长文", "--max-tokens", "10"], model=model)
    out = capsys.readouterr().out

    assert code == 0
    assert "前半段后半段" in out, "续写的两段没有被拼合成完整答复"


def test_reasoning_events_show_up_in_the_run_output(
    capsys: pytest.CaptureFixture,
) -> None:
    """reasoning 端到端: 思维链单独成行, 不混进正文 (验收要求带 reasoning)."""
    model = MockLLM.fixed(text_response("答案", reasoning="我先核对一下订单号"))

    main(["-q", "想想"], model=model)
    out = capsys.readouterr().out

    assert "[reasoning] 我先核对一下订单号" in out
    assert "[final]" in out


def test_tool_failure_then_self_correction_ends_with_an_answer(
    capsys: pytest.CaptureFixture,
) -> None:
    """工具失败 -> 可操作错误回填 -> 模型改对参数 -> 最终答复 (主路径之一).

    第一轮故意少给必填参数 `text`: 执行层回填的是「哪个字段缺了、期望什么」,
    模型据此在第二轮补齐后拿到结果.
    """
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("count_text_stats", '{"mode": "chars"}')),
            tool_call_response(
                make_tool_call("count_text_stats", '{"text": "hello", "mode": "chars"}')
            ),
            text_response("一共 5 个字符"),
        ]
    )

    code = main(["-q", "数一下 hello 的字符数"], model=model)
    out = capsys.readouterr().out

    assert code == 0
    assert "失败" in out, "第一轮的工具失败没有被展示出来"
    assert "text" in out, "可操作错误里没有指出缺哪个字段"
    assert "字符数: 5" in out, "改对参数后的工具结果没有被回填"
    assert "一共 5 个字符" in out


# ---------------------------------------------------------------------------
# 看历史 / 续跑 (一次性路径)
# ---------------------------------------------------------------------------


def test_history_mode_prints_a_plain_sentence_for_an_empty_session(
    capsys: pytest.CaptureFixture,
) -> None:
    """空会话看历史: 一句人话说明「没存过」, 退出码 0 (不是错误)."""
    code = main(["--history"], model=MockLLM.fixed(text_response("你好")))
    out = capsys.readouterr().out

    assert code == 0
    assert "没有历史帧" in out


def test_history_mode_prints_the_table_when_frames_exist(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """有帧时打印对齐表格 (存储经 monkeypatch 注入, 于是帧是预置的)."""
    saver = InMemoryCheckpointSaver()

    async def _seed() -> None:
        """往内存存储里先跑一轮, 制造出可读的历史帧."""
        session = app.ChatSession(
            MockLLM.fixed(text_response("done")),
            saver=saver,
            tools=[],
            thread_id=DEFAULT_THREAD_ID,
        )
        await session.ask("问题")

    asyncio.run(_seed())
    monkeypatch.setattr(app, "build_saver_for", lambda options: saver)

    code = main(["--history"], model=MockLLM.fixed(text_response("你好")))
    out = capsys.readouterr().out

    assert code == 0
    assert "轮次" in out and "父帧" in out


def test_resume_mode_without_a_checkpoint_says_so(
    capsys: pytest.CaptureFixture,
) -> None:
    """--resume 但没有存档 (内存后端新进程): 提示一句, 退出码 1.

    后面还跟着一句提问, 但续跑失败会让它不再往下走 —— 存档都没找到, 多半是
    环境不对 (后端换了 / 会话编号写错), 再发一次只是白烧 token.
    """
    model = MockLLM.fixed(text_response("你好"))

    code = main(["--resume", "-q", "接着问一句"], model=model)
    out = capsys.readouterr().out

    assert code == 1
    assert "没有可恢复的快照" in out
    assert model.calls == [], "续跑失败后仍然把后面的提问发了出去"


def test_history_rejects_questions_instead_of_dropping_them(
    capsys: pytest.CaptureFixture,
) -> None:
    """`--history` 与 `-q` 同给直接报错, 不静默丢掉用户敲的问题.

    原先的实现是「先判 --history, 直接 return」—— 问题被无声吞掉, 用户只看到一张
    历史表, 会以为自己的问题跑过了.
    """
    model = MockLLM.fixed(text_response("不该被问到"))

    code = main(["--history", "-q", "别把我的问题丢掉"], model=model)
    out = capsys.readouterr().out

    assert code == 1
    assert "不能同时给" in out
    assert model.calls == [], "报了错却仍然调了模型"


def test_thread_id_keeps_sessions_apart(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不同 `--thread-id` 各存各的: 甲的帧不会出现在乙的历史里."""
    saver = InMemoryCheckpointSaver()

    async def _seed(name: str, question: str) -> None:
        session = app.ChatSession(
            MockLLM.scripted(
                [
                    tool_call_response(
                        make_tool_call(
                            "convert_length",
                            '{"value": 1, "from_unit": "kilometer", "to_unit": "mile"}',
                        )
                    ),
                    text_response(f"{name} 的答复"),
                ]
            ),
            saver=saver,
            tools=app.DEMO_TOOLS,
            thread_id=name,
        )
        await session.ask(question)

    asyncio.run(_seed("sess-a", "甲会话的问题"))
    monkeypatch.setattr(app, "build_saver_for", lambda options: saver)

    model = MockLLM.fixed(text_response("x"))
    main(["--thread-id", "sess-a", "--history"], model=model)
    table_a = capsys.readouterr().out
    main(["--thread-id", "sess-b", "--history"], model=model)
    table_b = capsys.readouterr().out

    assert "convert_length" in table_a, "甲会话自己的帧没被打印出来"
    assert "没有历史帧" in table_b, "乙会话看到了甲的帧 (会话之间没有隔离)"


# ---------------------------------------------------------------------------
# 交互模式
# ---------------------------------------------------------------------------


def test_interactive_answers_then_quits(capsys: pytest.CaptureFixture) -> None:
    """交互模式: 问一句 -> 答一句 -> /quit 退出, 退出码 0."""
    reader = ScriptedReader("订单到哪了", "/quit")

    code = main([], model=MockLLM.fixed(text_response("已发货")), reader=reader)
    out = capsys.readouterr().out

    assert code == 0
    assert "CharAgent CLI (P0 验收演示)" in out
    assert "已发货" in out
    assert "[完成] 答完了" in out
    assert "再见" in out
    assert reader.prompts[0] == app.PROMPT


def test_interactive_eof_exits_like_quit(capsys: pytest.CaptureFixture) -> None:
    """输入流读完 (Ctrl-Z / 管道) 与 /quit 同一条退路, 不是错误."""
    code = main([], model=MockLLM.fixed(text_response("你好")), reader=ScriptedReader())

    assert code == 0
    assert "再见" in capsys.readouterr().out


def test_ctrl_c_at_the_prompt_is_a_normal_exit(
    capsys: pytest.CaptureFixture,
) -> None:
    """在提示符处按 Ctrl-C: 与 /quit 同一条退路 (不当错误, 也不留 traceback)."""

    def interrupt(prompt: str) -> str:
        raise KeyboardInterrupt

    code = main([], model=MockLLM.fixed(text_response("你好")), reader=interrupt)
    out = capsys.readouterr().out

    assert code == 0
    assert "再见" in out


def test_interactive_help_lists_the_commands(capsys: pytest.CaptureFixture) -> None:
    """/help 列出四条命令; 续跑指引由启动横幅给出 (/help 只留命令)."""
    reader = ScriptedReader("/help", "/quit")

    main([], model=MockLLM.fixed(text_response("你好")), reader=reader)
    out = capsys.readouterr().out

    assert "/resume" in out and "/history" in out and "/quit" in out
    # 2026-09-18: 原先跟在 /help 后面的三段续跑说明挪进了启动横幅, 断言跟着挪
    assert "打断 RUN 后说一句「继续」即可续跑" in out


def test_interactive_reports_a_misspelled_command_without_asking_the_model(
    capsys: pytest.CaptureFixture,
) -> None:
    """敲错的命令: 提示命令表, **不**把它当问题发给模型."""
    model = MockLLM.fixed(text_response("不该被问到"))
    reader = ScriptedReader("/resum", "/quit")

    main([], model=model, reader=reader)
    out = capsys.readouterr().out

    assert "不认识这条命令" in out
    assert "不该被问到" not in out
    assert model.calls == [], "敲错的命令被发给了模型"


def test_interactive_points_out_extra_arguments(capsys: pytest.CaptureFixture) -> None:
    """命令后面多写了东西: 点出来但仍照原样执行."""
    reader = ScriptedReader("/resume 3", "/quit")

    main([], model=MockLLM.fixed(text_response("你好")), reader=reader)
    out = capsys.readouterr().out

    assert "不吃参数" in out
    assert "没有可恢复的快照" in out


def test_interactive_resume_after_a_successful_run(
    capsys: pytest.CaptureFixture,
) -> None:
    """/resume 在已有快照时接着跑 (这里验证接线: 第二次也走到 [完成])."""
    model = MockLLM.fixed(text_response("你好"))
    reader = ScriptedReader("问一句", "/resume", "/quit")

    code = main([], model=model, reader=reader)
    out = capsys.readouterr().out

    assert code == 0
    assert out.count("[完成] 答完了") == 2
    assert {"role": "user", "content": "问一句"} in model.calls[1]["messages"]


def test_interactive_history_command_prints_the_table(
    capsys: pytest.CaptureFixture,
) -> None:
    """/history 在交互里也能看存档表 (问一句后至少一帧)."""
    reader = ScriptedReader("问一句", "/history", "/quit")

    main([], model=MockLLM.fixed(text_response("你好")), reader=reader)
    out = capsys.readouterr().out

    assert "轮次" in out and "父帧" in out


# ---------------------------------------------------------------------------
# 启动配置错
# ---------------------------------------------------------------------------


def test_missing_api_key_exits_with_one_human_readable_line(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺 Key: 一行人话 + 退出码 1 (不把 traceback 糊在屏幕上)."""

    def explode(options: CliOptions, writer: Any) -> Any:
        """替身装配函数: 一被调用就抛缺 Key 的错 (模拟 .env 没配)."""
        raise ModelConfigError("DEEPSEEK_API_KEY 未配置")

    monkeypatch.setattr(app, "build_model", explode)

    code = main(["-q", "随便问"])
    out = capsys.readouterr().out

    assert code == 1
    assert "启动失败" in out
    assert "DEEPSEEK_API_KEY 未配置" in out


def test_build_model_wraps_the_adapter_with_retry_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """默认装配 = 裸适配器外面套一层重试包装 —— 重试的接线点 (关键验收之一).

    这条盯的是「接线」本身, 不真发请求: 只断言对象形状与内层是谁. 在它之前
    `retry/` 没有任何生产调用点 (此前一直如此).
    """
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-charagent")
    monkeypatch.delenv("DEEPSEEK_MODEL_NAME", raising=False)

    wrapped = app.build_model(CliOptions(), print)
    bare = app.build_model(CliOptions(use_retry=False), print)

    assert isinstance(wrapped, RetryingChatModel)
    assert isinstance(wrapped._model, HttpXChatModel), "包装里套的不是裸适配器"
    assert isinstance(bare, HttpXChatModel), "--no-retry 应当直接给裸适配器"
    assert not isinstance(bare, RetryingChatModel)


class _FlakyModel:
    """前 n 次抛瞬态错误, 之后原样转发给里面的替身 (模拟 429 / 5xx / 连接失败).

    用 `ModelConnectionError` 而不是随便一个异常: 重试的判据是异常族自带的
    `retryable` 标记, 抛错了类型就变成「永久错误不重试」, 用例会以错误的理由红.
    """

    def __init__(self, inner: Any, *, fail_times: int) -> None:
        self._inner = inner
        self._fail_times = fail_times
        self.calls = 0

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        """前几次抛瞬态错, 之后放行 (记下被调用了几次)."""
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ModelConnectionError(f"模拟第 {self.calls} 次连接失败")
        return await self._inner.generate(*args, **kwargs)

    async def aclose(self) -> None:
        """转发关闭 (谁建谁关)."""
        await self._inner.aclose()


def test_a_transient_failure_is_retried_during_a_real_run(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """关键验收的**端到端**证据: 重试真的在一次 CLI 运行里发生过.

    为什么要单独一条: CLI 的端到端用例都注入 `model=` (ChatModel 薄协议那处缝),
    而 `main()` 是 `model if model is not None else build_model(...)` —— **注入时
    根本不走 `build_model`**, 于是重试包装在别的用例里一次都没被执行过.

    已有一半覆盖救不了它: `test_build_model_wraps_...` 只断言对象形状,
    `test_retry_chat_model.py` 测的是 `RetryingChatModel` 自己 (含 loop 组合),
    都不经过 CLI 这一层装配.

    做法: 只换最外层的生产工厂 `chat_model_from_env` —— 比注入 `model=` 更贴近
    真实路径, `build_model` / `RetryPolicy` / `on_retry` 全都真跑.
    """
    flaky = _FlakyModel(MockLLM.fixed(text_response("重试之后答上了")), fail_times=2)
    monkeypatch.setattr(app, "chat_model_from_env", lambda model=None: flaky)

    code = main(["-q", "你好", "--backend", "memory"])
    out = capsys.readouterr().out

    assert code == 0, "重试之后本该正常答完"
    assert flaky.calls == 3, "首次 + 两次重试 = 3 次调用"
    assert out.count("[retry]") == 2, "两次重试各该打一行提示 (不打印用户只会觉得慢)"
    assert "重试之后答上了" in out


def test_no_retry_lets_the_transient_failure_fail_the_run(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-retry` 的反面: 同一个瞬态错误不再重试, 这一轮直接失败 (退出码 1).

    两条合起来才说明白「重试是**这个开关**在管」—— 只测正面的话, 一个无条件
    重试的实现也能过.
    """
    flaky = _FlakyModel(MockLLM.fixed(text_response("不该到这里")), fail_times=2)
    monkeypatch.setattr(app, "chat_model_from_env", lambda model=None: flaky)

    code = main(["-q", "你好", "--backend", "memory", "--no-retry"])
    out = capsys.readouterr().out

    assert code == 1
    assert flaky.calls == 1, "关了重试就该只调一次"
    assert "[retry]" not in out
    assert "[失败] ModelConnectionError" in out


def test_build_saver_for_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不给 `--backend` 时听 `CHARAGENT_CHECKPOINT_BACKEND`; 给了就压过环境变量."""
    from CharAgent.checkpoint import RedisCheckpointSaver

    monkeypatch.setenv("CHARAGENT_CHECKPOINT_BACKEND", "redis")

    assert isinstance(app.build_saver_for(CliOptions()), RedisCheckpointSaver)
    assert isinstance(
        app.build_saver_for(CliOptions(backend="memory")), InMemoryCheckpointSaver
    ), "显式 --backend 应当压过环境变量"


# ---------------------------------------------------------------------------
# 标准输入输出的编码
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 标准输入输出的编码
# ---------------------------------------------------------------------------


def test_piped_utf8_chinese_survives_the_encoding_setup() -> None:
    """管道喂 UTF-8 中文: 重设编码后能原样读回来.

    为什么要起子进程: 编码这件事只在**真实的**标准输入上才看得见 —— pytest 会把
    sys.stdin 换成自己的对象, 在进程内断言等于测了个假的.

    这一条护住 2026-09-15 实测踩到的坑: 默认编码是 GBK 且 errors=surrogateescape,
    管进来的 UTF-8 中文被解成乱码加一串孤立代理项 (``'\\udcad'``), 一路带进请求体,
    最后在 httpx 里炸成 ``UnicodeEncodeError: surrogates not allowed`` —— 报错
    位置离真正的原因 (输入编码) 十万八千里.
    """
    question = "帮我把 3 公里换算成英里"
    script = (
        "import sys, CharAgent.client.app as app; "
        "app.use_utf8_stdio(); "
        "print(repr(sys.stdin.readline().strip()))"
    )

    proc = subprocess.run(
        [sys.executable, "-c", script],
        input=f"{question}\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        check=True,
    )

    assert question in proc.stdout


def test_interrupt_then_resume_does_not_rerun_completed_tools(
    capsys: pytest.CaptureFixture,
) -> None:
    """P0 验收第 4 条端到端: 打断 -> /resume 接着跑, 已执行过的工具不重跑.

    这条走的是**真的 `main()`**(真 `KillSwitch`、真 REPL、真会话、真演示工具集),
    唯一替身是模型 (ChatModel 薄协议). 打断方式与真按 Ctrl-C 同一条路径: 事件循环
    正跑着时主线程收到 SIGINT, `run_until_complete` 抛 KeyboardInterrupt,
    `KillSwitch` 取消任务并等它收尾. 定时器在第一次读输入时挂上 (那时 main 已经
    把常驻循环 `set_event_loop` 好了), 于是「第 0.3 秒按下去」正好落在第二轮模型
    调用里 —— 而第一轮的工具早已执行完并落进快照.

    证据口径用**屏幕上看得见的**东西: 事件流里 `[tool_call]` 只出现一次. 续跑若
    从零重放, 屏幕上就会再冒一次同样的调用行 —— 这正是演示时要让人看见的那件事.
    """

    # 第二轮的响应故意慢 (sleep 30s): 让打断落在它身上. 它被取消后脚本的第三条
    # 留给续跑那一轮 —— 于是「同一条脚本」能服务完打断前后两段
    async def slow_turn(messages: list[ModelMessage]) -> ModelResponse:
        """第二轮的慢响应: 一直睡到被 Ctrl-C 取消 (正常不返回)."""
        await asyncio.sleep(30)
        raise AssertionError("这一轮本该被打断")  # pragma: no cover (正常不达)

    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("query_order_status", '{"order_no": "20260701123456"}')
            ),
            slow_turn,
            text_response("续跑后的答复"),
        ]
    )
    scheduled = {"done": False}

    def reader(prompt: str) -> str:
        """第一次读输入时挂上「0.3 秒后按 Ctrl-C」, 然后给出问题与后续命令."""
        if not scheduled["done"]:
            scheduled["done"] = True
            loop = asyncio.get_event_loop()  # main 已经 set_event_loop
            loop.call_later(0.3, signal.raise_signal, signal.SIGINT)
            return "我的订单 20260701123456 到哪了"
        return _next_line()

    remaining = ["/resume", "/quit"]

    def _next_line() -> str:
        """取剧本里的下一行 (打断之后由 reader 继续吐 /resume 与 /quit)."""
        return remaining.pop(0)

    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        code = main(
            ["--backend", "memory", "--thread-id", "client-e2e"],
            model=model,
            reader=reader,
        )
    finally:
        signal.signal(signal.SIGINT, previous)
    out = capsys.readouterr().out

    assert code == 0
    assert "[打断]" in out, "本次运行没有被 Ctrl-C 打断"
    assert out.count("[tool_call]") == 1, "续跑把已完成的那次工具调用重跑了"
    assert "续跑后的答复" in out
    # 续跑的轮数是累计口径 (快照里 1 轮 + 本次 1 轮), 不是从 1 重来
    assert "2 轮" in out


class _InterruptOnCall:
    """真模型的包装: 第 n 次调用时挂一个「0.05 秒后 Ctrl-C」—— 让打断落在指定那一轮.

    与真按 Ctrl-C 同一条路径: 事件循环正跑着时主线程收到 SIGINT,
    `run_until_complete` 抛 KeyboardInterrupt, `KillSwitch` 取消任务并等它收尾.
    定时器挂在循环上 (而不是先用 sleep 赌时机), 所以打断位置是确定的.
    """

    def __init__(self, inner: Any, *, at_call: int = 1) -> None:
        self._inner = inner
        self._at = at_call
        self.calls = 0

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == self._at:
            loop = asyncio.get_running_loop()
            loop.call_later(0.05, signal.raise_signal, signal.SIGINT)
            await asyncio.sleep(30)  # 等被打断 (正常走不到这里)
        return await self._inner.generate(*args, **kwargs)

    async def aclose(self) -> None:
        await self._inner.aclose()


def test_interrupted_question_exits_with_130(capsys: pytest.CaptureFixture) -> None:
    """一次性提问被打断: 退出码 130 (沿用 128 + SIGINT 惯例)."""
    model = _InterruptOnCall(MockLLM.fixed(text_response("不该到这里")))
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        code = main(["-q", "问一句"], model=model)
    finally:
        signal.signal(signal.SIGINT, previous)
    out = capsys.readouterr().out

    assert code == 130
    assert "[打断]" in out
    assert "已收回对话历史" in out
    assert "不会重跑" in out
    # 存档情况是接在正文后面的 (分隔符别丢: 「停止;快照:」读起来是错的)
    assert "停止; 快照:" in out


def test_batch_failure_is_not_erased_by_the_following_chat(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--resume` 的失败/打断不被随后的交互盖成 0 (退出码优先关系).

    这条盯的是一个真发生过的错: `_dispatch` 原来直接 `return InteractiveRepl(...)`
    `.run()`,
    而交互循环恒返回 0 —— 已经排过的步骤失败过, 进程结论却是「一切正常」.
    这里让 `--resume` 那一轮被打断 (快照先由另一段跑出来), 然后交互里立刻 /quit.
    """
    saver = InMemoryCheckpointSaver()

    async def _seed() -> None:
        session = app.ChatSession(
            MockLLM.fixed(text_response("第一段的答复")),
            saver=saver,
            tools=[],
            thread_id=DEFAULT_THREAD_ID,
        )
        await session.ask("先跑一段, 好让 --resume 有档可续")

    asyncio.run(_seed())
    monkeypatch.setattr(app, "build_saver_for", lambda options: saver)

    model = _InterruptOnCall(MockLLM.fixed(text_response("续跑时的答复")))
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        code = main(
            ["--resume", "--thread-id", DEFAULT_THREAD_ID],
            model=model,
            reader=ScriptedReader("/quit"),
        )
    finally:
        signal.signal(signal.SIGINT, previous)
    out = capsys.readouterr().out

    assert "[打断]" in out, "这一次 --resume 没有被 Ctrl-C 打断"
    assert "再见" in out, "打断之后没有进交互模式"
    assert code == 130, "被随后的交互模式盖成了 0"


# ---------------------------------------------------------------------------
# kill switch (Ctrl-C 打断)
# ---------------------------------------------------------------------------


def test_kill_switch_cancels_the_running_task_and_keeps_the_loop_usable() -> None:
    """SIGINT 到达: 取消正在跑的任务、等它收尾完, 再抛 KeyboardInterrupt.

    这条是「打断后还能 /resume」的前提 —— 任务必须真的被取消并走完清理路径,
    而不是留一个悬空协程; 事件循环也必须还能继续用 (交互模式下一个问题还要靠它).
    """
    events: list[str] = []

    async def slow() -> None:
        """长睡任务: 被取消时记一笔, 用来证明清理路径真的走过了."""
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            events.append("cleaned-up")
            raise

    loop = asyncio.new_event_loop()
    runner = KillSwitch(loop)
    # 0.05 秒后在主线程里发一次 SIGINT (等价于按 Ctrl-C); 顺带把 SIGINT 的处理
    # 器钉成默认那个, 免得测试运行器自己装的处理器让这次「按键」落空
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    loop.call_later(0.05, signal.raise_signal, signal.SIGINT)
    try:
        with pytest.raises(KeyboardInterrupt):
            runner.run(slow())
        assert events == ["cleaned-up"]
        assert loop.is_running() is False
        # 循环还能继续用 (交互模式下被打断后还要接下一句提问)
        assert runner.run(asyncio.sleep(0, result="ok")) == "ok"
    finally:
        signal.signal(signal.SIGINT, previous)
        loop.close()


# ---------------------------------------------------------------------------
# 打断后说一句「继续」就能接着跑 (CLI 不做任何识别, 判断交给模型)
# ---------------------------------------------------------------------------


def _interrupted_after_one_tool_turn(
    capsys: pytest.CaptureFixture, *, thread_id: str, phrase: str, answer: str
) -> tuple[int, str, MockLLM]:
    """跑一套「问一句 -> 工具跑完 -> 第二轮被打断 -> 用户说一句话 -> /quit」.

    三条用例共用这段编排 (真 `main()` / 真 SIGINT / 真演示工具集), 差别只在
    打断之后用户说的那句话. 打断方式与真按 Ctrl-C 同一条路径: 事件循环正跑着时
    主线程收到 SIGINT, `run_until_complete` 抛 KeyboardInterrupt, `KillSwitch`
    取消任务并等它收尾 (第一次读输入时挂的定时器, 那时 main 已把常驻循环备好).

    打断之后那一步是重点: `ChatSession` 会去快照把已完成的工作收回来, 所以用户
    接下来那句**不管说什么**都会带着「问题 + 工具调用 + 工具结果」一起发给模型.

    Args:
        capsys: pytest 的输出采集器 (从这里读屏幕上打过的字).
        thread_id: 会话编号 (每条用例各用一个, 免得快照串味).
        phrase: 打断之后用户敲的那一行.
        answer: 之后那一轮模型给的答复.

    Returns:
        tuple[int, str, MockLLM]: 退出码, 屏幕上的全部输出, 模型替身 (拿它看
        模型到底收到过什么).
    """

    async def slow_turn(messages: list[ModelMessage]) -> ModelResponse:
        """第二轮的慢响应: 一直睡到被 Ctrl-C 取消 (正常不返回)."""
        await asyncio.sleep(30)
        raise AssertionError("这一轮本该被打断")  # pragma: no cover (正常不达)

    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("query_order_status", '{"order_no": "20260701123456"}')
            ),
            slow_turn,
            text_response(answer),
        ]
    )
    scheduled = {"done": False}
    remaining = [phrase, "/quit"]

    def reader(prompt: str) -> str:
        """第一次读输入时挂上「0.3 秒后按 Ctrl-C」, 之后按剧本吐行."""
        if not scheduled["done"]:
            scheduled["done"] = True
            loop = asyncio.get_event_loop()  # main 已经 set_event_loop
            loop.call_later(0.3, signal.raise_signal, signal.SIGINT)
            return "我的订单 20260701123456 到哪了"
        return remaining.pop(0)

    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        code = main(
            ["--backend", "memory", "--thread-id", thread_id],
            model=model,
            reader=reader,
        )
    finally:
        signal.signal(signal.SIGINT, previous)
    return code, capsys.readouterr().out, model


@pytest.mark.parametrize(
    "phrase",
    [
        "继续",
        # 全角逗号是用户真会敲的写法, 用例照原话保留 (RUF001 管的是注释标点)
        "刚才不小心中断任务了，继续刚才的任务",  # noqa: RUF001
        "那订单现在到底怎么样了",  # 连「继续」都没说 —— 一样接得上
    ],
    ids=["短句", "整句", "换了个说法"],
)
def test_saying_continue_after_an_interrupt_picks_up_where_it_stopped(
    phrase: str, capsys: pytest.CaptureFixture
) -> None:
    """打断之后说一句「继续」(或任何话) 就能接着跑 —— CLI 不做任何识别.

    设计要点: **这句话原样发给模型**, CLI 里没有任何「这句是续跑吗」的分支.
    能接得上的原因是打断时把已完成的工作从快照收回了对话历史, 于是模型那一轮
    看到的是完整上下文:

        [原问题, 模型的工具调用, 工具结果, 用户新说的这句]

    它自然知道「工具已经查过了」, 接着把答案给出来 —— 这也是「不重复已完成动作」
    在口语路径上的实现 (结果是历史里的一条消息, 没人会去重跑一条消息).

    证据三层:
    1. 模型那一轮收到的消息**逐条对得上** (原问题 / 工具调用 / 工具结果 / 新话)
       —— 证明收回真的发生了, 且这句话确实当提问发了出去
    2. 屏幕上 `[tool_call]` 只出现一次 —— 工具没被重跑
    3. 答复出来了
    """
    code, out, model = _interrupted_after_one_tool_turn(
        capsys, thread_id=f"client-nl-{phrase[:4]}", phrase=phrase, answer="接着答完了"
    )

    assert code == 0
    assert "[打断]" in out, "本次运行没有被 Ctrl-C 打断"
    assert "已收回对话历史" in out, "打断提示没说清已完成的工作去哪了"

    sent = model.calls[-1]["messages"]
    assert [message["role"] for message in sent] == [
        "system",
        "user",
        "assistant",
        "tool",
        "user",
    ], "模型那一轮看到的不是「身份说明 + 问题 + 工具调用 + 工具结果 + 新话」"
    assert sent[1]["content"] == "我的订单 20260701123456 到哪了"
    # 按角色找而不是按下标: 历史前面还压着身份说明, 数下标迟早数错
    tool_results = [m for m in sent if m["role"] == "tool"]
    assert any("20260701123456" in (m.get("content") or "") for m in tool_results), (
        "收回来的不是那条工具结果"
    )
    assert sent[-1]["content"] == phrase, "这句话没被原样发给模型 (被 CLI 截胡了?)"

    assert out.count("[tool_call]") == 1, "已完成的那次工具调用被重跑了"
    assert "接着答完了" in out


def test_a_new_question_after_an_interrupt_also_sees_the_finished_work(
    capsys: pytest.CaptureFixture,
) -> None:
    """打断之后问个新问题: 照样发出去, 而且**上下文更全** (带上了刚做完的事).

    这是收回进度的另一个好处 (相比原来「把提问撤回」): 模型看得到上一轮做过什么,
    于是「刚查到的订单」和「现在问的新东西」它都能用上, 而不是两眼一抹黑.
    """
    follow_up = "那顺便帮我把它换算成英里"
    code, out, model = _interrupted_after_one_tool_turn(
        capsys,
        thread_id="client-nl-new-question",
        phrase=follow_up,
        answer="新问题的答复",
    )

    assert code == 0
    assert "[打断]" in out, "本次运行没有被 Ctrl-C 打断"
    sent = model.calls[-1]["messages"]
    assert sent[-1]["content"] == follow_up, "新问题没被发给模型"
    assert any(message["role"] == "tool" for message in sent), (
        "新问题那一轮看不到上一轮已完成的工作"
    )
    assert "新问题的答复" in out


def test_continue_without_an_interrupt_is_just_a_question() -> None:
    """没被打断过时「继续」当然也是普通提问 —— 本来就没有任何特殊处理.

    这条钉的是「CLI 不做识别」: 这句话原样进模型的消息里, 没有任何分支拦它.
    反面 (有分支的写法) 就是曾经那版关键词启发式 —— 那时这句会被截胡成续跑.
    """
    model = MockLLM.fixed(text_response("好的, 接着讲"))
    reader = ScriptedReader("继续", "/quit")

    code = main([], model=model, reader=reader)

    assert code == 0
    assert model.calls, "这句「继续」没被发给模型"
    assert model.calls[0]["messages"][-1] == {"role": "user", "content": "继续"}
