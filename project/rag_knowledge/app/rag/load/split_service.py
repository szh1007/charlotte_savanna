import json
import re
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ...process.load.agent.state import LoadState
from ...shared.runtime.logger import logger, step_log
from .config import (
    CHUNK_MAX_SIZE,
    CHUNK_MIN,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CROSS_MERGE_BODY_MIN,
)

# HTML 表格整块匹配 (MinerU 输出的表格是单行, 不会跨行)
_TABLE_REGEX = re.compile(r"<table>.*?</table>", re.DOTALL | re.IGNORECASE)
# 剥掉表格自己的开闭标签, 供超长表格按行重组时复用
_TABLE_TAG_REGEX = re.compile(r"</?table>", re.IGNORECASE)
# 标题行, 捕获 `#` 的个数用于判断层级
_TITLE_LEVEL_REGEX = re.compile(r"^(#{1,6})\s+\S")
# content 首行是不是标题行 (只做匹配, 不关心层级)
_TITLE_LINE_REGEX = re.compile(r"^#{1,6}\s")


@step_log("split_document")
def split_document(state: LoadState) -> LoadState:
    # 1.获取并校验数据
    md_path, md_content, file_title = _validate_data(state)

    # 2.语义切割 (根据多级标题)
    chunks = _split_document_by_title(md_content, file_title)

    # 3.属性对齐 (parent_title / part)
    # 必须在下面的合并之前: 合并的判据是「两块 parent_title 相同」, 而未切分的块
    # 到这一步才拿到 parent_title —— 放在合并之后的话, 这类块在判据里恒为 None
    # (issue C02: 补齐与合并的先后反了).
    _padding_chunks_metadata(chunks)

    # 4.精细切割(递归切割 + 合并)
    chunks = _refine_split_and_merge_chunks(chunks)

    # 5.把祖先路径拼进检索正文.
    # **必须在切分/合并之后**: 那些判据 (剥首行标题算正文长度、判同节续块)
    # 都依赖 content 以标题行开头, 提前拼会把它们全部打乱
    _attach_section_path(chunks)

    # 6.备份 chunks json
    _backup_chunks_json(md_path, chunks)

    # 7.更新state
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


def _push_section(title_stack: list[tuple[int, str]], title_line: str) -> None:
    """把标题行压进章节栈, 压之前弹掉同级以及更深的.

    栈只用来拼「祖先路径」(见 `_section_path`), 不参与切分判据.

    **文档 H1 不入栈**: 文档身份已经由 `item_name` 过滤表达, 让每个块都带上
    项目名只会让全库的块互相更像、压低区分度. 只留 `##` 及以下.
    """
    match = _TITLE_LEVEL_REGEX.match(title_line)
    if not match:
        return
    level = len(match.group(1))
    if level == 1:
        return

    while title_stack and title_stack[-1][0] >= level:
        title_stack.pop()
    title_stack.append((level, title_line))


def _section_path(title_stack: list[tuple[int, str]]) -> str:
    """把章节栈拼成 `祖父 > 父 > 自己` 的路径串."""
    return " > ".join(title for _, title in title_stack)


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
    # 祖先链: [(层级, 标题行)]. 结算每一块时快照成 section_path ——
    # content 首行只是块自己的标题, 缺了所属父节, 见 _attach_section_path
    title_stack: list[tuple[int, str]] = []

    is_code: bool = False  # 记录当前是否在代码块中

    # 1.按行切割整个文档
    document_lines: list[str] = md_content.split("\n")

    # 2.正则筛选一级标题
    title_reg = re.compile(r"^\s*#{1,6}\s.+")
    # MinerU 会把步骤符号误判成标题 (典型: "## a 按 (Foil Save) ...").
    # 这类伪标题必须当内容行处理: 否则每个操作步骤都自成一个 section,
    # 标题退化成 "## a"、丢掉所属章节, 嵌出来的向量也就没了可检索的语义.
    fake_title_reg = re.compile(r"^\s*#{1,6}\s+[a-zA-Z]\s")

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

        # 3.2 当前是标题行 (伪标题不算标题, 见 fake_title_reg)
        is_real_title = bool(title_reg.match(line_strip)) and not (
            fake_title_reg.match(line_strip)
        )

        if not is_code and is_real_title:
            # 3.2.1 先结算上一块标题及内容 (路径取【旧栈】, 因为结算的是上一块)
            if current_title and len(current_title_lines) > 1:
                chunks.append(
                    {
                        "file_title": file_title,
                        "title": current_title,
                        "section_path": _section_path(title_stack),
                        "content": "\n".join(current_title_lines),
                    }
                )

            # 3.2.2 连续标题, 合并到父标题 xx_xx_..
            if current_title and len(current_title_lines) == 1:
                current_title = current_title + "_" + line_strip
                current_title_lines = [current_title]
                # 连写的两级都要进栈: 它们本来就是父子, 只是中间没有正文
                _push_section(title_stack, line_strip)
                continue

            # 3.2.3 无标题内容追加紧接着的下一个标题, 作为自己的标题
            if not current_title and len(current_title_lines) > 0:
                current_title_lines = [line_strip, *current_title_lines]
            else:
                current_title_lines = [line_strip]

            # 3.2.4 更新当前标题 (同时压栈, 记下这一块的祖先链)
            _push_section(title_stack, line_strip)
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
                "section_path": _section_path(title_stack),
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


