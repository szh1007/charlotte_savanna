from ...tools.tavily_tool import network_search
from ..prompt import sub_agents_config

network_search_agent = {
    "name": sub_agents_config["tavily"]["name"],
    "description": sub_agents_config["tavily"]["description"],
    "system_prompt": sub_agents_config["tavily"]["system_prompt"],
    "tools": [network_search],
}
