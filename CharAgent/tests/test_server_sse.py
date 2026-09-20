"""事件 → SSE 帧: 字段映射要钉死, 推流要真的是边跑边推.

两条容易被想当然的事, 各有一条用例:

1. **字段映射是跨进程契约** (前端 / curl 按它解析): `type` → SSE 的 `event`,
   `seq` → `id`, 载荷 → `data`, 外加本层注入的 `run_id`. 帧长什么样是接口的
   一部分, 所以这里逐字断言 (而不是只看字段在不在).
2. **推流不是攒完再吐**: 客户端要的是「边跑边看」—— 事件产出一个就该出去一个.
   这一条在 ASGI 层测不了 (测试用的 ASGI 客户端会把响应体缓冲下来), 所以直接
   驱动生成器: 闸门没开时第一帧就该到手.

外加收尾那一条: 客户端走了 (生成器被关掉), 这次运行要被叫停 —— 没人听了还
继续跑是白烧钱, 而且**不能留下没人收的任务**.
"""

from __future__ import annotations

import asyncio
import json

from CharAgent.server.runs import RunStream
from CharAgent.server.sse import encode_frame, sse_frame, sse_stream
from CharAgent.stream import EventType, StreamEvent


def parse_frame(frame: bytes) -> dict:
    """SSE 帧字节 → {"id": int, "event": str, "data": dict} (只解析本层产出的形状)."""
    text = frame.decode("utf-8")
    lines = dict(line.split(": ", 1) for line in text.strip().splitlines())
    return {
        "id": int(lines["id"]),
        "event": lines["event"],
        "data": json.loads(lines["data"]),
    }


# ---------------------------------------------------------------------------
# 帧格式 (跨进程契约)
# ---------------------------------------------------------------------------


def test_a_frame_carries_the_four_fields_the_client_needs() -> None:
    """一帧就是三行加一个空行: id / event / data (+ 注入的 run_id).

    seq 进 `id` 是按 SSE 契约给客户端一个稳定的序号 (排序 / 去重都靠它), type 进
    `event` 是为了前端的 addEventListener 挑处理函数, run_id 进 data 是为了让调用
    方能指名道姓地指这次运行 (取消与排查).
    """
    event = StreamEvent(type=EventType.FINAL, seq=2, data={"content": "答完了"})

    assert sse_frame(event, run_id="run-1") == (
        "id: 2\n"
        "event: final\n"
        'data: {"type": "final", "seq": 2, "content": "答完了", "run_id": "run-1"}\n'
        "\n"
    )


def test_the_payload_stays_on_one_line() -> None:
    """载荷必须单行 —— SSE 的数据字段按行读, 换行会把一帧劈成两帧.

    JSON 会把换行转义成 \\n, 所以多行正文 (前端真要渲染的那种) 也是安全的;
    这条用例把「安全」钉成断言, 免得哪天顺手改成 indent=2 就悄悄劈帧.
    """
    event = StreamEvent(
        type=EventType.FINAL, seq=1, data={"content": "第一行\n第二行\r\n第三行"}
    )

    frame = sse_frame(event, run_id="run-1")

    assert frame.count("\n") == 4, "三行 + 结尾的空行; 数据本身不许带换行"
    assert parse_frame(encode_frame(frame))["data"]["content"] == (
        "第一行\n第二行\r\n第三行"
    )


def test_an_unencodable_character_does_not_kill_the_stream() -> None:
    """上游吐出的孤立代理项不该让整条流死掉 (边界上退化成转义).

    代理项 (`\\ud800` 这类) 是**非法码点**: 直接 `.encode("utf-8")` 会抛, 而响应
    层用的正是默认策略 —— 一旦抛出去, 用户看到的是连接断在半路, 这次运行还会被
    连累着取消. 退化成 JSON 转义之后, 客户端解析出来仍是同一个字符, 其余内容
    一字不差.
    """
    event = StreamEvent(
        type=EventType.FINAL, seq=1, data={"content": "半个字符: \ud800 之后照常"}
    )

    frame = encode_frame(sse_frame(event, run_id="run-1"))

    assert b"\\ud800" in frame, "编不出来的那个字符要变成转义, 不是让整帧炸掉"
    parsed = parse_frame(frame)["data"]["content"]
    assert parsed == "半个字符: \ud800 之后照常", "其余内容一字不差"


def test_the_framework_run_id_wins_over_a_payload_lookalike() -> None:
    """载荷里就算有个同名键, 也覆盖不了本层注入的 run_id (本层的事实优先)."""
    event = StreamEvent(
        type=EventType.FINAL, seq=1, data={"content": "答完了", "run_id": "假的"}
    )

    frame = encode_frame(sse_frame(event, run_id="run-1"))

    assert parse_frame(frame)["data"]["run_id"] == "run-1"


# ---------------------------------------------------------------------------
# 推流与收尾
# ---------------------------------------------------------------------------


class Producer:
    """替身生产者: 推一个事件, 等闸门, 再推终局事件并收尾.

    闸门是刻意的: 它让「运行还没结束」这件事在测试里可控, 于是「第一帧在运行
    结束前就出去了」才可断言 (否则只能证明「最后拿到了全部事件」).
    """

    def __init__(self, stream: RunStream, gate: asyncio.Event) -> None:
        self._stream = stream
        self._gate = gate

    async def run(self) -> None:
        self._stream.push(
            StreamEvent(type=EventType.THINKING, seq=1, data={"message": "先查一下"})
        )
        await self._gate.wait()
        self._stream.push(
            StreamEvent(type=EventType.FINAL, seq=2, data={"content": "答完了"})
        )
        self._stream.finish()


async def test_frames_come_out_while_the_run_is_still_going() -> None:
    """边跑边推: 运行没结束, 已经到手的帧就该给客户端.

    这是整个服务层存在的理由 —— 用户要看到「正在查」而不是等十几秒后一次性
    看到结果. 如果哪天有人把生成器改成「先收集再返回」, 这条会红.
    """
    gate = asyncio.Event()
    stream = RunStream("run-1")
    task = asyncio.create_task(Producer(stream, gate).run())
    frames = sse_stream(stream, task)

    first = await frames.__anext__()

    assert parse_frame(first)["data"]["message"] == "先查一下"
    assert not task.done(), "第一帧出去的时候, 运行还在跑 (这才叫流式)"

    gate.set()
    rest = [frame async for frame in frames]

    assert [parse_frame(frame)["event"] for frame in rest] == ["final"]
    assert task.done()
    assert stream.queue.empty(), "生产与消费都收干净了"


async def test_closing_the_stream_stops_the_run() -> None:
    """客户端走了就没人听了: 生成器被关掉时要把这次运行叫停, 并等它停下来.

    等它停下来这一半同样重要: 取消是协作式的, 任务的收尾 (会话收进度 / 登记表
    出册) 要在这一轮里跑完 —— 不等就返回, 会在测试与真机上留下一串「任务还在
    销毁中」的收尾噪音.
    """
    gate = asyncio.Event()  # 一直不开: 模拟一次跑不完的运行
    stream = RunStream("run-1")
    task = asyncio.create_task(Producer(stream, gate).run())
    frames = sse_stream(stream, task)
    await frames.__anext__()

    await frames.aclose()

    assert task.cancelled(), "没人听的运行要被叫停"
    assert task.done(), "而且要在生成器收尾之前真的停下来"
