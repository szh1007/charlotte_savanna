import sys

from ....rag.query.rerank_service import rerank_documents
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task


@node_log("node_rerank")
def node_rerank(state):
    """使用 rerank 模型对 RRF 后的结果进行精确打分重排"""
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state.get("is_stream"))
    state = rerank_documents(state)
    add_done_task(state["session_id"], cur_func_name, state.get("is_stream"))
    return state
