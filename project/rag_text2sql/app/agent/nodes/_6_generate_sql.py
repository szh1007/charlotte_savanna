import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.prompt_loader import load_prompt
from app.agent.state import DataAgentState, MetricInfoState, TableInfoState
from app.core.log import logger


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "生成sql语句"})

    try:
        # 获取数据
        query = state["query"]
        table_infos: list[TableInfoState] = state["table_infos"]
        metric_infos: list[MetricInfoState] = state["metric_infos"]

        date_info = state["date_info"]
        db_info = state["db_info"]

        # 定义执行Chain
        template = await load_prompt("generate_sql")
        prompt = PromptTemplate(
            template=template,
            input_variables=[
                "query",
                "table_infos",
                "metric_infos",
                "date_info",
                "db_info",
            ],
        )
        output_parser = StrOutputParser()
        chain = prompt | llm | output_parser

        sql = await chain.ainvoke(
            {
                "query": query,
                "table_infos": yaml.dump(
                    table_infos,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                "metric_infos": yaml.dump(
                    metric_infos,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                "date_info": yaml.dump(
                    date_info,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                "db_info": yaml.dump(
                    db_info,
                    allow_unicode=True,
                    sort_keys=False,
                ),
            }
        )

        logger.info(f"SQL语句生成成功\n{sql}")
        return {"sql": sql}
    except Exception as e:
        logger.error(f"SQL语句生成失败\n{e!s}")
        raise
