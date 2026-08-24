from ....rag.load.pdf_parse_service import parse_pdf_to_markdown
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState


@node_log("node_pdf_to_md")
def node_pdf_to_md(state: LoadState) -> LoadState:
    """PDF转Markdown"""
    add_running_task(state["task_id"], "node_pdf_to_md")
    state = parse_pdf_to_markdown(state)
    add_done_task(state["task_id"], "node_pdf_to_md")
    return state
