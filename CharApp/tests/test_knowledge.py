"""知识库那条线 (L5-a) 的用例: 文档组装 / 切分 / 配置 / 降级 / 检索链路 / 索引.

一条纪律贯穿全篇: **不加载真模型、不连真 Milvus、不出网**. 两个本地模型各有一个
替身 (`FakeEmbedder` / `FakeReranker`), 向量库走 `FakeMilvus`, 商城走 conftest 的
respx 假商城 —— 于是这一页可以在任何机器上跑, 而它验的正是"接线对不对".

四组东西各自守一句话:

- **纯函数** (`documents` / `chunking`): 拼出来的文本与主键是可预期的 —— 主键
  `{slug}-{序号}` 是 C10 引用的基础, 拼错了后面全靠回查.
- **配置与降级**: 模型缺失时 embedding 报错 (带下载命令) / rerank 降级不精排;
  开关关掉或模型不在时改写原样返回.
- **链路**: 检索的四步顺序与两个 K 的来源.
- **索引脚本**: drop 发生在向量化**之后** (失败不毁索引), 且重跑结果一致 (幂等).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from conftest import TOKEN, agent_url

import CharApp.minimall.knowledge.prewarm as prewarm_module
from CharAgent.model.utils.types import ModelResponse
from CharApp.minimall.config import (
    KnowledgeConfig,
    MinimallConfigError,
    knowledge_config_from_env,
)
from CharApp.minimall.knowledge import index, milvus, retriever
from CharApp.minimall.knowledge.chunking import split_document
from CharApp.minimall.knowledge.documents import (
    KnowledgeDocument,
    as_text,
    build_document,
)
from CharApp.minimall.knowledge.embeddings import (
    BgeM3Embedder,
    get_embedder,
    reset_embedder,
)
from CharApp.minimall.knowledge.query_rewrite import rewrite_query
from CharApp.minimall.knowledge.rerank import (
    NoopReranker,
    get_reranker,
    rerank_status,
    reset_reranker,
)

# 一份"够长到会被切开"的 Markdown (四小节, 正文超过切分器的 500 字上限)
LONG_ARTICLE = KnowledgeDocument(
    slug="refund-policy",
    title="退款政策",
    category="policy",
    content="""## 什么情况可以申请退款

订单付款之后都可以提交退款申请, 订单状态为「已付款」「已发货」「已收货」
「已完成」的四种情况都在受理范围内. 还没付款的订单不涉及退款: 那种订单直接取消
即可, 钱从没有出去过, 也就没有退款要等. 同一笔订单同一时间只能有一条进行中的
退款申请, 想补充说明或调整金额, 请等这一条处理完再提.

## 申请之后会发生什么

申请一提交, 订单状态变成「退款中」, 这一单不会再发货. 管理员会在 1-3 个工作日内
与你协商退款金额, 退款金额以订单实付金额为上限, 具体退多少由协商结果确定. 协商
一致后打款, 钱退回站内余额, 不退到银行卡或第三方支付账户. 申请被驳回时, 订单会
恢复到申请之前的状态, 管理员会在备注里写明原因, 了解原因之后可以重新提交申请.

## 受理时间

售后申请由管理员在工作日 9:00-21:00 处理, 非工作时间提交的申请顺延到下一个工作
日; 周末与法定节假日顺延. 退款协商与打款都在这个时间段内进行, 请留意页面上的
退款记录与订单状态变化.

## 几点说明

退款不需要先把商品寄回: 本商城不涉及寄回流程, 请勿相信任何要求寄回或索要运费的
指引. 退款进度可以在订单详情里看, 也可以直接问客服助手「我的退款到哪一步了」.
""",
)


# ---------------------------------------------------------------------------
# 替身: 不加载真模型 / 不连真向量库
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """记录每次编码了什么, 返回定长假向量 (维度由构造参数给)."""

    def __init__(self, dim: int = 4) -> None:
        self._dim = dim
        self.documents_seen: list[list[str]] = []
        self.queries_seen: list[str] = []
        self.preloaded = False

    def preload(self) -> None:
        self.preloaded = True

    def embed_documents(self, texts: list[str]) -> dict:
        self.documents_seen.append(list(texts))
        return {
            "dense": [[0.1] * self._dim for _ in texts],
            "sparse": [{1: 0.5} for _ in texts],
        }

    def embed_query(self, text: str) -> dict:
        self.queries_seen.append(text)
        return {"dense": [0.2] * self._dim, "sparse": {1: 0.5}}


class FakeReranker:
    """按输入顺序取前 top_k (记录 query 与候选数)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.preloaded = False

    def preload(self) -> None:
        self.preloaded = True

    def rerank(self, query: str, passages: list[dict], top_k: int) -> list[dict]:
        self.calls.append((query, len(passages), top_k))
        return [dict(p, score=1.0) for p in passages[:top_k]]


