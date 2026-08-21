"""LLM 调用封装 (ADR-0005/0006/0008): DeepSeek, openai 兼容 SDK, 不引入 LangChain.

流式调用点 (测试 mock 目标, 均 yield 文本增量):
- summarize_stream(transcript, meta) -> Iterator[str]: 视频总结的 Markdown
  文档增量 (ADR-0008), 消费方流结束后调 parse_summary_text 解析回结构化 dict
- generate_mindmap(summary, meta) -> dict: 思维导图结构, 输入为结构化总结
  (非原始转录), 输出 {title, chapters} 与总结 chapters 同构, 前端直接渲染
- ask_stream(transcript, summary, question) -> Iterator[str]: AI 问答增量
  (上下文 = 转录 + 总结)
- polish_subtitle_stream(chunk_text, start, end, has_real_ts) -> Iterator[str]:
  字幕重排精修增量 (模型生成字幕增强, 按口播风格重塑; has_real_ts=True 保留
  原时间戳 / False 线性均匀生成), 消费方块结束后调 parse_polished_lines 解析
  回 [{start, end, text}]

openai SDK 惰性 import: 未安装 / 未配置 key 时模块可导入, 调用才报错
(测试用 mock 替换本模块, 无需真实 LLM 依赖).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from . import config

# 转录文本截断上限 (字符): DeepSeek v4-flash 128k 输入窗口, 中文约
# 0.7~1 token/字, 安全余量取 15 万字符 (≈ 2h 视频转录), 超长截头部
LLM_MAX_TRANSCRIPT_CHARS = 150_000

# 总结输出模板 (LLM 输出 Markdown 文档, 后端解析回结构化 dict, ADR-0008).
# 标题措辞与后端 _render_markdown / 前端 buildMarkdown 保持一致,
# 变更需四处同步 (llm 模板 / 解析器 / 后端导出 / 前端渲染)
SUMMARY_MD_TEMPLATE = """# 视频总结: {title}
> 时长: {duration}s

## 视频概述
(一句话总体概述)

## 章节时间线
### 章节标题 (MM:SS ~ MM:SS)
- 要点 1
- 要点 2

## 核心要点
- 知识点 1
- 知识点 2

