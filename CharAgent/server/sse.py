"""事件流 → SSE: 字段映射与响应体生成器.

一句话理解: 这一页只干两件事 —— 把一个事件写成一段 SSE 帧 (纯函数, 好测),
以及把「队列里的事件」抽成响应体 (生成器, 收尾时负责叫停没人听的运行).

SSE 帧长这样 (三行 + 一个空行结尾, 空行是**分隔符**不是装饰):

    id: 3
    event: tool_result
    data: {"type": "tool_result", "seq": 3, ... , "run_id": "9f3a..."}

字段映射 (左 = 框架事件, 右 = SSE 协议):

| 事件 | SSE | 为什么 |
|------|-----|--------|
| type | event 字段 | 前端的 addEventListener 按事件名挑处理函数 |
| seq | id 字段 | 断线重连时前端拿它说「我听到第几号了」(after_event_id) |
| 载荷 | data 字段 | JSON 一行; 同时带 type / seq / run_id, 只解析 data 也不缺信息 |
| (无) | run_id | 事件里**刻意没有** run_id (见 StreamEvent 注释), 由本层注入 |

一次响应的结束不是某个特殊事件, 而是**流结束** (生成器返回, 连接正常收线):
终局事件 (final / error) 是语义上的结束, 收尾哨兵是生产上的结束, 二者先后到达
(见 runs.RunStream.finish). 客户端不必靠超时猜「答完了没有」.

**本层不含断线重连** (`Last-Event-ID` 续推): `id` 字段按契约先发出去 (客户端可以
拿它做自己的去重与排序), 但服务端没有「从第 N 号接着推」的接口 —— 那次运行的事件
只在内存队列里过一次. 要补是另一件事 (PRD 没要求), 别看见 id 就以为续上了.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

from CharAgent.server.runs import RunStream
from CharAgent.stream import StreamEvent


def sse_frame(event: StreamEvent, *, run_id: str) -> str:
    """一个事件 → 一段 SSE 帧文本.

    Args:
        event: 框架事件 (type / seq 逐 run 从 1 起编号).
        run_id: 本次运行的编号, 由本层注入 data —— 事件对象里没有它.

    Returns:
        str: 可直接塞进响应体的帧文本 (`id` / `event` / `data` + 空行).
    """
    data = event.to_dict()
    # 注入而非合并: 这是本层的事实, 载荷里同名键覆盖不了它 (框架值优先)
    data["run_id"] = run_id
    return (
        f"id: {event.seq}\n"
        f"event: {event.type.value}\n"
        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    )


def encode_frame(frame: str) -> bytes:
    """帧文本 → UTF-8 字节 (出门前的最后一次编码).

    为什么自己编而不是交给响应层: 响应层编码用的是 `str.encode("utf-8")` 的默认
    策略 (遇到编不出来的字符就抛), 而事件载荷里的正文来自模型 —— 上游偶尔会吐出
    孤立代理项 (`\\ud800` 这类非法码点), 那时整条流会**在编码这一步死掉**: 用户
    看到的是连接断在半路, 而且这次运行会被连累着取消 (收尾把它当断连处理).
    一个字符赔上整次运行不划算, 所以在边界上退化成转义 (`\\ud800`) —— 它仍是
    合法的 JSON 转义, 客户端解析出来还是那个字符, 其余内容一字不差.

    (框架在别处也吃过代理项的亏, 见 tests/test_client_app.py 记的那次输入编码事故;
    那次的处置是修根因 —— 输入编码, 这一次的根因在上游, 修不了, 只能不让它炸.)
    """
    return frame.encode("utf-8", errors="backslashreplace")


async def sse_stream(
    stream: RunStream, task: asyncio.Task[None]
) -> AsyncIterator[bytes]:
    """把一次运行的事件抽成 SSE 帧 (这就是响应体; 逐帧 UTF-8 字节).

    循环一直转到收尾哨兵 (None) 为止 —— 不提前在终局事件上收线: 那时运行可能还
    在写会话状态 (把完成的历史收进会话, 或者把进度从快照收回来), 提前收线会连带
    把它取消掉.

    收尾 (finally) 只有一件事: **没人听了就叫停这次运行**. 走到这里有两种情形:
    客户端断开 (响应被关闭), 或者流正常结束 (任务早在哨兵之前就结束了, 什么也
    不做). 取消是协作式的 —— 打断的是 await, 不是已经在跑的工具线程; 被打断的
    运行会把已完成的工作收进会话历史 (ChatSession 的 _reclaim_progress), 所以
    用户接着说一句「继续」就接得上.

    注: 硬断连时 Starlette 可能**不关**这个生成器 (它直接抛 ClientDisconnect),
    那样 finally 要等生成器被回收才跑. 不影响正确性 —— 登记表的出册挂在任务自己
    的收尾回调上, 运行结束就出册; 这里只是把「没人听」这件事尽早告诉运行.
    """
    try:
        while (event := await stream.queue.get()) is not None:
            yield encode_frame(sse_frame(event, run_id=stream.run_id))
    finally:
        if not task.done():
            task.cancel()
            # 等它真的停下来: 取消要走到任务的收尾 (会话收进度 / 登记表出册)
            # 才算完 —— 不等就返回, 会在测试与真机上留下一串「任务还在销毁中」
            # 的收尾噪音. CancelledError 是这条路的预期; 万一这次运行是在别的
            # 异常上停下的, 也不该在此再抛一次 (那个异常已经由收尾回调补成终局
            # 事件送出去了, 这里是**收线**, 不是报错的地方).
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
