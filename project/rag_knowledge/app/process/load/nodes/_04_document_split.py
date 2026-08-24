from ....rag.load.split_service import split_document
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState


@node_log("node_document_split")
def node_document_split(state: LoadState) -> LoadState:
    """长文档切分成 Chunks"""
    add_running_task(state["task_id"], "node_document_split")
    state = split_document(state)
    add_done_task(state["task_id"], "node_document_split")
    return state
