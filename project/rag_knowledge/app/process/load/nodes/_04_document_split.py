from rich import print as rprint

from ....rag.load.split_service import split_document
from ....shared.runtime.logger import PROJECT_ROOT, node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState, create_default_state

"""
语义完成 -> 语义切割 + LangChain 递归切割
长度可控 -> chunk 适配上下文窗口和 Embedding 模型输入限制
边界清晰 -> 相邻 chunk 保留合理重叠, 避免丢失交界处的关键信息
可追溯   -> chunk 记录完成的【元数据】, 便于溯源和引用

## 参考
常规技术文档: 2000 + 5%~8%
论文/报告(长叙述文本): 4000 + 8%~12%
对话/日志/工单: 1000 + 15%~20%
法律/合同/制度文档(结构严谨): 1500 + 5%~10%
"""


@node_log("node_document_split")
def node_document_split(state: LoadState) -> LoadState:
    """长文档切分成 Chunks"""
    add_running_task(state["task_id"], "node_document_split")
    state = split_document(state)
    add_done_task(state["task_id"], "node_document_split")
    return state


if __name__ == "__main__":
    test_pdf_path = PROJECT_ROOT / "assets" / "hak180产品安全手册.pdf"
    test_md_path = (
        PROJECT_ROOT / "output" / test_pdf_path.stem / f"{test_pdf_path.stem}_new.md"
    )

    test_state = create_default_state(
        task_id="test_04_document_split",
        local_file_path=str(test_pdf_path),
        md_path=str(test_md_path),
        pdf_path=str(test_pdf_path),
        local_dir=str(PROJECT_ROOT / "output"),
        file_title=test_pdf_path.stem,
        is_md_read_enabled=True,
        is_pdf_read_enabled=True,
    )
    result = node_document_split(test_state)
    rprint(result)
