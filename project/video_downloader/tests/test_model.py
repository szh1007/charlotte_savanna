"""T13 模型状态与下载 (ADR-0006): 状态机 / 幂等触发 / 失败重试 / SSE 广播.

验收: GET /api/model/status → {status, progress, has_official_subtitle};
POST /api/model/download 幂等 (ready/downloading 不重复启动, missing 启动);
下载失败回 missing 可重试; 进度经 SSE model-update 事件广播 (同流, 不受
task_id 过滤); ready 以文件为准 (config.yaml + model.pt 均存在).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from backend import config
from backend.model_downloader import (
    STATUS_DOWNLOADING,
    STATUS_MISSING,
    STATUS_READY,
)
from sse_client import SseStream


@pytest.fixture
def fake_model_download(monkeypatch):
    """替换模型下载引擎调用点: 落地就绪文件 + 回调进度, 可阻塞/注入失败.

    返回 (calls, control): calls 记录触发次数 (断言幂等不重复触发);
    control.gate clear 阻塞下载 (观察中间态), control.fail 注入引擎异常
    (驱动失败回 missing 路径). 引擎按总字节 100 回调两段进度.
    """

    calls: list[str] = []
    gate = threading.Event()
    gate.set()
    fail = {"error": None}

    def _fake(model_id: str, local_dir, callback=None) -> None:
        calls.append(model_id)
        if fail["error"] is not None:
            raise fail["error"]
        gate.wait(timeout=5.0)
        d = Path(local_dir)
        if callback is not None:
            callback("model.pt", 50, 100)  # 中途进度: 观察 downloading 中间态
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.yaml").write_text("model: fake\n", encoding="utf-8")
        (d / "model.pt").write_bytes(b"fake-model-weights")
        if callback is not None:
            callback("model.pt", 100, 100)  # 完成进度

    monkeypatch.setattr("backend.model_downloader._snapshot_download", _fake)
    return calls, {"gate": gate, "fail": fail}


def _wipe_model_files(model_assets: dict) -> None:
    """删除模型就绪文件 (config.yaml + model.pt) → 状态回 missing."""
    for name in ("config.yaml", "model.pt"):
        (model_assets["model_dir"] / name).unlink(missing_ok=True)


def test_model_status_ready_with_files(client, model_assets) -> None:
    """就绪文件齐全 (fixture 预置): status → ready + progress 100."""
    resp = client.get("/api/model/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == STATUS_READY
    assert body["progress"] == 100.0
    assert "has_official_subtitle" in body


def test_model_status_missing_without_files(client, model_assets) -> None:
    """文件缺失: status → missing (ready 判定以文件为准, 不依赖内存残留)."""
    _wipe_model_files(model_assets)
    resp = client.get("/api/model/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == STATUS_MISSING
    assert body["progress"] == 0.0


def test_model_status_ready_tracks_file_changes(client, model_assets) -> None:
    """文件变化即时反映: 删权重文件 → missing, 补齐 → ready (动态同步)."""
    (model_assets["model_dir"] / "model.pt").unlink()
    assert client.get("/api/model/status").json()["status"] == STATUS_MISSING
    (model_assets["model_dir"] / "model.pt").write_bytes(b"recreated")
    assert client.get("/api/model/status").json()["status"] == STATUS_READY


def test_model_has_official_subtitle_reflects_cookie(client, monkeypatch) -> None:
    """has_official_subtitle = 是否配置 BILI_COOKIE (官方字幕能力提示).

    显式固定 cookie 值: 不依赖本地 .env 是否配置 (测试可移植).
    """
    monkeypatch.setattr(config, "BILI_COOKIE", "")
    assert client.get("/api/model/status").json()["has_official_subtitle"] is False
    monkeypatch.setattr(config, "BILI_COOKIE", "SESSDATA=fake")
    assert client.get("/api/model/status").json()["has_official_subtitle"] is True


def test_model_download_idempotent_when_ready(
    client, model_assets, fake_model_download
) -> None:
    """幂等: ready 时触发下载不动作 (引擎零调用, 状态不变)."""
    calls, _ = fake_model_download
    resp = client.post("/api/model/download")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == STATUS_READY
    assert body["progress"] == 100.0
    assert calls == []


def test_model_download_starts_when_missing(
    client, model_assets, fake_model_download
) -> None:
    """缺失时触发下载: downloading → ready, 引擎落地文件 + 进度回调."""
    _wipe_model_files(model_assets)
    calls, _ = fake_model_download
    resp = client.post("/api/model/download")
    assert resp.status_code == 200
    assert resp.json()["status"] == STATUS_DOWNLOADING
    assert calls == [config.ASR_MODEL]

    def ready() -> bool:
        return client.get("/api/model/status").json()["status"] == STATUS_READY

    from helpers import wait_until

    assert wait_until(ready)
    assert client.get("/api/model/status").json()["progress"] == 100.0


def test_model_download_idempotent_while_downloading(
    client, model_assets, fake_model_download
) -> None:
    """幂等: 下载中重复触发返回当前进度, 不重复启动线程 (引擎单次调用)."""
    _wipe_model_files(model_assets)
    calls, control = fake_model_download
    control["gate"].clear()  # 阻塞下载, 观察下载中中间态
    first = client.post("/api/model/download").json()
    assert first["status"] == STATUS_DOWNLOADING
    second = client.post("/api/model/download").json()
    assert second["status"] == STATUS_DOWNLOADING
    assert calls == [config.ASR_MODEL]  # 仅首次触发引擎
    control["gate"].set()  # 放行, 后台线程落地文件后置 ready

    from helpers import wait_until

    assert wait_until(
        lambda: client.get("/api/model/status").json()["status"] == STATUS_READY
    )


def test_model_download_failure_returns_missing_retryable(
    client, model_assets, fake_model_download
) -> None:
    """失败回 missing 可重试: 引擎异常 → missing + progress 0, 重试成功."""
    _wipe_model_files(model_assets)
    calls, control = fake_model_download
    control["fail"]["error"] = RuntimeError("网络中断")
    resp = client.post("/api/model/download")
    assert resp.status_code == 200
    assert resp.json()["status"] == STATUS_DOWNLOADING  # 先进入下载中

    from helpers import wait_until

    assert wait_until(
        lambda: client.get("/api/model/status").json()["status"] == STATUS_MISSING
    )
    control["fail"]["error"] = None  # 故障恢复: 重试
    resp = client.post("/api/model/download")
    assert resp.json()["status"] == STATUS_DOWNLOADING
    assert wait_until(
        lambda: client.get("/api/model/status").json()["status"] == STATUS_READY
    )
    assert len(calls) == 2


def test_model_download_file_incomplete_falls_back_missing(
    client, model_assets, monkeypatch
) -> None:
    """引擎成功但文件不完整 (缺权重): 视为下载失败回 missing (文件为唯一事实源)."""

    def _incomplete(model_id: str, local_dir, callback=None) -> None:
        # 只落地 config.yaml, 不写 model.pt (模拟下载截断)
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        (Path(local_dir) / "config.yaml").write_text("model: fake\n", encoding="utf-8")

    monkeypatch.setattr("backend.model_downloader._snapshot_download", _incomplete)
    _wipe_model_files(model_assets)
    resp = client.post("/api/model/download")
    assert resp.status_code == 200

    from helpers import wait_until

    assert wait_until(
        lambda: client.get("/api/model/status").json()["status"] == STATUS_MISSING
    )


def test_model_update_sse_broadcast_all_subscribers(
    client, model_assets, fake_model_download
) -> None:
    """model-update 同流广播: 订阅 task_ids 过滤的任务流也能收到模型事件.

    验收: SSE 新增 model-update 事件 (同流, 不受 task_id 过滤). 初始快照
    含模型状态帧 (断线重连恢复), 下载期间收到 downloading 进度帧.
    """
    stream = SseStream(client.app, "/api/events?task_ids=999")
    stream.wait_headers()
    # 初始快照: 即使订阅者只关注 task 999 (不存在), 也收到模型状态帧
    frames = []
    first = stream.next()
    assert "model-update" in first

    _wipe_model_files(model_assets)
    control = fake_model_download[1]
    control["gate"].clear()
    client.post("/api/model/download")

    from helpers import wait_until

    # 下载中广播 downloading 进度帧 (gate 阻塞保证下载未完成, 事件已发)
    assert wait_until(lambda: _has_model_frame(stream, frames, STATUS_DOWNLOADING))
    control["gate"].set()
    stream.close()  # 模拟客户端断开: 释放订阅, 防驱动线程挂起阻塞解释器退出
    stream.join()


def _has_model_frame(stream, frames: list[str], status: str) -> bool:
    """从事件流读取帧, 收集 model-update 事件, 命中指定状态返回 True."""
    import json
    import time

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        frame = stream.next()
        if "model-update" not in frame:
            continue
        data = json.loads(frame.split("data: ", 1)[1])
        frames.append(frame)
        if data.get("status") == status:
            return True
    return False
