from rich import print as rprint

from ..app.process.query.agent.main_graph import graph
from ..app.process.query.agent.state import create_query_default_state


def test_query_graph() -> None:
    test_state = create_query_default_state(
        session_id="test_query_graph",
        original_query="你好",
        is_stream=True,
    )
    result = graph.invoke(test_state)
    rprint(result)
    rprint(graph.get_graph().print_ascii())
    assert result["session_id"] == "test_query_graph"


test_query_graph()
