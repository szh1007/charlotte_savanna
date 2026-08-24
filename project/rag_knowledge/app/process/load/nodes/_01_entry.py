from rich import print as rprint

from ....rag.load.entry_service import resolve_input_file
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState, create_default_state


@node_log("node_entry")
def node_entry(state: LoadState) -> LoadState:
    """
    入口节点
    识别输入的文件类型, 修改 state 的对应状态
    """
    add_running_task(state["task_id"], "node_entry")
    state = resolve_input_file(state)
    add_done_task(state["task_id"], "node_entry")
    return state


if __name__ == "__main__":
    # rprint(node_entry(create_default_state(task_id="1", local_file_path="test.txt")))

    test_pdf = "./project/rag_knowledge/assets/hl3070使用说明书.pdf"
    rprint(node_entry(create_default_state(task_id="1", local_file_path=test_pdf)))
