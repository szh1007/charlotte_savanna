import sys

from ....rag.query.rrf_service import fuse_by_rrf
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task


@node_log("node_rrf")
def node_rrf(state):
    """RRF: 将多路召回的结果(向量、HyDE、Web)进行加权融合排序"""
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state.get("is_stream"))
    state = fuse_by_rrf(state)
    add_done_task(state["session_id"], cur_func_name, state.get("is_stream"))
    return state
