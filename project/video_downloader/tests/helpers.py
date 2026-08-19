"""测试共享工具函数 (fixtures 见 conftest.py)."""

import time
from collections.abc import Callable


def wait_until(cond: Callable[[], bool], timeout: float = 2.0) -> bool:
    """轮询直到条件成立或超时 (调度线程异步, 测试需等待)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False
