"""字幕重排 (模型生成增强): LLM 精修 + 流式输出 + 时间戳解析 + 失败降级.

验收: 模型生成字幕在返回前端前经 LLM 按口播风格重塑; LLM 输出每行
"MM:SS ~ MM:SS 文本" (线性均匀时间戳, 缺失/越界由 parse_polished_lines
兜底插值); 重排增量经 /transcript/stream SSE 实时可见; 重排失败降级
使用原始转写 (字幕可用性优先); 重排结果写字幕缓存 (后续命中直接复用).
"""

from __future__ import annotations

import json
import threading

import pytest
from backend import llm, subtitle_cache
from backend.task_manager import manager
from fastapi.testclient import TestClient
from helpers import find_task, wait_until
from sse_client import SseStream

BV_URL = "https://www.bilibili.com/video/BV1xx411c7mD"

FAKE_SEGMENTS = [
    {"start": 0.0, "end": 12.5, "text": "大家好, 今天讲字幕来源切换"},
    {"start": 12.5, "end": 60.0, "text": "官方字幕快路径与模型生成双路径"},
]
# 模拟 LLM 重排输出 (每行带线性均匀时间戳, 首末行与块范围 0~60s 对齐)
POLISHED_TEXT = (
    "00:00 ~ 00:12 大家好, 今天讲字幕来源切换\n00:12 ~ 01:00 官方字幕与模型生成双路径\n"
)


@pytest.fixture
def fake_meta(monkeypatch):
    """替换解析引擎 + mindmap (创建时元信息解析不触网, 同 test_subtitle_source)."""
    from conftest import FAKE_INFO

    monkeypatch.setattr("backend.downloader._extract", lambda url: dict(FAKE_INFO))
    monkeypatch.setattr(
        "backend.llm.generate_mindmap",
        lambda summary, meta: {"title": "测试视频标题", "chapters": []},
    )
    monkeypatch.setattr(
        "backend.llm.summarize_stream",
        lambda text, meta: iter(["# 视频总结\n## 视频概述\nok"]),
    )


@pytest.fixture
def fake_asr(monkeypatch):
    """替换 ASR 转写: 上报进度后返回原始字幕段."""

    def _fake(url: str, progress_cb=None, cancel_event=None):
        if progress_cb is not None:
            progress_cb("transcribe", 1.0, "转写完成")
        return [dict(s) for s in FAKE_SEGMENTS]

    monkeypatch.setattr("backend.asr.transcribe", _fake)


@pytest.fixture
def fake_polish(monkeypatch):
    """替换字幕重排 LLM 流: 记录输入 / 可阻塞 (观察流式中间态) / 注入失败.

    返回 (calls, control): calls 记录 (chunk_text, start, end) 调用;
    control.gate clear 阻塞第二行 (观察已入缓冲的增量), fail 注入 LLMError.
    """

    calls: list[tuple[str, float, float]] = []
    gate = threading.Event()
    gate.set()
    fail = {"error": None}

    def _fake(chunk_text: str, start: float, end: float):
        calls.append((chunk_text, start, end))
        if fail["error"] is not None:
            raise fail["error"]
        yield POLISHED_TEXT.splitlines()[0] + "\n"
        gate.wait(timeout=5.0)
        yield POLISHED_TEXT.splitlines()[1] + "\n"

    monkeypatch.setattr("backend.llm.polish_subtitle_stream", _fake)
    return {"calls": calls, "gate": gate, "fail": fail}


def _sse_frame_parts(frame: str) -> tuple[str, dict]:
    """解析 SseStream 单帧文本 → (event, data dict)."""
    event = "message"
    data = None
    for line in frame.split("\n"):
        if line.startswith("event: "):
            event = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            data = json.loads(line[len("data: ") :])
    assert data is not None, f"帧缺少 data: {frame!r}"
    return event, data


