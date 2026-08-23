"""测试假件 (Issue 07): FakeChatModel + 固定契约图谱/分析 JSON.

真实管道测试不触网不调 LLM: FakeChatModel 按 prompt 关键词返回预置
JSON (analyze → 分析 JSON; deconstruct → 契约图谱 JSON), 支持注入
调用序列 (sequence) 模拟重试场景 (首次输出非法 → 重试修正).
"""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

ANALYSIS_JSON = json.dumps(
    {
        "topic": "Python 装饰器",
        "summary": "装饰器是 Python 的函数包装机制",
        "concepts": ["一等公民", "闭包", "语法糖"],
        "suggested_queries": ["Python 装饰器 语法", "装饰器 应用场景"],
    },
    ensure_ascii=False,
)

GRAPH_JSON = json.dumps(
    {
        "version": 1,
        "title": "Python 装饰器",
        "chapters": [
            {
                "id": "ch_1",
                "title": "基础概念",
                "summary": "装饰器的前提知识",
                "knowledge_points": [
                    {
                        "id": "kp_1",
                        "title": "函数是一等公民",
                        "summary": "函数可传递可返回",
                        "prerequisites": [],
                        "sources": ["1"],
                    },
                    {
                        "id": "kp_2",
                        "title": "闭包",
                        "summary": "词法作用域捕获",
                        "prerequisites": ["kp_1"],
                        "sources": [],
                    },
                ],
            },
            {
                "id": "ch_2",
                "title": "装饰器实践",
                "summary": "语法与实战",
                "knowledge_points": [
                    {
                        "id": "kp_3",
                        "title": "装饰器语法糖",
                        "summary": "@ 语法",
                        "prerequisites": ["kp_2"],
                    },
                    {
                        "id": "kp_4",
                        "title": "带参数装饰器",
                        "summary": "三层嵌套",
                        "prerequisites": ["kp_3"],
                    },
                ],
            },
        ],
    },
    ensure_ascii=False,
)


class FakeChatModel(BaseChatModel):
    """按 prompt 关键词返回预置 JSON 的假模型.

    BaseChatModel 是 pydantic 模型, 字段必须显式声明 (不能 __init__ 赋值).
    sequence: [(关键词, 响应文本), ...] 优先于默认匹配 (模拟修正反馈);
    fail_first_n: 前 N 次调用返回非 JSON 文本 (模拟 LLM 首次输出非法,
    触发管道重试路径); calls 记录每次调用的人类消息内容, 供断言.
    """

    sequence: list[tuple[str, str]] = Field(default_factory=list)
    fail_first_n: int = 0
    calls: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        last = messages[-1]
        text = last.content if isinstance(last.content, str) else str(last.content)
        self.calls.append(text)
        for keyword, response in self.sequence:
            if keyword in text:
                return self._reply(response)
        if len(self.calls) <= self.fail_first_n:
            return self._reply("这不是 JSON, 是一段废话")
        if "解构以下学习主题" in text:
            return self._reply(GRAPH_JSON)
        if "学习材料如下" in text:
            return self._reply(ANALYSIS_JSON)
        raise AssertionError(f"FakeChatModel 未匹配的 prompt: {text[:80]}...")

    @staticmethod
    def _reply(content: str) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))]
        )