def _body_length(chunk: dict[str, str]) -> int:
    """块正文的长度 (剥掉标题行).

    判断一块有没有检索价值要看正文, 不能看整块长度: 标题可能很长,
    「## 6.2 菜单项 更改设置」整块 15 字而正文只有 5 个字.
    """
    content = chunk.get("content") or ""
    title = chunk.get("title") or ""
    if title and content.startswith(title):
        content = content[len(title) :]
    return len(content.strip())


@step_log("_merge_chunk_content")
def _merge_chunk_content(refine_chunks: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    合并相邻的块, 分两种情形, 判据不同:

    1. **同一节的续块** (parent_title 相同): 前一块小于 CHUNK_MIN 就并, 上限
       CHUNK_MAX_SIZE —— 它们本来就是同一节被切开的内容, 并回去是还原.
    2. **跨节**: 只有前一块是「碎片」(正文短于 CROSS_MERGE_BODY_MIN) 才吸收,
       且合并后不超过 CHUNK_SIZE. 这类合并是为了救那些只剩一个标题的碎块
       ("## 6.2 菜单项 / 更改设置" 整块 15 字, 正文 5 字, 零召回).

    跨节判据此前放宽到「同一份文档内相邻即可」, 结果把正常小节也吞了:
    实测 4 份项目文档 106 个块里 32 个含两个以上章节标题, 一个块里混
    「五、功能与付费差异」和「六、API 契约」—— 这种块对哪个主题都匹配不好.

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

        bpt = base_chunk.get("parent_title")
        npt = next_chunk.get("parent_title")
        is_same_parent_title = bool(bpt and npt and bpt == npt)

        if is_same_parent_title:
            need_check = len(base_chunk.get("content")) <= CHUNK_MIN
            max_size = CHUNK_MAX_SIZE
        else:
            # 跨节只有「正文极短的碎片」会被吸收; 上限给到 CHUNK_MAX_SIZE,
            # 免得碎片因为「后面那块太大」而落单 —— 几十字的块独立进库是纯噪音,
            # 而被吸收只是给正文挂上一个小标题, 对块的整体语义几乎无影响.
            need_check = _body_length(base_chunk) < CROSS_MERGE_BODY_MIN
            max_size = CHUNK_MAX_SIZE

        if need_check:
            is_same_file = base_chunk.get("file_title") == next_chunk.get("file_title")

            if is_same_file:
                bc: str = base_chunk.get("content")
                if is_same_parent_title:
                    # 同一节的续块: 下一块开头是重复的标题, 去掉
                    nc: str = next_chunk.get("content")[len(npt) + 1 :]
                else:
                    # 跨节: 下一块的标题是新小节的起点, 原样保留
                    nc: str = next_chunk.get("content")

                # base_chunk + next_chunk <= max --> need merge
                need_merge = (len(bc) + len(nc)) <= max_size

                if need_merge:
                    base_chunk["content"] = bc + "\n" + nc
                else:
                    # 合并后超过上限, 不合并
                    merge_refine_chunks.append(base_chunk)
                    base_chunk = next_chunk
            else:
                # 不是同一份文档, 不需要合并
                merge_refine_chunks.append(base_chunk)
                base_chunk = next_chunk
        else:
            # 不是需要吸收的碎片, 不需要合并
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
        # 末尾那个 "" 是 LangChain 的「实在切不动就按字符硬切」兜底, 不能省 ——
        # 少了它, 一段没有分隔符的文本 (典型: 被压成一行的 HTML 表格) 会整段返回,
        # chunk_size 形同虚设. 实测: chunk_size=574 切 'x'*1400 得到 1 片 1400 字符;
        # 修复前真实产物里有 793 / 944 / 1403 字符的块 (issue C02).
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],  # noqa: RUF001
    )

    # 表格整块摘出来单独处理, 不参与递归切分 (见 _split_content_by_table)
    sub_texts: list[str] = []
    for is_table, block in _split_content_by_table(deal_content):
        if is_table:
            sub_texts.extend(_split_table_block(block))
        else:
            sub_texts.extend(spliter.split_text(block))

    # 最后一片太短就并回前一片.
    # 不并的话它会变成「某节的尾巴块」: 要么独立成一个没有检索价值的碎块,
    # 要么被跨节合并带走 —— 无论哪种, 都让块的边界和章节边界对不上
    # (实测目录树被切成 9 片后, 尾巴块把「### 分层设计」和「## 3. 核心流程」一起吞了).
    if len(sub_texts) > 1 and len(sub_texts[-1]) < CHUNK_MIN:
        sub_texts[-2] = f"{sub_texts[-2]}\n{sub_texts[-1]}"
        sub_texts.pop()

    for index, text in enumerate(sub_texts, start=1):
        sub_chunks.append(
            {
                "file_title": chunk.get("file_title"),
                "parent_title": chunk.get("title"),  # 注意
                # 切片继承原块的祖先链 —— 路径描述的是「这段内容属于哪一节」,
                # 切开之后这个归属不变
                "section_path": chunk.get("section_path"),
                "title": f"{chunk.get('title')}_{index}",  # 注意
                "part": index,
                "content": prefix + text,
            }
        )

    return sub_chunks


