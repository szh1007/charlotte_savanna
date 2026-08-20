"""T12+ AI 总结验收测试 (HTTP seam, mock 字幕/ASR/LLM 三层, ADR-0005).

验收: 创建总结任务 / 字幕快路径与 ASR 回退 / 状态流转 (SSE) / 免费配额 429 /
AI 问答与配额 / 导出 / 过期 410 / 域名校验 / 字幕解析器单元.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from backend import asr, config, llm
from backend import cleaner as cleaner_mod
from backend import subtitle as subtitle_mod
from backend.task_manager import STATUS_EXPIRED
from conftest import FAKE_INFO
from fastapi.testclient import TestClient
from helpers import create_download, find_task, member_headers, wait_until
from sse_client import SseStream

VIDEO_URL = "https://www.bilibili.com/video/av-summary-test"

FAKE_SEGMENTS = [
    {"start": 0.0, "end": 12.5, "text": "大家好, 今天讲视频总结功能"},
    {"start": 12.5, "end": 60.0, "text": "核心流程是字幕优先, 无字幕时转写"},
]
FAKE_SUMMARY = {
    "title": "测试视频标题",
    "overview": "视频总结功能的核心流程",
    "chapters": [
        {
            "start": 0.0,
            "end": 60.0,
            "title": "核心流程",
            "points": ["字幕优先", "无字幕时转写"],
        }
    ],
    "key_points": ["字幕优先", "SenseVoice 转写兜底"],
    "conclusion": "总结流程闭环",
}


@pytest.fixture(autouse=True)
def fake_meta(monkeypatch):
    """替换解析引擎: summary 创建时的轻量元信息解析不触网 (FAKE_INFO 同 conftest).

    create_summary 内部会同步 downloader.resolve 取标题/封面/时长 (task_manager
    create_summary), 不替换则真实解析 yt-dlp 触网且慢; 替换后返回伪元信息.
    """

    monkeypatch.setattr("backend.downloader._extract", lambda url: dict(FAKE_INFO))


@pytest.fixture
def fake_subtitle(monkeypatch):
    """替换字幕快路径: 默认返回伪字幕段; holder["no_subtitle"]=True 模拟无字幕.

    返回 (release, holder): release 为 threading.Event, clear 后转录阻塞
    (观察中间状态); 无字幕回退场景前置 no_subtitle 标志即可 (调度线程
    异步执行, 创建前设置保证 worker 读取时已生效).
    """

    release = threading.Event()
    release.set()
    holder = {"no_subtitle": False}

    def _fake(url: str):
        release.wait(timeout=5.0)
        if holder["no_subtitle"]:
            return None
        return [dict(s) for s in FAKE_SEGMENTS]

    monkeypatch.setattr("backend.subtitle.get_subtitles", _fake)
    yield release, holder
    release.set()  # 兜底放行, 避免阻塞后台调度线程


@pytest.fixture
def fake_asr(monkeypatch):
    """替换 ASR 转写: 记录调用 + 上报一次进度, 可阻塞观察中间状态.

    返回 (calls, gate): gate 为 threading.Event, clear 后转写阻塞
    (观察 transcribing 中间进度); 默认放行. progress_cb 上报转写
    进度 (触发 task_manager 的 10~60% 进度映射, 验收 2 含 ASR 百分比).
    """

    calls: list[str] = []
    gate = threading.Event()
    gate.set()

    def _fake(url: str, progress_cb=None, cancel_event=None):
        calls.append(url)
        if progress_cb is not None:
            progress_cb("transcribe", 0.5, "转写中 1/2 片")  # 先上报进度再阻塞
        gate.wait(timeout=5.0)  # 阻塞点: 观察已上报进度的中间态
        return [dict(s) for s in FAKE_SEGMENTS]

    monkeypatch.setattr("backend.asr.transcribe", _fake)
    return calls, gate


@pytest.fixture
def fake_llm(monkeypatch):
    """替换 LLM: summarize 返回伪结构化总结, ask 返回伪答案."""

    monkeypatch.setattr("backend.llm.summarize", lambda text, meta: dict(FAKE_SUMMARY))
    monkeypatch.setattr("backend.llm.ask", lambda t, s, q: f"回答: {q}")


@pytest.fixture(autouse=True)
def default_ttl(monkeypatch):
    """固定 TTL 为默认契约值 (24h/72h): 断言不依赖运行环境配置 (同 TTL 测试)."""
    monkeypatch.setattr(config, "FREE_DELIVERY_TTL", 24 * 3600)
    monkeypatch.setattr(config, "MEMBER_DELIVERY_TTL", 72 * 3600)


@pytest.fixture
def clock(monkeypatch):
    """可注入时钟 (cleaner._now): 推进时间越过 TTL 断言过期 (同 TTL 测试)."""
    tick = {"now": time.time()}
    monkeypatch.setattr(cleaner_mod, "_now", lambda: tick["now"])

    def sync_now() -> None:
        tick["now"] = time.time()

    tick["sync_now"] = sync_now
    return tick


def create_summary(
    client: TestClient, url: str = VIDEO_URL, client_id: str = "test-client"
) -> int:
    """POST /api/summarize 并断言 200, 返回 task_id."""
    resp = client.post(
        "/api/summarize", json={"url": url}, headers={"X-Client-Id": client_id}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


def wait_completed(client: TestClient, task_id: int, timeout: float = 5.0) -> dict:
    """轮询任务直至 completed (调度线程异步)."""
    assert wait_until(
        lambda: find_task(client, task_id)["status"] == "completed", timeout
    )
    return find_task(client, task_id)


# ----- 创建与全链路 -----


def test_summary_completes_and_exposes_results(client, fake_subtitle, fake_llm) -> None:
    """字幕路径全链路: 创建 → completed → summary / transcript 接口就绪."""
    task_id = create_summary(client)
    wait_completed(client, task_id)

    resp = client.get(f"/api/tasks/{task_id}/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == FAKE_SUMMARY
    assert body["title"] == "测试视频标题"
    assert "expires_at" not in body  # 总结无交付时刻, 过期由 TTL 清理 + 410 表达

    resp = client.get(f"/api/tasks/{task_id}/transcript")
    assert resp.status_code == 200
    data = resp.json()
    assert data["segments"][0]["text"] == "大家好, 今天讲视频总结功能"
    assert "[00:00]" in data["text"]


def test_summary_task_kind_and_list_visibility(client, fake_subtitle, fake_llm) -> None:
    """总结任务以 kind=summary 出现在任务列表, 列表响应不携带大字段."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    task = find_task(client, task_id)
    assert task["kind"] == "summary"
    # 列表轻量契约: transcript / summary 大字段不入列表响应
    assert "transcript" not in task
    assert "summary" not in task


