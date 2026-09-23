"""快照测试 (#63): 工具自测 + 三处形状锁定 + 确定性.

三段内容:

1. **工具自测**: 首次生成 / 一致通过 / 不一致报差异 / UPDATE_SNAPSHOTS 覆盖 ——
   快照工具坏掉的后果是「形状变了也没人知道」, 所以它自己要有防线.
2. **快照锁定** (数据源是真实样本回放, 零网络): 事件序列快照 (SSE 契约):
   `thinking → tool_call → tool_result → final` 的逐字段载荷; checkpoint 帧
   快照: loop 每轮落盘那两帧的序列化形状 (schema_version / 进度 / 观察值).
3. **确定性 (#61)**: 同一输入 (同一脚本 + 钉死的采样参数 + 注入的时钟) 跑两次,
   事件流与轨迹必须逐字相同 —— 这是所有快照用例成立的前提.

时间 / 编号 / 耗时这类每次不同的值在进快照前先归一 (见 snapshots.py 的两条
投影函数), 否则快照会变成偶发.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import snapshots
from doubles import EventCollector, FakeClock
from mock_llm import MockLLM, make_tool_call, text_response, tool_call_response
from record_llm_samples import TOOLS, USER_QUESTION, make_loop
from snapshots import (
    MS_PLACEHOLDER,
    assert_json_snapshot,
    assert_text_snapshot,
    normalize_checkpoint_record,
    project_events,
)
from trace_assertions import pinned_sampling, trace_of

from CharAgent.agent import AgentLoop, LoopGuard
from CharAgent.checkpoint.memory import InMemoryCheckpointSaver
from CharAgent.checkpoint.serialization import DEFAULT_CODEC
from CharAgent.stream.utils.types import EventType, StreamEvent
from CharAgent.tool import tool


def _echo(message: str) -> str:
    """回显载体: 返回收到的消息."""
    return f"echo:{message}"


def echo_tool() -> Any:
    """本地回显工具 (确定性的纯函数)."""
    return tool(_echo, name="echo")


def make_event(event_type: EventType, **data: Any) -> StreamEvent:
    """造一个事件 (归一投影的用例只需形状, 不必真跑 run)."""
    return StreamEvent(type=event_type, seq=1, data=dict(data))


# ---------------------------------------------------------------------------
# 工具自测
# ---------------------------------------------------------------------------


def test_first_run_writes_snapshot_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次运行: 生成文件 + 失败 (新快照等于没有防线, 得有人看一眼)."""
    monkeypatch.setattr(snapshots, "SNAPSHOT_DIR", tmp_path)

    with pytest.raises(pytest.fail.Exception, match="已生成"):
        assert_json_snapshot("demo", {"a": 1})

    written = json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))
    assert written == {"a": 1}
    assert_json_snapshot("demo", {"a": 1})  # 第二次就过了


def test_changed_snapshot_reports_line_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内容变了: 报逐行差异, 而不是只丢一句「不相等」."""
    monkeypatch.setattr(snapshots, "SNAPSHOT_DIR", tmp_path)
    # 本用例要验「不覆盖就报差异」, 所以显式清掉覆盖开关 (否则整份文件带
    # UPDATE_SNAPSHOTS=1 跑时会互相打架)
    monkeypatch.delenv(snapshots.UPDATE_ENV, raising=False)
    (tmp_path / "demo.json").write_text(
        json.dumps({"a": 1}, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(pytest.fail.Exception) as excinfo:
        assert_json_snapshot("demo", {"a": 2})

    message = str(excinfo.value)
    assert "不一致" in message
    assert "快照 (已提交)" in message and "本次输出" in message
    assert '-  "a": 1' in message and '+  "a": 2' in message


def test_update_env_overwrites_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UPDATE_SNAPSHOTS=1: 覆盖并放行 (有意的形状变更走这条路)."""
    monkeypatch.setattr(snapshots, "SNAPSHOT_DIR", tmp_path)
    (tmp_path / "demo.json").write_text("旧内容\n", encoding="utf-8")
    monkeypatch.setenv(snapshots.UPDATE_ENV, "1")

    assert_text_snapshot("demo", "新内容\n")

    assert (tmp_path / "demo.json").read_text(encoding="utf-8") == "新内容\n"


