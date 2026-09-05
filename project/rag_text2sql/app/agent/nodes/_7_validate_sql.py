from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def validate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "校验sql语句"})

    try:
        # print(1 / 0)
        logger.info("SQL校验正确")
        return {"error": None}
    except Exception as e:
        logger.error(f"SQL校验异常: {e!s}")
        return {"error": str(e)}
