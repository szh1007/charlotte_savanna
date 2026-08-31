import sys

from rich import print as rprint

from ....rag.query.item_name_confirm_service import confirm_item_name
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import QueryState, create_query_default_state


@node_log("node_item_name_confirm")
def node_item_name_confirm(state: QueryState) -> QueryState:
    """确认用户问题中的核心主体名称"""
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state["is_stream"])
    state = confirm_item_name(state)
    add_done_task(state["session_id"], cur_func_name, state["is_stream"])
    return state


if __name__ == "__main__":
    state = create_query_default_state(
        session_id="test_08_item_name_confirm",
        original_query="华为手机怎么样",
    )
    state = node_item_name_confirm(state)
    rprint(state)
