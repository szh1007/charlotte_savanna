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
from backend.quota import quota as daily_quota
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

FAKE_MINDMAP = {
    "title": "测试视频标题",
    "chapters": [
        {
            "start": 0.0,
            "end": 60.0,
            "title": "核心流程",
            "points": ["字幕优先", "无字幕时转写"],
        }
    ],
}


@pytest.fixture(autouse=True)
def fake_meta(monkeypatch):
    """替换解析引擎: summary 创建时的轻量元信息解析不触网 (FAKE_INFO 同 conftest).

    create_summary 内部会同步 downloader.resolve 取标题/封面/时长 (task_manager
    create_summary), 不替换则真实解析 yt-dlp 触网且慢; 替换后返回伪元信息.
    同时 mock generate_mindmap (四子任务之一, 不 mock 则真实调用 LLM API 触网).
    """

    monkeypatch.setattr("backend.downloader._extract", lambda url: dict(FAKE_INFO))
    monkeypatch.setattr(
        "backend.llm.generate_mindmap", lambda text, meta: dict(FAKE_MINDMAP)
    )


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
    """替换 LLM 三层: summarize / generate_mindmap / ask 全部返回伪数据."""

    monkeypatch.setattr("backend.llm.summarize", lambda text, meta: dict(FAKE_SUMMARY))
    monkeypatch.setattr(
        "backend.llm.generate_mindmap", lambda text, meta: dict(FAKE_MINDMAP)
    )
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
    """SSE 事件流: queued → running → completed, 事件携带 subtasks 且转录先行."""
    stream = SseStream(client.app, "/api/events")
    stream.wait_headers()
    create_summary(client)  # 创建后事件流由调度线程驱动, 无需持有 task_id

    statuses: list[str] = []
    seen_transcript_done = False
    seen_summary_done = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and "completed" not in statuses:
        frame = stream.next()
        data_line = next(
            line for line in frame.split("\n") if line.startswith("data: ")
        )
        evt = json.loads(data_line.removeprefix("data: "))
        statuses.append(evt["status"])
        subs = evt.get("subtasks") or {}
        # 四子任务独立状态随事件携带 (前端四 tab 数据源)
        assert set(subs) == {"transcript", "summary", "mindmap", "qa"}
        if subs.get("transcript", {}).get("status") == "done":
            seen_transcript_done = True
        if subs.get("summary", {}).get("status") == "done":
            seen_summary_done = True
    stream.close()
    stream.join()

    assert "running" in statuses
    assert statuses[-1] == "completed"
    # 依赖序: 转录先完成, 总结 (依赖转录) 后完成
    assert seen_transcript_done
    assert seen_summary_done


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
    """ASR 转写进度: 转录子任务 progress 落 10~100 区间 (四 tab 进度条数据源)."""
    _, holder = fake_subtitle
    holder["no_subtitle"] = True
    _, gate = fake_asr
    gate.clear()  # 阻塞 ASR 返回: 观察转写中间进度

    task_id = create_summary(client)

    def reached() -> bool:
        task = find_task(client, task_id)
        sub = task["subtasks"]["transcript"]
        return sub["status"] == "running" and sub["progress"] >= 10.0

    assert wait_until(reached)
    assert find_task(client, task_id)["subtasks"]["transcript"]["progress"] < 100.0
    gate.set()
    wait_completed(client, task_id)


def test_summary_failed_when_asr_errors(client, fake_subtitle, monkeypatch) -> None:
    """转录失败 → 转录子任务 failed, 依赖它的子任务 blocked, 任务 failed."""
    _, holder = fake_subtitle
    holder["no_subtitle"] = True

    def _boom(url: str, progress_cb=None, cancel_event=None):
        raise asr.TranscriptError("音频下载失败: 测试")

    monkeypatch.setattr("backend.asr.transcribe", _boom)
    task_id = create_summary(client)
    assert wait_until(lambda: find_task(client, task_id)["status"] == "failed")
    task = find_task(client, task_id)
    assert "音频下载失败" in task["error"]
    # 子任务语义: 转录自身失败, 依赖转录的其余子任务 blocked (等重试恢复)
    subs = task["subtasks"]
    assert subs["transcript"]["status"] == "failed"
    assert subs["summary"]["status"] == "blocked"
    assert subs["mindmap"]["status"] == "blocked"
    assert subs["qa"]["status"] == "blocked"
    # 转录不可访问 (子任务未完成)
    assert client.get(f"/api/tasks/{task_id}/transcript").status_code == 409


