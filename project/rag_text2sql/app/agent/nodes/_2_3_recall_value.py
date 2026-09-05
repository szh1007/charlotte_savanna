from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.prompt_loader import load_prompt
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.es import ValueInfoEs


async def recall_value(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "召回字段取值"})

    try:
        # 获取数据
        query = state["query"]
        keywords = state["keywords"]
        value_es = runtime.context["value_es_repository"]

        # 1.字段取值扩展

        # 加载提示词
        template = await load_prompt("extend_keywords_for_value_recall")
        prompt = PromptTemplate(template=template, input_variables=["query"])

        # 定义 chain
        output_parser = JsonOutputParser()
        chain = prompt | llm | output_parser

        # LLM 执行
        result = await chain.ainvoke({"query": query})

        # 合并关键词
        merged_keywords = list(set(keywords + result))
        extend_keywords = [k for k in merged_keywords if k not in keywords]
        logger.info(f"关键词列表 - LLM字段取值扩展完成\n{keywords} + {extend_keywords}")

        # 2.字段取值召回 - ES
        # 定义字典结构去除召回的重复字段取值信息
        # 因为根据关键词召回字段取值时, 不同的关键词可能会召回同一个字段取值
        retrieved_value_map: dict[str, ValueInfoEs] = {}
        for keyword in merged_keywords:
            values: list[ValueInfoEs] = await value_es.search(keyword)
            if not values:
                continue

            # 遍历ES检索结果
            for value in values:
                value_id = value["id"]
                if value_id not in retrieved_value_map:
                    retrieved_value_map[value_id] = value

        retrieved_values = list(retrieved_value_map.values())

        logger.info(f"字段取值召回完成\n{list(retrieved_value_map.keys())}")

        return {"retrieved_values": retrieved_values}
    except Exception as e:
        logger.error(f"字段取值召回失败\n{e!s}")
        raise
