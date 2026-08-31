import sys

from rich import print as rprint

from ....rag.query.hyde_search_service import search_by_hyde
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import QueryState, create_query_default_state


@node_log("node_search_embedding_hyde")
def node_search_embedding_hyde(state: QueryState) -> QueryState:
    """
    HyDE: 先让 LLM 生成假设性答案, 再对答案进行向量检索, 提高召回率
    为什么需要假设性答案
    1.问题本身内容表达不清晰、不完整
    2.重写问题 -> 模型生成初步答案, 重写问题 + 初步答案 -> 检索更合理
    3.直接联网检索有一些参考答案, 但是并不标准, 不是定制化
    """
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state.get("is_stream"))
    hyde_embedding_chunks = search_by_hyde(state)
    add_done_task(state["session_id"], cur_func_name, state.get("is_stream"))
    return {"hyde_embedding_chunks": hyde_embedding_chunks}


if __name__ == "__main__":
    state = create_query_default_state(
        item_names=["HAK_180烫金机"],
        rewritten_query="HAK180烫金机不放平会怎么样",
    )
    result = node_search_embedding_hyde(state)
    rprint(result)
