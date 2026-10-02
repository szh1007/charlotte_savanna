"""state schema 契约: 节点写进 state 的字段必须在 `QueryState` 里声明.

LangGraph 只把 schema 里声明过的 key 当 channel —— 节点返回未声明的 key 会被
**静默丢弃**: 图照跑、不报错, 只是下游节点拿不到、最终输出里少一个字段.

这个坑真实踩过: `rerank_service` 曾往 state 塞一个没声明的中间字段, 线上走图时
它直接消失 —— 而评测因为**直接调节点函数**(不经过图), 反而看不出任何异常.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.process.query.agent.state import QueryState, create_query_default_state


def _run_one_node_through_graph(node) -> dict:
    """用真实 QueryState 建最小图跑一个节点, 返回下游节点看到的 state."""
    seen: dict = {}

    def capture(state):
        seen.update(state)
        return {}

    graph = (
        StateGraph(state_schema=QueryState)
        .add_node(node)
        .add_node(capture)
        .set_entry_point(node.__name__)
        .add_edge(node.__name__, "capture")
        .add_edge("capture", END)
    ).compile()
    graph.invoke(create_query_default_state())
    return seen


def test_declared_fields_survive_the_graph():
    """声明过的字段要能穿过图 —— 这是节点之间传数据的前提."""

    def fake_node(state):
        return {
            "reranked_docs": [{"chunk_id": "1"}],
            "answer": "最终答案",
        }

    seen = _run_one_node_through_graph(fake_node)

    assert seen.get("reranked_docs") == [{"chunk_id": "1"}]
    assert seen.get("answer") == "最终答案"


def test_undeclared_state_key_is_dropped_by_the_graph():
    """反例本体: 没声明过的 key 确实会被丢掉, 而且不报错.

    钉的是 LangGraph 的行为契约 —— 哪天它改成报错或保留, 这个用例会红,
    提醒重新审视上面那条断言的前提.
    """

    def fake_node(state):
        return {"reranked_docs": [1], "not_declared_anywhere": [1, 2, 3]}

    seen = _run_one_node_through_graph(fake_node)

    assert "not_declared_anywhere" not in seen
    assert seen.get("reranked_docs") == [1], "声明过的字段要照常送达"
