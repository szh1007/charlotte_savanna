from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import step_log


@step_log("enrich_markdown_images")
def enrich_markdown_images(state: LoadState) -> LoadState:
    return state
