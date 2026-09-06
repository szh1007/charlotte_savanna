from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "校验sql语句"})

    try:
        # 获取数据和上下文
        sql = state["sql"]
        dw_mysql_repository = runtime.context["dw_mysql_repository"]

        # 校验 SQL 语句是否有效
        await dw_mysql_repository.validate_sql(sql)

        logger.info(f"SQL校验成功\n{sql}")
        return {"error": None}
    except Exception as e:
        logger.error(f"SQL校验失败\n{e!s}")
        return {"error": f"{e!s}"}
