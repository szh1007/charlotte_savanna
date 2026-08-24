from ....rag.load.item_name_service import recognize_and_index_item_name
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState


@node_log("node_item_name_recognition")
def node_item_name_recognition(state: LoadState) -> LoadState:
    """主体识别, 识别文档核心描述的主体名称"""
    add_running_task(state["task_id"], "node_item_name_recognition")
    state = recognize_and_index_item_name(state)
    add_done_task(state["task_id"], "node_item_name_recognition")
    return state