def test_summary_failed_when_llm_errors(client, fake_subtitle, monkeypatch) -> None:
    """总结子任务失败 → 任务 completed (部分完成), 转录仍可访问, 重试后可恢复.

    失败只影响自身: 转录/导图子任务保持 done, 任务级不再整体 failed
    (部分完成语义, 失败子任务经重试接口补齐).
    """
    boom = {"active": True}

    def _boom(text: str, meta: dict):
        if boom["active"]:
            raise llm.LLMError("LLM 调用失败: 测试超时")
        return dict(FAKE_SUMMARY)

    monkeypatch.setattr("backend.llm.summarize", _boom)
    task_id = create_summary(client)
    assert wait_until(lambda: find_task(client, task_id)["status"] == "completed")
    task = find_task(client, task_id)
    subs = task["subtasks"]
    assert subs["summary"]["status"] == "failed"
    assert "LLM 调用失败" in subs["summary"]["error"]
    assert subs["transcript"]["status"] == "done"  # 转录不受影响
    assert subs["mindmap"]["status"] == "done"
    # 转录仍可访问 (转录先完成即先查看, 不依赖总结成功)
    assert client.get(f"/api/tasks/{task_id}/transcript").status_code == 200
    # 总结接口 409 (子任务未完成)
    assert client.get(f"/api/tasks/{task_id}/summary").status_code == 409

    # 重试总结子任务: 只重跑 summary, 恢复后接口可用
    boom["active"] = False
    resp = client.post(f"/api/tasks/{task_id}/retry", json={"subtask": "summary"})
    assert resp.status_code == 200
    assert wait_until(
        lambda: find_task(client, task_id)["subtasks"]["summary"]["status"] == "done"
    )
    assert client.get(f"/api/tasks/{task_id}/summary").status_code == 200


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
    for i in range(config.FREE_SUMMARY_DAILY):
        # 不同 url 规避幂等命中 (同 url 活跃任务不重复创建/不扣配额, 幂等验收另测)
        create_summary(client, url=f"{VIDEO_URL}?free={i}")
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
    for i in range(config.FREE_SUMMARY_DAILY):
        # 不同 url 规避幂等命中 (同 url 活跃任务不重复创建/不扣配额, 幂等验收另测)
        create_summary(client, client_id="client-a", url=f"{VIDEO_URL}?a={i}")
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


def test_qa_requires_ready_context(client, fake_subtitle, fake_llm) -> None:
    """问答上下文未就绪 (转录阻塞中, qa 子任务 pending) 提问 → 409."""
    release, _ = fake_subtitle
    release.clear()  # 阻塞转录: qa 依赖 (转录+总结) 未就绪
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


# ----- 四标签独立进度与分组字段 (UI 改造: 任务按视频分组 + 四 tab 进度条) -----


