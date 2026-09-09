"""CharAgent: 从零手写 AI Agent 运行时框架 (P0: 模型层 + 工具层).

注意: 小写 tool (@tool 装饰器) 不在顶层导出 —— 其与子包 CharAgent.tool
同名会遮蔽包属性, 统一从 CharAgent.tool 导入 (from CharAgent.tool import tool).
"""

from CharAgent.model import (
    ChatModel,
    FinishReason,
    HttpXChatModel,
    ModelConfigError,
    ModelConnectionError,
    ModelError,
    ModelProtocolError,
    ModelResponse,
    ModelStatusError,
    ModelTimeoutError,
    ModelToolCall,
    Usage,
    chat_model_from_env,
)
from CharAgent.tool import (
    Tool,
    ToolActionableError,
    ToolConfigError,
    ToolError,
    ToolExecution,
    execute_tool,
)

__all__ = [
    "ChatModel",
    "FinishReason",
    "HttpXChatModel",
    "ModelConfigError",
    "ModelConnectionError",
    "ModelError",
    "ModelProtocolError",
    "ModelResponse",
    "ModelStatusError",
    "ModelTimeoutError",
    "ModelToolCall",
    "Tool",
    "ToolActionableError",
    "ToolConfigError",
    "ToolError",
    "ToolExecution",
    "Usage",
    "chat_model_from_env",
    "execute_tool",
]
