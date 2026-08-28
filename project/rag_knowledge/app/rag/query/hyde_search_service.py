from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import step_log


@step_log("search_by_hyde")
def search_by_hyde(state: QueryState) -> QueryState:
    return state
