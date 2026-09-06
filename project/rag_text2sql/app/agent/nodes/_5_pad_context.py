from datetime import datetime

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, DateInfoState
from app.core.log import logger


async def pad_context(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "添加额外上下文"})

    try:
        dw_mr = runtime.context["dw_mysql_repository"]

        # 添加时间信息
        today = datetime.today()
        date_info = DateInfoState(
            date=today.strftime("%Y-%m-%d"),
            weekday=today.strftime("%A"),
            quarter=f"Q{(today.month - 1) // 3 + 1}",
        )

        # 添加数据库信息
        db_info = await dw_mr.get_db_info()

        logger.info(f"额外上下文添加成功\n{date_info}, {db_info}")
        return {"date_info": date_info, "db_info": db_info}
    except Exception as e:
        logger.error(f"额外上下文添加失败\n{e!s}")
        raise
