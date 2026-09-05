from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.prompt_loader import load_prompt
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.qdrant import ColumnInfoQdrant


async def recall_column(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "召回字段信息"})

    try:
        # 获取数据和上下文
        query = state["query"]
        keywords = state["keywords"]
        embeddings = runtime.context["embeddings"]
        column_qr = runtime.context["column_qdrant_repository"]

        # 1.字段扩展

        # 加载提示词
        template = await load_prompt("extend_keywords_for_column_recall")
        prompt = PromptTemplate(template=template, input_variables=["query"])

        # 定义 chain
        output_parser = JsonOutputParser()
        chain = prompt | llm | output_parser

        # LLM 执行
        result = await chain.ainvoke({"query": query})

        # 合并关键词
        merged_keywords = list(set(keywords + result))
        extend_keywords = [k for k in merged_keywords if k not in keywords]
        logger.info(f"关键词列表 - LLM字段扩展完成\n{keywords} + {extend_keywords}")

        # 2.字段召回 - qdrant
        # 定义字典结构去除召回的重复字段信息
        # 因为字段信息存储qdrant时, 同一个字段根据 name, description, alias 存储了多次
        # 检索同一个字段的这3个属性如果相似度都较高, 就会重复召回, 所以需要去重
        retrieved_column_map: dict[str, ColumnInfoQdrant] = {}
        for keyword in merged_keywords:
            embedding = await embeddings.aembed_query(keyword)
            payloads: list[ColumnInfoQdrant] = await column_qr.search(embedding)

            # 遍历召回结果
            for payload in payloads:
                column_id = payload["id"]
                if column_id not in retrieved_column_map:
                    retrieved_column_map[column_id] = payload

        # 获取召回字段列表
        retrieved_columns = list(retrieved_column_map.values())

        logger.info(f"字段信息召回成功\n{list(retrieved_column_map.keys())}")

        return {"retrieved_columns": retrieved_columns}
    except Exception as e:
        logger.error(f"字段信息召回失败\n{e!s}")
        raise
