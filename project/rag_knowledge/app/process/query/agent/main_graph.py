from langgraph.graph import END, StateGraph

from ..nodes._08_item_name_confirm import node_item_name_confirm
from ..nodes._09_1_search_embedding import node_search_embedding
from ..nodes._09_2_search_embedding_hyde import node_search_embedding_hyde
from ..nodes._09_3_web_search_mcp import node_web_search_mcp
from ..nodes._10_rrf import node_rrf
from ..nodes._11_rerank import node_rerank
from ..nodes._12_answer_output import node_answer_output
from .state import QueryState


def router_after_item_name_confirm(state: QueryState):
    """路由函数"""
    if state.get("answer"):
        # 未识别出 item_name, 或者识别出的置信度不高, 直接回答检索不到相关信息
        return "DIRECT_OUTPUT"
    else:
        # 识别出 item_name, 正常进行多路召回 + 混合检索 + 重排序, 最后输出检索结果
        return "COMMON_SEARCH", "HYDE", "WEB_SEARCH"


graph = (
    StateGraph(state_schema=QueryState)
    .add_node(node_item_name_confirm)
    .add_node(node_search_embedding)
    .add_node(node_search_embedding_hyde)
    .add_node(node_web_search_mcp)
    .add_node(node_rrf)
    .add_node(node_rerank)
    .add_node(node_answer_output)
    .set_entry_point("node_item_name_confirm")
    .add_conditional_edges(
        "node_item_name_confirm",
        router_after_item_name_confirm,
        path_map={
            "DIRECT_OUTPUT": "node_answer_output",
            "COMMON_SEARCH": "node_search_embedding",
            "HYDE": "node_search_embedding_hyde",
            "WEB_SEARCH": "node_web_search_mcp",
        },
    )
    .add_edge("node_search_embedding", "node_rrf")
    .add_edge("node_search_embedding_hyde", "node_rrf")
    .add_edge("node_web_search_mcp", "node_rrf")
    .add_edge("node_rrf", "node_rerank")
    .add_edge("node_rerank", "node_answer_output")
    .add_edge("node_answer_output", END)
).compile()
