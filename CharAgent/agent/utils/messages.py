"""agent loop 的消息构造、指令文案与 token 计量 (loop.py 拆出的纯函数/常量).

对齐 tool/utils/messages.py 的拆分动机 —— 行为 (AgentLoop 的 while 循环)
与「要发给模型的 wire 消息长什么样 / 用什么文案引导模型 / 一次响应记多少
token」分离:
- assistant_wire / tool_wire: 模型响应与工具执行 → 回填历史的 wire 消息
  (assistant 带 reasoning_content 与 tool_calls 的原样保真 #1/#11; tool 消息
  与 tool_call_id 配对 #10; 失败回填可操作错误文本 #2 —— 错误自纠错的载体)
- TRUNCATION_*_TEXT: length 截断后回填模型的指令 (role=system; 语义上
  指令来自框架而非用户, 防止模型把它当新请求; OpenAI/DeepSeek 兼容端点
  均允许中间 system 消息)
- count_tokens: 单次响应的 token 消耗 (guard 的 token 预算按其累计)
"""

from __future__ import annotations

from CharAgent.model.utils.types import ModelMessage, ModelResponse, Usage
from CharAgent.tool import ToolExecution


def count_tokens(usage: Usage | None) -> int:
    """单次响应的 token 消耗 (无 usage 的响应计 0, 如部分 mock/流式).

    优先 total_tokens; 缺失时回退输入 + 输出之和 (上游未给总量时的兜底).
    """
    if usage is None:
        return 0
    if usage.total_tokens is not None:
        return usage.total_tokens
    return (usage.input_tokens or 0) + (usage.output_tokens or 0)


def assistant_wire(response: ModelResponse) -> ModelMessage:
    """ModelResponse → assistant wire 消息 (回填历史, 字段原样保真 #1).

    reasoning 必须回填 (#11 + DeepSeek 思考模式契约): 官方文档要求请求携带
    tools 时, 历史轮次的 reasoning_content 须完整回传 (文档称缺失即 400),
    且回传后会被拼接进上下文。2026-09-11 实测 11 组条件 (deepseek-flash /
    deepseek-v4-pro x 默认与 beta 端点, httpx 裸调与官方 SDK 样例流程,
    流式与非流式, 缺失 / 空串 / null / 部分回传四种回传形态) 均未复现该
    400 —— 但不复现不等于契约不存在 (官方文档措辞明确, 触发条件可能更窄
    或按灰度放开), 框架仍按文档执行以保留交错思考 (模型跨工具调用复用推理链).
    无 tools 时 API 忽略该字段, 故无条件携带 (保真优先, 不按请求形态分支)。

    注意 reasoning 与 content 是两条通道: 此处只负责 wire 回填, 前端折叠
    Thinking 区的展示走 reasoning 事件 (issue 05), 不混入 content 字段。
    """
    message: ModelMessage = {"role": "assistant", "content": response.content}
    if response.reasoning:
        message["reasoning_content"] = response.reasoning
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
