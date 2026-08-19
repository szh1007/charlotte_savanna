"""清除记录验收测试: DELETE /api/tasks/{id} + POST /api/tasks/purge-unfinished.

场景: 未过期任务清除 (删文件 + 移除任务) / 过期任务直接清除 / 进行中任务
取消 (排队/下载中) / 批量清除全部未完成记录 (含失败) / 孤儿文件清理 /
任务序列化新字段 (format_id/expires_at).
清理逻辑直接调用 (HTTP seam, 引擎 mock, 可注入时钟), 与 test_ttl_cleanup 同模式.
"""

import os
import time
from pathlib import Path

import pytest
from backend import cleaner as cleaner_mod
from backend import config
from backend import task_manager as tm
from backend.task_manager import (
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_QUEUED,
)
from fastapi.testclient import TestClient
from helpers import create_download, find_task, member_headers, wait_until

VIDEO_URL = "https://www.bilibili.com/video/av-clear"
FORMAT_720P = "22"
FORMAT_1080P = "999"  # 会员档位 (会员任务创建用)


@pytest.fixture(autouse=True)
def default_ttl(monkeypatch):
    """固定 TTL 为默认契约值 (24h/72h): 断言不依赖运行环境配置.

    本地 .env 的演示配置 (60s/120s) 会破坏过期判定假设, 与
    test_ttl_cleanup 同一模式 (bugfix/0002).
    """
    monkeypatch.setattr(config, "FREE_DELIVERY_TTL", 24 * 3600)
    monkeypatch.setattr(config, "MEMBER_DELIVERY_TTL", 72 * 3600)


@pytest.fixture
def clock(monkeypatch):
    """可注入时钟 (cleaner._now): 推进时间越过 TTL 断言过期."""
    tick = {"now": time.time()}
    monkeypatch.setattr(cleaner_mod, "_now", lambda: tick["now"])

    def sync_now() -> None:
        tick["now"] = time.time()

    tick["sync_now"] = sync_now
    return tick


@pytest.fixture
def downloads_dir(monkeypatch, tmp_path):
    """交付目录指向 tmp_path: 孤儿文件清理扫描范围可断言 (隔离真实目录)."""
    monkeypatch.setattr(config, "DOWNLOADS_DIR", tmp_path)
    return tmp_path


def complete_download(
    client: TestClient,
    url: str,
    format_id: str,
    headers: dict[str, str] | None = None,
) -> int:
    """创建下载任务并等待完成, 返回 task_id."""
    resp = client.post(
        "/api/downloads", json={"url": url, "format_id": format_id}, headers=headers
    )
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    assert wait_until(lambda: find_task(client, task_id)["status"] == STATUS_COMPLETED)
    return task_id


