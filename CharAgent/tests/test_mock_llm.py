"""MockLLM 三模式 + 录制回放的自测 (issue 09 / #61).

为什么测试基建自己要有防线: 断言帮手与假模型写错, 会让一批用例「假装在测」
(红不了, 也验不出东西). 本文件测的是「假大脑本身的行为边界」:

- 三种模式各自的语义 (固定返回不判耗尽 / 脚本化按序弹空即报错 / 回放走样本)
- 兼容面: `ScriptedModel([])` 这类既有用法 (只为构造 loop) 不被破坏
- 录制器: 记下请求与响应原文、缺 raw 时给可操作错误、能读回来
- 样本文件的形状与版本闸门 (老样本不许硬读)

被测代码零改动这条也在这里钉一下: MockLLM 与真适配器在 loop 里可互换.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from helpers import TOOL_SCHEMA, text_completion_json
from mock_llm import (
    MockLLM,
    MockMode,
    RecordingChatModel,
    ScriptedModel,
    load_sample,
    make_tool_call,
    text_response,
    tool_call_response,
)
from record_llm_samples import TOOLS, USER_QUESTION, make_loop
from trace_assertions import trace_of

from CharAgent.agent import LoopOutcome
from CharAgent.model.parse import parse_chat_completion
from CharAgent.model.utils.types import FinishReason, ModelResponse

USER_MSG = {"role": "user", "content": "订单 20260701123456 到哪了"}

# 录制好的真实样本 (tests/fixtures/llm/); 回放用例的数据源
REAL_SAMPLES = ("text_stop", "tool_path", "tool_path_thinking")


def recorded_response(content: str = "订单已发货") -> ModelResponse:
    """一份**带 raw 原文**的响应 (录制器的前提; 真适配器都保留 raw).

    走生产解析函数把 wire 样本解析出来, 于是 raw 与真链路上拿到的同形.
    """
    payload = text_completion_json()
    payload["choices"][0]["message"]["content"] = content
    return parse_chat_completion(payload)


# ---------------------------------------------------------------------------
# 三种模式
# ---------------------------------------------------------------------------


async def test_fixed_mode_repeats_response_without_exhaustion() -> None:
    """模式一 (固定返回): 调多少次都给同一条, 不判耗尽."""
    model = MockLLM.fixed(text_response("好的"))

    responses = [await model.generate([USER_MSG]) for _ in range(3)]

    assert model.mode is MockMode.FIXED
    assert [response.content for response in responses] == ["好的"] * 3
    assert len(model.calls) == 3  # 每次调用仍留痕 (轨迹断言的素材)


async def test_scripted_mode_pops_in_order() -> None:
    """模式二 (脚本化序列): 按调用次数依次弹 (第一次 A 第二次 B)."""
    model = MockLLM.scripted(
        [
            tool_call_response(make_tool_call("echo", '{"message": "hi"}')),
            text_response("查完了"),
        ]
    )

    first = await model.generate([USER_MSG], [TOOL_SCHEMA])
    second = await model.generate([USER_MSG])

    assert model.mode is MockMode.SCRIPTED
    assert first.finish_reason is FinishReason.TOOL_CALLS
    assert second.content == "查完了"


async def test_scripted_mode_reports_exhaustion_with_turn_number() -> None:
    """脚本弹空时显式报错 (说清第几次被调用), 不静默返回错东西."""
    model = MockLLM.scripted([text_response("只有一轮")])
    await model.generate([USER_MSG])

    with pytest.raises(AssertionError) as excinfo:
        await model.generate([USER_MSG])

    message = str(excinfo.value)
    assert "第 2 次被调用" in message
    assert "脚本" in message


async def test_empty_script_is_allowed_until_called() -> None:
    """空脚本构造合法 (既有用法: 一批构造校验用例只需要一个模型实例)."""
    model = ScriptedModel([])

    assert model.calls == []
    with pytest.raises(AssertionError, match="第 1 次被调用"):
        await model.generate([USER_MSG])


async def test_scripted_alias_keeps_issue04_name_and_call_shape() -> None:
    """`ScriptedModel` 仍是 MockLLM 的别名 (issue 04 那批测试零改动)."""
    model = ScriptedModel([text_response("旧名字照用")])

    assert ScriptedModel is MockLLM
    assert (await model.generate([USER_MSG])).content == "旧名字照用"


async def test_callable_step_sees_current_messages() -> None:
    """脚本元素可以是可调用: 按当轮 messages 现算 (注入轮间逻辑)."""

    async def echo_last(messages: list[dict[str, Any]]) -> ModelResponse:
        return text_response(f"看到 {len(messages)} 条消息")

    model = MockLLM.scripted([echo_last])

    assert (await model.generate([USER_MSG])).content == "看到 1 条消息"


async def test_sampling_params_passed_through_and_recorded() -> None:
    """采样参数透传 + 落进 calls (确定性断言 #61 的取数口)."""
    model = MockLLM.fixed(text_response("ok"))

    await model.generate([USER_MSG], None, temperature=0.0, seed=20260914)
    await model.generate([USER_MSG], [TOOL_SCHEMA])

    assert model.calls[0]["temperature"] == 0.0
    assert model.calls[0]["seed"] == 20260914
    assert model.calls[0]["tools"] is None
    assert model.calls[1]["tools"] == [TOOL_SCHEMA]
    assert model.no_tools_calls == 1


async def test_call_records_freeze_history_at_call_time() -> None:
    """calls 里的 messages 是调用时刻的快照 (后续 append 不污染历史轮次)."""
    model = MockLLM.fixed(text_response("ok"))
    history = [USER_MSG]

    await model.generate(history)
    history.append({"role": "assistant", "content": "ok"})

    assert len(model.calls[0]["messages"]) == 1


# ---------------------------------------------------------------------------
# 录制器
# ---------------------------------------------------------------------------


async def test_recorder_captures_request_and_raw_response(tmp_path: Path) -> None:
    """录制器: 记下请求与响应原文, 落盘后能原样读回."""
    inner = MockLLM.fixed(recorded_response("订单 20260701123456 已发货"))
    recorder = RecordingChatModel(inner, name="tmp_sample")
    path = tmp_path / "tmp_sample.json"

    await recorder.generate([USER_MSG], [TOOL_SCHEMA], temperature=0.0)
    recorder.dump(sampling={"temperature": 0.0}, note="自测样本", path=path)

    sample = load_sample(path)
    assert sample.name == "tmp_sample"
    assert sample.note == "自测样本"
    assert sample.model == "deepseek-flash"
    assert sample.exchanges[0].request["messages"] == [USER_MSG]
    assert sample.responses[0].content == "订单 20260701123456 已发货"
    assert recorder.responses[0].finish_reason is FinishReason.STOP


async def test_recorder_rejects_response_without_raw() -> None:
    """响应没有原文时显式报错 (说清要保留 ModelResponse.raw, 不静默录空)."""
    inner = MockLLM.fixed(text_response("没有 raw 的响应"))
    recorder = RecordingChatModel(inner, name="tmp_sample")

    with pytest.raises(AssertionError, match="raw"):
        await recorder.generate([USER_MSG])


async def test_recorder_closes_inner_model() -> None:
    """录制器转发 aclose (谁建的谁关, 不吞连接)."""
    inner = MockLLM.fixed(recorded_response())
    recorder = RecordingChatModel(inner, name="tmp_sample")

    await recorder.aclose()

    assert inner.calls == []  # 只关不调


def test_sample_rejects_unknown_version(tmp_path: Path) -> None:
    """样本版本不认识就拒绝读 (硬读未来格式会把字段读错位)."""
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps({"sample_version": 99, "exchanges": [{"response": {}}]}),
        encoding="utf-8",
    )

    with pytest.raises(AssertionError, match="格式版本"):
        load_sample(path)


