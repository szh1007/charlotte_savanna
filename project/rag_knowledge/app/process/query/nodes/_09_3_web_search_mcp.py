import sys

from rich import print as rprint

from ....rag.query.web_search_service import search_by_web
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import QueryState, create_query_default_state


@node_log("node_web_search_mcp")
def node_web_search_mcp(state: QueryState) -> QueryState:
    """
    调用外部搜索引擎补充信息
    弥补本地知识库文件老旧, 内容残缺的问题
    """
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state["is_stream"])
    web_search_docs = search_by_web(state)
    add_done_task(state["session_id"], cur_func_name, state["is_stream"])
    return {"web_search_docs": web_search_docs}


if __name__ == "__main__":
    state = create_query_default_state(
        item_names=["HAK_180烫金机"],
        rewritten_query="HAK180烫金机不放平会怎么样",
    )
    result = search_by_web(state)
    rprint(result)
