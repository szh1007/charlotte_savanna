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
        merged_keywords = list(set(keywords + result))
        extend_keywords = [k for k in merged_keywords if k not in keywords]
        logger.info(f"关键词列表 - LLM指标扩展完成\n{keywords} + {extend_keywords}")

        # 2.指标召回 - qdrant
        # 定义字典结构去除召回的重复指标信息
        # 因为指标信息存储qdrant时, 同一个指标根据 name, description, alias 存储了多次
        # 检索同一个指标的这3个属性如果相似度都较高, 就会重复召回, 所以需要去重
        #
        # C18: 与列召回同款 —— 一次批量嵌入 + 带上限地并发检索, 不再逐关键词 await
        vectors = await embeddings.aembed_documents(merged_keywords)
        payload_lists = await gather_limited(
            (metric_qr.search(vector) for vector in vectors),
            limit=app_config.recall.concurrency,
        )

        retrieved_metric_map: dict[str, MetricInfoQdrant] = {}
        # 保序回填, 去重仍是「先到先得」(与并发化之前一致)
        for payloads in payload_lists:
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