def test_sample_missing_reports_actionable_error(tmp_path: Path) -> None:
    """样本缺失时提示怎么录 (可操作错误, 与 #2 同风格)."""
    with pytest.raises(AssertionError, match="record_llm_samples"):
        load_sample(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# 回放模式 (真实样本)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", REAL_SAMPLES)
def test_recorded_samples_are_well_formed(name: str) -> None:
    """仓库里的真实样本格式正确 (随代码一起提交, 也要一起回归)."""
    sample = load_sample(name)

    assert sample.exchanges, f"样本 {name} 是空的"
    assert sample.model, f"样本 {name} 没记模型名"
    assert sample.sampling.get("seed"), f"样本 {name} 没记确定性种子"
    for exchange in sample.exchanges:
        assert exchange.request["messages"], f"样本 {name} 有一轮没有请求消息"
    for response in sample.responses:  # 逐轮都能解析 (畸形字段当场报错)
        assert response.finish_reason


async def test_replay_returns_sample_responses() -> None:
    """模式三 (录制回放): 按顺序给出样本里的响应, 走生产解析路径."""
    model = MockLLM.replay("text_stop")

    response = await model.generate([USER_MSG])

    assert model.mode is MockMode.REPLAY
    assert response.content  # 真实样本里有正文
    assert response.finish_reason is FinishReason.STOP
    assert response.usage is not None  # 真实响应带 usage


async def test_replay_exhaustion_names_the_sample() -> None:
    """样本轮次用尽时报错要指出是样本 (而不是含糊的「脚本耗尽」)."""
    model = MockLLM.replay("text_stop")
    await model.generate([USER_MSG])

    with pytest.raises(AssertionError) as excinfo:
        await model.generate([USER_MSG])

    message = str(excinfo.value)
    assert "text_stop.json" in message
    assert "重录样本" in message


async def test_replay_verify_requests_accepts_matching_wire() -> None:
    """回放校验: 请求与录制时一致 → 放行 (契约没漂)."""
    sample = load_sample("text_stop")
    model = MockLLM.replay("text_stop", verify_requests=True)
    recorded = sample.requests[0]

    await model.generate(recorded["messages"], recorded["tools"])


async def test_replay_verify_requests_flags_wire_drift() -> None:
    """回放校验: 请求与录制时不一致 → 报错并给出重录指引 (#63 契约回归)."""
    model = MockLLM.replay("text_stop", verify_requests=True)

    with pytest.raises(AssertionError) as excinfo:
        await model.generate([{"role": "user", "content": "换了一个问题"}])

    message = str(excinfo.value)
    assert "与录制时不一致" in message
    assert "重录样本" in message


async def test_replay_used_by_agent_loop_without_code_change() -> None:
    """被测代码零改动: loop 拿真样本回放跑完整工具链路, 且 wire 逐字对得上.

    这是本文件里最重的一条: 样本是在真实链路上录的, 回放时开着
    `verify_requests` —— 于是「模型看到的东西」必须与录制时逐字一致 (工具
    回填格式 / tool_call_id 配对 / 历史顺序), 任何一处漂移都会红.
    """
    model = MockLLM.replay("tool_path", verify_requests=True)
    loop = make_loop(model, tools=TOOLS)

    result = await loop.run([{"role": "user", "content": USER_QUESTION}])

    trace = trace_of(model)
    trace.assert_turn_count(2)
    trace.assert_tool_calls([("query_order", {"order_no": "20260701123456"})])
    trace.assert_tool_result_backfilled("query_order", contains="已发货")
    assert result.outcome is LoopOutcome.FINISHED
    assert result.content


# ---------------------------------------------------------------------------
# 录制脚本的落盘时序 (录歪了不覆盖仓库里那份好样本)
# ---------------------------------------------------------------------------


async def test_record_script_keeps_existing_sample_when_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """体检不过 → 不落盘: 仓库里那份好样本保持原样, 只剩退出码 1.

    这是脚本自述的承诺 («录歪了不如不录»); 先写盘再体检的实现会在这里红.
    """
    import record_llm_samples as script

    monkeypatch.setattr("mock_llm.SAMPLE_DIR", tmp_path)
    existing = tmp_path / "tool_path.json"
    existing.write_text('{"sample_version": 1, "exchanges": []}', encoding="utf-8")

    async def fake_record(name: str, **kwargs: Any) -> RecordingChatModel:
        recorder = RecordingChatModel(MockLLM.fixed(recorded_response()), name=name)
        await recorder.generate([USER_MSG])
        return recorder

    monkeypatch.setattr(script, "_record", fake_record)
    monkeypatch.setattr(script, "_check_tool_path", lambda recorder: ["模拟的体检问题"])
    monkeypatch.setattr(script, "_check_thinking", lambda recorder: [])

    exit_code = await script.main()

    assert exit_code == 1
    assert existing.read_text(encoding="utf-8") == (
        '{"sample_version": 1, "exchanges": []}'
    )


async def test_record_script_saves_sample_after_check_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """体检通过 → 落盘 (三份样本都在, 元信息补齐)."""
    import record_llm_samples as script

    monkeypatch.setattr("mock_llm.SAMPLE_DIR", tmp_path)

    async def fake_record(name: str, **kwargs: Any) -> RecordingChatModel:
        recorder = RecordingChatModel(MockLLM.fixed(recorded_response()), name=name)
        await recorder.generate([USER_MSG])
        return recorder

    monkeypatch.setattr(script, "_record", fake_record)
    monkeypatch.setattr(script, "_check_tool_path", lambda recorder: [])
    monkeypatch.setattr(script, "_check_thinking", lambda recorder: [])

    exit_code = await script.main()

    assert exit_code == 0
    saved = load_sample(tmp_path / "tool_path.json")
    assert saved.sampling["seed"]  # 元信息来自 _save, 不是空壳
    assert saved.note
