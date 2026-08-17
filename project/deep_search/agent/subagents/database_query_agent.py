from ...tools.mysql_tools import (
    execute_sql_data,
    list_table_names,
    show_table_data,
)
from ..prompt import sub_agents_config

database_query_agent = {
    "name": sub_agents_config["db"]["name"],
    "description": sub_agents_config["db"]["description"],
    "system_prompt": sub_agents_config["db"]["system_prompt"],
    "tools": [execute_sql_data, list_table_names, show_table_data],
}
