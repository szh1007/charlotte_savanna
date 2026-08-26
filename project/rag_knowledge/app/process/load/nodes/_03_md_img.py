from rich import print as rprint

from ....rag.load.enrich_markdown_images import enrich_markdown_images
from ....shared.runtime.logger import PROJECT_ROOT, node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState, create_default_state


@node_log("node_md_img")
def node_md_img(state: LoadState) -> LoadState:
    """处理 Markdown 中的图片资源"""
    add_running_task(state["task_id"], "node_md_img")
    state = enrich_markdown_images(state)
    add_done_task(state["task_id"], "node_md_img")
    return state


if __name__ == "__main__":
    test_pdf_path = PROJECT_ROOT / "assets" / "hak180产品安全手册.pdf"
    test_md_path = (
        PROJECT_ROOT / "output" / test_pdf_path.stem / f"{test_pdf_path.stem}.md"
    )

    test_state = create_default_state(
        task_id="test_03_md_img",
        local_file_path=str(test_pdf_path),
        md_path=str(test_md_path),
        pdf_path=str(test_pdf_path),
        local_dir=str(PROJECT_ROOT / "output"),
        file_title=test_pdf_path.stem,
        is_md_read_enabled=True,
        is_pdf_read_enabled=True,
    )
    result = node_md_img(test_state)
    rprint(result)
