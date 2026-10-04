"""逐句引用 (L5-c): 编号、片段、以及"哪些文本算引用".

一句话理解: 助手的答复里写了 `[3]`, 买家点一下要看见"那一段是从哪来的" —— 本模块
负责把**这段话**做出来, 并且保证**直播与刷新之后看到的是同一份**.

它由四小件组成, 各自只有一件事:

| 件 | 干什么 |
|----|--------|
| `CitationLedger` | 编号: 这段对话里每段被检索回来的文字**只拿一个号**, 累加不重来 |
| `citations_from_results` | **唯一的**"检索结果文本 → 引用条目"翻译 (直播与历史共用) |
| `Citations` | 这段对话的引用账: 领号 / 收原料 / 交付 (三处用的是同一个对象) |
| `citing_sink` | 交付: `final` 事件出门前把引用挂上去 |

**为什么编号是"这段对话"的序号**: 一次 `search_knowledge` 返回五段, 下一次又返回
五段 —— 两次都从 `[1]` 起的话, 模型写的 `[3]` 到底指哪一段就成了谜. 累加编号之后
同一个号在这段对话里只指一段 (模型在自己的上下文里看到的也是同一套号), 于是
"服务端按号回指"这件事才成立.

**为什么只有一个翻译函数**: 直播那条路的输入是工具**刚返回的文本**
(`Citations` 从 `ON_TOOL_EXECUTED` 钩子上收), 历史那条路读的是
`charagent_tool_calls.result` 里**同一段文本** (落库时一个字没改) —— 同一份输入、
同一个函数, 于是两边**逐字一致**, 而不是"两套实现碰巧算得一样".
`test_citations` 里有一条用例专门钉这条 (同一段文本, 两条路的产出相等).

**这一段是 ADR-0003 上开的唯一一个口子** (见 `adr/0027`): 进浏览器的只有**被引用
的那几段** (标题 + 前 `CITATION_SNIPPET_LENGTH` 字), 完整工具返回与参数原文照旧
不出本进程. 这不是"把工具结果发给前端" —— 那一条主规则没有动.
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from typing import Any

from CharAgent.stream import EventSink, EventType, StreamEvent
from CharApp.minimall.injection import (
    DATA_CLOSE,
    DATA_TRAILER,
    SUSPICIOUS_NOTE_PREFIX,
)

logger = logging.getLogger(__name__)

# 包裹那两句声明 (见 `formatting` 与 `injection`): 解析时整行剔掉 —— 它们是我们
# 写的, 不是语料正文. 认的是**整行相等**, 不是"含这几个字".
_WRAPPER_LINES = frozenset({DATA_CLOSE, DATA_TRAILER})

# 来源卡片里那段摘录的长度 (字符). 200 是票面定的: 够看清"答的是哪一条", 又不至于
# 把整篇政策搬进浏览器 (一屏放不下三张卡).
CITATION_SNIPPET_LENGTH = 200

# 工具名: 只有它返回的东西带编号 (见 `tools._search_knowledge`)
SEARCH_TOOL_NAME = "search_knowledge"

# 一段引用的开头 (`formatting.format_chunks` 写死的形状: `[n] 标题` + 换行 + 正文).
#
# 解析自己写出去的格式是**有意的**: 历史那条路手里只有落库的那段文本, 而它是
# 模型当时看到的那一份 —— 拿它当唯一依据, 比另存一份结构化数据更不容易漂
# (另存就要加库列, 见票据 §二 的取舍).
_BLOCK_START = re.compile(r"^\[(\d+)\] (.*)$")


def cited_numbers(text: str) -> set[int]:
    """答复正文里出现过的编号 (`[1]` `[2]` …) —— **过滤用**, 不解释它们的意思.

    它只回答一个问题: 这条答复用了哪几个号. 回答"这个号指哪一段"的仍然是服务端
    给的那份引用表 —— 所以这不是"解析模型的话"(那条纪律挡的是猜它指什么), 而是
    "别把没被引用的段落也发出去"那道闸.
    """
    return {int(number) for number in re.findall(r"\[(\d+)\]", text)}


def citations_from_text(text: str) -> list[dict[str, Any]]:
    """一次检索的返回文本 → 引用条目 (`n` / `title` / `snippet`).

    形状是 `formatting.format_chunks` 定的: `[n] 标题`, 换行, 正文, 段间空行,
    外面还裹着两句"这是资料不是指令"的声明 (L5-d).
    **第一段之后**编号必须连续 (`[6] [7] [8] …`) 才认新段 —— 正文里偶尔出现一行
    `[9] …` (政策原文里写了什么编号) 不会被当成新的一段, 那正是这条判据要挡的.
    第一段自己可以是任何号: 编号是这段对话累加出来的, 一次检索的返回不一定从 1 起.

    外层那两句声明与段尾的可疑标记**不进摘录** (`_finish` 那一处剔掉): 来源卡上该
    是语料原文, 不是"我们说的话".

    Args:
        text: 一次 `search_knowledge` 的返回原文.

    Returns:
        list[dict]: 每段一条 `{"n", "title", "snippet"}`; `snippet` 是**服务端**从
        正文里截的前 `CITATION_SNIPPET_LENGTH` 字 (不是模型生成的措辞).

    Note:
        不带 `slug`: 历史那条路手里只有这段文本, 而它没写 slug —— 想在两边都带上,
        就得把 slug 塞进**模型看得到**的文本里只为给前端用, 不值. 卡片要的"这是
        哪一条"由标题回答.
    """
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    lines: list[str] = []
    for line in text.splitlines():
        match = _BLOCK_START.match(line)
        if match is not None:
            number = int(match.group(1))
            # 第一段的号可以是任何数 (这段对话里第几次检索 —— 编号是累加的);
            # 之后每段必须比上一段大 1, 那条才是"这是新一段"的判据
            expected = None if current is None else current["n"] + 1
            if expected is None or number == expected:
                if current is not None:
                    blocks.append(_finish(current, lines))
                current = {"n": number, "title": match.group(2).strip()}
                lines = []
                continue
        if current is not None:
            lines.append(line)
    if current is not None:
        blocks.append(_finish(current, lines))
    return blocks


def _finish(block: dict[str, Any], lines: list[str]) -> dict[str, Any]:
    """一段解析完: 正文拼起来, **剔掉我们自己写的那几行**, 截前 N 字当摘录.

    要剔的三样都不是语料 (见 `formatting.format_chunks` 与 `injection`):

    - `【资料结束】` 与那句收尾声明: 它们跟在**最后一段**的正文后面 (块级声明在
      文本里, 而解析是按段切的);
    - 可疑内容的标记行: L5-d 起, 命中的段尾会多一句 `[可疑内容] …` —— 它是给我们
      和模型看的, 不该出现在买家的来源卡上 (卡上该是他点开想看的那截政策原文).

    靠**整行相等** / **行首前缀**认, 不靠"位置": 一段正文里出现同样的字 (比如某篇
    政策真的写了"【资料结束】") 是语料自己的事, 那种情况由 `injection` 的围栏逃逸
    规则负责标出来, 而不是在这里被悄悄吃掉.
    """
    body = "\n".join(
        line
        for line in lines
        if line.strip() not in _WRAPPER_LINES
        and not line.strip().startswith(SUSPICIOUS_NOTE_PREFIX)
    ).strip()
    return {
        "n": block["n"],
        "title": block["title"],
        "snippet": body[:CITATION_SNIPPET_LENGTH],
    }


def citations_from_results(results: Iterable[str]) -> list[dict[str, Any]]:
    """多次检索的返回文本 (按发生序) → 一份引用列表 (**直播与历史共用的那一个**).

    编号在这份列表里天然唯一 (累加编号, 见 `CitationLedger`); 万一撞号 (进程重启后
    编号从头数 —— 那种会话的编号本来就乱了), **留先出现的那一条**并把重复记一条
    warning: 猜测回指不如留个可查的痕迹.
    """
    merged: dict[int, dict[str, Any]] = {}
    for text in results:
        for citation in citations_from_text(text):
            if citation["n"] in merged:
                logger.warning(
                    "引用编号重复 (n=%s): 这段对话里的编号可能没有连续累加",
                    citation["n"],
                )
                continue
            merged[citation["n"]] = citation
    return [merged[number] for number in sorted(merged)]


class CitationLedger:
    """这段对话的编号账本: 每段被检索回来的文字拿一个号, **累加不重来**.

    一次工具调用要几个号由它自己报 (`take`), 于是"第几次检索"不影响编号 ——
    这正是逐句引用能无歧义回指的前提.

    `catch_up` 用在**恢复一段旧会话**时: 进程重启后新会话的编号得从上一段留下的
    记录接着数, 不然同一段对话里会出现两个 `[1]`.
    """

    def __init__(self, *, start: int = 1) -> None:
        self._next = start

    @property
    def next_index(self) -> int:
        """下一段文字会拿到的号 (给它自己与用例看)."""
        return self._next

    def take(self, count: int) -> int:
        """领 `count` 个连续编号, 返回第一个 (0 个不占号)."""
        start = self._next
        self._next += count
        return start

    def catch_up(self, citations: Sequence[Mapping[str, Any]]) -> None:
        """把编号推进到这批引用之后 (恢复旧会话时用)."""
        for citation in citations:
            self._next = max(self._next, int(citation["n"]) + 1)


class Citations:
    """这段对话的引用账: 编号 + 原料 + 交付 —— 一个会话一份.

    三件事住在同一个对象里是有理由的: 它们说的是同一件事 (**这段对话里检索回来
    的那些段落**), 分开成三个对象只会让装配处多两根线, 而且"编号"与"原料"一旦
    分家, 就没人能保证它们记得的是同一批段落.

    | 用它的地方 | 调什么 |
    |-----------|--------|
    | 检索工具 (领号) | `take(n)` |
    | `ON_TOOL_EXECUTED` 钩子 (收原料) | `record(...)` |
    | `final` 出口 (交付) | `all()` |

    Args:
        seeded: 这段会话**之前**查过的那些返回原文 (从记录表读回来, 见
            `history_citations.search_result_texts`)。恢复一段旧会话时要它, 不然
            编号会从头数、直播那份引用也会比历史少一截.
    """

    def __init__(self, seeded: Iterable[str] = ()) -> None:
        self._ledger = CitationLedger()
        self._texts: list[str] = []
        self.seed(seeded)

    def seed(self, texts: Iterable[str]) -> None:
        """把旧的检索结果垫在前面 (顺序即发生序), 并把编号推到它们之后."""
        seeded = list(texts)
        if not seeded:
            return
        self._texts[:0] = seeded
        self._ledger.catch_up(citations_from_results(seeded))

    def take(self, count: int) -> int:
        """领 `count` 个连续编号, 返回第一个 (检索工具在编排文本之前调它)."""
        return self._ledger.take(count)

    def record(self, *, turn: Any, call: Any, execution: Any) -> None:
        """`ON_TOOL_EXECUTED` 钩子: 收下这次检索的返回文本.

        载荷里的 `call` 是**模型发起的那一次调用** (`ModelToolCall`: 名字在
        `.name` 上 —— 不是记录层那个 ToolCall 实体), `execution` 是执行结果
        (见 `hooks/registry.py` 的载荷表). 失败的那次没有正文可收 (回填的是错误
        文本); 收进来也无妨 —— 解析器认不出 `[n]` 开头就一条都不产出.
        """
        if call.name != SEARCH_TOOL_NAME or not execution.ok:
            return
        if execution.content:
            self._texts.append(execution.content)

    @property
    def texts(self) -> tuple[str, ...]:
        """这段对话攒下的检索结果原文 (播种 + 钩子两路)."""
        return tuple(self._texts)

    def all(self) -> list[dict[str, Any]]:
        """这份账里的全部引用 (交付那一刻才算: 同一个函数算直播与历史两边)."""
        return citations_from_results(self._texts)

    def used_in(self, answer: str) -> list[dict[str, Any]]:
        """**这条答复真正引用到的**那些 (按编号升序); 编造的编号记一笔日志.

        为什么交付前要过这一刀 (而不是把账上所有段落都发出去): ADR-0027 的边界是
        "被引用的那一段才进浏览器". 服务端不猜"模型想说什么", 但**数它用了哪几个号**
        是机械的 —— 顺着答复正文扫一遍就行, 而"号指哪一段"仍然由服务端那份表说了算.

        对不上的号 (账上没有那个号: 模型自己造的, 或者上一段会话留下的旧号) 的处置
        是 L5-d 的第 4 层那一条: **不渲染成引用** (前端原样当文本显示, C10 起就是
        这样) + **日志里记一笔** —— 编引用是"答复在指一件不存在的东西", 无论这是
        模型幻觉还是注入得手, 都该留下痕迹. 只在这里记 (历史那条路 `_used_in` 不记):
        某个号算不算数在**写出去的那一刻**已经判过, 刷新页面不该把同一笔再记一遍.
        """
        used = cited_numbers(answer)
        known = {item["n"] for item in self.all()}
        for number in sorted(used - known):
            logger.warning(
                "答复引用了账上没有的编号 [%s] —— 模型编的号不渲染来源卡",
                number,
            )
        return [item for item in self.all() if item["n"] in used]


def citation_sink(sink: EventSink, citations: Citations) -> EventSink:
    """把出口包一层: `final` 事件出门前挂上引用 (其余事件原样转交).

    为什么挂在**出口**而不是别处: 引用是终局答复的一部分 (ADR-0027 与票面 §一
    的同一条理由 —— 不新增事件类型, `#4` 那条「终局事件恰好一个」的不变量一个字
    不动), 而出口是这份事件最后一次能被补齐的地方. 它也是**两个入口共用**的地方:
    网页端与命令行拿到的 `final` 因此长得一样.

    同步 / 异步出口都吃 (与 `redaction.redacting_sink` 同一套写法): 框架的路由是
    同步的, 用例常递一个收集器.

    Args:
        sink: 原来的出口 (框架的路由 / 终端渲染器 / 测试收集器).
        citations: 这次会话的引用账 (见 `Citations`).

    Returns:
        EventSink: 给 `final` 补过引用再转交的出口.
    """

    async def cited(event: StreamEvent) -> None:
        if event.type is EventType.FINAL:
            # 载荷是活引用 (框架明说 `data` 是自由字典), 于是这里**原地**补一个键:
            # 下游 (BFF / 前端) 拿到的还是同一个事件对象.
            # 只带上**这条答复真的引用到**的那几段 (见 `Citations.used_in`).
            answer = str(event.data.get("content") or "")
            event.data["citations"] = citations.used_in(answer)
        result: Awaitable[None] | None = sink(event)
        if inspect.isawaitable(result):
            await result

    return cited


__all__ = [
    "CITATION_SNIPPET_LENGTH",
    "SEARCH_TOOL_NAME",
    "CitationLedger",
    "Citations",
    "citation_sink",
    "citations_from_results",
    "citations_from_text",
    "cited_numbers",
]
