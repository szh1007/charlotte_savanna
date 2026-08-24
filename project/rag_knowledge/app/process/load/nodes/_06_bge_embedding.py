from ....rag.load.embedding_service import generate_chunk_embeddings
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState


@node_log("node_bge_embedding")
def node_bge_embedding(state: LoadState) -> LoadState:
    """向量化, 使用 BGE-M3 模型将文本转换为向量"""
    add_running_task(state["task_id"], "node_bge_embedding")
    state = generate_chunk_embeddings(state)
    add_done_task(state["task_id"], "node_bge_embedding")
    return state
