from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser

from ...infra.milvus import infra_milvus
from ...infra.model import infra_model
from ...process.query.agent.state import QueryState
from ...shared.runtime.load_prompt import load_prompt
from ...shared.runtime.logger import logger, step_log
from .config import MILVUS_CHUNK_RRF_TOP_K


@step_log("search_by_hyde")
def search_by_hyde(state: QueryState) -> QueryState:
    # 1.获取并校验参数
    item_names, rewritten_query = _validate_data(state)

    # 2.根据问题生成假设性答案
    hyde_answer = _call_llm_by_rewritten_query(rewritten_query)

    # 3.向量数据库混合检索
    real_response = _select_chunks_in_milvus(item_names, rewritten_query, hyde_answer)

    # 4.解析结果 embedding_chunks
    embedding_chunks_hyde = _after_deal_milvus_result(real_response)

    return embedding_chunks_hyde


@step_log("_validate_data")
def _validate_data(state: QueryState):
    item_names: list[str] = state.get("item_names", [])
    rewritten_query: str = state.get("rewritten_query")

    if not item_names or not rewritten_query:
        logger.error("item_names / rewritten_query 参数为空")
        raise ValueError("item_names / rewritten_query 参数为空")

    return item_names, rewritten_query


@step_log("_call_llm_by_rewritten_query")
def _call_llm_by_rewritten_query(rewritten_query: str) -> str:
    """根据问题生成假设性答案"""
    llm_client = infra_model.llm_model()

    hyde_prompt: str = load_prompt("hyde_prompt", rewritten_query=rewritten_query)
    chains = llm_client | StrOutputParser()

    hyde_answer = chains.invoke([HumanMessage(hyde_prompt)])
    logger.info(
        f"已基于改写的问题: {rewritten_query}, 让模型初步生成假设性答案: {hyde_answer}"
    )

    return hyde_answer


@step_log("_select_chunks_in_milvus")
def _select_chunks_in_milvus(
    item_names: list[str],
    rewritten_query: str,
    hyde_answer: str,
):
    """向量检索"""
    # 1.获取 rewritten_query + hyde_answer 的稠密/稀疏向量
    text = f"问题: {rewritten_query}, 假设性答案: {hyde_answer}"
    result = infra_model.embedding([text])
    dense_vector = result["dense"][0]
    sparse_vector = result["sparse"][0]

    # 2.使用向量生成查询 AnnSearchRequest
    reqs_list = infra_milvus.create_requests(
        dense_vector=dense_vector,
        sparse_vector=sparse_vector,
        expr=f"item_name in {item_names}",
        limit=MILVUS_CHUNK_RRF_TOP_K * 2,
    )

    # 3.混合查询
    response = infra_milvus.hybrid_search(
        collection_name=infra_milvus.chunks_collection,
        reqs=reqs_list,
        ranker_weights=(0.7, 0.3),  # rewritten_query检索, 同时兼顾双方权重
        norm_score=True,
        limit=MILVUS_CHUNK_RRF_TOP_K,
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
    embedding_chunks_hyde: list[dict] = []
    if real_response:
        for item in real_response:
            chunk = item.get("entity", {})
            embedding_chunks_hyde.append(
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

    logger.info(f"已完成问题向量检索, 检索的数量: {len(embedding_chunks_hyde)}")
    return embedding_chunks_hyde
