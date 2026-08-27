from ....rag.load.index_service import index_chunks
from ....shared.runtime.logger import PROJECT_ROOT, node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState, create_default_state
from ._06_bge_embedding import node_bge_embedding


@node_log("node_import_milvus")
def node_import_milvus(state: LoadState) -> LoadState:
    """导入向量库, 将处理好的向量数据写入 Milvus"""
    add_running_task(state["task_id"], "node_import_milvus")
    state = index_chunks(state)
    add_done_task(state["task_id"], "node_import_milvus")
    return state


if __name__ == "__main__":
    test_md_path = (
        PROJECT_ROOT / "output" / "hak180产品安全手册" / "hak180产品安全手册_new.md"
    )

    test_state = create_default_state(md_path=str(test_md_path))
    test_state = node_bge_embedding(test_state)
    result = node_import_milvus(test_state)
