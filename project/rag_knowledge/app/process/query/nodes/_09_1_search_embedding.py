import sys

from rich import print as rprint

from ....rag.query.embedding_search_service import search_by_embedding
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import QueryState, create_query_default_state


@node_log("node_search_embedding")
def node_search_embedding(state: QueryState) -> QueryState:
    """进行向量内容检索"""
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state.get("is_stream"))
    embedding_chunks = search_by_embedding(state)
    add_done_task(state["session_id"], cur_func_name, state.get("is_stream"))
    return {"embedding_chunks": embedding_chunks}


if __name__ == "__main__":
    state = create_query_default_state(
        item_names=["HAK_180烫金机"],
        rewritten_query="HAK180烫金机不放平会怎么样",
    )
    result = node_search_embedding(state)
    rprint(result)