def test_update_env_zero_is_treated_as_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UPDATE_SNAPSHOTS=0: 视为关 (环境变量被别的工具设成 0 时不该意外覆盖)."""
    monkeypatch.setattr(snapshots, "SNAPSHOT_DIR", tmp_path)
    (tmp_path / "demo.json").write_text("旧内容\n", encoding="utf-8")
    monkeypatch.setenv(snapshots.UPDATE_ENV, "0")

    with pytest.raises(pytest.fail.Exception):
        assert_text_snapshot("demo", "新内容\n")

    assert (tmp_path / "demo.json").read_text(encoding="utf-8") == "旧内容\n"


def test_project_events_swaps_ms_fields_for_placeholder() -> None:
    """耗时不进快照 (换占位符), 其余字段逐字保留."""
    events = [
        make_event(EventType.TOOL_RESULT, tool_name="echo", duration_ms=3.2),
        make_event(EventType.FINAL, content="ok", elapsed_ms=123.4, tokens=42),
    ]

    projected = project_events(events)

    assert projected[0]["duration_ms"] == MS_PLACEHOLDER
    assert projected[1]["elapsed_ms"] == MS_PLACEHOLDER
    assert projected[1]["tokens"] == 42
    assert [item["type"] for item in projected] == ["tool_result", "final"]


def test_normalize_checkpoint_record_swaps_identity_fields() -> None:
    """编号 / 时刻 / 耗时归一; 没有父帧时 parent_id 保持 None (语义要留住)."""
    record = {
        "checkpoint_id": "abc123",
        "thread_id": "thread-1",
        "parent_id": None,
        "created_at": {"__charagent_type__": "datetime", "value": "2026-09-14"},
        "metadata": {"turn_elapsed_ms": 12.5, "tool_names": ["echo"]},
    }

    normalized = normalize_checkpoint_record(record)
    rooted = normalize_checkpoint_record({**record, "parent_id": "abc123"})

    assert normalized["checkpoint_id"] == snapshots.ID_PLACEHOLDER
    assert normalized["created_at"] == snapshots.TIMESTAMP_PLACEHOLDER
    assert normalized["parent_id"] is None
    assert normalized["metadata"]["turn_elapsed_ms"] == MS_PLACEHOLDER
    assert normalized["metadata"]["tool_names"] == ["echo"]
    assert rooted["parent_id"] == snapshots.ID_PLACEHOLDER


# ---------------------------------------------------------------------------
# 快照: 真实样本回放出的形状
# ---------------------------------------------------------------------------


# 固定的循环编号: 不给的话 loop 每次生成一个新 uuid, 会跟着快照落盘并让它变偶发
SNAPSHOT_LOOP_ID = "loop-snapshot"


async def run_replay_loop(
    collector: EventCollector | None = None,
    **loop_kwargs: Any,
) -> MockLLM:
    """用真实样本跑一轮工具链路 (零网络), 返回回放模型 (轨迹从这里取).

    loop 交给录制场景那份构造器 (make_loop): 采样参数与防护设置于是与录制时
    同一份, 快照锁的才是「真实链路会产出什么」.
    """
    model = MockLLM.replay("tool_path")
    loop = make_loop(model, tools=TOOLS, event_sink=collector, **loop_kwargs)
    await loop.run(
        [{"role": "user", "content": USER_QUESTION}], loop_id=SNAPSHOT_LOOP_ID
    )
    return model


async def test_event_stream_snapshot_tool_path() -> None:
    """事件序列快照 (#4/#63): thinking → tool_call → tool_result → final 的载荷.

    事件流是前端渲染的唯一依据, 也是 P1 SSE 契约的底座 —— 它的字段改名 /
    少字段 / 顺序变化都该是一条红色用例.
    """
    collector = EventCollector()
    await run_replay_loop(collector)

    assert collector.terminal_types == ["final"]
    assert_json_snapshot("event_stream_tool_path", project_events(collector.events))


async def test_checkpoint_frames_snapshot_tool_path() -> None:
    """checkpoint 帧快照 (#5/#63): loop 每轮落盘那两帧的序列化形状.

    锁的是「存进存储里的 JSON 长什么样」—— 序列化格式一变, 老快照的读回能力
    与 Redis / Postgres 的兼容性都受影响, 所以逐字钉住.
    """
    saver = InMemoryCheckpointSaver()
    thread_id = "snapshot-thread"
    await run_replay_loop(saver=saver, thread_id=thread_id)

    frames = await saver.list_history(thread_id)
    assert len(frames) == 2  # 两轮各落一帧
    payloads = [
        normalize_checkpoint_record(DEFAULT_CODEC.encode_record(frame))
        for frame in frames
    ]

    assert_json_snapshot("checkpoint_frames_tool_path", payloads)
    await saver.aclose()


async def test_snapshot_replay_trace_matches_expectation() -> None:
    """回放轨迹断言 (与快照互补: 快照锁形状, 轨迹锁行为)."""
    model = await run_replay_loop()

    trace = trace_of(model)
    trace.assert_tool_calls([("query_order", {"order_no": "20260701123456"})])
    trace.assert_tool_result_backfilled("query_order")


# ---------------------------------------------------------------------------
# 确定性 (#61)
# ---------------------------------------------------------------------------


async def test_same_input_same_output_with_pinned_sampling() -> None:
    """同一输入跑两次: 事件流与轨迹逐字相同 (快照用例成立的前提).

    三件套缺一不可: 钉死采样 (temperature=0 + seed) 让模型侧可复现; 注入固定
    时钟让耗时字段确定; 工具是纯函数 (同样输入同样输出).
    """
    first_events = await _deterministic_run()
    second_events = await _deterministic_run()

    assert first_events == second_events


async def _deterministic_run() -> list[dict[str, Any]]:
    """一次确定性 run 的投影结果 (事件流 + 轨迹)."""
    collector = EventCollector()
    model = MockLLM.scripted(
        [
            tool_call_response(
                make_tool_call("echo", '{"message": "hi"}', call_id="call_0")
            ),
            text_response("回显完成"),
        ]
    )
    loop = AgentLoop(
        model=model,
        tools=[echo_tool()],
        guard=LoopGuard(time_source=FakeClock()),
        event_sink=collector,
        **pinned_sampling(),
    )
    await loop.run([{"role": "user", "content": "帮我回显"}])

    trace = trace_of(model)
    trace.assert_pinned_sampling()
    return [*project_events(collector.events), {"trace": trace.describe()}]