@step_log("_split_content_by_table")
def _split_content_by_table(text: str) -> list[tuple[bool, str]]:
    """把内容按 HTML 表格切成 (是否表格, 文本) 的块序列.

    为什么单拎出来:
    MinerU 把整张表压成一行, 且表格内部没有换行/句号这类分隔符. 直接交给
    RecursiveCharacterTextSplitter 会被末尾的 "" 兜底规则按字符硬切, 切出来的
    碎片长这样: "## 6.2 菜单项 d>○</td><td>○</td></tr>..." —— 表头没了、
    标签断在半截, 语义完全丢失 (实测 18/131 个 chunk 带这类 HTML 残渣).
    表格本身是完整的语义单元, 整块保留才能被检索到.
    """
    blocks: list[tuple[bool, str]] = []
    cursor = 0
    for match in _TABLE_REGEX.finditer(text):
        if match.start() > cursor:
            blocks.append((False, text[cursor : match.start()]))
        blocks.append((True, match.group(0)))
        cursor = match.end()
    if cursor < len(text):
        blocks.append((False, text[cursor:]))
    return [(is_table, block) for is_table, block in blocks if block.strip()]


@step_log("_split_table_block")
def _split_table_block(table: str) -> list[str]:
    """超长表格按行边界切, 每一片自己补回 `<table>` / `</table>` 以保证闭合.

    正常长度的表格整块返回 —— 不为了凑 chunk_size 把一张表拆成读不懂的碎片.

    每片单独闭合是必须的: 直接把 `</table>` 留在最后一片, 前面几片就成了
    「有 <td> 没有 <table>」的残片, 与本次要修的问题同源 (实测切完出现
    「开1闭0」+「开0闭1」的一对块).
    """
    if len(table) <= CHUNK_MAX_SIZE:
        return [table]

    body = _TABLE_TAG_REGEX.sub("", table)
    rows = [row for row in body.split("</tr>") if row.strip()]

    pieces: list[str] = []
    current = ""
    for row in rows:
        piece = row + "</tr>"
        if current and len(current) + len(piece) > CHUNK_MAX_SIZE:
            pieces.append(f"<table>{current}</table>")
            current = piece
        else:
            current += piece
    if current:
        pieces.append(f"<table>{current}</table>")
    return pieces


@step_log("_padding_chunks_metadata")
def _padding_chunks_metadata(chunks: list[dict[str, str]]):
    """
    补充未精细切割的chunks的属性 parent_title, part

    对未切分的块, `parent_title` 就是它自己的标题 —— 也就是说这类块的合并只可能
    发生在「同一标题出现两次」的情形; 标题各不相同的相邻块不会被误并.
    """
    for chunk in chunks:
        if "parent_title" not in chunk:
            chunk["parent_title"] = chunk.get("title")
        if "part" not in chunk:
            chunk["part"] = 1

    logger.info("chunks 属性对齐完成")


@step_log("_attach_section_path")
def _attach_section_path(chunks: list[dict[str, str]]) -> None:
    """把「祖先章节路径」拼进检索正文, 让每个块带上自己所属的节.

    每个块的 content 首行原本只是它自己的标题 (`### 5.2 环境变量`), 不含所属
    父节 —— 问题问「快速开始包含哪些步骤」时, 这一块整段没有「快速开始」四个
    字, 向量和精排都认不出它属于那一节: 实测它在全文档 24 条候选里排第 23 名,
    而同节的 §5.1 (首行带 `## 5. 快速开始`) 排第 1. 拼上路径
    (`## 5. 快速开始 > ### 5.2 环境变量`) 就补上了这个语义锚.

    **首行是替换而不是另加一行**: 路径末段就是当前标题, 再加一行等于重复.
    只动首行 —— 跨节合并出来的块后面还有别的标题行, 那是另一些节的起点,
    原样保留.

    Args:
        chunks: 切分合并后的块, 就地修改 (调用点与入参是同一个列表)
    """
    for chunk in chunks:
        path = chunk.get("section_path")
        content = chunk.get("content") or ""
        if not path or not content:
            continue
        first_line, sep, rest = content.partition("\n")
        if _TITLE_LINE_REGEX.match(first_line):
            chunk["content"] = path + sep + rest
        else:
            chunk["content"] = path + "\n" + content


@step_log("_backup_chunks_json")
def _backup_chunks_json(md_path: str, chunks: list[dict[str, str]]):
    """最终 chunks json 备份"""
    json_path_obj: Path = Path(md_path).parent / f"{Path(md_path).stem}.json"
    json_path_obj.write_text(
        data=json.dumps(chunks, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    logger.info(f"chunks 数据备份完成, 备份位置:{json_path_obj!s}")
