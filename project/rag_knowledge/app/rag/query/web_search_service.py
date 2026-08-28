from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import step_log


@step_log("search_by_web")
def search_by_web(state: QueryState) -> QueryState:
    return state