def test_delete_completed_task_removes_file_and_record(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 清除未过期 completed 任务 → 204, 文件删除, 任务移除, 直链 404."""
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)
    delivered = tm.manager.get_task(task_id).file_path
    assert Path(delivered).is_file()

    resp = client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 204

    assert tm.manager.get_task(task_id) is None  # 任务已从存储移除
    assert not Path(delivered).exists()  # 视频文件已删除
    assert client.get(f"/api/files/{task_id}").status_code == 404  # 直链失效


def test_delete_expired_task_clears_record(
    client: TestClient, fake_extract, fake_download, clock
) -> None:
    """验收: 清除 expired 任务 → 文件已被周期清理 (无残留), 任务移除."""
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)
    clock["sync_now"]()
    clock["now"] += config.FREE_DELIVERY_TTL + 1
    assert cleaner_mod.cleaner.cleanup_expired() == [task_id]  # 标记 expired
    assert tm.manager.get_task(task_id).status == STATUS_EXPIRED

    resp = client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 204
    assert tm.manager.get_task(task_id) is None


def test_delete_downloading_task_cancels(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 下载中任务清除 → 取消下载 (204), 任务移除, 并发槽释放.

    用户反馈: 非已完成任务都要能清除记录; 下载中任务经取消信号中断引擎,
    worker 收尾的 update_status 防御跳过 (任务已移除), 槽位按派发身份释放.
    """
    _call_args, release = fake_download
    release.clear()
    task_id = create_download(client, VIDEO_URL, FORMAT_720P)
    assert wait_until(
        lambda: find_task(client, task_id)["status"] == STATUS_DOWNLOADING
    )

    resp = client.delete(f"/api/tasks/{task_id}")
    assert resp.status_code == 204
    assert tm.manager.get_task(task_id) is None  # 任务已移除

    release.set()  # 放行 mock 下载, worker 收尾 (任务已移除, 更新防御跳过)
    assert wait_until(lambda: tm.manager._active[False] == 0)  # 并发槽已释放


def test_delete_queued_task_cancels(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 排队中任务清除 → 204, 任务移除 (未开始下载, 无副作用)."""
    _call_args, release = fake_download
    release.clear()
    first = create_download(client, f"{VIDEO_URL}?a=1", FORMAT_720P)
    assert wait_until(lambda: find_task(client, first)["status"] == STATUS_DOWNLOADING)
    # 免费并发槽已被占用: 第二个任务稳定处于排队中
    second = create_download(client, f"{VIDEO_URL}?b=1", FORMAT_720P)
    assert find_task(client, second)["status"] == STATUS_QUEUED

    resp = client.delete(f"/api/tasks/{second}")
    assert resp.status_code == 204
    assert tm.manager.get_task(second) is None

    release.set()  # 放行首个任务, worker 收尾后槽位释放
    assert wait_until(lambda: tm.manager._active[False] == 0)


def test_delete_failed_task_clears_record(client: TestClient, fake_extract) -> None:
    """验收: 失败任务清除 → 204, 任务移除 (无交付文件, 直接移除)."""
    # 免费用户选锁定档位: 创建被拒 (400), 但任务已落库 failed (T05)
    resp = client.post(
        "/api/downloads", json={"url": VIDEO_URL, "format_id": FORMAT_1080P}
    )
    assert resp.status_code == 400
    assert find_task(client, 1)["status"] == STATUS_FAILED

    assert client.delete("/api/tasks/1").status_code == 204
    assert tm.manager.get_task(1) is None


def test_delete_missing_task_returns_404(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 不存在的任务清除 → 404."""
    assert client.delete("/api/tasks/99999").status_code == 404


def test_purge_unfinished_clears_expired_keeps_fresh(
    client: TestClient, fake_extract, fake_download, clock
) -> None:
    """验收: 一键清除全部未完成记录 — 过期任务移除, 已完成任务保留 (文件不删).

    未过期对照组用会员任务 (72h TTL): 推进 24h+1s 后免费任务过期而会员
    未到期, 制造「过期 / 未过期」差异 (与 test_ttl_differs_by_member_tier
    同模式, 注入时钟无法对同 TTL 任务制造差异).
    """
    expired_id = complete_download(client, f"{VIDEO_URL}?e=1", FORMAT_720P)
    fresh_id = complete_download(
        client, f"{VIDEO_URL}?f=1", FORMAT_1080P, member_headers(client)
    )
    fresh_file = tm.manager.get_task(fresh_id).file_path

    clock["sync_now"]()
    clock["now"] += config.FREE_DELIVERY_TTL + 1
    assert cleaner_mod.cleaner.cleanup_expired() == [expired_id]  # 仅免费任务过期

    resp = client.post("/api/tasks/purge-unfinished")
    assert resp.status_code == 200
    assert resp.json()["removed"] == [expired_id]

    assert tm.manager.get_task(expired_id) is None
    fresh = tm.manager.get_task(fresh_id)
    assert fresh is not None  # 已完成任务不受影响
    assert fresh.status == STATUS_COMPLETED
    assert Path(fresh_file).is_file()


def test_purge_unfinished_handles_overdue_completed_immediately(
    client: TestClient, fake_extract, fake_download, clock
) -> None:
    """验收: 已超 TTL 但周期清理未及执行的 completed 任务也被立即清除."""
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)

    clock["sync_now"]()
    clock["now"] += config.FREE_DELIVERY_TTL + 1  # 越过 TTL, 但不触发周期清理
    resp = client.post("/api/tasks/purge-unfinished")
    assert resp.json()["removed"] == [task_id]
    assert tm.manager.get_task(task_id) is None


def test_purge_unfinished_clears_failed_and_queued(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 批量清除覆盖失败与排队任务 (用户反馈: 失败记录必须能删).

    失败任务: 免费用户选锁定档位创建被拒, 任务落库 failed;
    排队任务: 免费并发槽被占, 第二任务保持 queued.
    """
    # 失败任务
    resp = client.post(
        "/api/downloads", json={"url": f"{VIDEO_URL}?f=1", "format_id": FORMAT_1080P}
    )
    assert resp.status_code == 400
    assert find_task(client, 1)["status"] == STATUS_FAILED

    # 排队任务 (占满免费并发槽后创建)
    _call_args, release = fake_download
    release.clear()
    first = create_download(client, f"{VIDEO_URL}?a=1", FORMAT_720P)
    assert wait_until(lambda: find_task(client, first)["status"] == STATUS_DOWNLOADING)
    second = create_download(client, f"{VIDEO_URL}?b=1", FORMAT_720P)
    assert find_task(client, second)["status"] == STATUS_QUEUED

    resp = client.post("/api/tasks/purge-unfinished")
    assert resp.status_code == 200
    assert sorted(resp.json()["removed"]) == [1, first, second]
    assert tm.manager.get_task(1) is None
    assert tm.manager.get_task(second) is None

    release.set()  # 放行首个任务, worker 收尾后槽位释放
    assert wait_until(lambda: tm.manager._active[False] == 0)


def test_purge_unfinished_cancels_downloading_task(
    client: TestClient, fake_extract, fake_download
) -> None:
    """验收: 批量清除覆盖下载中任务 — 取消下载, 任务移除, 槽位释放."""
    _call_args, release = fake_download
    release.clear()
    task_id = create_download(client, VIDEO_URL, FORMAT_720P)
    assert wait_until(
        lambda: find_task(client, task_id)["status"] == STATUS_DOWNLOADING
    )

    resp = client.post("/api/tasks/purge-unfinished")
    assert resp.status_code == 200
    assert resp.json()["removed"] == [task_id]
    assert tm.manager.get_task(task_id) is None

    release.set()
    assert wait_until(lambda: tm.manager._active[False] == 0)


def test_purge_unfinished_cleans_orphan_files(
    client: TestClient, fake_extract, fake_download, clock, downloads_dir
) -> None:
    """验收: purge 顺带清理孤儿文件 — 无引用且超 24h 的文件删除,
    被任务引用的文件与新文件 (下载中) 保留."""
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)
    delivered = tm.manager.get_task(task_id).file_path

    orphan = downloads_dir / "orphan.part"
    orphan.write_bytes(b"leftover")
    old = time.time() - config.FREE_DELIVERY_TTL - 3600  # 超 24h
    os.utime(orphan, (old, old))
    fresh = downloads_dir / "fresh.part"
    fresh.write_bytes(b"downloading-now")  # mtime 为当前时刻

    resp = client.post("/api/tasks/purge-unfinished")
    assert resp.status_code == 200

    assert not orphan.exists()  # 孤儿文件已清理
    assert fresh.exists()  # 新文件 (下载中) 保留
    assert Path(delivered).is_file()  # 被任务引用的文件保留
    assert tm.manager.get_task(task_id) is not None  # 已完成任务保留


def test_task_out_exposes_format_id_and_expires_at(
    client: TestClient, fake_extract, fake_download
) -> None:
    """契约: 任务序列化暴露 format_id (标题清晰度) 与 expires_at (倒计时).

    expires_at = completed_at + 身份 TTL (免费 24h), 未完成任务为 None.
    """
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)
    detail = find_task(client, task_id)
    assert detail["format_id"] == FORMAT_720P
    assert detail["expires_at"] is not None
    completed_at = tm.manager.get_task(task_id).completed_at
    assert detail["expires_at"] == pytest.approx(completed_at + 24 * 3600)

    # 下载中任务 (未完成): 无交付时刻, expires_at 为 None
    _call_args, release = fake_download
    release.clear()
    running_id = create_download(client, f"{VIDEO_URL}?r=1", FORMAT_720P)
    assert wait_until(
        lambda: find_task(client, running_id)["status"] == STATUS_DOWNLOADING
    )
    assert find_task(client, running_id)["expires_at"] is None
    release.set()
    # 放行后等待 worker 结束再退出: 否则 clean_state 清空任务存储时
    # worker 仍在运行, update_status 抛 KeyError (未处理线程异常警告)
    assert wait_until(
        lambda: find_task(client, running_id)["status"] == STATUS_COMPLETED
    )
