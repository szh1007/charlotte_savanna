"""FastAPI 侧测试基建 (Issue 03).

环境隔离: 测试专用 Redis db 15 (不清 0 库) + stub 阶段零延迟加速;
Django 落库由 monkeypatch 隔离 (Django 侧已有独立测试, 不触网).
HTTP 客户端用 starlette TestClient (内置独立事件循环), 避免 pytest-asyncio
循环与 Windows Proactor 的 socket 绑定问题; 后台任务跑在 TestClient
portal 循环内, 每个测试前 flushdb 保证隔离.
"""

import os
import time

os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/15"
os.environ["CHARPLOT_INTERNAL_TOKEN"] = "test-internal-token"

import pytest
import redis
from starlette.testclient import TestClient
from tests.fakes import FakeChatModel

from project.charplot.agents.search_agent import SearchReport
from project.charplot.api import tasks
from project.charplot.api.server import app
from project.charplot.pipeline import llm as pipeline_llm
from project.charplot.pipeline.stages import search as search_stage


@pytest.fixture(autouse=True)
def flush_redis():
    """每个测试前清空测试库 + 重置任务系统状态.

    tasks._redis 惰性单例绑定创建时的 TestClient portal 事件循环, 每测试
    portal 重建 (新循环), 不复用会触发 "Event loop is closed"; 注册表
    残留 Task 引用旧循环, 一并清空.
    """
    r = redis.Redis.from_url(os.environ["REDIS_URL"])
    r.flushdb()
    r.close()
    tasks._redis = None
    tasks._tasks_registry.clear()
    yield


@pytest.fixture(autouse=True)
def fake_llm_and_search(monkeypatch):
    """隔离真实管道的外部依赖 (Issue 07): LLM / 检索 subagent / Django 取文件.

    - get_chat_model → FakeChatModel (按 prompt 关键词返回预置 JSON)
    - run_search_agent → 固定空检索报告 (不触网)
    - fetch_journey_content → 固定 txt 材料 (file 输入不触 Django)
    单测阶段可通过 monkeypatch.setattr 覆盖 fixture 行为.
    """
    monkeypatch.setattr(pipeline_llm, "get_chat_model", lambda: FakeChatModel())

    async def fake_search(sources, topic, queries):
        return SearchReport(topic=topic, queries=queries)

    monkeypatch.setattr(search_stage, "run_search_agent", fake_search)

    from project.charplot.api import django_client

    async def fake_fetch_content(journey_id):
        return "upload.txt", "这是一份测试学习材料\n\n包含若干知识点内容".encode()

    monkeypatch.setattr(django_client, "fetch_journey_content", fake_fetch_content)


@pytest.fixture
def client():
    """同步 TestClient: app 跑在内部独立循环, 跨请求保活后台任务."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_django_save(monkeypatch):
    """隔离 Django 内部端点调用 (落库/失败标记), 记录调用供断言."""
    calls: list[tuple] = []

    async def fake_save(journey_id, task_id, graph):
        calls.append(("save", journey_id, task_id, graph))

    async def fake_failed(journey_id, task_id, error_message):
        calls.append(("failed", journey_id, task_id, error_message))

    monkeypatch.setattr(tasks, "save_graph_to_django", fake_save)
    monkeypatch.setattr(tasks, "mark_journey_failed", fake_failed)
    return calls


def wait_until(condition, timeout=5.0, interval=0.02):
    """轮询等待条件成立 (video_downloader helpers 同款)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def wait_task_status(client, task_id, status, **kwargs):
    """等待任务进入指定状态 (经 GET /ai/tasks/{id} 轮询)."""

    def condition():
        resp = client.get(f"/ai/tasks/{task_id}")
        return resp.status_code == 200 and resp.json()["status"] == status

    return wait_until(condition, **kwargs)