## 结论
(结论或行动建议)"""


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


def _chat_stream(messages: list[dict[str, str]]) -> Iterator[str]:
    """流式对话补全: 逐块 yield 文本增量 (失败抛 LLMError, 语义同 _chat).

    跳过无内容块 (usage 尾块 / delta 为空); finally 关闭底层 HTTP 流,
    调用方中途放弃 (客户端断开) 时等效取消请求 (ADR-0007).
    """
    kwargs: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "stream": True,
    }
    try:
        stream = _client().chat.completions.create(**kwargs)
    except LLMError:
        raise
    except Exception as e:  # SDK 网络 / API 错误: 明确失败原因
        raise LLMError(f"LLM 调用失败: {e}") from e
    try:
        for chunk in stream:
            if not chunk.choices:  # usage 尾块等无内容块
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
    except LLMError:
        raise
    except Exception as e:  # 流中途网络 / API 错误
        raise LLMError(f"LLM 调用失败: {e}") from e
    finally:
        stream.close()


def _truncate_transcript(transcript: str) -> str:
    """超长转录截头部 (LLM 窗口保护), 提示 LLM 文本为截断版本."""
    if len(transcript) <= LLM_MAX_TRANSCRIPT_CHARS:
        return transcript
    return transcript[:LLM_MAX_TRANSCRIPT_CHARS] + "\n\n[转录文本过长, 已截断]"


def summarize_stream(transcript: str, meta: dict[str, Any]) -> Iterator[str]:
    """流式生成视频总结: yield Markdown 文档增量 (ADR-0008).

    meta: {title, duration, site} 视频元信息, 注入总结上下文.
    消费方流结束后调 parse_summary_text 解析回结构化 dict
    (LLM 输出缺章节时间线时抛 LLMError, 可重试).
    """
    title = meta.get("title") or "未知标题"
    duration = meta.get("duration")
    prompt = (
        f"你是视频学习助手. 下面是视频《{title}》的转录文本"
        f"(时长 {duration or '未知'} 秒). 请总结为 Markdown 文档, 严格按如下"
        f"模板, 不要修改任何标题行 (## / ###), 不要输出模板以外的内容:\n"
        f"{SUMMARY_MD_TEMPLATE.format(title=title, duration=duration or '未知')}\n\n"
        f"要求:\n"
        f"1. 章节 (###) 按内容自然分段, 行尾时间戳 (MM:SS ~ MM:SS) 使用转录"
        f"时间戳, 每章标题 10 字内, 每章 3~8 条要点 (统一用 '- ' 前缀)\n"
        f"2. 核心要点 5~10 条, 面向快速学习\n"
        f"3. 概述一句话, 结论给出行动建议\n"
        f"4. 使用中文, 忠实转录内容, 不编造转录中没有的信息\n\n"
        f"转录文本:\n{_truncate_transcript(transcript)}"
    )
    yield from _chat_stream(
        [
            {
                "role": "system",
                "content": "你是严谨的中文视频内容总结助手, 只输出 Markdown 文档.",
            },
            {"role": "user", "content": prompt},
        ]
    )


# 章节时间戳正则: "### 标题 (MM:SS ~ MM:SS)", 兼容中文括号与分不补零
# (noqa RUF001: 字符类中的中文全角括号为有意匹配)
_CHAPTER_TS_RE = re.compile(
    r"[\(（](\d{1,3}):(\d{2})\s*[~\-]\s*(\d{1,3}):(\d{2})[\)）]\s*$"  # noqa: RUF001
)


def parse_summary_text(text: str) -> dict[str, Any]:
    """解析 LLM 输出的 Markdown 总结文档 → 结构化 dict (ADR-0008).

    行级扫描: # 标题行 → title (剥离「视频总结:」前缀); ## 小节按关键词
    识别 (概述/章节时间线/核心要点/结论, 容忍 LLM 微调措辞); ### 在章节
    节内开启新章 (行尾 (MM:SS ~ MM:SS) 解析 start/end, 缺失 → 0.0);
    - / * 列表项归入当前小节. 缺「章节时间线」小节判非法抛 LLMError
    (可重试, 对齐 generate_mindmap 缺 chapters 抛错), 其余小节缺失容忍空值.
    """
    summary: dict[str, Any] = {
        "title": "",
        "overview": "",
        "chapters": [],
        "key_points": [],
        "conclusion": "",
    }
    section = ""  # 当前小节: overview / chapters / key_points / conclusion
    chapter: dict[str, Any] | None = None
    has_chapters = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("# "):  # 一级标题 → title
            title = line[2:].strip()
            for prefix in ("视频总结:", "总结:"):
                if title.startswith(prefix):
                    title = title[len(prefix) :].strip()
                    break
            summary["title"] = title
        elif line.startswith("## "):  # 二级标题 → 小节切换
            heading = line[3:].strip()
            if "概述" in heading:
                section = "overview"
            elif "章节" in heading or "时间线" in heading:
                section = "chapters"
                has_chapters = True
            elif "要点" in heading or "知识点" in heading:
                section = "key_points"
            elif "结论" in heading:
                section = "conclusion"
            else:
                section = ""  # 未知小节: 忽略其内容
        elif line.startswith("### ") and section == "chapters":
            chapter = {
                "start": 0.0,
                "end": 0.0,
                "title": line[4:].strip(),
                "points": [],
            }
            m = _CHAPTER_TS_RE.search(chapter["title"])
            if m:
                chapter["start"] = float(int(m.group(1)) * 60 + int(m.group(2)))
                chapter["end"] = float(int(m.group(3)) * 60 + int(m.group(4)))
                chapter["title"] = chapter["title"][: m.start()].strip()
            summary["chapters"].append(chapter)
        elif line.startswith(("- ", "* ")):  # 列表项 → 当前小节
            item = line[2:].strip()
            if section == "chapters" and chapter is not None:
                chapter["points"].append(item)
            elif section == "key_points":
                summary["key_points"].append(item)
        elif line and section in ("overview", "conclusion"):  # 文本行 → 概述/结论
            if summary[section]:
                summary[section] += "\n"
            summary[section] += line
    if not has_chapters:
        raise LLMError("总结结构非法: 缺少章节时间线")
    return summary


# 思维导图输出结构 (与 summary.chapters 同构, 前端 MindMapCanvas 直接消费)
MINDMAP_SCHEMA_HINT = """{
  "title": "视频标题",
  "chapters": [
    {"start": 0.0, "end": 120.0, "title": "章节标题", "points": ["要点1", "要点2"]}
  ]
}"""


def generate_mindmap(summary: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """基于结构化总结生成思维导图结构 (章节树 + 要点, JSON).

    输入是 summarize 输出的结构化总结 (非原始转录, 用户反馈: 导图应以总结
    后的数据为准), DAG 上 mindmap 依赖 summary (task_manager.SUBTASK_DEPS).
    meta: {title, duration, site} 注入上下文; 返回 MINDMAP_SCHEMA_HINT 结构.
    """
    title = meta.get("title") or "未知标题"
    summary_text = json.dumps(summary, ensure_ascii=False, indent=2)
    prompt = (
        f"你是视频学习助手. 下面是从视频《{title}》的转录文本提炼出的"
        f"【结构化总结】(时长 {meta.get('duration') or '未知'} 秒). "
        f"请基于该总结提炼为思维导图结构, 严格按如下 JSON 结构输出, "
        f"不要输出 JSON 以外的内容:\n{MINDMAP_SCHEMA_HINT}\n\n"
        f"要求:\n"
        f"1. chapters 按内容自然分段, start/end 保留总结中的时间戳 (秒, 数值), "
        f"每章标题 10 字内\n"
        f"2. 每章 points 为该章 3~8 条要点, 突出知识层级与逻辑关系, 面向思维导图展示\n"
        f"3. 使用中文, 忠实总结内容, 不编造总结中没有的信息\n\n"
        f"【结构化总结】\n{summary_text}"
    )
    content = _chat(
        [
            {
                "role": "system",
                "content": "你是严谨的中文视频思维导图生成助手, 只输出 JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        json_mode=True,
    )
    data = _parse_json(content)
    # 结构校验 (用户反馈: LLM 偶发返回缺 chapters 的非法结构, 前端导图空白;
    # 抛错使子任务标 failed 可重试, 而非静默展示空导图)
    if not isinstance(data.get("chapters"), list):
        raise LLMError("思维导图结构非法: 缺少 chapters")
    return data


def ask_stream(
    transcript: str, summary: dict[str, Any], question: str
) -> Iterator[str]:
    """流式回答: yield 文本增量 (上下文 = 转录 + 结构化总结, 单次塞入).

    增量原样透传, 不逐块 strip (strip 会吞 chunk 边界空格, 丢字, ADR-0007);
    两端空白由消费端整体处理.
    """
    summary_text = json.dumps(summary, ensure_ascii=False, indent=2)
    prompt = (
        f"你是视频学习助手. 基于下面视频的【结构化总结】与【转录文本】回答用户"
        f"问题. 回答用中文, 简洁准确, 引用转录中的具体内容; 转录中没有的信息"
        f"明确说「视频中未提及」, 不要编造.\n\n"
        f"【结构化总结】\n{summary_text}\n\n"
        f"【转录文本 (截断)】\n{_truncate_transcript(transcript)}\n\n"
        f"用户问题: {question}"
    )
    yield from _chat_stream(
        [
            {"role": "system", "content": "你是严谨的中文视频学习问答助手."},
            {"role": "user", "content": prompt},
        ]
    )


def _extract_first_object(text: str) -> str | None:
    """从文本中截取第一个完整 JSON 对象 (平衡括号扫描, 跳过字符串与转义).

    LLM 偶发输出多个 JSON 拼接 / JSON 后跟多余文本 (用户反馈: Extra data),
    只取首个对象, 其余忽略.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None  # 括号未闭合: 交给 json.loads 报具体错误


