"""T02 真实链接下载 E2E: 起服务 → 解析 → 选档下载 → 直链取回 → 验证 MP4.

用法: python scripts/e2e_download.py [url] [format_id]
默认使用 B 站公开 MV (YouTube 需 cookies 验证, 不适合无头 E2E),
输出保存到 downloads/e2e_output.mp4.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx

# 脚本位于 scripts/, 项目根目录为其上一级
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = BASE_DIR / "downloads" / "e2e_output.mp4"
BASE_URL = "http://127.0.0.1:8010"
DEFAULT_URL = "https://www.bilibili.com/video/BV1GJ411x7h7"


def _wait_health(client: httpx.Client, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = client.get("/api/health")
            if resp.status_code == 200:
                return
        except httpx.ConnectError:
            pass
        time.sleep(0.5)
    raise RuntimeError("服务在超时时间内未就绪")


def _wait_task(client: httpx.Client, task_id: int, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for t in client.get("/api/tasks").json()["tasks"]:
            if t["task_id"] == task_id:
                if t["status"] in ("completed", "failed"):
                    return t
                print(f"  status={t['status']} progress={t['progress']}%")
                break
        time.sleep(1.0)
    raise RuntimeError(f"任务 {task_id} 在超时时间内未完成")


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    format_id = sys.argv[2] if len(sys.argv) > 2 else "18"

    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", "8010"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=BASE_URL, timeout=60.0) as client:
            _wait_health(client)

            print(f"[1/4] 解析: {url}")
            resp = client.post("/api/resolve", json={"url": url})
            resp.raise_for_status()
            info = resp.json()
            print(
                f"  title={info['title']} site={info['site']} "
                f"formats={len(info['formats'])} 档"
            )

            print(f"[2/4] 创建下载任务: format_id={format_id}")
            resp = client.post(
                "/api/downloads", json={"url": url, "format_id": format_id}
            )
            if resp.status_code != 200:
                print(f"  创建失败: {resp.status_code} {resp.text}")
                return 1
            task_id = resp.json()["task_id"]
            print(f"  task_id={task_id}")

            print("[3/4] 等待下载完成")
            task = _wait_task(client, task_id)
            if task["status"] != "completed":
                print(f"  下载失败: {task['error']}")
                return 1
            print(f"  completed, 进度 {task['progress']}%")

            print("[4/4] 直链取回文件")
            resp = client.get(f"/api/files/{task_id}")
            resp.raise_for_status()
            OUTPUT_FILE.write_bytes(resp.content)
            size = OUTPUT_FILE.stat().st_size
            print(f"  已保存: {OUTPUT_FILE} ({size} bytes)")
            if size <= 0:
                print("  校验失败: 文件为空")
                return 1
            print("E2E 通过: 真实链接解析 → 下载 → 直链交付全链路 OK")
            return 0
    finally:
        server.terminate()
        server.wait(timeout=10.0)


if __name__ == "__main__":
    sys.exit(main())
