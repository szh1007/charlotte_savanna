import json
import re
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import logger, step_log
from .config import CHUNK_MAX_SIZE, CHUNK_MIN, CHUNK_OVERLAP, CHUNK_SIZE


@step_log("split_document")
def split_document(state: LoadState) -> LoadState:
    # 1.获取并校验数据
    md_path, md_content, file_title = _validate_data(state)

    # 2.语义切割 (根据多级标题)
    chunks = _split_document_by_title(md_content, file_title)

    # 3.精细切割(递归切割 + 合并)
    chunks = _refine_split_and_merge_chunks(chunks)

    # 4.属性对齐
    _padding_chunks_metadata(chunks)

    # 5.备份 chunks json
    _backup_chunks_json(md_path, chunks)

    # 6.更新state
    state["chunks"] = chunks
    return state


@step_log("_validate_data")
def _validate_data(state: LoadState) -> tuple[str, str, str]:
    """
    获取并校验数据

    Args:
        state: 加载状态

    Returns:
        tuple[str, str]: 文档内容 和 文件标题
    Raises:
        ValueError: 如果 md_content 为空, 且 md_path 不存在/不是文件
    """
    md_path = state.get("md_path")  # new
    md_content = state.get("md_content")
    file_title = state.get("file_title")

    if not md_content:
        if not md_path or not Path(md_path).is_file():
            logger.error(f"md_content 为空, {md_path} 不存在/不是文件")
            raise ValueError(f"md_content 为空, {md_path} 不存在/不是文件")

        md_content = Path(md_path).read_text(encoding="utf-8")
        state["md_content"] = md_content
        logger.warning(f"md_content 为空, 使用 {md_path} 填充")

    if not file_title:
        file_title = (Path(md_path).stem or "default").replace("_new", "")
        state["file_title"] = file_title
        logger.warning(f"file_title 为空, 使用 {md_path} 填充 / 设为 default")

    md_content = md_content.replace("\r\n", "\n").replace("\r", "\n")
    return md_path, md_content, file_title


@step_log("_split_document_by_title")
def _split_document_by_title(md_content: str, file_title: str) -> list[dict[str, str]]:
    """
    先根据多级标题切割文档内容
    1.考虑【多级标题】连续出现 -> 子标题拼接父标题
    2.考虑【无标题的内容】-> 当前是合并到紧接着的下一个标题
    3.考虑【代码块】(包含#) -> 整个代码块内容归属当前标题

    Args:
        md_content: 文档内容
        file_title: 文件标题

    Returns:
        list[dict[str, str]]: 标题切块后的文档内容
    """
    chunks: list[dict[str, str]] = []

    current_title: str | None = None  # 记录当前处理的标题
    current_title_lines: list[str] = []  # 记录当前处理标题下的所有行

    is_code: bool = False  # 记录当前是否在代码块中

    # 1.按行切割整个文档
    document_lines: list[str] = md_content.split("\n")

    # 2.正则筛选一级标题
    title_reg = re.compile(r"^\s*#{1,6}\s.+")

    # 3.遍历所有行
    for i, line in enumerate(document_lines):
        logger.debug(f" |```当前行: line {i}")

        line_strip = line.strip()

        # 3.1 跳过空行
        if not line_strip:
            logger.debug(f" |```当前空行, 跳过: line {i}")
            continue

        # 3.1 处理代码块
        if "```" in line_strip or "~~~" in line_strip:
            is_code = not is_code  # 进入为 True, 跳出为 False
            logger.debug(f" |```{'进入' if is_code else '跳出'}代码块: line {i}")
            current_title_lines.append(line_strip)
            continue

        # 3.2 当前是标题行
        if not is_code and title_reg.match(line_strip):
            # 3.2.1 先结算上一块标题及内容
            if current_title and len(current_title_lines) > 1:
                chunks.append(
                    {
                        "file_title": file_title,
                        "title": current_title,
                        "content": "\n".join(current_title_lines),
                    }
                )

            # 3.2.2 连续标题, 合并到父标题 xx_xx_..
            if current_title and len(current_title_lines) == 1:
                current_title = current_title + "_" + line_strip
                current_title_lines = [current_title]
                continue

            # 3.2.3 无标题内容追加紧接着的下一个标题, 作为自己的标题
            if not current_title and len(current_title_lines) > 0:
                current_title_lines = [line_strip, *current_title_lines]
            else:
                current_title_lines = [line_strip]

            # 3.2.4 更新当前标题
            current_title = line_strip

        # 3.3 当前不是标题行, 是内容行
        else:
            current_title_lines.append(line_strip)

    # 4.考虑最后一段标题内容没有结算的情况
    if current_title and len(current_title_lines) > 1:
        chunks.append(
            {
                "file_title": file_title,
                "title": current_title,
                "content": "\n".join(current_title_lines),
            }
        )

    logger.info(f"|```根据标题切块 chunks: {len(chunks)}")
    return chunks


