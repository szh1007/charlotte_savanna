from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import step_log


@step_log("rerank_documents")
def rerank_documents(state: QueryState) -> QueryState:
    return state
