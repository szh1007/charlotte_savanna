"""agent loop 的消息构造与面向模型的指令文案 (loop.py 拆出的纯函数/常量).

对齐 tool/utils/messages.py 的拆分动机 —— 行为 (AgentLoop 的 while 循环)
与「要发给模型的 wire 消息长什么样 / 用什么文案引导模型」分离:
- assistant_wire / tool_wire: 模型响应与工具执行 → 回填历史的 wire 消息
  (assistant 带 tool_calls 的原样保真 #1; tool 消息与 tool_call_id 配对
  #10; 失败回填可操作错误文本 #2 —— 错误自纠错的载体)
- TRUNCATION_*_TEXT: length 截断后回填模型的指令 (role=system; 语义上
  指令来自框架而非用户, 防止模型把它当新请求; OpenAI/DeepSeek 兼容端点
  均允许中间 system 消息)
"""

from __future__ import annotations

from CharAgent.model.utils.types import ModelMessage, ModelResponse
from CharAgent.tool import ToolExecution


def assistant_wire(response: ModelResponse) -> ModelMessage:
    """ModelResponse → assistant wire 消息 (回填历史, tool_calls 原样保真 #1).

    reasoning 不进入 wire 历史 (#11): 模型层已把 reasoning_content 剥离到
    ModelResponse.reasoning, 此处只回填 content 与 tool_calls.
    """
    message: ModelMessage = {"role": "assistant", "content": response.content}
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ]
    return message


def tool_wire(call_id: str, execution: ToolExecution) -> ModelMessage:
    """单条工具执行 → tool wire 消息 (role=tool, 与 tool_call_id 配对 #10).

    失败时回填的是可操作错误文本 (#2) —— 这正是「错误自纠错」的载体,
    模型下一轮能看到哪里错了并自行修正.
    """
    content = execution.content if execution.ok else (execution.error or "")
    return {"role": "tool", "tool_call_id": call_id, "content": content}


# 截断续写指令 (CONTINUE 策略, #10): 保留截断前缀于历史, 提示接着中断处续写
TRUNCATION_CONTINUE_TEXT = (
    "你的上一轮回答因超出输出长度限制被截断. "
    "请接着被截断的位置继续输出, 不要重复已输出的内容, "
    "直接给出剩余部分的完整回答."
)

# 截断精简指令 (CONDENSE 策略, #10): 截断前缀不完整, 提示模型精简重答
TRUNCATION_CONDENSE_TEXT = (
    "你的上一轮回答因超出输出长度限制被丢弃 (内容不完整). "
    "请以更精炼的方式重新回答, 聚焦关键信息并控制篇幅, "
    "确保能在长度限制内完成."
)