def test_task_out_carries_grouping_fields(client, fake_subtitle, fake_llm) -> None:
    """任务列表携带分组/元信息/子任务字段: url 分组键 + 四子任务状态, 大字段不入列表."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    task = find_task(client, task_id)
    assert task["url"] == VIDEO_URL  # 分组键 (同视频多清晰度合并一行)
    # 视频元信息 (卡片展示: up主/播放量/简介)
    assert task["uploader"] == "测试UP主"
    assert task["view_count"] == 123456
    assert task["description"] == "测试视频简介"
    # 四子任务独立状态 (前端四 tab 数据源), 完成后全部 done
    subs = task["subtasks"]
    assert set(subs) == {"transcript", "summary", "mindmap", "qa"}
    assert all(subs[n]["status"] == "done" for n in subs)
    # 平铺镜像进度 (旧契约兼容)
    assert task["transcript_progress"] == 100.0
    assert task["summary_progress"] == 100.0
    # 轻量契约仍在: transcript / summary / mindmap 大字段不入列表响应
    assert "transcript" not in task
    assert "summary" not in task
    assert "mindmap" not in task


def test_transcript_progress_reported_during_asr(
    client, fake_subtitle, fake_asr, fake_llm
) -> None:
    """
    ASR 转写中间态: 转录子任务 progress 10~99,
    平铺镜像与加权任务进度同步 (旧契约兼容).
    """
    _, holder = fake_subtitle
    holder["no_subtitle"] = True
    _, gate = fake_asr
    gate.clear()  # 阻塞 ASR 返回: 观察转写中间进度

    task_id = create_summary(client)

    def reached() -> bool:
        task = find_task(client, task_id)
        sub = task["subtasks"]["transcript"]
        return task["status"] == "running" and sub["progress"] >= 10.0

    assert wait_until(reached)
    task = find_task(client, task_id)
    assert task["subtasks"]["transcript"]["progress"] < 100.0
    assert task["summary_progress"] == 0.0  # 总结未启动, 镜像进度为 0
    # 平铺镜像 = 子任务进度; 任务级 progress = 转录加权 (40%), 其余子任务 0
    assert task["transcript_progress"] == task["subtasks"]["transcript"]["progress"]
    assert 4.0 <= task["progress"] < 40.0
    gate.set()
    wait_completed(client, task_id)
    done = find_task(client, task_id)
    assert done["transcript_progress"] == 100.0
    assert done["summary_progress"] == 100.0


def test_sse_event_carries_tab_progress(client, fake_subtitle, fake_llm) -> None:
    """SSE 事件携带分组键 (source_url) 与四子任务状态 (前端四 tab 数据源)."""
    stream = SseStream(client.app, "/api/events")
    stream.wait_headers()
    create_summary(client)

    seen_source = False
    seen_all_done = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not (seen_source and seen_all_done):
        frame = stream.next()
        data_line = next(
            line for line in frame.split("\n") if line.startswith("data: ")
        )
        evt = json.loads(data_line.removeprefix("data: "))
        assert "source_url" in evt
        assert "transcript_progress" in evt
        assert "summary_progress" in evt
        subs = evt.get("subtasks") or {}
        assert set(subs) == {"transcript", "summary", "mindmap", "qa"}
        if evt["source_url"] == VIDEO_URL:
            seen_source = True
        if evt["status"] == "completed" and all(
            s["status"] == "done" for s in subs.values()
        ):
            seen_all_done = True
    stream.close()
    stream.join()

    assert seen_source
    assert seen_all_done


# ----- 转录先行 / 思维导图 / 字幕导出 (四子任务独立语义, ADR-0005) -----


def test_transcript_accessible_before_summary_ready(
    client, fake_subtitle, monkeypatch
) -> None:
    """转录先行: 总结生成中 (LLM 阻塞) 转录已可查看, 总结接口 409."""
    gate = threading.Event()
    gate.clear()  # 阻塞 LLM 总结: 观察转录先完成

    def _slow(text: str, meta: dict) -> dict:
        gate.wait(timeout=5.0)
        return dict(FAKE_SUMMARY)

    monkeypatch.setattr("backend.llm.summarize", _slow)
    task_id = create_summary(client)
    assert wait_until(
        lambda: find_task(client, task_id)["subtasks"]["transcript"]["status"] == "done"
    )
    # 转录就绪即可查看, 无需等总结完成
    assert client.get(f"/api/tasks/{task_id}/transcript").status_code == 200
    assert client.get(f"/api/tasks/{task_id}/summary").status_code == 409
    gate.set()
    wait_completed(client, task_id)


def test_mindmap_endpoint_returns_structure(client, fake_subtitle, fake_llm) -> None:
    """mindmap 接口: 导图子任务完成后返回独立生成的结构 (与总结解耦)."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    resp = client.get(f"/api/tasks/{task_id}/mindmap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mindmap"] == FAKE_MINDMAP
    assert body["title"] == "测试视频标题"


def test_mindmap_409_while_transcribing(client, fake_subtitle, fake_llm) -> None:
    """导图子任务未完成 (转录阻塞中) → 409."""
    release, _ = fake_subtitle
    release.clear()
    task_id = create_summary(client)
    assert client.get(f"/api/tasks/{task_id}/mindmap").status_code == 409
    release.set()


def test_export_srt_timeline_format(client, fake_subtitle, fake_llm) -> None:
    """导出 SRT: 序号 + 逗号毫秒时间轴 (00:00:00,000)."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    resp = client.get(f"/api/tasks/{task_id}/export?format=srt")
    assert resp.status_code == 200
    assert resp.text.startswith("1\n")  # 序号
    assert "00:00:00,000 --> 00:00:12,500" in resp.text  # 首段 0.0s ~ 12.5s
    assert "大家好, 今天讲视频总结功能" in resp.text


def test_export_vtt_webvtt_format(client, fake_subtitle, fake_llm) -> None:
    """导出 VTT: WEBVTT 头 + 点号毫秒时间轴 (00:00:00.000)."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    resp = client.get(f"/api/tasks/{task_id}/export?format=vtt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/vtt")
    assert resp.text.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:12.500" in resp.text


# ----- 子任务重试 (失败/阻塞独立恢复, 不扣配额) -----


def test_retry_transcript_unblocks_dependents(
    client, fake_subtitle, fake_llm, monkeypatch
) -> None:
    """转录重试: blocked 的依赖子任务自动解锁, 全链路恢复 completed."""
    _, holder = fake_subtitle
    holder["no_subtitle"] = True
    boom = {"active": True}

    def _boom(url: str, progress_cb=None, cancel_event=None):
        if boom["active"]:
            raise asr.TranscriptError("音频下载失败: 测试")
        return [dict(s) for s in FAKE_SEGMENTS]

    monkeypatch.setattr("backend.asr.transcribe", _boom)
    task_id = create_summary(client)
    assert wait_until(lambda: find_task(client, task_id)["status"] == "failed")
    subs = find_task(client, task_id)["subtasks"]
    assert subs["transcript"]["status"] == "failed"
    assert all(subs[n]["status"] == "blocked" for n in ("summary", "mindmap", "qa"))

    boom["active"] = False
    resp = client.post(f"/api/tasks/{task_id}/retry", json={"subtask": "transcript"})
    assert resp.status_code == 200
    wait_completed(client, task_id)
    done = find_task(client, task_id)
    assert all(done["subtasks"][n]["status"] == "done" for n in done["subtasks"])
    # 转录恢复后查看/导出/提问全部可用
    assert client.get(f"/api/tasks/{task_id}/transcript").status_code == 200
    assert client.get(f"/api/tasks/{task_id}/summary").status_code == 200


