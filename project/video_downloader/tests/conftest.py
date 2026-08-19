"""pytest 共享 fixtures: TestClient + yt-dlp 引擎 mock.

测试 seam: HTTP API 层为主, 通过 TestClient 打 HTTP 断言行为,
引擎调用 (backend.downloader._extract / _download) 被替换为伪数据, 不触网.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from backend import main
from backend import task_manager as tm
from backend.auth import member_manager
from backend.events import bus
from fastapi.testclient import TestClient

# 伪解析结果 (模拟 yt-dlp extract_info 返回)
FAKE_INFO: dict = {
    "id": "test-video",
    "title": "测试视频标题",
    "thumbnail": "https://example.com/cover.jpg",
    "duration": 125.5,
    "extractor_key": "BiliBili",
    "formats": [
        # 360p 含音频 MP4
        {
            "format_id": "18",
            "height": 360,
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
        },
        # 720p 含音频 MP4
        {
            "format_id": "22",
            "height": 720,
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
        },
        # 1080p 无音频 MP4
        {
            "format_id": "137",
            "height": 1080,
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "none",
        },
        # 1080p 含音频 WEBM (同高度应优先含音频)
        {
            "format_id": "999",
            "height": 1080,
            "ext": "webm",
            "vcodec": "vp9",
            "acodec": "opus",
        },
        # 纯音频流 (不构成档位, 应被跳过)
        {
            "format_id": "140",
            "height": None,
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a",
        },
    ],
}


@pytest.fixture(autouse=True)
def clean_state():
    """每个测试前清空内存态存储 (任务/序号/并发计数/会员会话/SSE 订阅).

    保证断言基于干净状态.
    """
    tm.manager._tasks.clear()
    tm.manager._seq = 0
    tm.manager._active = 0
    member_manager._sessions.clear()
    with bus._lock:  # 测试中断时 collector 可能未关闭, 清理订阅防串扰
        bus._subs.clear()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def fake_extract(monkeypatch):
    """替换引擎调用点返回伪元信息, 并记录解析期间任务状态.

    返回 seen 列表: 解析执行中任务应处于 resolving 状态,
    用于断言 pending → resolving → resolved 的流转.
    """

    seen: list[str] = []

    def _fake_extract(url: str) -> dict:
        task = tm.manager.list_tasks()[0]
        seen.append(task.status)
        return FAKE_INFO

    monkeypatch.setattr("backend.downloader._extract", _fake_extract)
    return seen


@pytest.fixture
def fake_download(monkeypatch, tmp_path):
    """替换引擎下载调用: 默认放行, 可阻塞 / 上报进度 / 产出伪文件.

    返回 (call_args, release): call_args 记录 (url, format_id, out_dir) 调用,
    release 为 threading.Event (测试可 clear 阻塞下载以观察中间状态).
    进度 hook 在阻塞前触发, 保证下载期间任务 progress 已更新.
    """

    release = threading.Event()
    release.set()  # 默认放行, 需要观察中间状态的测试手动 clear
    call_args: list[tuple[str, str, Path]] = []

    def _fake_download(url: str, format_id: str, out_dir, progress_hook=None) -> str:
        call_args.append((url, format_id, str(out_dir)))
        if progress_hook:
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 50,
                    "total_bytes": 100,
                }
            )
        release.wait(timeout=5.0)
        path = tmp_path / "output.mp4"
        path.write_bytes(b"fake-video-content")
        if progress_hook:
            progress_hook({"status": "finished"})
        return str(path)

    monkeypatch.setattr("backend.downloader._download", _fake_download)
    yield call_args, release
    release.set()  # 兜底放行, 避免阻塞后台调度线程
