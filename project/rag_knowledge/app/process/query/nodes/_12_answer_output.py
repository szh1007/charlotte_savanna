import sys

from ....rag.query.answer_service import generate_answer
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task


@node_log("node_answer_output")
def node_answer_output(state):
    """生成最终回答交付给用户 (支持流式/非流式)"""
    cur_func_name = sys._getframe().f_code.co_name
    add_running_task(state["session_id"], cur_func_name, state["is_stream"])
    state = generate_answer(state)
    add_done_task(state["session_id"], cur_func_name, state["is_stream"])
    return state
