from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import logger, step_log
from .config import MILVUS_CHUNK_RRF_TOP_K


@step_log("_collect_route_chunks")
def _collect_route_chunks(state: QueryState):
    """获取两路召回结果.

    **单路为空不是错误**: 它只意味着这一路没有贡献 (知识库里没有该主体的内容,
    或某一路的外部依赖没返回东西). 上一版在这里 raise, 于是「单路为空」会把整条
    问答链变成 500 —— 评测里必须塞一条假 web 文档才跑得起来, 正是这条硬伤的副产品
    (issue C02). 两路都空时同样不抛, 由 `_use_rrf_rank` 返回空列表,
    交给作答节点走「检索不到」的正常分支.
    """
    embedding_chunks = state.get("embedding_chunks") or []
    hyde_embedding_chunks = state.get("hyde_embedding_chunks") or []

    if not embedding_chunks or not hyde_embedding_chunks:
        logger.warning(
            "召回有一路为空, 按「这一路没有贡献」降级继续: "
            f"embedding={len(embedding_chunks)} hyde={len(hyde_embedding_chunks)}"
        )
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
    # 1.取两路召回结果 (空值不抛, 见 _collect_route_chunks)
    embedding_chunks, hyde_embedding_chunks = _collect_route_chunks(state)

    # 2.RRF排名融合
    weights = [
        (0.5, embedding_chunks),
        (0.5, hyde_embedding_chunks),
    ]
    rrf_chunks = _use_rrf_rank(weights)

    state["rrf_chunks"] = rrf_chunks
    return state
