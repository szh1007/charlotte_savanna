from ...infra.milvus import infra_milvus
from ...infra.model import infra_model
from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import logger, step_log


@step_log("search_by_embedding")
def search_by_embedding(state: QueryState) -> QueryState:
    # 1.获取并校验参数
    item_names, rewritten_query = _validate_data(state)

    # 2.向量数据库混合检索
    real_response = _select_chunks_in_milvus(item_names, rewritten_query)

    # 3.解析结果 embedding_chunks
    embedding_chunks = _after_deal_milvus_result(real_response)

    return embedding_chunks


@step_log("_validate_data")
def _validate_data(state: QueryState):
    item_names: list[str] = state.get("item_names", [])
    rewritten_query: str = state.get("rewritten_query")

    if not item_names or not rewritten_query:
        logger.error("item_names / rewritten_query 参数为空")
        raise ValueError("item_names / rewritten_query 参数为空")

    return item_names, rewritten_query


@step_log("_select_chunks_in_milvus")
def _select_chunks_in_milvus(item_names: list[str], rewritten_query: str):
    """向量检索"""
    # 1.获取 rewritten_query 的稠密/稀疏向量
    result = infra_model.embedding([rewritten_query])
    dense_vector = result["dense"][0]
    sparse_vector = result["sparse"][0]

    # 2.使用向量生成查询 AnnSearchRequest
    reqs_list = infra_milvus.create_requests(
        dense_vector=dense_vector,
        sparse_vector=sparse_vector,
        expr=f"item_name in {item_names}",
        limit=5 * 2,
    )

    # 3.混合查询
    response = infra_milvus.hybrid_search(
        collection_name=infra_milvus.chunks_collection,
        reqs=reqs_list,
        ranker_weights=(0.7, 0.3),  # rewritten_query检索, 同时兼顾双方权重
        norm_score=True,
        limit=5,
        output_fields=[
            "chunk_id",
            "file_title",
            "item_name",
            "parent_title",
            "title",
            "part",
            "content",
        ],
    )

    return response[0]


@step_log("_after_deal_milvus_result")
def _after_deal_milvus_result(real_response: list[dict]) -> list[dict]:
    """解析检索结果"""
    embedding_chunks: list[dict] = []
    if real_response:
        for item in real_response:
            chunk = item.get("entity", {})
            embedding_chunks.append(
                {
                    "chunk_id": chunk.get("chunk_id"),  # 必要, 后续测评用
                    "file_title": chunk.get("file_title"),
                    "item_name": chunk.get("item_name"),
                    "parent_title": chunk.get("parent_title"),
                    "title": chunk.get("title"),
                    "part": chunk.get("part"),
                    "content": chunk.get("content"),
                    "score": item.get("distance", 0.0),
                    "type": "milvus",
                }
            )

    logger.info(f"已完成问题向量检索, 检索的数量: {len(embedding_chunks)}")
    return embedding_chunks