@step_log("_refine_split_and_merge_chunks")
def _refine_split_and_merge_chunks(
    chunks: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    检查每个文档块是否超过最大长度, 超过则进行精细切割, 否则直接添加

    Args:
        chunks: 标题切块后的文档内容

    Returns:
        list[dict[str, str]]: 标题切块后, 针对每块精细切割后的文档内容
    """
    refine_chunks: list[dict[str, str]] = []

    # 超长就要精细切割
    for chunk in chunks:
        if len(chunk.get("content")) > CHUNK_SIZE:
            refine_chunks.extend(_split_chunk_content(chunk))
        else:
            refine_chunks.append(chunk)

    merge_refine_chunks = _merge_chunk_content(refine_chunks)

    logger.info(
        f"语义分块后的chunks, 递归分块+合并后的数量: {len(merge_refine_chunks)}"
    )
    return merge_refine_chunks


@step_log("_merge_chunk_content")
def _merge_chunk_content(refine_chunks: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    合并同一个 parent_title 下, 前一个chunk小于400且合并后小于1000的chunks

    Args:
        refine_chunks: 标题切块后, 针对每块精细切割后的文档内容

    Returns:
        list[dict[str, str]]: 手动合并后的 refine_chunks
    """
    merge_refine_chunks: list[dict[str, str]] = []

    base_chunk: dict[str, str] = None
    for next_chunk in refine_chunks:
        if base_chunk is None:
            base_chunk = next_chunk
            continue

        # base_chunk <= min -> check if merge
        need_check = len(base_chunk.get("content")) <= CHUNK_MIN

        if need_check:
            bpt = base_chunk.get("parent_title")
            npt = next_chunk.get("parent_title")

            # 检查是否是同一个父标题
            is_same_parent_title = bpt and npt and bpt == npt

            if is_same_parent_title:
                bc: str = base_chunk.get("content")
                nc: str = next_chunk.get("content")[len(npt) + 1 :]

                # base_chunk + next_chunk <= max --> need merge
                need_merge = (len(bc) + len(nc)) <= CHUNK_MAX_SIZE

                if need_merge:
                    base_chunk["content"] = bc + "\n" + nc
                else:
                    # 同一个父标题, 但是合并大于1000, 不需要合并
                    merge_refine_chunks.append(base_chunk)
                    base_chunk = next_chunk
            else:
                # 不是同一个父标题, 不需要合并
                merge_refine_chunks.append(base_chunk)
                base_chunk = next_chunk
        else:
            # base_chunk 大于 400, 不需要合并
            merge_refine_chunks.append(base_chunk)
            base_chunk = next_chunk

    # 合并最后一定会留一个未被合并的 base_chunk (无论是合并后还是不用合并的)
    if base_chunk:
        merge_refine_chunks.append(base_chunk)

    logger.info(f"sub_chunks 合并后的数量: {len(merge_refine_chunks)}")
    return merge_refine_chunks


@step_log("_split_chunk_content")
def _split_chunk_content(chunk: dict[str, str]) -> list[dict[str, str]]:
    """
    对文档块进行精细切割, 每个文档块的长度不超过最大长度
    chunk_content = #xx\n行1\n行2...
    sub_chunks = #xx\n块1, #xx\n块2, ...

    Args:
        chunk: 文档内容

    Returns:
        list[dict[str, str]]: 文档内容的精细切割结果
    """
    sub_chunks: list[dict[str, str]] = []

    content = chunk.get("content")
    deal_content = content[len(chunk.get("title")) + 1 :]  # 先剔除标题、再切割
    prefix = chunk.get("title") + "\n"  # 标题前缀

    spliter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE - len(prefix),  # 600 - 标题前缀的长度
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " "],  # noqa: RUF001
    )

    for index, text in enumerate(spliter.split_text(deal_content), start=1):
        sub_chunks.append(
            {
                "file_title": chunk.get("file_title"),
                "parent_title": chunk.get("title"),  # 注意
                "title": f"{chunk.get('title')}_{index}",  # 注意
                "part": index,
                "content": prefix + text,
            }
        )

    return sub_chunks


@step_log("_padding_chunks_metadata")
def _padding_chunks_metadata(chunks: list[dict[str, str]]):
    """
    补充未精细切割的chunks的属性 parent_title, part
    """
    for chunk in chunks:
        if "parent_title" not in chunk:
            chunk["parent_title"] = chunk.get("title")
        if "part" not in chunk:
            chunk["part"] = 1

    logger.info("chunks 属性对齐完成")


@step_log("_backup_chunks_json")
def _backup_chunks_json(md_path: str, chunks: list[dict[str, str]]):
    """最终 chunks json 备份"""
    json_path_obj: Path = Path(md_path).parent / f"{Path(md_path).stem}.json"
    json_path_obj.write_text(
        data=json.dumps(chunks, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    logger.info(f"chunks 数据备份完成, 备份位置:{json_path_obj!s}")
