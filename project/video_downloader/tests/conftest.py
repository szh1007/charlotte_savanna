"""pytest 共享 fixtures: TestClient + yt-dlp 引擎 mock.

测试 seam: HTTP API 层为主, 通过 TestClient 打 HTTP 断言行为,
引擎调用 (backend.downloader._extract / _download) 被替换为伪数据, 不触网.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from backend import config, main
from backend import model_downloader as model_dl
from backend import task_manager as tm
from backend.auth import member_manager
from backend.cleaner import cleaner as delivery_cleaner
from backend.events import bus
from backend.quota import quota as daily_quota
from fastapi.testclient import TestClient

# 测试统一会员密钥 (真实密钥仅存在于 .env, 不入库)
MEMBER_KEY = "test-member-key-2026"

# 伪解析结果 (模拟 yt-dlp extract_info 返回)
FAKE_INFO: dict = {
    "id": "test-video",
    "title": "测试视频标题",
    "thumbnail": "https://example.com/cover.jpg",
    "duration": 125.5,
    "extractor_key": "BiliBili",
    "uploader": "测试UP主",
    "view_count": 123456,
    "description": "测试视频简介",
    "formats": [
        # 360p 含音频 MP4
        {
            "format_id": "18",
            "height": 360,
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
            "filesize": 1048576,  # 1 MB
        },
        # 720p 含音频 MP4
        {
            "format_id": "22",
            "height": 720,
            "ext": "mp4",
            "vcodec": "avc1",
            "acodec": "mp4a",
        },
        # 1080p 无音频 MP4 (DASH video-only, has_audio=False → 下载时合并音频流)
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

    保证断言基于干净状态. TTL 清理线程先停止并 join (周期测试缩短过间隔),
    再清空任务存储, 防止残留线程扫描时更新已清空的任务抛 KeyError.
    """
    delivery_cleaner.stop()
    if delivery_cleaner._thread is not None:
        delivery_cleaner._thread.join(timeout=1.0)
    # 停止并 join 任务调度线程 + 已派发 worker: 跨测试存活的调度线程会
    # 继续派发任务, 残留 worker 的子任务线程 (如取消任务中轮询等待的转录
    # 线程) 可能在上一测试 monkeypatch 撤销后运行 (读到真实 MODELS_DIR /
    # 真实 asr), 导致真实下载与状态串扰 (ADR-0006 测试稳定性). join 在
    # _active 重置前: 残留 worker 收尾的槽位释放减到旧值, 重置后归零
    tm.manager.stop_scheduler()
    if tm.manager._scheduler is not None:
        tm.manager._scheduler.join(timeout=1.0)
    tm.manager.join_workers(timeout=1.0)
    tm.manager._tasks.clear()
    tm.manager._seq = 0
    tm.manager._active = {False: 0, True: 0}  # 免费/会员并发槽占用 (T05 按身份拆分)
    member_manager._sessions.clear()
    daily_quota._usages.clear()  # 每日配额计数 (ADR-0005): 测试间隔离
    with bus._lock:  # 测试中断时 collector 可能未关闭, 清理订阅防串扰
        bus._subs.clear()
    # 模型下载状态机重置 (ADR-0006): 残留的 downloading/ready 状态不串扰
    # (线程句柄保留: is_alive() 判定防双线程; 旧线程收尾的状态覆盖无害,
    # 下次 status()/download() 以文件为准重新同步)
    model_dl.model_downloader._status = model_dl.STATUS_MISSING
    model_dl.model_downloader._progress = 0.0
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def model_assets(monkeypatch, tmp_path):
    """模型资产目录隔离 (ADR-0006): MODELS_DIR/SUBTITLES_DIR 指向 tmp 目录.

    预置 ready 模型文件 (config.yaml + model.pt): 现有 ASR 回退路径测试
    依赖模型就绪才能走到转写 (缺失会 failed「请先下载模型」), ready 以
    真实文件判定, 无需 mock is_ready. 转写主模型 + VAD 模型 (fsmn-vad,
    双模型统一管理) 均预置; 模型下载/状态/缓存测试按需删文件或注入伪
    下载引擎驱动状态流转; SUBTITLES_DIR 落在 MODELS_DIR 下 (与生产布局
    一致, 缓存与模型本体目录分离).
    """
    models_dir = tmp_path / "models"
    subtitles_dir = models_dir / "subtitles"

    def _make_ready(dirname: str) -> Path:
        d = models_dir / dirname
        d.mkdir(parents=True)
        (d / "config.yaml").write_text("model: fake\n", encoding="utf-8")
        (d / "model.pt").write_bytes(b"fake-model-weights")
        return d

    model_dir = _make_ready(config.MODEL_DIR_NAME)
    vad_model_dir = _make_ready(config.MODEL_VAD_DIR_NAME)
    monkeypatch.setattr(config, "MODELS_DIR", models_dir)
    monkeypatch.setattr(config, "SUBTITLES_DIR", subtitles_dir)
    return {
        "models_dir": models_dir,
        "subtitles_dir": subtitles_dir,
        "model_dir": model_dir,
        "vad_model_dir": vad_model_dir,
    }


@pytest.fixture(autouse=True)
def member_key(monkeypatch):
    """会员相关测试统一使用已知密钥 (真实密钥仅存在于 .env, 不入库)."""
    monkeypatch.setattr(config, "MEMBER_KEY", MEMBER_KEY)


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
    call_args: list[tuple[str, str, str, bool]] = []

    def _fake_download(
        url: str, format_id: str, out_dir, progress_hook=None, merge_audio: bool = False
    ) -> str:
        call_args.append((url, format_id, str(out_dir), merge_audio))
        if progress_hook:
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 50,
                    "total_bytes": 100,
                }
            )
        release.wait(timeout=5.0)
        # 文件名派生自档位, 贴近生产 outtmpl (同测试多任务产出独立文件, T06)
        path = tmp_path / f"{format_id}.mp4"
        path.write_bytes(b"fake-video-content")
        if progress_hook:
            progress_hook({"status": "finished"})
        return str(path)

    monkeypatch.setattr("backend.downloader._download", _fake_download)
    yield call_args, release
    release.set()  # 兜底放行, 避免阻塞后台调度线程
