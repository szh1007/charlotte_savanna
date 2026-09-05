import asyncio

from langgraph.graph import END, StateGraph

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.clients.embedding import embedding_client
from app.clients.es import es_client
from app.clients.mysql import dw_client, meta_client
from app.clients.qdrant import qdrant_client
from app.repositories.qdrant.column import ColumnQdrantRepository
from app.repositories.qdrant.metric import MetricQdrantRepository

from .nodes._1_extract_keywords import extract_keywords
from .nodes._2_1_recall_column import recall_column
from .nodes._2_2_recall_metric import recall_metric
from .nodes._2_3_recall_value import recall_value
from .nodes._3_merge_retrieve import merge_retrieve
from .nodes._4_1_filter_table import filter_table
from .nodes._4_2_filter_metric import filter_metric
from .nodes._5_pad_context import pad_context
from .nodes._6_generate_sql import generate_sql
from .nodes._7_validate_sql import validate_sql
from .nodes._8_correct_sql import correct_sql
from .nodes._9_execute_sql import execute_sql

graph = (
    StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)
    .add_node("extract_keywords", extract_keywords)
    .add_node("recall_column", recall_column)
    .add_node("recall_metric", recall_metric)
    .add_node("recall_value", recall_value)
    .add_node("merge_retrieve", merge_retrieve)
    .add_node("filter_table", filter_table)
    .add_node("filter_metric", filter_metric)
    .add_node("pad_context", pad_context)
    .add_node("generate_sql", generate_sql)
    .add_node("validate_sql", validate_sql)
    .add_node("correct_sql", correct_sql)
    .add_node("execute_sql", execute_sql)
    .set_entry_point("extract_keywords")
    .add_edge("extract_keywords", "recall_column")
    .add_edge("extract_keywords", "recall_metric")
    .add_edge("extract_keywords", "recall_value")
    .add_edge("recall_column", "merge_retrieve")
    .add_edge("recall_metric", "merge_retrieve")
    .add_edge("recall_value", "merge_retrieve")
    .add_edge("merge_retrieve", "filter_table")
    .add_edge("merge_retrieve", "filter_metric")
    .add_edge("filter_table", "pad_context")
    .add_edge("filter_metric", "pad_context")
    .add_edge("pad_context", "generate_sql")
    .add_edge("generate_sql", "validate_sql")
    .add_conditional_edges(
        "validate_sql",
        lambda state: "EXECUTE" if state["error"] is None else "CORRECT",
        path_map={
            "EXECUTE": "execute_sql",
            "CORRECT": "correct_sql",
        },
    )
    .add_edge("correct_sql", "execute_sql")
    .add_edge("execute_sql", END)
).compile()


if __name__ == "__main__":
    # print(graph.get_graph().draw_mermaid())

    async def test():
        # 1.初始化客户端
        dw_client.init()
        meta_client.init()
        qdrant_client.init()
        embedding_client.init()
        es_client.init()

        # 2.创建上下文
        context = DataAgentContext(
            embeddings=embedding_client.embeddings,
            column_qdrant_repository=ColumnQdrantRepository(qdrant_client.client),
            metric_qdrant_repository=MetricQdrantRepository(qdrant_client.client),
        )

        # 3.测试执行
        async for chunk in graph.astream(
            input=DataAgentState(query="统计华北地区的销售总额"),
            context=context,
            stream_mode="custom",
        ):
            print(chunk)

        # 4.释放资源
        await dw_client.close()
        await meta_client.close()
        await qdrant_client.close()
        await es_client.close()

    asyncio.run(test())
