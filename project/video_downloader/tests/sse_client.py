"""自定义 ASGI 流式测试客户端: 驱动 SSE 端点逐帧消费 (T03).

httpx ASGITransport 不支持流式响应 (app 协程须完整跑完, 响应体全量缓冲),
故直接驱动 ASGI app: 独立线程跑 asyncio 事件循环, 帧经线程安全队列暴露.
断开语义: close() 后服务端下一次 send 抛异常, 与真实连接断开行为一致,
触发生成器 finally 清理订阅.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any


class SseStream:
    """驱动 app 的 SSE 连接: 提供逐帧读取 / 状态码 / 头 / 断开能力."""

    def __init__(self, app: Any, path: str) -> None:
        self.status_code: int | None = None
        self.headers: dict[str, str] = {}
        self.error: Exception | None = None
        self._frames: deque[str] = deque()
        self._buffer = ""
        self._closed = threading.Event()  # close() 信号, 跨线程直接 set
        self._app_done = threading.Event()
        self._path, _, self._query = path.partition("?")
        self._thread = threading.Thread(target=self._run, args=(app,), daemon=True)
        self._thread.start()

    # ---- 服务器驱动线程 ----

    def _run(self, app: Any) -> None:
        try:
            asyncio.run(self._drive(app))
        except Exception as e:
            self.error = e  # 测试客户端: 收集服务器侧异常供断言
        finally:
            self._app_done.set()

    async def _drive(self, app: Any) -> None:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": self._query.encode(),
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 123),
            "server": ("testserver", 80),
        }
        body_sent = False

        async def receive() -> dict[str, Any]:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            # 请求体已读完: 挂起直到 close() (模拟客户端连接保持)
            await asyncio.to_thread(self._closed.wait)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                self.status_code = message["status"]
                self.headers = {
                    k.decode(): v.decode() for k, v in message.get("headers", [])
                }
            elif message["type"] == "http.response.body":
                if self._closed.is_set():
                    # 断开后服务端继续 send 失败 (与真实连接断开行为一致)
                    raise RuntimeError("client disconnected")
                body = message.get("body", b"")
                if body:
                    self._put_chunk(body)
                if not message.get("more_body", False):
                    self._app_done.set()

        await app(scope, receive, send)

    def _put_chunk(self, chunk: bytes) -> None:
        """按空行切分完整 SSE 帧, 追加到帧队列 (跨线程 deque 安全)."""
        self._buffer += chunk.decode("utf-8")
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            self._frames.append(frame)

    # ---- 客户端 (测试线程) 接口 ----

    def wait_headers(self, timeout: float = 2.0) -> None:
        """等待响应头到达 (驱动线程异步, 构造后需先等 start 消息)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.status_code is not None or self.error is not None:
                return
            time.sleep(0.02)
        raise TimeoutError("SSE 响应头未在超时时间内到达")

    def next(self, timeout: float = 2.0) -> str:
        """返回下一完整 SSE 帧, 超时抛 TimeoutError."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._frames:
                return self._frames.popleft()
            if self.error is not None:
                raise self.error
            if self._app_done.is_set():
                raise RuntimeError("SSE 流已结束 (app 退出)")
            time.sleep(0.02)
        raise TimeoutError

    def close(self) -> None:
        """模拟客户端断开: 服务端下一次 send 抛异常, 生成器 finally 清理订阅."""
        self._closed.set()

    def join(self, timeout: float = 2.0) -> None:
        """等待服务器驱动线程退出 (断开后需事件/心跳唤醒生成器)."""
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise AssertionError("SSE 服务端未在断开后退出")