def test_summary_sse_status_flow(client, fake_subtitle, fake_llm) -> None:
    """SSE 事件流含 transcribing → summarizing → completed 全链路."""
    stream = SseStream(client.app, "/api/events")
    stream.wait_headers()
    create_summary(client)  # 创建后事件流由调度线程驱动, 无需持有 task_id

    statuses: list[str] = []
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and "completed" not in statuses:
        frame = stream.next()
        data_line = next(
            line for line in frame.split("\n") if line.startswith("data: ")
        )
        statuses.append(json.loads(data_line.removeprefix("data: "))["status"])
    stream.close()
    stream.join()

    assert "transcribing" in statuses
    assert "summarizing" in statuses
    assert statuses[-1] == "completed"


def test_summary_falls_back_to_asr(client, fake_subtitle, fake_asr, fake_llm) -> None:
    """无字幕时回退 ASR 转写: transcribe 被调用, 结果同样可查."""
    _, holder = fake_subtitle
    holder["no_subtitle"] = True
    task_id = create_summary(client)
    wait_completed(client, task_id)

    calls, _ = fake_asr
    assert calls == [VIDEO_URL]  # 回退路径确实走了 ASR
    resp = client.get(f"/api/tasks/{task_id}/transcript")
    assert resp.status_code == 200


def test_summary_transcribing_progress_reported(
    client, fake_subtitle, fake_asr, fake_llm
) -> None:
    """ASR 转写进度: 中间态 progress 落 10~60 区间 (验收 2 含 ASR 百分比)."""
    _, holder = fake_subtitle
    holder["no_subtitle"] = True
    _, gate = fake_asr
    gate.clear()  # 阻塞 ASR 返回: 观察 transcribing 中间进度

    task_id = create_summary(client)

    def reached() -> bool:
        task = find_task(client, task_id)
        return task["status"] == "transcribing" and task["progress"] >= 10.0

    assert wait_until(reached)
    assert find_task(client, task_id)["progress"] < 60.0
    gate.set()
    wait_completed(client, task_id)


