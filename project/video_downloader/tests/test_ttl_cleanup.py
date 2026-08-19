"""T06 TTL 清理 + 直链过期验收测试 (HTTP seam, 引擎 mock, 可注入时钟).

验收: 后台清理周期执行 / 超 TTL 删文件并标记 expired / 免费 24h 会员 72h /
过期直链 410 明确提示 / 不误伤未过期任务 / 重复清理幂等.
清理逻辑通过 cleaner.cleanup_expired() 直接触发 (确定性强), 后台线程的
周期性另用缩短间隔的测试验证机制.
"""

import time
from pathlib import Path

import pytest
from backend import cleaner as cleaner_mod
from backend import config
from backend import task_manager as tm
from backend.task_manager import STATUS_COMPLETED, STATUS_EXPIRED
from fastapi.testclient import TestClient
from helpers import find_task, member_headers, wait_until

VIDEO_URL = "https://www.bilibili.com/video/av-ttl"
FORMAT_720P = "22"  # 免费可下载档位
FORMAT_1080P = "999"  # 会员档位 (会员任务创建用, 免费档不可选)


@pytest.fixture
def clock(monkeypatch):
    """可注入时钟 (cleaner._now): 推进时间越过 TTL 断言过期.

    completed_at 由 task_manager 以真实时间写入, 推进前须同步基准为
    当前时刻 (sync_now), 避免任务完成时刻与测试开始时刻的差值
    挤占推进余量 (高负载下不稳定).
    """
    tick = {"now": time.time()}
    monkeypatch.setattr(cleaner_mod, "_now", lambda: tick["now"])

    def sync_now() -> None:
        tick["now"] = time.time()

    tick["sync_now"] = sync_now
    return tick


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


def cleanup() -> list[int]:
    """触发一次清理 (直接调用, 不依赖 60s 后台周期, 断言更确定)."""
    return cleaner_mod.cleaner.cleanup_expired()


def test_free_task_expired_after_24h(
    client: TestClient, fake_extract, fake_download, clock
) -> None:
    """验收: 免费任务超 24h TTL → 文件删除 + 任务标记 expired + 事件字段失效."""
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)
    task = tm.manager.get_task(task_id)
    assert task.status == STATUS_COMPLETED
    assert task.file_path is not None
    delivered = task.file_path
    assert Path(delivered).is_file()

    clock["sync_now"]()
    clock["now"] += config.FREE_DELIVERY_TTL + 1  # 越过 24h 有效期
    assert cleanup() == [task_id]

    task = tm.manager.get_task(task_id)
    assert task.status == STATUS_EXPIRED
    assert task.file_path is None  # 交付路径作废, 事件 url 不再暴露
    assert task.message == "交付链接已过期, 文件已清理"
    assert not Path(delivered).exists()  # 磁盘文件已被清理


def test_ttl_differs_by_member_tier(
    client: TestClient, fake_extract, fake_download, clock
) -> None:
    """验收: TTL 按身份区分 — 免费 24h 过期时会员任务 (72h) 仍有效不误伤."""
    free_id = complete_download(client, f"{VIDEO_URL}?f=1", FORMAT_720P)
    member_id = complete_download(
        client, f"{VIDEO_URL}?m=1", FORMAT_1080P, member_headers(client)
    )
    member_file = tm.manager.get_task(member_id).file_path

    clock["sync_now"]()
    clock["now"] += config.FREE_DELIVERY_TTL + 1  # 24h+1s: 仅免费过期
    assert cleanup() == [free_id]
    assert tm.manager.get_task(free_id).status == STATUS_EXPIRED
    member_task = tm.manager.get_task(member_id)
    assert member_task.status == STATUS_COMPLETED  # 会员 72h: 未到期
    assert member_task.file_path == member_file
    assert Path(member_file).is_file()

    clock["now"] += config.MEMBER_DELIVERY_TTL - config.FREE_DELIVERY_TTL  # 累计 72h
    assert cleanup() == [member_id]
    assert tm.manager.get_task(member_id).status == STATUS_EXPIRED


def test_cleanup_not_touch_fresh_task(
    client: TestClient, fake_extract, fake_download, clock
) -> None:
    """验收: 未过期任务不被误伤 — 文件保留, 状态不变, 直链仍可下载."""
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)
    file_path = tm.manager.get_task(task_id).file_path

    clock["sync_now"]()
    clock["now"] += 3600  # 1h << 24h TTL
    assert cleanup() == []
    task = tm.manager.get_task(task_id)
    assert task.status == STATUS_COMPLETED
    assert task.file_path == file_path
    assert Path(file_path).is_file()
    assert client.get(f"/api/files/{task_id}").status_code == 200


def test_cleanup_idempotent(
    client: TestClient, fake_extract, fake_download, clock
) -> None:
    """验收: 重复清理幂等 — 不报错, 已 expired 任务不重复标记, 无副作用."""
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)
    clock["sync_now"]()
    clock["now"] += config.FREE_DELIVERY_TTL + 1
    assert cleanup() == [task_id]
    assert cleanup() == []  # 已 expired: 扫描范围外
    assert cleanup() == []  # 连续调用无副作用
    assert tm.manager.get_task(task_id).status == STATUS_EXPIRED


def test_expired_delivery_link_returns_410(
    client: TestClient, fake_extract, fake_download, clock
) -> None:
    """验收: 过期任务直链返回 410 + 明确提示 (文件已清理), 非泛化 404."""
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)
    clock["sync_now"]()
    clock["now"] += config.FREE_DELIVERY_TTL + 1
    cleanup()

    resp = client.get(f"/api/files/{task_id}")
    assert resp.status_code == 410
    detail = resp.json()["detail"]
    assert "过期" in detail and "已清理" in detail


def test_cleaner_loop_runs_periodically(
    client: TestClient, fake_extract, fake_download, clock, monkeypatch
) -> None:
    """验收: 后台清理周期性执行 (间隔常量缩短验证机制, 非单次手动触发).

    推进时钟越过 TTL 后不手动调用清理, 由后台线程在周期内自动扫描.
    """
    monkeypatch.setattr(cleaner_mod, "_CLEANER_INTERVAL", 0.05)
    cleaner_mod.cleaner.start()
    task_id = complete_download(client, VIDEO_URL, FORMAT_720P)

    clock["sync_now"]()
    clock["now"] += config.FREE_DELIVERY_TTL + 1
    assert wait_until(
        lambda: tm.manager.get_task(task_id).status == STATUS_EXPIRED, timeout=2.0
    )
    assert cleaner_mod.cleaner._thread.is_alive()  # 线程仍在周期运行
