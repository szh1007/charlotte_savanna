"""Milvus 索引与混合检索 (Issue 10, SPEC §7.2).

collection 生命周期 (全量重建策略, Q18b): ensure_collection 每次 drop +
create (物理剔除软删文档, collection 名沿用 Django 侧 cp_kb_{id}).
schema 含软删有效标记 (valid, 索引时恒 True) 与来源 metadata
(doc_id/title/filename/chunk_index), 检索 filter 排除软删 (CONTRACT §6.6).

混合检索: 稠密 (dense_vector, IP) + 稀疏 (sparse_vector, SPARSE 倒排,
BM25 语义) 双 AnnSearchRequest + WeightedRanker 加权融合 (参考
rag_knowledge milvus_utils); filter expr 带 valid == true 与软删 doc_id
not in [...] (软删立即生效: 检索时实时向 Django 查询软删集合).
"""

import logging

from ..api import config

logger = logging.getLogger(__name__)

# 稠密/稀疏融合权重 (等权, bge-m3 双向量均已归一化)
RANKER_WEIGHTS = (0.5, 0.5)

_milvus_client = None


def get_milvus_client():
    """MilvusClient 惰性单例 (测试 monkeypatch 本函数注入假件, 不连真库)."""
    global _milvus_client
    if _milvus_client is None:
        from pymilvus import MilvusClient

        logger.info("初始化 Milvus 客户端 (uri=%s)", config.MILVUS_URL)
        _milvus_client = MilvusClient(config.MILVUS_URL)
    return _milvus_client


def _build_schema(dim: int) -> object:
    """collection schema: 来源 metadata + 双向量字段 (稠密 1024 维 bge-m3)."""
    from pymilvus import CollectionSchema, DataType, FieldSchema

    return CollectionSchema(
        fields=[
            # 主键 = doc_id + chunk_index 组合 (全量重建 drop+create, 无残留)
            FieldSchema(
                name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64
            ),
            FieldSchema(name="kb_id", dtype=DataType.INT64),
            FieldSchema(name="doc_id", dtype=DataType.INT64),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="filename", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            # 软删有效标记: 索引时恒 True, 检索 filter 兜底排除
            FieldSchema(name="valid", dtype=DataType.BOOL),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="dense_vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
        ],
        enable_dynamic_field=False,
    )


def _build_index_params(client) -> object:
    """双向量索引: 稠密 HNSW/IP (L2 归一化后 IP=余弦), 稀疏倒排/IP.

    pymilvus 3.0 API: index_type/metric_type 传字符串, 经
    client.prepare_index_params() 构建 (IndexParams 未顶层导出),
    随 create_collection 一次创建 (无需单独 create_index).
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


def ensure_collection(collection_name: str, dim: int | None = None) -> None:
    """全量重建: drop 旧 collection + create + 双索引 (Q18b 物理剔除).

    dim 默认 config.EMBEDDING_DIM (bge-m3 1024 维); 若已存在同名
    collection 则丢弃重建 (任何变更触发全量重建, 数据一致).
    """
    client = get_milvus_client()
    dimension = dim or config.EMBEDDING_DIM
    if client.has_collection(collection_name):
        logger.info("重建 collection: drop 旧 %s (物理剔除软删)", collection_name)
        client.drop_collection(collection_name)
    client.create_collection(
        collection_name,
        schema=_build_schema(dimension),
        index_params=_build_index_params(client),
    )
    logger.info("collection %s 已创建 (dim=%s)", collection_name, dimension)


def insert_chunks(collection_name: str, rows: list[dict]) -> None:
    """批量写入 chunk 行 (含 dense/sparse 向量 + metadata)."""
    if not rows:
        return
    client = get_milvus_client()
    client.insert(collection_name, data=rows)
    logger.info("写入 %s 条 chunks → %s", len(rows), collection_name)


def _build_filter_expr(deleted_doc_ids: list[int]) -> str:
    """软删过滤表达式: 恒排除无效标记 + 软删 doc_id 集合 (立即生效)."""
    parts = ["valid == true"]
    if deleted_doc_ids:
        ids = ", ".join(str(doc_id) for doc_id in deleted_doc_ids)
        parts.append(f"doc_id not in [{ids}]")
    return " and ".join(parts)


def hybrid_search(
    collection_name: str,
    query_dense: list,
    query_sparse: dict,
    deleted_doc_ids: list[int] | None = None,
    limit: int = 20,
) -> list[dict]:
    """稠密 + 稀疏混合检索 (WeightedRanker 融合), filter 排除软删.

    返回 [{id, doc_id, title, filename, chunk_index, content, score}]
    (按融合分降序, 精排前 Top-K); Milvus 异常抛 RuntimeError (任务/接口
    层转 error, 可重试).
    """
    from pymilvus import AnnSearchRequest, WeightedRanker

    client = get_milvus_client()
    expr = _build_filter_expr(deleted_doc_ids or [])
    # 稠密/稀疏搜索各自召回 limit (融合后仍需足够候选供精排)
    dense_req = AnnSearchRequest(
        data=[query_dense],
        anns_field="dense_vector",
        param={"metric_type": "IP"},
        expr=expr,
        limit=limit,
    )
    sparse_req = AnnSearchRequest(
        data=[query_sparse],
        anns_field="sparse_vector",
        param={"metric_type": "IP"},
        expr=expr,
        limit=limit,
    )
    ranker = WeightedRanker(*RANKER_WEIGHTS, norm_score=True)
    try:
        raw = client.hybrid_search(
            collection_name=collection_name,
            reqs=[dense_req, sparse_req],
            ranker=ranker,
            limit=limit,
            output_fields=[
                "doc_id",
                "title",
                "filename",
                "chunk_index",
                "content",
                "valid",
            ],
        )
    except Exception as exc:
        logger.error("混合检索失败 (%s): %s", collection_name, exc)
        raise RuntimeError(f"Milvus 混合检索失败: {exc}") from exc

    results = []
    hits = raw[0] if raw else []
    for hit in hits:
        entity = hit.get("entity", {})
        results.append(
            {
                "doc_id": entity.get("doc_id"),
                "title": entity.get("title", ""),
                "filename": entity.get("filename", ""),
                "chunk_index": entity.get("chunk_index", 0),
                "content": entity.get("content", ""),
                "score": float(hit.get("distance", 0.0)),
            }
        )
    logger.info("混合检索返回 %d 条 (collection=%s)", len(results), collection_name)
    return results
