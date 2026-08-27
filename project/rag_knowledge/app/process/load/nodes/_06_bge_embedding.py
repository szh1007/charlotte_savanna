from ....rag.load.embedding_service import generate_chunk_embeddings
from ....shared.runtime.logger import PROJECT_ROOT, node_log
from ....shared.utils.task_utils import add_done_task, add_running_task
from ..agent.state import LoadState, create_default_state


@node_log("node_bge_embedding")
def node_bge_embedding(state: LoadState) -> LoadState:
    """向量化, 使用 BGE-M3 模型将文本转换为向量"""
    add_running_task(state["task_id"], "node_bge_embedding")
    state = generate_chunk_embeddings(state)
    add_done_task(state["task_id"], "node_bge_embedding")
    return state


if __name__ == "__main__":
    # python -m project.rag_knowledge.app.process.load.nodes._06_bge_embedding
    test_pdf_path = PROJECT_ROOT / "assets" / "hak180产品安全手册.pdf"
    test_md_path = (
        PROJECT_ROOT / "output" / test_pdf_path.stem / f"{test_pdf_path.stem}_new.md"
    )

    test_state = create_default_state(md_path=str(test_md_path))
    result = node_bge_embedding(test_state)
