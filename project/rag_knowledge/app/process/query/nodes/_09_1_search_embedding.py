import sys

from ....rag.query.embedding_search_service import search_by_embedding
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task


@node_log("node_search_embedding")
def node_search_embedding(state):
    """进行向量内容检索"""
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state.get("is_stream"))
    state = search_by_embedding(state)
    add_done_task(state["session_id"], cur_func_name, state.get("is_stream"))
    return {"embedding_chunks": []}
