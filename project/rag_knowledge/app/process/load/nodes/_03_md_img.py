from ....rag.load.enrich_markdown_images import enrich_markdown_images
from ....shared.runtime.logger import node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState


@node_log("node_md_img")
def node_md_img(state: LoadState) -> LoadState:
    """处理 Markdown 中的图片资源"""
    add_running_task(state["task_id"], "node_md_img")
    state = enrich_markdown_images(state)
    add_done_task(state["task_id"], "node_md_img")
    return state
