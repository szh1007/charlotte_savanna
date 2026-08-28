from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import step_log


@step_log("search_by_embedding")
def search_by_embedding(state: QueryState) -> QueryState:
    return state
