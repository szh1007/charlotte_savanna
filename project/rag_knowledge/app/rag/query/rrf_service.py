from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import logger, step_log
from .config import MILVUS_CHUNK_RRF_TOP_K


@step_log("_validate_data")
def _validate_data(state: QueryState):
    """获取核心参数并且校验"""

    embedding_chunks = state.get("embedding_chunks")
    hyde_embedding_chunks = state.get("hyde_embedding_chunks")

    if not embedding_chunks or not hyde_embedding_chunks:
        logger.error("embedding_chunks / hyde_embedding_chunks 参数为空")
        raise ValueError("embedding_chunks / hyde_embedding_chunks 参数为空")
    return embedding_chunks, hyde_embedding_chunks


@step_log("_use_rrf_rank")
def _use_rrf_rank(weights: list, k: int = 60):
    """
    使用rrf的权重排名 (官方默认设置 k=60)
    rrf_score = weight * (1 / (k + rank))
    """
    # 实时记录每个 chunk 的累加的 rrf_score
    chunk_score: dict[str, dict] = {}
    # 记录所有去重后的 chunk
    chunk_dict: dict[str, dict] = {}

    for weight, chunks in weights:
        for rank, chunk in enumerate(chunks, start=1):
            chunk_id = chunk.get("chunk_id")

            rrf_score = weight * (1 / (k + rank))
            current_rrf_score = chunk_score.get(chunk_id, 0.0) + rrf_score

            chunk_score[chunk_id] = current_rrf_score
            chunk["score"] = current_rrf_score

            chunk_dict[chunk_id] = chunk

    rrf_chunks: list[dict] = list(chunk_dict.values())

    # rrf_chunks 根据分数排序
    bef_sort_score_list = [c.get("score", 0.0) for c in rrf_chunks]
    logger.debug(f"排序之前, socre list: {bef_sort_score_list}")
    rrf_chunks.sort(key=lambda c: c.get("score", 0.0), reverse=True)
    aft_sort_score_list = [c.get("score", 0.0) for c in rrf_chunks]
    logger.debug(f"排序之后, socre list: {aft_sort_score_list}")

    # 截取分最高的topk
    return rrf_chunks[:MILVUS_CHUNK_RRF_TOP_K]


@step_log("fuse_by_rrf")
def fuse_by_rrf(state: QueryState) -> QueryState:
    # 1.获取并校验参数
    embedding_chunks, hyde_embedding_chunks = _validate_data(state)

    # 2.RRF排名融合
    weights = [
        (0.5, embedding_chunks),
        (0.5, hyde_embedding_chunks),
    ]
    rrf_chunks = _use_rrf_rank(weights)

    state["rrf_chunks"] = rrf_chunks
    return state
