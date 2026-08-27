import json
from pathlib import Path

from ...infra.model import infra_model
from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import logger, step_log
from .config import EMBEDDING_BATCH_SIZE


@step_log("generate_chunk_embeddings")
def generate_chunk_embeddings(state: LoadState) -> LoadState:
    # 1. 获取并校验参数
    chunks = _validate_data(state)

    # 2. 批量chunks生成向量
    _batch_generate_vector(chunks)

    state["embeddings"] = chunks
    return state


@step_log("_validate_data")
def _validate_data(state: LoadState) -> list[dict[str, str]]:
    """获取并校验数据"""
    chunks: list[dict[str, str]] = state.get("chunks")

    md_path: str = state.get("md_path", "")
    md_path_obj: Path = Path(md_path) if md_path else None

    if not chunks:
        if md_path_obj.is_file():
            json_path_obj: Path = md_path_obj.with_name(f"{md_path_obj.stem}.json")
            if json_path_obj.is_file():
                chunks = json.loads(json_path_obj.read_text(encoding="utf-8"))
                state["chunks"] = chunks
            else:
                logger.error("chunks为空, 且json备份文件不存在")
                raise ValueError("chunks为空, 且json备份文件不存在")
        else:
            logger.error("chunks为空, 且Markdown文件不存在")
            raise ValueError("chunks为空, 且Markdown文件不存在")

    return chunks


@step_log("_batch_generate_vector")
def _batch_generate_vector(chunks: list[dict[str, str]]):
    """批量chunks生成向量"""
    count = 0
    for idx in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        current_chunks = chunks[idx : idx + EMBEDDING_BATCH_SIZE]
        content_list: list[str] = [
            f"{chunk.get('item_name')}_{chunk.get('content')}"
            for chunk in current_chunks
        ]
        result = infra_model.embedding(content_list)
        sparse_list, dense_list = result["sparse"], result["dense"]
        for idx, chunk in enumerate(current_chunks):
            chunk["sparse_vector"] = sparse_list[idx]
            chunk["dense_vector"] = dense_list[idx]

        count += len(current_chunks)
        logger.info(f"已生成的向量的chunks: {count}")

    logger.info("chunks 已批量生成向量")
    logger.debug(f"chunk-1_dense_vector: {chunks[0]['dense_vector'][:5]}")
