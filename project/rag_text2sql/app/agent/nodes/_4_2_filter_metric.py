import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.prompt_loader import load_prompt
from app.agent.state import DataAgentState
from app.core.log import logger


async def filter_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "过滤指标信息"})

    try:
        # 获取数据
        query = state["query"]
        metric_infos = state["metric_infos"]

        # 定义执行Chain
        template = await load_prompt("filter_metric_info")
        prompt = PromptTemplate(
            template=template,
            input_variables=["query", "metric_infos"],
        )
        output_parser = JsonOutputParser()
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {
                "query": query,
                "metric_infos": yaml.dump(
                    metric_infos,
                    allow_unicode=True,  # 中文展示
                    sort_keys=False,  # 不排序
                ),
            }
        )

        """
        返回的结果: [metric1, metric2, ...]
        """
        logger.info(f"指标过滤信息\n{result}")

        for metric_info in metric_infos[:]:  # 浅拷贝: 遍历中有删除操作, 所以要复制一份
            if metric_info["name"] not in result:
                metric_infos.remove(metric_info)

        logger.info(f"指标过滤成功\n{[metric['name'] for metric in metric_infos]}")

        return {"metric_infos": metric_infos}
    except Exception as e:
        logger.error(f"指标过滤失败:\n{e!s}")
        raise
