"""手工跑一遍加载图的**脚本**, 不是单元测试.

它需要真实 MySQL / Milvus / MinerU, 且在 import 期就执行 —— 作为 pytest 用例会在
**收集期**就炸 (issue C02 收口时改掉). `__test__ = False` 让 pytest 跳过整个模块;
要跑它仍然可以: `python -m tests.test_load_graph`.
"""

import pytest
from rich import print as rprint

from app.process.load.agent.main_graph import graph
from app.process.load.agent.state import create_default_state

# pytest 见到它就整模块跳过 (这是脚本, 不是用例)
__test__ = False
# 再挂 integration 标记: `-m "not integration"` 的全量跑默认排除这类脚本
pytestmark = pytest.mark.integration


def run_load_graph() -> None:
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


if __name__ == "__main__":
    run_load_graph()
