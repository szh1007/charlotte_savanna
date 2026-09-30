"""手工跑一遍查询图的**脚本**, 不是单元测试.

需要真实 Milvus / LLM / Mongo, 且此前在 import 期就执行 —— 作为 pytest 用例会在
**收集期**就炸 (issue C02 收口时改掉). `__test__ = False` 让 pytest 跳过整个模块;
要跑它仍然可以: `python -m tests.test_query_graph`.
"""

from rich import print as rprint

from app.process.query.agent.main_graph import graph
from app.process.query.agent.state import create_query_default_state

# pytest 见到它就整模块跳过 (这是脚本, 不是用例)
__test__ = False


def run_query_graph() -> None:
    test_state = create_query_default_state(
        session_id="test_query_graph",
        original_query="你好",
        is_stream=True,
    )
    result = graph.invoke(test_state)
    rprint(result)
    rprint(graph.get_graph().print_ascii())
    assert result["session_id"] == "test_query_graph"


if __name__ == "__main__":
    run_query_graph()
