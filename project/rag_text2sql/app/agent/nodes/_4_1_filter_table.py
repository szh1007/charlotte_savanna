import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.prompt_loader import load_prompt
from app.agent.state import DataAgentState
from app.core.log import logger


async def filter_table(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "过滤表信息"})

    try:
        # 获取数据
        query = state["query"]
        table_infos = state["table_infos"]

        # 定义执行Chain
        template = await load_prompt("filter_table_info")
        prompt = PromptTemplate(
            template=template,
            input_variables=["query", "table_infos"],
        )
        output_parser = JsonOutputParser()
        chain = prompt | llm | output_parser

        result = await chain.ainvoke(
            {
                "query": query,
                "table_infos": yaml.dump(
                    table_infos,
                    allow_unicode=True,  # 中文展示
                    sort_keys=False,  # 不排序
                ),
            }
        )

        """
        返回的结果:
        {
            table1: [column1, column2, ...],
            table2: [column1, column2, ...]
        }
        """
        logger.info(f"表结构过滤信息\n{result}")

        for table_info in table_infos[:]:  # 浅拷贝: 遍历中有删除操作, 所以要复制一份
            table_name = table_info["name"]
            columns = table_info["columns"]

            if table_name not in result:
                table_infos.remove(table_info)
            else:
                for column in columns[:]:  # 浅拷贝: 遍历中有删除操作, 所以要复制一份
                    if column["name"] not in result[table_name]:
                        columns.remove(column)

        logger.info(f"表结构过滤成功\n{[table['name'] for table in table_infos]}")

        return {"table_infos": table_infos}
    except Exception as e:
        logger.error(f"表结构过滤失败\n{e!s}")
        raise