def test_summary_failed_when_asr_errors(client, fake_subtitle, monkeypatch) -> None:
    """ASR 转写失败 → 任务 failed, 原因透传 (TranscriptError, 验收 2)."""
    _, holder = fake_subtitle
    holder["no_subtitle"] = True

    def _boom(url: str, progress_cb=None, cancel_event=None):
        raise asr.TranscriptError("音频下载失败: 测试")

    monkeypatch.setattr("backend.asr.transcribe", _boom)
    task_id = create_summary(client)
    assert wait_until(lambda: find_task(client, task_id)["status"] == "failed")
    assert "音频下载失败" in find_task(client, task_id)["error"]


def test_summary_failed_when_llm_errors(client, fake_subtitle, monkeypatch) -> None:
    """LLM 生成总结失败 → 任务 failed, 原因透传 (LLMError, 验收 2)."""

    def _boom(text: str, meta: dict):
        raise llm.LLMError("LLM 调用失败: 测试超时")

    monkeypatch.setattr("backend.llm.summarize", _boom)
    task_id = create_summary(client)
    assert wait_until(lambda: find_task(client, task_id)["status"] == "failed")
    assert "LLM 调用失败" in find_task(client, task_id)["error"]


def test_summary_domain_validation(client, fake_subtitle, fake_llm) -> None:
    """非 B 站域名创建总结任务 → 422 明确拒绝 (域名白名单共用, ADR-0004)."""
    resp = client.post(
        "/api/summarize",
        json={"url": "https://www.youtube.com/watch?v=abc"},
        headers={"X-Client-Id": "c"},
    )
    assert resp.status_code == 422
    assert "仅支持哔哩哔哩" in resp.json()["detail"]


# ----- 免费配额 -----


def test_free_summary_quota_exceeded(client, fake_subtitle, fake_llm) -> None:
    """免费档每日总结配额: 第 4 次创建 → 429 明确提示, 会员不限."""
    for _ in range(config.FREE_SUMMARY_DAILY):
        create_summary(client)
    resp = client.post(
        "/api/summarize",
        json={"url": VIDEO_URL},
        headers={"X-Client-Id": "test-client"},
    )
    assert resp.status_code == 429
    assert "每日" in resp.json()["detail"]

    # 会员不受配额限制: 同一 client_id 下继续创建不 429
    headers = member_headers(client)
    for _ in range(3):
        resp = client.post(
            "/api/summarize",
            json={"url": VIDEO_URL},
            headers={**headers, "X-Client-Id": "test-client"},
        )
        assert resp.status_code == 200


def test_free_summary_quota_counts_per_client(client, fake_subtitle, fake_llm) -> None:
    """配额按 client_id 独立计数: 不同客户端互不影响."""
    for _ in range(config.FREE_SUMMARY_DAILY):
        create_summary(client, client_id="client-a")
    resp = client.post(
        "/api/summarize",
        json={"url": VIDEO_URL},
        headers={"X-Client-Id": "client-b"},  # 新客户端不受 client-a 配额影响
    )
    assert resp.status_code == 200


# ----- AI 问答 -----


def test_qa_answers_from_video(client, fake_subtitle, fake_llm) -> None:
    """completed 后提问 → 200 答案 (LLM mock 透传)."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    resp = client.post(
        f"/api/tasks/{task_id}/qa",
        json={"question": "核心流程是什么?"},
        headers={"X-Client-Id": "c"},
    )
    assert resp.status_code == 200
    assert resp.json()["answer"] == "回答: 核心流程是什么?"


def test_qa_quota_free_limited(client, fake_subtitle, fake_llm) -> None:
    """免费档每日问答配额: 第 11 次提问 → 429."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    headers = {"X-Client-Id": "qa-client"}
    for _ in range(config.FREE_QA_DAILY):
        resp = client.post(
            f"/api/tasks/{task_id}/qa",
            json={"question": "q"},
            headers=headers,
        )
        assert resp.status_code == 200
    resp = client.post(
        f"/api/tasks/{task_id}/qa", json={"question": "超限"}, headers=headers
    )
    assert resp.status_code == 429


