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
        # 必须推一条 error 事件: 本节点是链路终点, 不推的话前端既收不到 result
        # 也收不到 error, 最后一个步骤永远停在 running (issue C01).
        # 事件形状对齐 QueryService 与 _7_validate_sql 的既有口径.
        writer({"error": str(e)})
        return {"error": str(e)}
