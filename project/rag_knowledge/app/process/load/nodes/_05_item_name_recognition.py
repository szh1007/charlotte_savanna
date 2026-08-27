from rich import print as rprint

from ....rag.load.item_name_service import recognize_and_index_item_name
from ....shared.runtime.logger import PROJECT_ROOT, node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState, create_default_state


@node_log("node_item_name_recognition")
def node_item_name_recognition(state: LoadState) -> LoadState:
    """主体识别, 识别文档核心描述的主体名称"""
    add_running_task(state["task_id"], "node_item_name_recognition")
    state = recognize_and_index_item_name(state)
    add_done_task(state["task_id"], "node_item_name_recognition")
    return state


if __name__ == "__main__":
    # python -m project.rag_knowledge.app.process.load.nodes._05_item_name_recognition
    test_pdf_path = PROJECT_ROOT / "assets" / "hak180产品安全手册.pdf"
    test_md_path = (
        PROJECT_ROOT / "output" / test_pdf_path.stem / f"{test_pdf_path.stem}_new.md"
    )

    test_state = create_default_state(md_path=str(test_md_path))
    result = node_item_name_recognition(test_state)
    rprint(result["item_name"])