def _parse_json(content: str) -> dict[str, Any]:
    """解析 LLM JSON 输出 (容忍代码块包裹 / 多 JSON 拼接, 失败抛 LLMError)."""
    text = content.strip()
    if text.startswith("```"):  # LLM 偶尔用 markdown 代码块包裹 JSON
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 整段解析失败: 截取第一个完整 JSON 对象再试 (尾部多余文本/第二个 JSON)
        first = _extract_first_object(text)
        if first is None:
            raise LLMError("LLM 返回非法 JSON") from None
        try:
            data = json.loads(first)
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM 返回非法 JSON: {e}") from e
    if not isinstance(data, dict):
        raise LLMError("LLM 返回结构非法 (应为 JSON 对象)")
    return data


# 字幕重排 (模型生成字幕增强): LLM 按视频口播风格重塑 ASR 粗稿, 并顺带
# 生成线性均匀时间戳 (每行 "MM:SS ~ MM:SS 文本"), 块范围由调用方注入
# (noqa RUF001: 提示词原文使用全角标点, 系用户确认的交付文案)
_SUBTITLE_POLISH_SYSTEM = (
    "你是专业的视频字幕编辑。用户会给你一段视频语音识别（ASR）生成的字幕粗稿，"  # noqa: RUF001
    "请把它重塑为通顺、自然、符合视频口播风格的精修字幕。"
)

