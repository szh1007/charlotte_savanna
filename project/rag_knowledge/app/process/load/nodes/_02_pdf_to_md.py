from rich import print as rprint

from ....rag.load.pdf_parse_service import parse_pdf_to_markdown
from ....shared.runtime.logger import PROJECT_ROOT, node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState, create_default_state


@node_log("node_pdf_to_md")
def node_pdf_to_md(state: LoadState) -> LoadState:
    """PDF转Markdown"""
    add_running_task(state["task_id"], "node_pdf_to_md")
    state = parse_pdf_to_markdown(state)
    add_done_task(state["task_id"], "node_pdf_to_md")
    return state


if __name__ == "__main__":
    test_pdf_path = PROJECT_ROOT / "assets" / "hak180产品安全手册.pdf"

    test_state = create_default_state(
        task_id="test_02_pdf_to_md",
        local_file_path=str(test_pdf_path),
        md_path=None,
        pdf_path=str(test_pdf_path),
        local_dir=str(PROJECT_ROOT / "output"),
        file_title=test_pdf_path.stem,
        is_md_read_enabled=False,
        is_pdf_read_enabled=True,
    )
    result = node_pdf_to_md(test_state)
    rprint(result)
