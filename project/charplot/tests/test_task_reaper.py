"""孤儿任务回收测试 (韧性: FastAPI 重启 → 任务 hash 残留但执行体已消失).

覆盖: 死任务判定 (进程注册表无此任务 + hash 状态仍 running) → 写终止 error
事件 (SSE 客户端据此走失败分支) + 实体经内部端点推回失败态; 活任务不误判;
同一任务只回收一次 (幂等). 任务态直接伪造进 Redis, 不经执行体, 不触网.
"""

import asyncio
import json
import os

import redis
from tests.test_pipeline_api import start_pipeline
from tests.test_tasks_sse import read_stream

from project.charplot.api import tasks


def dead_task_state(task_id: str, **overrides) -> str:
    """伪造「服务重启遗留」的任务态: hash 仍在 running, 注册表里没有."""
    state = {
        "status": "running",
        "stage": "deconstructing",
        "progress": 90,
        "journey_id": "3",
        "entity_id": "3",
        "entity_type": "journey",
        "task_type": tasks.TASK_TYPE_PIPELINE,
        "error_message": "",
    }
    state.update(overrides)
    r = redis.Redis.from_url(os.environ["CHARPLOT_REDIS_URL"], decode_responses=True)
    r.hset(f"charplot:task:{task_id}", mapping=state)
    r.rpush(
        f"charplot:task:{task_id}:events",
        json.dumps(
            {
                "task_id": task_id,
                "stage": state["stage"],
                "progress": state["progress"],
                "message": "阶段执行中",
            },
            ensure_ascii=False,
        ),
    )
    r.expire(f"charplot:task:{task_id}", 60)
    r.close()
    return task_id


def test_orphan_pipeline_task_reaped(client, monkeypatch):
    """旅程任务孤儿: 终止 error 帧 + 崩溃前进度 + 旅程推回失败态."""
    calls: list[tuple] = []

    async def fake_mark(journey_id, task_id, error_message):
        calls.append((journey_id, task_id, error_message))

    monkeypatch.setattr(tasks, "mark_journey_failed", fake_mark)
    task_id = dead_task_state("orphan-1", entity_id="7")

    events = read_stream(client, task_id)

    # 已推事件 + 回收写入的终止帧; id 连续 (Last-Event-ID 增量语义不破)
    assert [e[2]["stage"] for e in events] == ["deconstructing", "error"]
    assert [e[0] for e in events] == ["0", "1"]
    assert events[-1][2]["progress"] == 90  # 崩溃前那一阶段的进度, 不再恒 0
    assert "中断" in events[-1][2]["message"]
    # 实体推回失败态 (best-effort 内部端点)
    assert [c[0] for c in calls] == [7]
    # 任务态落库为 error: 前端 getTaskStatus 探测直接判死
    body = client.get(f"/ai/tasks/{task_id}").json()
    assert body["status"] == "error"
    assert "中断" in body["error_message"]


def test_orphan_level_task_reaped_with_seq(client, monkeypatch):
    """出题任务孤儿: 按 hash 里的 entity_seq 定位关卡推送失败标记."""
    calls: list[tuple] = []

    async def fake_mark(journey_id, level_seq, task_id, error_message):
        calls.append((journey_id, level_seq, task_id))

    monkeypatch.setattr(tasks, "mark_level_generation_failed", fake_mark)
    task_id = dead_task_state(
        "orphan-2",
        task_type=tasks.TASK_TYPE_LEVEL_GENERATION,
        entity_id="5",
        entity_seq="4",
        stage="generating",
        progress=60,
    )

    events = read_stream(client, task_id)

    assert events[-1][2]["progress"] == 60
    assert calls == [(5, 4, "orphan-2")]


def test_orphan_kb_task_reaped(client, monkeypatch):
    """索引任务孤儿 → 知识库推回失败态 (管理页可重新索引)."""
    calls: list[tuple] = []

    async def fake_mark(kb_id, task_id, error_message):
        calls.append((kb_id, task_id))

    monkeypatch.setattr(tasks, "mark_kb_index_failed", fake_mark)
    task_id = dead_task_state(
        "orphan-3",
        task_type=tasks.TASK_TYPE_KB_INDEX,
        entity_type="kb",
        entity_id="9",
        stage="indexing",
        progress=90,
    )

    read_stream(client, task_id)

    assert calls == [(9, "orphan-3")]


def test_orphan_reaped_only_once(client, monkeypatch):
    """二次订阅: 重放已有终止帧, 不重复回收 (幂等)."""
    calls: list[str] = []

    async def fake_mark(journey_id, task_id, error_message):
        calls.append(task_id)

    monkeypatch.setattr(tasks, "mark_journey_failed", fake_mark)
    task_id = dead_task_state("orphan-4")

    first = read_stream(client, task_id)
    second = read_stream(client, task_id)

    assert [e[2]["stage"] for e in first] == ["deconstructing", "error"]
    assert [e[2]["stage"] for e in second] == ["deconstructing", "error"]
    assert calls == ["orphan-4"]


def test_live_task_not_reaped(client, monkeypatch, mock_django_save):
    """执行体仍在注册表 (运行中, 阶段间有停顿) → 不判孤儿, 无中断帧."""

    async def slow_pipeline(inp, emit):
        await emit("parsing", 15, "解析输入")
        # 停顿期间 SSE 无新事件, 会走孤儿判定分支 (注册表命中 → 放行)
        await asyncio.sleep(0.8)
        await emit("done", 100, "完成")
        return {"version": 1, "title": "t", "chapters": []}

    monkeypatch.setattr(tasks, "run_pipeline", slow_pipeline)
    task_id = start_pipeline(client).json()["task_id"]

    events = read_stream(client, task_id)

    assert [e[2]["stage"] for e in events] == ["parsing", "done"]
    assert client.get(f"/ai/tasks/{task_id}").json()["status"] == "done"
