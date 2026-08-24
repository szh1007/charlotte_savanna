from ....rag.load.index_service import index_chunks
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState


@node_log("node_import_milvus")
def node_import_milvus(state: LoadState) -> LoadState:
    """导入向量库, 将处理好的向量数据写入 Milvus"""
    add_running_task(state["task_id"], "node_import_milvus")
    state = index_chunks(state)
    add_done_task(state["task_id"], "node_import_milvus")
    return state
