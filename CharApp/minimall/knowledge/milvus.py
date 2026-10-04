"""Milvus 索引与混合检索 (L5-a).

搬运自 `project/charplot/rag/milvus.py`, 三处按本票的形态改掉:

- **单集合 `ca_knowledge`** (charplot 按知识库分 `cp_kb_{id}`): 本业务的语料是
  一份全局政策库, 没有"多个知识库"这回事, 常量比参数准.
- **没有软删**: 下架的文章靠 `ensure_collection` 的 **drop + create 物理剔除**
  (索引脚本每次全量重建), 于是 schema 里没有 `valid` 列, 检索也没有
  `doc_id not in [...]` 那种实时查询 —— 少一个会漂的状态.
- **元数据换成来源字段**: `slug` / `title` / `category` / `chunk_index`
  (charplot 那边是 doc_id / filename / kb_id). `slug` + `chunk_index` 就是 chunk
  主键的两半, C10 的逐句引用拿主键做稳定标识.

检索仍是**混合检索**: 稠密 (dense_vector, IP) + 稀疏 (sparse_vector, 倒排),
双 `AnnSearchRequest` + `WeightedRanker` 等权融合 (bge-m3 两路都已归一化) ——
纯向量对精确关键词不敏感, 而政策问题里"48 小时""15 天"这类词恰好是关键词.
"""

from __future__ import annotations

import logging
from typing import Any

from CharApp.minimall.config import KnowledgeConfig

logger = logging.getLogger(__name__)

# 这个业务唯一的 collection (全局政策库; 不按知识库分 —— 本业务没有多个知识库)
KNOWLEDGE_COLLECTION = "ca_knowledge"

# 稠密/稀疏融合权重 (等权, bge-m3 双向量均已归一化)
RANKER_WEIGHTS = (0.5, 0.5)

_milvus_client = None


def get_milvus_client(config: KnowledgeConfig):
    """MilvusClient 惰性单例 (用例 monkeypatch 本函数注入假件, 不连真库)."""
    global _milvus_client
    if _milvus_client is None:
        from pymilvus import MilvusClient

        logger.info("初始化 Milvus 客户端 (uri=%s)", config.milvus_url)
        _milvus_client = MilvusClient(config.milvus_url)
    return _milvus_client


def reset_client() -> None:
    """把单例丢掉 —— **只给用例用** (与 embeddings / rerank 的两个 reset 同一条)."""
    global _milvus_client
    _milvus_client = None


def _build_schema(dim: int) -> Any:
    """collection schema: 来源 metadata + 双向量字段 (稠密 dim 维, bge-m3 = 1024)."""
    from pymilvus import CollectionSchema, DataType, FieldSchema

    return CollectionSchema(
        fields=[
            # 主键 = f"{slug}-{chunk_index}" (全量重建 drop+create, 无残留)
            FieldSchema(
                name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64
            ),
            FieldSchema(name="slug", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=32),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="dense_vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
        ],
        enable_dynamic_field=False,
    )


def _build_index_params(client) -> Any:
    """双向量索引: 稠密 HNSW/IP (L2 归一化后 IP=余弦), 稀疏倒排/IP.

    pymilvus 3.0 API: index_type/metric_type 传字符串, 经
    `client.prepare_index_params()` 构建, 随 create_collection 一次创建
    (不需要单独 create_index).
    """
    params = client.prepare_index_params()
    params.add_index(
        field_name="dense_vector",
        index_type="HNSW",
        metric_type="IP",
        params={"M": 16, "efConstruction": 200},
    )
    params.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="IP",
    )
    return params


def ensure_collection(config: KnowledgeConfig, dim: int | None = None) -> None:
    """全量重建: drop 旧 collection + create + 双索引.

    `dim` 默认 `config.embedding_dim` (bge-m3 = 1024); 同名 collection 已存在就
    直接丢弃重建 —— 任何内容变化都走全量重建, 于是**下架的文章被物理剔除**,
    索引结果对同一份输入恒定 (幂等).
    """
    client = get_milvus_client(config)
    dimension = dim or config.embedding_dim
    if client.has_collection(KNOWLEDGE_COLLECTION):
        logger.info(
            "重建 collection: drop 旧 %s (物理剔除下架文档)", KNOWLEDGE_COLLECTION
        )
        client.drop_collection(KNOWLEDGE_COLLECTION)
    client.create_collection(
        KNOWLEDGE_COLLECTION,
        schema=_build_schema(dimension),
        index_params=_build_index_params(client),
    )
    logger.info("collection %s 已创建 (dim=%s)", KNOWLEDGE_COLLECTION, dimension)


def insert_chunks(config: KnowledgeConfig, rows: list[dict]) -> None:
    """批量写入 chunk 行 (含 dense/sparse 向量 + 来源 metadata)."""
    if not rows:
        return
    client = get_milvus_client(config)
    client.insert(KNOWLEDGE_COLLECTION, data=rows)
    logger.info("写入 %s 条 chunks → %s", len(rows), KNOWLEDGE_COLLECTION)


def hybrid_search(
    config: KnowledgeConfig,
    query_dense: list,
    query_sparse: dict,
    limit: int = 20,
) -> list[dict]:
    """稠密 + 稀疏混合检索 (WeightedRanker 融合).

    Returns:
        list[dict]: `[{id, slug, title, category, chunk_index, content, score}]`,
        按融合分降序 (精排前的 Top-K).

    Raises:
        RuntimeError: Milvus 侧任何异常 (检索工具 / 脚本据此报"暂时查不到",
            而不是把半截结果当答案).
    """
    from pymilvus import AnnSearchRequest, WeightedRanker

    client = get_milvus_client(config)
    # 稠密/稀疏各自召回 limit (融合后仍需足够候选供精排)
    dense_req = AnnSearchRequest(
        data=[query_dense],
        anns_field="dense_vector",
        param={"metric_type": "IP"},
        limit=limit,
    )
    sparse_req = AnnSearchRequest(
        data=[query_sparse],
        anns_field="sparse_vector",
        param={"metric_type": "IP"},
        limit=limit,
    )
    ranker = WeightedRanker(*RANKER_WEIGHTS, norm_score=True)
    try:
        raw = client.hybrid_search(
            collection_name=KNOWLEDGE_COLLECTION,
            reqs=[dense_req, sparse_req],
            ranker=ranker,
            limit=limit,
            output_fields=["slug", "title", "category", "chunk_index", "content"],
        )
    except Exception as exc:
        logger.error("混合检索失败 (%s): %s", KNOWLEDGE_COLLECTION, exc)
        raise RuntimeError(f"Milvus 混合检索失败: {exc}") from exc

    results = []
    hits = raw[0] if raw else []
    for hit in hits:
        entity = hit.get("entity", {})
        results.append(
            {
                "id": hit.get("id", ""),
                "slug": entity.get("slug", ""),
                "title": entity.get("title", ""),
                "category": entity.get("category", ""),
                "chunk_index": entity.get("chunk_index", 0),
                "content": entity.get("content", ""),
                "score": float(hit.get("distance", 0.0)),
            }
        )
    logger.info(
        "混合检索返回 %d 条 (collection=%s)", len(results), KNOWLEDGE_COLLECTION
    )
    return results


__all__ = [
    "KNOWLEDGE_COLLECTION",
    "RANKER_WEIGHTS",
    "ensure_collection",
    "get_milvus_client",
    "hybrid_search",
    "insert_chunks",
    "reset_client",
]
