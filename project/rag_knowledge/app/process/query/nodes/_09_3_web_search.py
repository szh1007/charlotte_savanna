import sys

from rich import print as rprint

from ....rag.query.web_search_service import search_by_web
from ....shared.runtime.logger import node_log
from ....shared.runtime.route_isolation import isolate_route
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import QueryState, create_query_default_state


@node_log("node_web_search")
def node_web_search(state: QueryState) -> QueryState:
    """
    调用外部搜索引擎补充信息
    弥补本地知识库文件老旧, 内容残缺的问题

    名字里去掉了 `_mcp`: 这一路走的是 **Tavily SDK** (`_call_tavily_search`),
    那段 MCP 调用代码从来没被启用过, 已删 (issue C02). 名实不符会让人以为
    这条链路依赖 MCP.
    """
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state["is_stream"])
    web_search_docs = isolate_route("联网召回", lambda: search_by_web(state), [])
    add_done_task(state["session_id"], cur_func_name, state["is_stream"])
    return {"web_search_docs": web_search_docs}


if __name__ == "__main__":
    state = create_query_default_state(
        item_names=["HAK_180烫金机"],
        rewritten_query="HAK180烫金机不放平会怎么样",
    )
    result = search_by_web(state)
    rprint(result)
