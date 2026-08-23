"""
MCP 配置模块, 负责读取联网检索相关环境变量.
"""

from dataclasses import dataclass

from .common import env_str


@dataclass
class McpConfig:
    mcp_base_url: str
    api_key: str


mcp_config = McpConfig(
    mcp_base_url=env_str("RK_MCP_DASHSCOPE_BASE_URL"),
    api_key=env_str("RK_OPENAI_API_KEY"),
)
