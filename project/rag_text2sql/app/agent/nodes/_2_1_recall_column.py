from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.prompt_loader import load_prompt
from app.agent.state import DataAgentState
from app.conf.app_config import app_config
from app.core.concurrency import gather_limited
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
        #
        # C18: 此前是逐关键词 `await aembed_query` + `await search` —— N 个关键词
        # 就是 2N 个 RTT 相加. 现在一次批量嵌入 + 带上限地并发检索; 上限是给
        # 嵌入服务 / Qdrant 的连接池留的余量 (见 app_config.recall).
        vectors = await embeddings.aembed_documents(merged_keywords)
        payload_lists = await gather_limited(
            (column_qr.search(vector) for vector in vectors),
            limit=app_config.recall.concurrency,
        )

        retrieved_column_map: dict[str, ColumnInfoQdrant] = {}
        # 按关键词顺序回填 (gather_limited 保序) —— 去重仍是「先到先得」,
        # 与并发化之前一致
        for payloads in payload_lists:
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