# 重排字幕行时间戳正则: "MM:SS ~ MM:SS 文本" (分钟 1~3 位, 兼容全角破折号)
# (noqa RUF001: 字符类中的全角破折号为有意匹配)
_POLISHED_TS_RE = re.compile(
    r"^\s*(\d{1,3}):(\d{2})\s*[~\-～—]\s*(\d{1,3}):(\d{2})\s*(.*)$"  # noqa: RUF001
)


def polish_subtitle_stream(
    chunk_text: str, start: float, end: float, has_real_ts: bool = True
) -> Iterator[str]:
    """LLM 重排字幕粗稿 (模型生成字幕增强): yield 精修文本增量, 失败抛 LLMError.

    start/end 为块对应的视频时间范围 (秒); has_real_ts 为块内字幕是否自带
    真实时间戳 (ASR 句子级/词级输出, 用户反馈: 保留口播节奏, 不整体线性
    重排): True → 输入行已带 "MM:SS ~ MM:SS" 前缀, 提示词要求保留原时间戳
    (仅微调异常间隔); False (ASR 无时间戳兜底估算) → 输入为纯文本, 提示词
    要求在块范围内线性均匀生成时间戳. 输出格式统一 "MM:SS ~ MM:SS 文本",
    消费方块结束后调 parse_polished_lines 解析回 [{start, end, text}].
    """
    # 时间戳要求按来源分支 (noqa RUF001: 提示词原文使用全角标点, 系用户确认的交付文案)
    if has_real_ts:
        ts_requirement = (
            "时间戳要求：\n"  # noqa: RUF001
            "- 如果已有详细时间戳就不需要自动计算线性时间戳了：输入字幕行自带时间戳"  # noqa: RUF001
            "（每行以“MM:SS ~ MM:SS 文本”开头），这些时间戳来自语音识别，保留了真实说话节奏\n"  # noqa: RUF001, E501
            "- 保留原时间戳，不要重新均匀计算；可微调相邻行使间隔合理（修正重叠/倒序），"  # noqa: RUF001, E501
            "微调幅度尽量小\n"
            f"- 若某行时间戳明显异常，可在 {int(start)} ~ {int(end)} 秒范围内适当修正\n"  # noqa: RUF001
        )
    else:
        ts_requirement = (
            "时间戳要求：\n"  # noqa: RUF001
            f"- 根据最终生成的字幕行数，在 {int(start)} ~ {int(end)} 秒范围内自动计算生成线性均匀的时间戳\n"  # noqa: RUF001, E501
            "- 每行格式：MM:SS ~ MM:SS 字幕文本（如 00:05 ~ 00:12 大家好，欢迎观看本期视频）\n"  # noqa: RUF001, E501
            f"- 首行从 {int(start)} 秒开始，末行在 {int(end)} 秒结束\n"  # noqa: RUF001
        )
    # (noqa RUF001: 提示词原文使用全角标点, 系用户确认的交付文案)
    prompt = (
        "这是一篇视频语音识别（ASR）后的字幕粗稿，请按照视频口播的风格重塑这段字幕，"  # noqa: RUF001
        f"输出精修后的字幕文本。本段字幕对应的视频时间段为 {int(start)} ~ {int(end)} 秒。\n\n"  # noqa: E501
        "重塑要求：\n"  # noqa: RUF001
        "1. 语句通顺自然：修正识别错误、错别字与断句问题，补齐缺失的标点\n"  # noqa: RUF001
        "2. 保持口播风格：保留说话者自然的口语表达与语气，不要改成书面语腔调\n"  # noqa: RUF001
        "3. 精简冗余：删除“嗯”“啊”“呃”等语气词、口头禅、无意义重复（如“就是就是”“然后然后”）；"  # noqa: RUF001, E501
        "信息必须完整保留，不做过度删减\n"  # noqa: RUF001
        "4. 忠实原意：不增删任何事实信息；数字、专有名词、人名、品牌名等不确定的识别内容"  # noqa: RUF001, E501
        "保留原文，不要擅自修改\n"  # noqa: RUF001
        "5. 合理断句：按语义断句，一句一个完整意思；过长的句子拆成两句，零碎片段适当合并"  # noqa: RUF001, E501
        "（最多合并 3 句），句数保持与原文同一量级\n\n"  # noqa: RUF001
        + ts_requirement
        + "\n输出格式：\n"  # noqa: RUF001
        "- 每行按上述时间戳格式，按原文顺序排列\n"  # noqa: RUF001
        "- 不要编号，不要任何解释，只输出字幕文本\n"  # noqa: RUF001
        "- 不输出除字幕以外的任何内容\n\n"
        f"字幕粗稿：\n{chunk_text}"  # noqa: RUF001
    )
    yield from _chat_stream(
        [
            {"role": "system", "content": _SUBTITLE_POLISH_SYSTEM},
            {"role": "user", "content": prompt},
        ]
    )


