import sys

from ....rag.query.hyde_search_service import search_by_hyde
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import QueryState


@node_log("node_search_embedding_hyde")
def node_search_embedding_hyde(state: QueryState) -> QueryState:
    """HyDE: 先让 LLM 生成假设性答案, 再对答案进行向量检索, 提高召回率"""
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state.get("is_stream"))
    state = search_by_hyde(state)
    add_done_task(state["session_id"], cur_func_name, state.get("is_stream"))
    return {"embedding_chunks_hyde": []}
