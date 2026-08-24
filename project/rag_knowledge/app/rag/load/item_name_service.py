from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import step_log


@step_log("recognize_and_index_item_name")
def recognize_and_index_item_name(state: LoadState) -> LoadState:
    return state