def parse_polished_lines(text: str, start: float, end: float) -> list[dict[str, Any]]:
    """解析重排字幕文本 → [{start, end, text}] (缺失/越界时间戳线性插值兜底).

    LLM 输出按提示词为每行 "MM:SS ~ MM:SS 文本"; 格式不符或时间戳越界
    (超出 [start, end] 范围) 的行按行序号在块范围内线性均匀插值, 保证
    下游 (总结/导出) 拿到的字幕行时间戳始终有效.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    segments: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        m = _POLISHED_TS_RE.match(line)
        body = m.group(5).strip() if m else line
        if not body:
            continue
        if m:
            seg_start = float(m.group(1)) * 60 + float(m.group(2))
            seg_end = float(m.group(3)) * 60 + float(m.group(4))
            if start <= seg_start < seg_end <= end + 1.0:  # 合法且在块范围内
                segments.append(
                    {
                        "start": round(seg_start, 2),
                        "end": round(seg_end, 2),
                        "text": body,
                    }
                )
                continue
        seg_start, seg_end = _interpolate_times(idx, len(lines), start, end)
        segments.append({"start": seg_start, "end": seg_end, "text": body})
    return segments


def _interpolate_times(
    idx: int, total: int, start: float, end: float
) -> tuple[float, float]:
    """线性均匀插值: 第 idx 行 (共 total 行) 的时间区间 = [start, end] 均分."""
    span = end - start
    seg_start = start + span * idx / total
    seg_end = start + span * (idx + 1) / total
    return round(seg_start, 2), round(seg_end, 2)
