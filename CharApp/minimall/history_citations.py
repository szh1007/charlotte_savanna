"""引用那一半的历史 (L5-c): 记录表 → 这段会话检索过的原文 / 每条消息该带什么.

一句话理解: 刷新页面之后, 答复里的 `[n]` 还得点得开 —— 而历史正文来自记录表,
引用得从**同一段对话的检索记录**里重新读回来.

三条设计决定 (票据 §二 的取舍, 这里是它的落地):

1. **不加库列**: 读的是 `charagent_tool_calls` 里当时那几次 `search_knowledge` 的
   `result` —— 它就是模型当时看到的正文 (ADR-0016 起原文落库), 与直播那条路喂给
   `citations_from_results` 的是**同一段文本**.
2. **编号是回读出来的, 不是重算的**: 文本里写着的 `[n]` 就是模型看到的号 (编号由
   `CitationLedger` 在写入时累加, 见 `knowledge/citations.py`), 历史这条路上一个数
   都不重新分配 —— "两边一致"因此不是"两套算法碰巧一致".
3. **按会话累加**: 第 k 轮该带的引用是**这段会话到第 k 轮为止**的全部检索结果 ——
   与直播那份 `final` 载荷同一口径 (模型上下文里一直有前面几轮的条文, 它可以引用
   早先的号).

**N+1 条查询如实记账**: 每个 run 一次 `list_for_run` (仓库层没有"按会话取全部工具
调用"的口子, 而业务这一侧不写 SQL 是既定纪律). 一段演示对话几十个 run, 代价是几十
次索引命中的小查询; 真嫌慢时该动的是仓库层 (加一个方法), 不是在这里拼 SQL.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from CharAgent.db import MessagesRepository, PgDatabase, ToolCallsRepository
from CharAgent.db.entities import Message, ToolCallStatus
from CharApp.minimall.knowledge.citations import (
    SEARCH_TOOL_NAME,
    citations_from_results,
    cited_numbers,
)


def _used_in(
    answer: str, citations: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """这条答复引用到的那几段 (与 `Citations.used_in` 同一个判据).

    两处各写一遍是因为选的对象不同 (直播那边手里是账本, 这边是已经算好的列表),
    但判据只有一个 —— `cited_numbers`.

    **对不上的号在这里不记账** (与直播那一侧的分工): 它算不算数在写出去的那一刻
    已经判过, 刷新页面时把同一笔再记一遍只会让日志虚高 —— 那一笔 (以及它的措辞)
    归 `Citations.used_in`.
    """
    used = cited_numbers(answer)
    return [dict(item) for item in citations if item["n"] in used]


__all__ = ["HistoryCitations", "search_result_texts"]


async def _results_by_run(
    database: PgDatabase, thread_id: str
) -> list[tuple[str, list[str]]]:
    """这段会话的每一轮 → 那一轮检索回来的原文 (按发生序).

    轮次顺序取自记录表里消息的出现顺序 (与前端看到的顺序同一个来源), 而不是运行
    表的创建时间 —— 两侧用同一个排序, 复读出来的顺序才不会与页面上差一拍.
    """
    messages = MessagesRepository(database)
    calls = ToolCallsRepository(database)
    ordered: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for row in await messages.list_conversation(thread_id):
        run_id = row.run_id
        if not run_id or run_id in seen:
            continue
        seen.add(run_id)
        texts = [
            call.result
            for call in await calls.list_for_run(run_id)
            if call.tool_name == SEARCH_TOOL_NAME
            # 值比较, 不用 `is`: 落库那一侧存的可能是普通字符串 (StrEnum 与它相等,
            # 但身份不同 —— 假库上真踩过这一脚)
            and call.status == ToolCallStatus.SUCCEEDED
            and call.result
        ]
        ordered.append((run_id, texts))
    return ordered


async def search_result_texts(database: PgDatabase, thread_id: str) -> list[str]:
    """这段会话检索回来的全部原文 (按发生序) —— **恢复旧会话时垫给收集器的那一份**.

    少了它, 进程重启后接着聊时: 编号会从 `[1]` 重数 (同一段对话里出现两个 `[1]`),
    直播那份引用也会比历史少掉前半截.
    """
    by_run = await _results_by_run(database, thread_id)
    return [text for _, texts in by_run for text in texts]


class HistoryCitations:
    """框架那个插座: 记录行 → 每条消息该补的字段 (L5-c 的历史那一半).

    形状与 `MessageExtras` 协议一致: `provide(thread_id, rows)` → `{下标: {字段}}`.
    框架只做一次浅合并 (`messages[i].update(extras.get(i, {}))`), **不认识**
    `citations` 是什么 —— 引用是业务的话术, 框架只负责把它捎到浏览器.

    三条口径:

    - **只给 `assistant` 行**: 会写 `[n]` 的是模型说的话; 用户那一句里出现的 `[n]`
      是买家自己打的字, 不该被点开.
    - **值恒是列表** (没有检索过就是空列表): 前端因此不必分辨"没有这个字段"与
      "字段是空的".
    - **行里没有 `run_id` 的 (老数据 / 系统行) 不补**: 没有那一轮的检索记录可回读.
    """

    def __init__(self, database: PgDatabase) -> None:
        self._database = database

    async def provide(
        self, thread_id: str, rows: Sequence[Message]
    ) -> Mapping[int, Mapping[str, Any]]:
        """按行序给出每条该补的字段 (见类说明)."""
        # 逐轮累加着解析一遍 (不是每行重算一次): 每一轮结束时那份引用列表就是
        # **该轮答复**该带的那一份 —— 与直播时收集器攒到那一刻的结果逐字一致.
        cumulative: dict[str, list[dict[str, Any]]] = {}
        accumulated: list[str] = []
        for run_id, texts in await _results_by_run(self._database, thread_id):
            accumulated.extend(texts)
            cumulative[run_id] = citations_from_results(accumulated)

        extras: dict[int, Mapping[str, Any]] = {}
        for index, row in enumerate(rows):
            if getattr(row, "role", "") != "assistant":
                continue
            run_id = getattr(row, "run_id", None)
            if run_id in cumulative:
                # 与直播那一路同一个判据: 只带**这条答复真的引用到**的那几段
                extras[index] = {
                    "citations": _used_in(row.content or "", cumulative[run_id])
                }
        return extras
