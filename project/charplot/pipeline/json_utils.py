"""LLM 输出 → JSON 提取通用工具 (Issue 07).

LLM 文本输出常带 ```json 代码块 / 前后叙述, 统一提取逻辑供
analyze / deconstruct 阶段复用; 提取失败抛 ValueError (触发重试).
"""

import json


def extract_json(raw: str) -> dict:
    """从 LLM 输出提取 JSON 对象 (```json 块优先, 否则找首个 {...})."""
    text = raw.strip()
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("{") and block.endswith("}"):
                text = block
                break
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        else:
            raise ValueError("LLM 输出中未找到 JSON 对象")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc}") from exc
