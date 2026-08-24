from rich import print as rprint

from ..app.process.load.agent.main_graph import graph
from ..app.process.load.agent.state import create_default_state


def test_load_graph() -> None:
    """加载图完整执行: PDF 读取路径"""
    test_state = create_default_state(
        task_id="test_load_graph",
        local_file_path="test.pdf",
        is_md_read_enabled=False,
        is_pdf_read_enabled=True,
    )
    result = graph.invoke(test_state)
    rprint(result)
    rprint(graph.get_graph().print_ascii())
    assert result["task_id"] == "test_load_graph"


test_load_graph()