def create_summary(
    client: TestClient,
    url: str = BV_URL,
    subtitle_source: str = "model",
) -> int:
    """创建 model 路径总结任务 (字幕重排主路径), 返回 task_id."""
    resp = client.post(
        "/api/summarize",
        json={"url": url, "subtitle_source": subtitle_source},
        headers={"X-Client-Id": "test-client"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


def wait_transcript_done(client: TestClient, task_id: int) -> dict:
    """等待转录子任务 done, 返回任务快照."""
    assert wait_until(
        lambda: find_task(client, task_id)["subtasks"]["transcript"]["status"] == "done"
    ), "转录子任务未在超时内完成"
    return find_task(client, task_id)


# ---- parse_polished_lines 单测 (时间戳解析与兜底插值) ----


def test_parse_polished_lines_normal() -> None:
    """合法时间戳行: 原样解析为 [{start, end, text}] (秒)."""
    out = llm.parse_polished_lines(POLISHED_TEXT, 0.0, 60.0)
    assert out == [
        {"start": 0.0, "end": 12.0, "text": "大家好, 今天讲字幕来源切换"},
        {"start": 12.0, "end": 60.0, "text": "官方字幕与模型生成双路径"},
    ]


def test_parse_polished_lines_fancy_dash() -> None:
    """兼容全角破折号分隔的时间戳 (LLM 输出变体)."""
    # 测试数据故意使用全角波浪线 (兼容目标字符), 行尾 noqa 抑制 RUF001
    out = llm.parse_polished_lines("00:05 ～ 00:12 大家好\n", 0.0, 60.0)  # noqa: RUF001
    assert out == [{"start": 5.0, "end": 12.0, "text": "大家好"}]


def test_parse_polished_lines_missing_ts_interpolates() -> None:
    """缺失时间戳的行: 按行序号在块范围内线性均匀插值 (兜底不丢文本)."""
    out = llm.parse_polished_lines("第一行没有时间戳\n第二行也没有\n", 0.0, 60.0)
    assert out == [
        {"start": 0.0, "end": 30.0, "text": "第一行没有时间戳"},
        {"start": 30.0, "end": 60.0, "text": "第二行也没有"},
    ]


def test_parse_polished_lines_out_of_range_interpolates() -> None:
    """越界时间戳 (超出块范围): 按插值兜底, 不采信 LLM 乱编时间."""
    out = llm.parse_polished_lines("99:00 ~ 99:10 越界时间戳\n", 0.0, 60.0)
    assert out == [{"start": 0.0, "end": 60.0, "text": "越界时间戳"}]


def test_parse_polished_lines_skips_empty() -> None:
    """空文本行跳过 (LLM 输出可能夹带空行/纯时间戳行)."""
    out = llm.parse_polished_lines("00:00 ~ 00:05 有效行\n\n   \n", 0.0, 10.0)
    assert out == [{"start": 0.0, "end": 5.0, "text": "有效行"}]


# ---- 任务流程: 重排成功 / 降级 / 缓存 / 流式输出 ----


def test_model_path_polishes_transcript(
    client, fake_meta, fake_asr, fake_polish
) -> None:
    """model 路径: ASR 原始段经 LLM 重排 → transcript 为精修结果 + 写缓存."""
    task_id = create_summary(client)
    task = wait_transcript_done(client, task_id)

    # 重排输入 = 拼接的 ASR 文本 + 块时间范围 (首句 0s ~ 末句 60s)
    assert fake_polish["calls"] == [
        ("大家好, 今天讲字幕来源切换\n官方字幕快路径与模型生成双路径", 0.0, 60.0)
    ]
    # 精修完成语义: subtask message 标注
    assert task["subtasks"]["transcript"]["message"] == "字幕精修完成"

    # transcript = 重排结果 (LLM 时间戳), 非原始 ASR 段
    resp = client.get(f"/api/tasks/{task_id}/transcript")
    assert resp.status_code == 200
    segments = resp.json()["segments"]
    assert [s["text"] for s in segments] == [
        "大家好, 今天讲字幕来源切换",
        "官方字幕与模型生成双路径",
    ]
    assert segments[0]["start"] == 0.0 and segments[1]["end"] == 60.0

    # 字幕缓存写入重排结果 (后续命中直接复用, 不再重排)
    cache = subtitle_cache.get(BV_URL)
    assert cache is not None
    assert [s["text"] for s in cache] == [s["text"] for s in segments]


def test_model_path_polish_failure_degrades(
    client, fake_meta, fake_asr, fake_polish
) -> None:
    """重排失败 (LLMError): 降级使用原始 ASR 段, 转录不失败."""
    fake_polish["fail"]["error"] = llm.LLMError("LLM 调用失败: 测试超时")
    task_id = create_summary(client)
    task = wait_transcript_done(client, task_id)

    assert "使用原始转写" in (task["subtasks"]["transcript"]["message"] or "")
    resp = client.get(f"/api/tasks/{task_id}/transcript")
    assert resp.status_code == 200
    segments = resp.json()["segments"]
    # 原始 ASR 段原样保留 (含原始时间戳)
    assert segments == FAKE_SEGMENTS
    # 重排流缓冲已清空 (前端不再展示半成品精修文本)
    snap = manager.transcript_stream_snapshot(task_id)
    assert snap is not None and snap[2] == []


def test_transcript_stream_sse(client, fake_meta, fake_asr, fake_polish) -> None:
    """transcript/stream: 重排增量实时推送 (snapshot → delta → done)."""
    fake_polish["gate"].clear()  # 阻塞第二行, 观察已入缓冲的第一行
    task_id = create_summary(client)

    stream = SseStream(client.app, f"/api/tasks/{task_id}/transcript/stream")
    stream.wait_headers()
    # 首帧 snapshot: 累积全文 (阻塞在第二行, 第一行已入缓冲)
    event, data = _sse_frame_parts(stream.next())
    assert event == "snapshot"
    assert "大家好" in data["text"]
    fake_polish["gate"].set()  # 放行第二行: worker 写缓冲, 端点轮询推 delta
    event, data = _sse_frame_parts(stream.next())
    assert event == "delta"
    assert "官方字幕" in data["text"]
    event, _ = _sse_frame_parts(stream.next())
    assert event == "done"
    stream.close()
    stream.join()

    # 转录结果 = 完整重排文本 (含 LLM 时间戳, segments_to_text 格式)
    wait_transcript_done(client, task_id)
    resp = client.get(f"/api/tasks/{task_id}/transcript")
    text = resp.json()["text"]
    assert "[00:00] 大家好, 今天讲字幕来源切换" in text
    assert "[00:12] 官方字幕与模型生成双路径" in text
