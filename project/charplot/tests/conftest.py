"""FastAPI 侧测试基建 (Issue 03).

环境隔离: 测试专用 Redis db 15 (不清 0 库) + stub 阶段零延迟加速;
Django 落库由 monkeypatch 隔离 (Django 侧已有独立测试, 不触网).
HTTP 客户端用 starlette TestClient (内置独立事件循环), 避免 pytest-asyncio
循环与 Windows Proactor 的 socket 绑定问题; 后台任务跑在 TestClient
portal 循环内, 每个测试前 flushdb 保证隔离.
"""

import os
import re
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


class FakeEmbedder:
    """假 embedding: 固定 1024 维稠密 + 单特征稀疏, 记录查询文本.

    embed_query 记录 last_query (断言 query rewriting 改写生效用).
    """

    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.last_query = ""

    def embed_documents(self, texts):
        return {
            "dense": [[0.1] * self.dim for _ in texts],
            "sparse": [{1: 0.5} for _ in texts],
        }

    def embed_query(self, text):
        self.last_query = text
        return {"dense": [0.1] * self.dim, "sparse": {1: 0.5}}


class FakeMilvusClient:
    """假 MilvusClient: 记录 collection 重建/写入/混合检索调用 (不连真库).

    hybrid_hits 为预置检索结果 (混合检索返回值, entity 去掉 score 字段
    后返回, 与真实 Milvus hit 结构同构). hybrid_calls 记录请求参数
    (含 reqs 的 filter expr, 断言软删过滤用).
    """

    def __init__(self, hybrid_hits: list[dict] | None = None):
        self.collections: set[str] = set()
        self.inserted: list[dict] = []
        self.hybrid_calls: list[dict] = []
        self._hits = hybrid_hits or []

    def has_collection(self, name):
        return name in self.collections

    def drop_collection(self, name):
        self.collections.discard(name)

    def prepare_index_params(self):
        class _IndexParams:
            def add_index(self, field_name, index_type="", index_name="", **kwargs):
                pass

        return _IndexParams()

    def create_collection(self, name, schema=None, index_params=None, **kwargs):
        self.collections.add(name)

    def create_index(self, name, field, params):
        pass

    def insert(self, name, data):
        self.inserted.extend(data)

    def hybrid_search(self, collection_name, reqs, ranker, limit, output_fields):
        self.hybrid_calls.append(
            {
                "collection_name": collection_name,
                "reqs": reqs,
                "limit": limit,
                "output_fields": output_fields,
            }
        )
        # 模拟软删过滤: 解析 "doc_id not in [...]" 表达式 (expr 由
        # rag.milvus._build_filter_expr 构造), 排除命中项
        expr = reqs[0].expr if reqs else ""
        excluded: set[int] = set()
        match = re.search(r"doc_id not in \[([\d, ]*)\]", expr)
        if match:
            excluded = {int(x) for x in match.group(1).split(",") if x.strip()}
        hits = [h for h in self._hits if h["doc_id"] not in excluded][:limit]
        return [
            [
                {
                    "distance": hit["score"],
                    "entity": {k: v for k, v in hit.items() if k != "score"},
                }
                for hit in hits
            ]
        ]


@pytest.fixture
def fake_rag_deps(monkeypatch):
    """隔离 RAG 外部依赖 (embedding 模型 / Milvus 客户端), 返回假件实例.

    需 patch 两处 embedding 入口:
    - rag.embeddings.get_embedder: 索引任务函数内 import 动态查找
    - rag.retriever.get_embedder: retriever 模块级 from-import 静态绑定
    milvus 客户端: 索引任务函数内 import 与 retriever.milvus 模块属性
    均动态查找, patch rag.milvus.get_milvus_client 一处生效.
    """
    from project.charplot.rag import embeddings as rag_embeddings
    from project.charplot.rag import milvus as rag_milvus
    from project.charplot.rag import retriever as rag_retriever

    embedder = FakeEmbedder()
    milvus_client = FakeMilvusClient()
    monkeypatch.setattr(rag_embeddings, "get_embedder", lambda: embedder)
    monkeypatch.setattr(rag_retriever, "get_embedder", lambda: embedder)
    monkeypatch.setattr(rag_milvus, "get_milvus_client", lambda: milvus_client)
    return embedder, milvus_client


# 知识库索引任务测试夹具 (test_kb_index / test_kb_rag 共用)
KB_DOCUMENTS = [
    {
        "id": 1,
        "title": "a.txt",
        "filename": "a.txt",
        "file_size": 1024,
        "extension": "txt",
    },
    {
        "id": 2,
        "title": "b.md",
        "filename": "b.md",
        "file_size": 512,
        "extension": "md",
    },
]

# 文档内容 (doc_id → (filename, text), 供解析/切分)
KB_CONTENTS = {
    1: (
        "a.txt",
        "Python 装饰器是函数包装机制, 用于在不修改原函数代码的\n情况下扩展功能。\n\n"
        * 30,
    ),
    2: ("b.md", "闭包能够捕获其定义处的词法作用域, 记录外部变量引用。\n\n" * 30),
}


@pytest.fixture
def mock_kb_endpoints(monkeypatch, fake_rag_deps):
    """隔离索引内部端点 + RAG 外部依赖, 记录 claim / save / failed 调用."""
    calls = {"claim": [], "save": [], "failed": []}

    async def fake_claim(kb_id, task_id):
        calls["claim"].append((kb_id, task_id))
        return True, {"documents": KB_DOCUMENTS}

    async def fake_save(kb_id, task_id):
        calls["save"].append((kb_id, task_id))

    async def fake_failed(kb_id, task_id, error_message):
        calls["failed"].append((kb_id, task_id, error_message))

    async def fake_fetch_content(doc_id):
        filename, text = KB_CONTENTS[int(doc_id)]
        return filename, text.encode()

    monkeypatch.setattr(tasks, "claim_kb_index", fake_claim)
    monkeypatch.setattr(tasks, "save_kb_index_success", fake_save)
    monkeypatch.setattr(tasks, "mark_kb_index_failed", fake_failed)
    monkeypatch.setattr(tasks, "fetch_kb_document_content", fake_fetch_content)
    return calls


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


def wait_until(condition, timeout=15.0, interval=0.05):
    """轮询等待条件成立 (video_downloader helpers 同款).

    默认超时 15s: 真实索引任务完成路径实测 ~6s (轮询 HTTP 往返 +
    同步 AI 操作); interval 0.05 (50ms) 避免密集轮询与后台任务争抢
    TestClient 事件循环 (真实索引任务含同步解析/embedding).
    """
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