def test_qa_requires_completed_task(client, fake_subtitle, fake_llm) -> None:
    """总结进行中提问 → 409 (字幕阻塞观察中间状态)."""
    release, _ = fake_subtitle
    release.clear()  # 阻塞转录: 任务停留在 transcribing
    task_id = create_summary(client)
    resp = client.post(
        f"/api/tasks/{task_id}/qa",
        json={"question": "q"},
        headers={"X-Client-Id": "c"},
    )
    assert resp.status_code == 409
    release.set()
    wait_completed(client, task_id)


# ----- 导出 -----


def test_export_markdown_contains_structure(client, fake_subtitle, fake_llm) -> None:
    """导出 Markdown: 包含概述 / 章节时间线 / 核心知识点 / 结论."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    resp = client.get(f"/api/tasks/{task_id}/export?format=md")
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    text = resp.text
    assert "# 视频总结" in text
    assert "## 章节时间线" in text
    assert "字幕优先" in text
    assert "## 结论" in text


def test_export_txt_contains_transcript(client, fake_subtitle, fake_llm) -> None:
    """导出 TXT: 转录全文含时间戳标记."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    resp = client.get(f"/api/tasks/{task_id}/export?format=txt")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "[00:00]" in resp.text


def test_export_invalid_format_rejected(client, fake_subtitle, fake_llm) -> None:
    """非法导出格式 → 422 (Query pattern 校验)."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    resp = client.get(f"/api/tasks/{task_id}/export?format=pdf")
    assert resp.status_code == 422


# ----- 状态与过期 -----


def test_summary_endpoints_404_for_unknown(client) -> None:
    """不存在的任务 → 404 (summary / transcript / qa / export 一致)."""
    assert client.get("/api/tasks/999/summary").status_code == 404
    assert client.get("/api/tasks/999/transcript").status_code == 404
    resp = client.post("/api/tasks/999/qa", json={"question": "q"})
    assert resp.status_code == 404
    assert client.get("/api/tasks/999/export").status_code == 404


def test_summary_endpoints_400_for_download_task(client, fake_download) -> None:
    """下载任务调用总结接口 → 400 (kind 校验)."""
    task_id = create_download(client, VIDEO_URL, "22")
    assert wait_until(lambda: find_task(client, task_id)["status"] == "completed")
    resp = client.get(f"/api/tasks/{task_id}/summary")
    assert resp.status_code == 400
    assert "不是总结任务" in resp.json()["detail"]


def test_summary_expired_returns_410(client, fake_subtitle, fake_llm, clock) -> None:
    """TTL 过期后总结接口 → 410 明确提示 (与交付直链同语义)."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    clock["sync_now"]()
    clock["now"] += config.FREE_DELIVERY_TTL + 1  # 越过免费 TTL

    cleaner_mod.cleaner.cleanup_expired()
    assert find_task(client, task_id)["status"] == STATUS_EXPIRED
    resp = client.get(f"/api/tasks/{task_id}/summary")
    assert resp.status_code == 410
    assert "已过期" in resp.json()["detail"]


# ----- 字幕解析器单元 (seam 豁免: 纯函数, 字幕内容经 HTTP 不可控, 同 TTL 先例) -----


@pytest.mark.parametrize(
    ("content", "first_text"),
    [
        # B 站 AI 字幕 JSON
        (
            '{"body": [{"from": 0.0, "to": 5.0, "content": "你好世界"}]}',
            "你好世界",
        ),
        # WEBVTT
        (
            "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n你好世界\n\n",
            "你好世界",
        ),
        # SRT
        (
            "1\n00:00:00,000 --> 00:00:05,000\n你好世界\n\n",
            "你好世界",
        ),
    ],
)
def test_subtitle_parser_variants(content: str, first_text: str) -> None:
    """三种字幕格式 (JSON / VTT / SRT) 统一解析为时间戳段."""
    segments = subtitle_mod._parse_caption(content)
    assert segments[0]["text"] == first_text
    assert segments[0]["start"] == 0.0


def test_subtitle_parser_skips_empty_lines() -> None:
    """空文本段跳过 (不产生空转录)."""
    content = '{"body": [{"from": 0.0, "to": 5.0, "content": "  "},'
    content += '{"from": 5.0, "to": 9.0, "content": "有效内容"}]}'
    segments = subtitle_mod._parse_caption(content)
    assert len(segments) == 1
    assert segments[0]["text"] == "有效内容"