def test_retry_only_reruns_failed_subtask(client, fake_subtitle, monkeypatch) -> None:
    """重试只重跑失败子任务: 字幕获取调用计数保持 1 (done 子任务结果保留)."""
    calls: list[str] = []

    def _counting(url: str):
        calls.append(url)
        return [dict(s) for s in FAKE_SEGMENTS]

    boom = {"active": True}

    def _boom(text: str, meta: dict):
        if boom["active"]:
            raise llm.LLMError("生成失败")
        return dict(FAKE_SUMMARY)

    monkeypatch.setattr("backend.subtitle.get_subtitles", _counting)
    monkeypatch.setattr("backend.llm.summarize", _boom)
    task_id = create_summary(client)
    assert wait_until(lambda: find_task(client, task_id)["status"] == "completed")
    assert calls == [VIDEO_URL]  # 首次转录只取一次字幕

    boom["active"] = False
    resp = client.post(f"/api/tasks/{task_id}/retry", json={"subtask": "summary"})
    assert resp.status_code == 200
    assert wait_until(
        lambda: find_task(client, task_id)["subtasks"]["summary"]["status"] == "done"
    )
    assert calls == [VIDEO_URL]  # 重试 summary 不重跑转录 (字幕未再次获取)


def test_retry_does_not_charge_quota(client, fake_subtitle, monkeypatch) -> None:
    """重试不扣配额: 修复性操作, 免费配额计数不因重试增加."""
    monkeypatch.setattr(
        "backend.llm.summarize",
        lambda t, m: (_ for _ in ()).throw(llm.LLMError("生成失败")),
    )
    task_id = create_summary(client)  # 创建扣 1 次配额
    assert wait_until(lambda: find_task(client, task_id)["status"] == "completed")
    client_id = "test-client"
    assert daily_quota._usages[client_id].summary_count == 1

    for _ in range(2):  # 连续重试 (均失败) 不增加配额计数
        resp = client.post(f"/api/tasks/{task_id}/retry", json={"subtask": "summary"})
        assert resp.status_code == 200
        assert wait_until(
            lambda: find_task(client, task_id)["subtasks"]["summary"]["status"]
            == "failed"
        )
    assert daily_quota._usages[client_id].summary_count == 1


def test_retry_rejects_invalid_subtask(client, fake_subtitle, fake_llm) -> None:
    """无效子任务名 → 422 (body pattern 校验)."""
    task_id = create_summary(client)
    resp = client.post(f"/api/tasks/{task_id}/retry", json={"subtask": "invalid"})
    assert resp.status_code == 422


def test_retry_rejects_non_failed_state(client, fake_subtitle, fake_llm) -> None:
    """子任务未失败时重试 → 409 明确提示 (杜绝双 worker)."""
    task_id = create_summary(client)
    wait_completed(client, task_id)
    resp = client.post(f"/api/tasks/{task_id}/retry", json={"subtask": "summary"})
    assert resp.status_code == 409
    assert "未失败" in resp.json()["detail"]


def test_retry_rejects_download_task(client, fake_download) -> None:
    """下载任务重试子任务 → 400 (kind 校验)."""
    task_id = create_download(client, VIDEO_URL, "22")
    assert wait_until(lambda: find_task(client, task_id)["status"] == "completed")
    resp = client.post(f"/api/tasks/{task_id}/retry", json={"subtask": "summary"})
    assert resp.status_code == 400
    assert "不是总结任务" in resp.json()["detail"]


def test_retry_404_for_unknown_task(client) -> None:
    """不存在的任务重试 → 404."""
    resp = client.post("/api/tasks/999/retry", json={"subtask": "summary"})
    assert resp.status_code == 404


# ----- 创建幂等 (按钮防重复点击, 刷新后仍收敛) -----


def test_summarize_idempotent_for_active_task(client, fake_subtitle, fake_llm) -> None:
    """幂等: 同 url 活跃总结任务重复创建 → 相同 task_id, 配额只扣一次."""
    release, _ = fake_subtitle
    release.clear()  # 阻塞转录: 任务停留在 running (活跃)
    first = create_summary(client)
    second = create_summary(client)  # 幂等命中: 不新建、不扣配额
    assert second == first
    assert daily_quota._usages["test-client"].summary_count == 1
    release.set()
    wait_completed(client, first)