class _FakeIndexParams:
    """`client.prepare_index_params()` 的返回值 (只有一个 add_index)."""

    def __init__(self) -> None:
        self.added: list[dict] = []

    def add_index(self, **kwargs: Any) -> None:
        self.added.append(kwargs)


@dataclass
class FakeMilvus:
    """向量库客户端替身: 记下 ensure/insert 的载荷 (检索那条链另用函数替身)."""

    existing: bool = True
    dropped: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    inserted: list[list[dict]] = field(default_factory=list)

    def has_collection(self, name: str) -> bool:
        return self.existing

    def drop_collection(self, name: str) -> None:
        self.dropped.append(name)

    def prepare_index_params(self) -> _FakeIndexParams:
        return _FakeIndexParams()

    def create_collection(self, name: str, *, schema: Any, index_params: Any) -> None:
        self.created.append(name)

    def insert(self, name: str, *, data: list[dict]) -> None:
        self.inserted.append(list(data))


class RecordingModel:
    """记录 generate 的入参, 返回预置文本 (查询改写用例用)."""

    def __init__(self, content: str | None = "改写后的查询") -> None:
        self._content = content
        self.calls: list[dict] = []

    async def generate(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return ModelResponse(content=self._content)

    async def aclose(self) -> None:
        return None


class RaisingModel:
    """每次调用都抛 (改写失败的降级路径)."""

    async def generate(self, messages, tools=None, **kwargs):
        raise RuntimeError("上游超时")

    async def aclose(self) -> None:
        return None


@pytest.fixture
def config(tmp_path) -> KnowledgeConfig:
    """一份钉死的配置: modelscope 根指向空目录 (= 模型"本地不存在")."""
    return KnowledgeConfig(modelscope_root=str(tmp_path / "modelscope"))


@pytest.fixture(autouse=True)
def _clean_singletons():
    """每个用例前后清掉三个惰性单例 (embedder / reranker / milvus client).

    它们是进程级的 (生产就该如此), 而用例的"进程"从头到尾只有这一个 —— 不清理
    就会串味: 前一条用例建的对象会被后一条当成自己的.
    """
    reset_embedder()
    reset_reranker()
    milvus.reset_client()
    yield
    reset_embedder()
    reset_reranker()
    milvus.reset_client()


# ---------------------------------------------------------------------------
# 文档组装 (documents)
# ---------------------------------------------------------------------------


def test_document_is_assembled_with_the_title_first() -> None:
    """组装出的正文第一行是 `# 标题` —— 切分器按小节切, 第一块因此带着标题."""
    doc = build_document(
        {
            "slug": "shipping-and-delivery",
            "title": "运费与配送说明",
            "category": "shipping",
            "content": "## 运费\n\n全站免运费.",
            "updated_at": "2026-10-04T10:00:00+08:00",
        }
    )

    assert doc == KnowledgeDocument(
        slug="shipping-and-delivery",
        title="运费与配送说明",
        category="shipping",
        content="## 运费\n\n全站免运费.",
    )
    assert as_text(doc) == "# 运费与配送说明\n\n## 运费\n\n全站免运费."


def test_a_document_missing_a_field_fails_loudly() -> None:
    """缺字段当场报错 (带上缺的是什么) —— 静默拼出残文档的症状离真因很远."""
    with pytest.raises(ValueError, match="content"):
        build_document({"slug": "x", "title": "标题", "category": "policy"})


def test_an_empty_body_still_has_the_title_line() -> None:
    doc = KnowledgeDocument(slug="x", title="只有标题", category="policy", content="")
    assert as_text(doc) == "# 只有标题"


# ---------------------------------------------------------------------------
# 切分 (chunking)
# ---------------------------------------------------------------------------


def test_chunks_carry_an_explicit_primary_key() -> None:
    """主键是 `{slug}-{序号}` 且从 0 连续 —— C10 的引用靠它做稳定标识."""
    chunks = split_document(LONG_ARTICLE)

    assert [c["id"] for c in chunks] == [
        f"refund-policy-{i}" for i in range(len(chunks))
    ]
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    assert all(c["slug"] == "refund-policy" for c in chunks)
    assert all(c["title"] == "退款政策" for c in chunks)
    assert all(c["category"] == "policy" for c in chunks)


def test_the_first_chunk_starts_with_the_article_title() -> None:
    """标题进正文是有意的: 只问"退款政策是什么"的查询要能命中第一块."""
    chunks = split_document(LONG_ARTICLE)

    assert len(chunks) > 1, "这篇语料就是拿来验证会切开的"
    assert chunks[0]["content"].startswith("# 退款政策")


def test_a_blank_body_is_skipped_without_raising() -> None:
    blank = KnowledgeDocument(
        slug="blank", title="空文章", category="policy", content=""
    )
    assert split_document(blank) == []


# ---------------------------------------------------------------------------
# 配置 (config)
# ---------------------------------------------------------------------------


def test_knowledge_config_has_defaults() -> None:
    """一个变量都不配也能跑 (默认就是 .env.example 里写的那套)."""
    config = knowledge_config_from_env({})

    assert config.embedding_model == "bge-m3"
    assert config.embedding_model_name == "BAAI/bge-m3"
    assert config.embedding_dim == 1024
    # 5 / 3 是被当前语料规模钉住的默认 (6 篇 7 条 chunk: 召回 5 已过大半库),
    # 而不是照抄 charplot 的 20 / 5 —— 精排在 CPU 上逐对前向 (约 1.1 秒/对)
    assert config.retrieve_top_k == 5
    assert config.rerank_top_k == 3
    assert config.query_rewrite is True
    assert config.milvus_url.startswith("http://")


def test_knowledge_config_reads_overrides() -> None:
    config = knowledge_config_from_env(
        {
            "CHARAPP_MILVUS_URL": "http://milvus.test:19530",
            "CHARAPP_MODELSCOPE_ROOT": "D:/models",
            # 3 这个数**要避开默认值**: 默认就是 3 之后, 拿它当覆盖值的话, "覆盖
            # 生效了" 与 "压根没读那个变量" 会给出同一个结果 (用例从此验不出东西)
            "CHARAPP_RERANK_TOP_K": "7",
            "CHARAPP_RETRIEVE_TOP_K": "9",
            "CHARAPP_QUERY_REWRITE": "off",
            "CHARAPP_EMBEDDING_FP16": "yes",
        }
    )

    assert config.milvus_url == "http://milvus.test:19530"
    assert config.modelscope_root == "D:/models"
    assert config.rerank_top_k == 7
    assert config.retrieve_top_k == 9
    assert config.query_rewrite is False
    assert config.embedding_fp16 is True


def test_a_bad_number_is_a_startup_error() -> None:
    with pytest.raises(MinimallConfigError, match="CHARAPP_RETRIEVE_TOP_K"):
        knowledge_config_from_env({"CHARAPP_RETRIEVE_TOP_K": "零"})


def test_model_reference_resolves_into_the_modelscope_layout(tmp_path) -> None:
    """`BAAI/bge-m3` 这样的引用按 modelscope 平铺布局解析到本地目录."""
    model_dir = tmp_path / "models" / "BAAI" / "bge-m3"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    config = KnowledgeConfig(modelscope_root=str(tmp_path))

    assert config.resolve_local_model_path("BAAI/bge-m3") == str(model_dir)
    # 本地没有的引用解析成 None (调用方据此报错 / 降级, 不触发自动下载)
    assert config.resolve_local_model_path("BAAI/nope") is None


# ---------------------------------------------------------------------------
# 模型缺失时的两条路: embedding 报错, rerank 降级
# ---------------------------------------------------------------------------


def test_a_missing_embedding_model_raises_with_a_download_command(config) -> None:
    """模型不在本地: 报错并给可照抄的下载命令, **绝不静默从 HF 下载**."""
    embedder = BgeM3Embedder(config)

    with pytest.raises(RuntimeError) as excinfo:
        embedder.embed_query("退款政策")

    message = str(excinfo.value)
    assert "modelscope download --model BAAI/bge-m3" in message
    assert "拒绝加载" in message


def test_an_unregistered_embedding_name_is_a_config_error() -> None:
    with pytest.raises(MinimallConfigError, match="未注册"):
        get_embedder(KnowledgeConfig(embedding_model="no-such-model"))


def test_a_missing_reranker_degrades_instead_of_failing(config) -> None:
    """精排缺失 = 设计内的降级: 保持召回顺序, 状态里说清原因."""
    reranker = get_reranker(config)

    assert isinstance(reranker, NoopReranker)
    status = rerank_status(config)
    assert status["degraded"] is True
    assert "bge-reranker-v2-m3" in status["reason"]
    kept = reranker.rerank("query", [{"content": "a"}, {"content": "b"}], top_k=1)
    assert [c["content"] for c in kept] == ["a"]


def test_an_unset_reranker_is_not_reported_as_missing() -> None:
    """没配 reranker 与配了但缺文件是两件事 (前者是"没启用", 不是缺件)."""
    status = rerank_status(KnowledgeConfig(reranker_model=""))

    assert status["degraded"] is True
    assert "未配置" in status["reason"]


# ---------------------------------------------------------------------------
# 查询改写 (query_rewrite)
# ---------------------------------------------------------------------------


async def test_rewrite_is_skipped_when_the_switch_is_off() -> None:
    model = RecordingModel()

    result = await rewrite_query(
        "能退吗", model=model, config=KnowledgeConfig(query_rewrite=False)
    )

    assert result == "能退吗"
    assert model.calls == [], "关掉了就不该调模型"


async def test_rewrite_is_skipped_without_a_model() -> None:
    """索引脚本那条路没有会话模型 —— 跳过改写, 不报错."""
    result = await rewrite_query("能退吗", model=None, config=KnowledgeConfig())

    assert result == "能退吗"


async def test_rewrite_uses_the_model_without_thinking() -> None:
    """改写是格式化任务: 关掉思考模式 (省 token, 也省掉一个变数)."""
    model = RecordingModel("退款的条件与流程是什么")

    rewritten = await rewrite_query("能退吗", model=model, config=KnowledgeConfig())

    assert rewritten == "退款的条件与流程是什么"
    assert model.calls[0]["thinking"] is False


@pytest.mark.parametrize(
    "content", [None, "", "   ", "能退吗"], ids=["None", "空串", "空白", "复述原文"]
)
async def test_a_useless_rewrite_falls_back_to_the_original(content) -> None:
    model = RecordingModel(content)

    result = await rewrite_query("能退吗", model=model, config=KnowledgeConfig())

    assert result == "能退吗"


async def test_a_failing_rewrite_falls_back_to_the_original() -> None:
    """任何异常都降级 —— 改写是增强, 不是检索的前提."""
    result = await rewrite_query(
        "能退吗", model=RaisingModel(), config=KnowledgeConfig()
    )

    assert result == "能退吗"


async def test_an_overlong_rewrite_is_truncated() -> None:
    model = RecordingModel("退" * 500)

    rewritten = await rewrite_query("能退吗", model=model, config=KnowledgeConfig())

    assert rewritten == "退" * 200


# ---------------------------------------------------------------------------
# 检索链路 (retriever)
# ---------------------------------------------------------------------------


async def test_search_runs_the_chain_in_order(monkeypatch, config) -> None:
    """改写 → 向量化 → 混合检索 → 精排, 两个 K 都来自配置."""
    embedder = FakeEmbedder()
    reranker = FakeReranker()
    searches: list[tuple[Any, Any, int]] = []
    hits = [
        {
            "id": "refund-policy-0",
            "slug": "refund-policy",
            "title": "退款政策",
            "category": "policy",
            "chunk_index": 0,
            "content": "# 退款政策",
            "score": 0.9,
        }
    ]

    def fake_search(config_, dense, sparse, limit=20):
        searches.append((dense, sparse, limit))
        return list(hits)

    model = RecordingModel("退款流程")
    monkeypatch.setattr(retriever, "get_embedder", lambda _: embedder)
    monkeypatch.setattr(retriever, "get_reranker", lambda _: reranker)
    monkeypatch.setattr(milvus, "hybrid_search", fake_search)

    chunks = await retriever.KnowledgeRetriever(config, model=model).search("能退吗")

    assert embedder.queries_seen == ["退款流程"], "向量化用的是改写后的查询"
    assert searches[0][2] == config.retrieve_top_k
    assert reranker.calls == [("退款流程", len(hits), config.rerank_top_k)]
    assert [c["id"] for c in chunks] == ["refund-policy-0"]


async def test_an_empty_query_is_a_caller_error(config) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        await retriever.KnowledgeRetriever(config).search("   ")


# ---------------------------------------------------------------------------
# 向量库 (milvus)
# ---------------------------------------------------------------------------


def test_ensure_collection_rebuilds_from_scratch(config, monkeypatch) -> None:
    """全量重建 = 有旧的先 drop 再 create (下架文档靠这一步物理剔除)."""
    fake = FakeMilvus(existing=True)
    monkeypatch.setattr(milvus, "get_milvus_client", lambda _: fake)

    milvus.ensure_collection(config, dim=8)

    assert fake.dropped == [milvus.KNOWLEDGE_COLLECTION]
    assert fake.created == [milvus.KNOWLEDGE_COLLECTION]


def test_insert_is_a_noop_for_empty_rows(config, monkeypatch) -> None:
    """一条都没有 (比如全下架了) 不碰向量库 —— drop+create 已经把它清空了."""
    fake = FakeMilvus()
    monkeypatch.setattr(milvus, "get_milvus_client", lambda _: fake)

    milvus.insert_chunks(config, [])

    assert fake.inserted == []


# ---------------------------------------------------------------------------
# 预热 (prewarm)
# ---------------------------------------------------------------------------


async def test_prewarm_loads_both_models(monkeypatch, config) -> None:
    embedder = FakeEmbedder()
    reranker = FakeReranker()
    monkeypatch.setattr(prewarm_module, "get_embedder", lambda _: embedder)
    monkeypatch.setattr(prewarm_module, "rerank_status", lambda _: {"degraded": False})
    monkeypatch.setattr(prewarm_module, "get_reranker", lambda _: reranker)

    report = await prewarm_module.prewarm(config)

    assert (report.embedder_ready, report.reranker_ready) == (True, True)
    assert embedder.preloaded is True
    assert reranker.preloaded is True


async def test_prewarm_reports_a_missing_embedder_without_raising(
    monkeypatch, config
) -> None:
    """缺 embedding 模型: RAG 不可用, 但服务照起 (报错不抛)."""

    class BrokenEmbedder(FakeEmbedder):
        def preload(self) -> None:
            raise RuntimeError("模型不在本地")

    monkeypatch.setattr(prewarm_module, "get_embedder", lambda _: BrokenEmbedder())
    monkeypatch.setattr(prewarm_module, "rerank_status", lambda _: {"degraded": False})
    monkeypatch.setattr(prewarm_module, "get_reranker", lambda _: FakeReranker())

    report = await prewarm_module.prewarm(config)

    assert report.embedder_ready is False
    assert "模型不在本地" in (report.embedder_error or "")
    assert report.reranker_ready is True


async def test_prewarm_keeps_running_when_the_reranker_is_degraded(
    monkeypatch, config
) -> None:
    """rerank 走降级时预热不报错, 报告里如实写「降级 + 原因」."""
    monkeypatch.setattr(prewarm_module, "get_embedder", lambda _: FakeEmbedder())
    monkeypatch.setattr(
        prewarm_module,
        "rerank_status",
        lambda _: {"degraded": True, "reason": "本地模型未找到"},
    )

    report = await prewarm_module.prewarm(config)

    assert report.embedder_ready is True
    assert report.reranker_ready is False
    assert report.reranker_reason == "本地模型未找到"


# ---------------------------------------------------------------------------
# 索引脚本 (index)
# ---------------------------------------------------------------------------


class StubClient:
    """只会一件事的商城客户端替身: 按给定行返回 (顺带记下被问过几次)."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls = 0

    async def list_knowledge_articles(self) -> list[dict]:
        self.calls += 1
        return self._rows


def rows_of(*slug_and_body: tuple[str, str]) -> list[dict]:
    return [
        {
            "slug": slug,
            "title": f"{slug} 的标题",
            "category": "policy",
            "content": body,
            "updated_at": "2026-10-04T10:00:00+08:00",
        }
        for slug, body in slug_and_body
    ]


async def test_rebuild_writes_chunks_with_vectors(monkeypatch, config) -> None:
    fake = FakeMilvus(existing=True)
    embedder = FakeEmbedder(dim=4)
    monkeypatch.setattr(milvus, "get_milvus_client", lambda _: fake)
    client = StubClient(rows_of(("refund-policy", LONG_ARTICLE.content)))

    report = await index.rebuild(client, config=config, embedder=embedder)

    assert (report.articles, report.skipped) == (1, ())
    assert report.chunks == len(embedder.documents_seen[0])
    assert report.chunks > 1
    written = fake.inserted[0]
    assert [row["id"] for row in written] == [
        f"refund-policy-{i}" for i in range(report.chunks)
    ]
    assert all(len(row["dense_vector"]) == 4 for row in written)
    assert all(row["sparse_vector"] == {1: 0.5} for row in written)
    assert fake.dropped == [milvus.KNOWLEDGE_COLLECTION]


async def test_rebuild_is_idempotent(monkeypatch, config) -> None:
    """同一份输入跑两次, 写进去的行一模一样 (全量重建的幂等承诺)."""
    fake = FakeMilvus()
    monkeypatch.setattr(milvus, "get_milvus_client", lambda _: fake)
    client = StubClient(rows_of(("a", LONG_ARTICLE.content), ("b", "## 小节\n\n正文.")))

    await index.rebuild(client, config=config, embedder=FakeEmbedder())
    await index.rebuild(client, config=config, embedder=FakeEmbedder())

    assert fake.inserted[0] == fake.inserted[1]
    assert fake.created == [milvus.KNOWLEDGE_COLLECTION] * 2


async def test_a_failed_embedding_does_not_touch_the_index(monkeypatch, config) -> None:
    """向量化失败时**还没 drop** —— 盘上那份索引原样留着, 检索不至于变空."""
    fake = FakeMilvus()
    monkeypatch.setattr(milvus, "get_milvus_client", lambda _: fake)
    client = StubClient(rows_of(("a", LONG_ARTICLE.content)))

    class BrokenEmbedder(FakeEmbedder):
        def embed_documents(self, texts):
            raise RuntimeError("模型不在本地")

    with pytest.raises(RuntimeError, match="模型不在本地"):
        await index.rebuild(client, config=config, embedder=BrokenEmbedder())

    assert fake.dropped == [] and fake.created == [] and fake.inserted == []


async def test_a_blank_article_is_reported_as_skipped(monkeypatch, config) -> None:
    fake = FakeMilvus()
    monkeypatch.setattr(milvus, "get_milvus_client", lambda _: fake)
    client = StubClient(rows_of(("ok", "## 小节\n\n正文."), ("blank", "   ")))

    report = await index.rebuild(client, config=config, embedder=FakeEmbedder())

    assert report.articles == 2
    assert report.skipped == ("blank",)


# ---------------------------------------------------------------------------
# 商城客户端那条新路 (公共数据, 不带买家身份)
# ---------------------------------------------------------------------------


async def test_knowledge_articles_travel_without_a_buyer(client, mall) -> None:
    """索引脚本读的是公共数据: 只带令牌, **不发 X-User-Id**."""
    route = mall.get(agent_url("knowledge/articles/")).mock(
        return_value=httpx.Response(200, json=[{"slug": "x"}])
    )

    rows = await client.list_knowledge_articles()

    assert rows == [{"slug": "x"}]
    headers = route.calls[0].request.headers
    assert headers["X-Internal-Token"] == TOKEN
    assert "X-User-Id" not in headers, "这份数据不属于任何买家"
