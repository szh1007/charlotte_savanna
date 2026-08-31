import sys

from ....rag.query.web_search_service import search_by_web
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import QueryState


@node_log("node_web_search_mcp")
def node_web_search_mcp(state: QueryState) -> QueryState:
    """调用外部搜索引擎补充信息"""
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state["is_stream"])
    state = search_by_web(state)
    add_done_task(state["session_id"], cur_func_name, state["is_stream"])
    return {"web_search_docs": []}
