from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import step_log


@step_log("fuse_by_rrf")
def fuse_by_rrf(state: QueryState) -> QueryState:
    return state
