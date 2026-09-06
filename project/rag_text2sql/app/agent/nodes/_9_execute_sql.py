from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def execute_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "执行sql语句"})

    try:
        # 获取数和上下文
        sql = state["sql"]
        dw_mysql_repository = runtime.context["dw_mysql_repository"]

        result = await dw_mysql_repository.execute_sql(sql)

        writer({"result": result})
        logger.info(f"SQL语句执行成功\n{result}")

    except Exception as e:
        logger.error(f"SQL语句执行失败\n{e!s}")
        return {"error": str(e)}
