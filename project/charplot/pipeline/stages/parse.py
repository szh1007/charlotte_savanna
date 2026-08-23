"""阶段 1: 归一化解析 (Issue 07, ADR-0002 统一管道输入侧).

text / link / file / kb (Issue 11) 形态统一归一化为 ParsedMaterial:
- text: 内容归一化 (压缩空白/截断)
- link: 抓取网页正文 (httpx + bs4)
- file: 经 Django 内部端点取文件二进制 (CONTRACT.md §5) + 按格式解析
- kb: 经 Django 内部端点取知识库元信息 (名称/描述), 名称+描述作为材料
  (analyze 阶段输入); 知识库非就绪 (创建后被下线/删除) 快速失败

解析失败抛 ParseError → 任务 error (前端可重试, 幂等落库).
"""

import logging

from ...api import django_client
from ...pipeline import parsers
from ..types import ParsedMaterial, PipelineState

logger = logging.getLogger(__name__)


async def parse_node(state: PipelineState) -> dict:
    """归一化解析节点: 产出 ParsedMaterial 写入 state["material"]."""
    inp = state["inp"]
    if inp.input_type == "kb":
        meta = await django_client.fetch_kb_meta(inp.kb_id)
        if meta.get("status") != "ready":
            raise RuntimeError(
                f"知识库当前不可用 (status={meta.get('status')}), 请稍后重试"
            )
        name = meta.get("name") or "知识库"
        description = (meta.get("description") or "").strip()
        text = f"{name}\n\n{description}".strip() if description else name
        logger.info("kb 输入解析完成 (journey=%s, kb=%s)", inp.journey_id, inp.kb_id)
        return {"material": ParsedMaterial(title=name, text=text, origin="kb")}
    if inp.input_type == "file":
        filename, data = await django_client.fetch_journey_content(inp.journey_id)
        text = parsers.parse_document(filename, data)
        title = parsers.extract_title(text)
        logger.info(
            "file 输入解析完成 (journey=%s, %s, %d chars)",
            inp.journey_id,
            filename,
            len(text),
        )
        return {
            "material": ParsedMaterial(
                title=title, text=text, origin="file", filename=filename
            )
        }
    if inp.input_type == "link":
        url = inp.content.strip()
        text = parsers.fetch_link_content(url)
        title = parsers.extract_title(text, fallback=url)
        logger.info(
            "link 输入抓取完成 (journey=%s, %d chars)", inp.journey_id, len(text)
        )
        return {"material": ParsedMaterial(title=title, text=text, origin="link")}
    # text 输入: 内容归一化
    text = parsers.normalize_text(inp.content)
    title = parsers.extract_title(text, fallback="学习主题")
    return {"material": ParsedMaterial(title=title, text=text, origin="text")}
