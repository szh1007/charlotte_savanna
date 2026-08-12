from ...tools.ragflow_tools import (
    create_session_ask,
    show_chat_list,
)
from ..prompt import sub_agents_config

kownledge_base_agent = {
    "name": sub_agents_config["ragflow"]["name"],
    "description": sub_agents_config["ragflow"]["description"],
    "system_prompt": sub_agents_config["ragflow"]["system_prompt"],
    "tools": [show_chat_list, create_session_ask],
}
