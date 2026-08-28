from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import step_log


@step_log("generate_answer")
def generate_answer(state: QueryState) -> QueryState:
    return state
