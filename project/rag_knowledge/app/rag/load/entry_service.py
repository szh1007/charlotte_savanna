from pathlib import Path

from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import logger, step_log


@step_log("resolve_input_file")
def resolve_input_file(state: LoadState) -> LoadState:
    local_file_path = state.get("local_file_path")
    if not local_file_path:
        logger.error(f"local_file_path 参数为空: {local_file_path}")
        raise ValueError(f"local_file_path 参数为空: {local_file_path}")

    if local_file_path.lower().endswith(".md"):
        state["md_path"] = local_file_path
        state["pdf_path"] = None
        state["is_md_read_enabled"] = True
        state["is_pdf_read_enabled"] = False
        logger.info(f"识别到Markdown文件: {local_file_path}")

    elif local_file_path.lower().endswith(".pdf"):
        state["md_path"] = None
        state["pdf_path"] = local_file_path
        state["is_md_read_enabled"] = False
        state["is_pdf_read_enabled"] = True
        logger.info(f"识别到PDF文件: {local_file_path}")

    else:
        logger.error(f"不支持的文件类型(md/pdf): {local_file_path}")
        raise ValueError(f"不支持的文件类型(md/pdf): {local_file_path}")

    local_file_path_obj = Path(local_file_path)
    if not local_file_path_obj.is_file():
        logger.error(f"文件不存在/不是文件: {local_file_path_obj}")
        raise FileNotFoundError(f"文件不存在/不是文件: {local_file_path_obj}")

    state["file_title"] = local_file_path_obj.stem
    return state
