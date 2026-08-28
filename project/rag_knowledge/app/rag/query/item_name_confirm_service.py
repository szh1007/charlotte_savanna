from ...process.query.agent.state import QueryState
from ...shared.runtime.logger import step_log


@step_log("confirm_item_name")
def confirm_item_name(state: QueryState) -> QueryState:
    return state
