"""LLM 调用封装 (ADR-0005): DeepSeek, openai 兼容 SDK, 不引入 LangChain.

两个独立调用点 (测试 mock 目标):
- summarize(transcript, meta) -> dict: 结构化视频总结 (章节时间线 + 要点),
  思维导图与前端展示由同一份 JSON 渲染
- ask(transcript, summary, question) -> str: AI 问答 (上下文 = 转录 + 总结)

openai SDK 惰性 import: 未安装 / 未配置 key 时模块可导入, 调用才报错
(测试用 mock 替换本模块, 无需真实 LLM 依赖).
"""

from __future__ import annotations

import json
from typing import Any

from . import config

# 转录文本截断上限 (字符): DeepSeek v4-flash 128k 输入窗口, 中文约
# 0.7~1 token/字, 安全余量取 15 万字符 (≈ 2h 视频转录), 超长截头部
LLM_MAX_TRANSCRIPT_CHARS = 150_000

# 总结输出结构 (与前端思维导图共享的 JSON 契约, 变更需同步前端渲染)
SUMMARY_SCHEMA_HINT = """{
  "title": "视频标题",
  "overview": "一句话总体概述",
  "chapters": [
    {"start": 0.0, "end": 120.0, "title": "章节标题", "points": ["要点1", "要点2"]}
  ],
  "key_points": ["核心知识点1", "核心知识点2"],
  "conclusion": "结论或行动建议"
}"""


class LLMError(Exception):
    """LLM 调用失败 (网络 / 限流 / 响应非法), 透传可读原因."""


def _client():
    """惰性创建 openai 客户端 (模块导入时不加载 SDK, 保持测试轻量)."""
    try:
        from openai import OpenAI
    except ImportError as e:  # 未安装 openai: 调用时明确报错而非导入期崩溃
        raise LLMError("LLM SDK (openai) 未安装, 请执行 pip install openai") from e
    if not config.LLM_API_KEY:
        raise LLMError("未配置 LLM API Key (LLM_API_KEY / DEEPSEEK_API_KEY)")
    return OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)


def _chat(messages: list[dict[str, str]], json_mode: bool = False) -> str:
    """单次对话补全 (失败抛 LLMError, 原因透传不猜测)."""
    kwargs: dict[str, Any] = {"model": config.LLM_MODEL, "messages": messages}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = _client().chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
    except LLMError:
        raise
    except Exception as e:  # SDK 网络 / API 错误: 明确失败原因
        raise LLMError(f"LLM 调用失败: {e}") from e
    return content


def _truncate_transcript(transcript: str) -> str:
    """超长转录截头部 (LLM 窗口保护), 提示 LLM 文本为截断版本."""
    if len(transcript) <= LLM_MAX_TRANSCRIPT_CHARS:
        return transcript
    return transcript[:LLM_MAX_TRANSCRIPT_CHARS] + "\n\n[转录文本过长, 已截断]"


def summarize(transcript: str, meta: dict[str, Any]) -> dict[str, Any]:
    """生成结构化视频总结 (章节时间线 + 要点, JSON).

    meta: {title, duration, site} 视频元信息, 注入总结上下文.
    返回 SUMMARY_SCHEMA_HINT 结构的 dict; LLM 返回非法 JSON 时抛 LLMError.
    """
    title = meta.get("title") or "未知标题"
    duration = meta.get("duration")
    prompt = (
        f"你是视频学习助手. 下面是视频《{title}》的转录文本"
        f"(时长 {duration or '未知'} 秒). 请总结为结构化 JSON, 严格按如下"
        f"结构, 不要输出 JSON 以外的内容:\n{SUMMARY_SCHEMA_HINT}\n\n"
        f"要求:\n"
        f"1. chapters 按内容自然分段, start/end 为转录时间戳 (秒, 数值), "
        f"每章标题 10 字内, points 为 3~8 条该章要点\n"
        f"2. key_points 提炼 5~10 条核心知识点, 面向快速学习\n"
        f"3. overview 一句话, conclusion 给出行动建议\n"
        f"4. 使用中文, 忠实转录内容, 不编造转录中没有的信息\n\n"
        f"转录文本:\n{_truncate_transcript(transcript)}"
    )
    content = _chat(
        [
            {
                "role": "system",
                "content": "你是严谨的中文视频内容总结助手, 只输出 JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        json_mode=True,
    )
    return _parse_json(content)


def ask(transcript: str, summary: dict[str, Any], question: str) -> str:
    """针对视频内容回答问题 (上下文 = 转录 + 结构化总结, 单次塞入)."""
    summary_text = json.dumps(summary, ensure_ascii=False, indent=2)
    prompt = (
        f"你是视频学习助手. 基于下面视频的【结构化总结】与【转录文本】回答用户"
        f"问题. 回答用中文, 简洁准确, 引用转录中的具体内容; 转录中没有的信息"
        f"明确说「视频中未提及」, 不要编造.\n\n"
        f"【结构化总结】\n{summary_text}\n\n"
        f"【转录文本 (截断)】\n{_truncate_transcript(transcript)}\n\n"
        f"用户问题: {question}"
    )
    return _chat(
        [
            {"role": "system", "content": "你是严谨的中文视频学习问答助手."},
            {"role": "user", "content": prompt},
        ]
    ).strip()


def _parse_json(content: str) -> dict[str, Any]:
    """解析 LLM JSON 输出 (容忍代码块包裹, 失败抛 LLMError)."""
    text = content.strip()
    if text.startswith("```"):  # LLM 偶尔用 markdown 代码块包裹 JSON
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM 返回非法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise LLMError("LLM 返回结构非法 (应为 JSON 对象)")
    return data
