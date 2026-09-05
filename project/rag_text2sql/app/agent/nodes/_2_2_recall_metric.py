from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.prompt_loader import load_prompt
from app.agent.state import DataAgentState
from app.core.log import logger
from app.models.qdrant import MetricInfoQdrant


async def recall_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "召回指标信息"})

    try:
        # 获取数据和上下文
        query = state["query"]
        keywords = state["keywords"]
        embeddings = runtime.context["embeddings"]
        metric_qr = runtime.context["metric_qdrant_repository"]

        # 1.指标扩展

        # 加载提示词
        template = await load_prompt("extend_keywords_for_metric_recall")
        prompt = PromptTemplate(template=template, input_variables=["query"])

        # 定义 chain
        output_parser = JsonOutputParser()
        chain = prompt | llm | output_parser

        # LLM 执行
        result = await chain.ainvoke({"query": query})

        # 合并关键词
        keywords = list(set(keywords + result))
        logger.info(f"关键词列表 - LLM指标扩展完成\n{keywords}")

        # 2.指标召回 - qdrant
        # 定义字典结构去除召回的重复指标信息
        # 因为指标信息存储qdrant时, 同一个指标根据 name, description, alias 存储了多次
        # 检索同一个指标的这3个属性如果相似度都较高, 就会重复召回, 所以需要去重
        retrieved_metric_map: dict[str, MetricInfoQdrant] = {}
        for keyword in keywords:
            embedding = await embeddings.aembed_query(keyword)
            payloads: list[dict] = await metric_qr.search(embedding)

            # 遍历召回结果
            for payload in payloads:
                metric_id = payload["id"]
                if metric_id not in retrieved_metric_map:
                    retrieved_metric_map[metric_id] = payload

        # 获取召回指标列表
        retrieved_metrics = list(retrieved_metric_map.values())

        logger.info(f"指标信息召回成功\n{list(retrieved_metric_map.keys())}")

        return {"retrieved_metrics": retrieved_metrics}
    except Exception as e:
        logger.error(f"指标信息召回失败\n{e!s}")
        raise
