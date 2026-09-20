"""一次运行的流与在册: 事件队列 + 序号记账 + 可取消的任务句柄.

一句话理解: 一次运行的产物有两个 —— 一串事件 (给客户端) 与一个任务句柄 (给
取消用). 前者收在 RunStream 里, 后者登记在 RunRegistry 里. 事件的产出不需要
本模块做任何事 (会话的字句经事件出口流进来); 本模块管的是**结束**: 一次运行
不论怎么结束, 事件流都要有一个明确的结尾.

为什么终局事件由本层补 (而不是 loop): loop 只在**正常结束时**发终局事件;
取消与「模型 / 存储直接失败」这两种结束, loop 刻意不发 —— 它的注释写明了
「error(cancelled) 由 server 层负责」. 本层补的时候守两条:

1. **恰好一个**: 流自己记账 (是否已经终局), 补之前先看有没有 —— 不许出现
   第二个终局事件 (事件流的第四条不变量).
2. **接着编号**: 补的那个接在最后一个事件之后 (seq = 上一个 + 1). 客户端的
   连续性靠 seq, 中间断一号就等于「有事件丢了」.
   注: seq 是 loop 那台总线逐 run 编的号, 本层补的这一个属于同一条流, 所以
   接着数 —— 但**别指望 seq 跨 run 单调** (每次运行都从 1 起).

收尾为什么挂在**任务的收尾回调**上 (close_stream), 而不是跑任务那段协程的
finally 里: 任务**可能一步都没跑就被取消** (请求刚建好任务, 客户端就走了) ——
那种情况下协程体一行都不执行, finally 也不会跑, 事件流就永远等不到结尾. 收尾
回调是「只要任务结束就一定会响」的那个位置 (正常结束 / 失败 / 取消, 连没跑起来
就取消也算).

本模块是框架里唯一打日志的地方 (别处都上抛给调用方): server 层是进程里的最后
一站, 一次运行的失败只有两个去处 —— 客户端的事件流 (对方可能已经走了) 与进程
日志. 所以这里对「没人再保管的失败」记一笔 traceback, 其余一律照旧上抛.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import uuid4

from CharAgent.server.utils.types import (
    CANCELLED_CODE,
    RUN_FAILED_CODE,
)
from CharAgent.stream import TERMINAL_TYPES, EventType, StreamEvent

logger = logging.getLogger("charagent.server")

# 取消时补的终局事件文案: 陈述事实, 不是给人看的话术 (降级文案归业务).
CANCELLED_TEXT = "本次运行在完成前被取消"


def new_run_id() -> str:
    """发一个新的运行编号 (uuid4 hex).

    为什么要发号: 取消与排查都要指名道姓地指一次运行, 而这是它唯一的身份.
    与 loop 写进快照的 run_id **不是一个东西** —— 那是框架存储层的运行编号
    (loop 自己生成, 落库用), 本层这个只活在进程里, 管这次 HTTP 运行.
    """
    return uuid4().hex


class RunStream:
    """一次运行的事件流: 队列 + 序号记账 + 补发终局事件的唯一出口.

    队列不设上限: 事件的产出节奏受模型与工具调用约束 (每个事件背后是一次几百
    毫秒到几秒的等待), 而消费端就在同一个进程里逐条转发 —— 真要限流是服务治理
    的事, 不是本层顺手做的.

    attributes:
        run_id: 本次运行的编号.
        queue: 事件队列 (SSE 生成器消费; 末尾那个 None 是收尾哨兵, 见 finish).
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        # 哨兵用 None: 它不可能是事件 (事件恒为 StreamEvent), 判起来最直白
        self.queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        self._seq = 0
        self._closed = False

    @property
    def last_seq(self) -> int:
        """已进流的最后一个事件序号 (补发终局事件时接着它数)."""
        return self._seq

    @property
    def closed(self) -> bool:
        """这条流是否已经终局 (补之前先看它)."""
        return self._closed

    def push(self, event: StreamEvent) -> None:
        """收一个事件: 记账 + 入队 (EventSink 出口, 同步且不阻塞).

        终局事件自己也算「已终局」—— 之后本层不会再补第二个.
        """
        self._seq = event.seq
        if event.type in TERMINAL_TYPES:
            self._closed = True
        self.queue.put_nowait(event)

    def emit_terminal(self, code: str, message: str) -> bool:
        """补一个终局 error 事件 (loop 没产时才补).

        这个事件**不经过 EventBus**: 那台总线是 loop 那次 run 的内部零件, 运行
        已经结束了它也就没了 (本层手上只有这条流). 于是它不做总线那条「工具还没
        回填完不许终局」的校验 —— 而被取消的运行本来就会有没闭合的工具调用, 前端
        按「这一轮没完」处理即可.

        Args:
            code: 机器读的错误码 (CANCELLED_CODE / RUN_FAILED_CODE 等).
            message: 事实性说明 (异常类名 + 说明, 或一句中性描述);
                面向用户的话术由业务按 code 决定.

        Returns:
            bool: 真的补了 (True) / 已经终局, 什么都没做 (False).
        """
        if self._closed:
            return False
        self._closed = True
        self._seq += 1
        self.queue.put_nowait(
            StreamEvent(
                type=EventType.ERROR,
                seq=self._seq,
                data={"error": {"code": code, "message": message}},
            )
        )
        return True

    def finish(self) -> None:
        """投收尾哨兵: 「我这边说完了」.

        与终局事件分工: 终局事件是**语义**上的结束 (对客户端), 哨兵是**生产**
        上的结束 (对生成器). 有了它, 生成器不必靠「看见 final 就收线」提前
        收尾 —— 那时运行可能还在写会话状态 (收尾被打断就白干了).
        """
        self.queue.put_nowait(None)


@dataclass(slots=True)
class RunHandle:
    """登记在册的一次运行: 编号 + 算哪段会话 + 可取消的任务句柄.

    attributes:
        run_id: 本次运行的编号 (响应头与每个事件的载荷里都有它).
        thread_id: 这段对话的会话编号 (出事时一眼看出是哪段会话).
        task: 跑这次运行的 asyncio 任务 (取消就是 `task.cancel()`) ——
            框架在创建它的那一刻登记, 于是任何拿到 run_id 的地方都够得着它.
    """

    run_id: str
    thread_id: str
    task: asyncio.Task[None]


class RunRegistry:
    """在跑的运行登记表 (run_id → 句柄).

    为什么要登记: 取消需要那次运行的任务句柄, 而句柄只有创建它的那一处有 ——
    不登记就再也够不着 (客户端拿着 run_id 来按「停止」时, 端点手上只有一个
    字符串). 本片只立「在册」这件事与它的收尾; 取消的 HTTP 语义 (已结束的
    run / 不存在的 run 各回什么) 归 07.

    出册的唯一入口是任务自己的收尾回调 (finish), 所以「在册」与「在跑」永远
    同真同假 —— 不会出现一个已经没有任务在跑、却还挂在表里的幽灵条目.
    """

    def __init__(self) -> None:
        self._runs: dict[str, RunHandle] = {}

    def register(self, handle: RunHandle) -> None:
        """把一次运行登记在册 (创建任务的那一刻)."""
        self._runs[handle.run_id] = handle

    def finish(self, run_id: str) -> None:
        """出册 (运行任务收尾时调用); 重复调用安全 (幂等)."""
        self._runs.pop(run_id, None)

    def get(self, run_id: str) -> RunHandle | None:
        """查一次运行 (在跑则给句柄, 否则 None) —— 排查、测试与 07 的取消端点用."""
        return self._runs.get(run_id)

    @property
    def run_ids(self) -> tuple[str, ...]:
        """在跑的运行编号 (登记顺序) —— 测试与排查用."""
        return tuple(self._runs)


def _describe(error: BaseException) -> str:
    """异常 → 一句事实性说明; 连异常的 `__str__` 自己坏了也要给得出一句话.

    为什么要防这一手: 这句说明会进客户端看到的终局事件, 而「异常对象自己抛异常」
    是会发生的 (自定义异常里读了没初始化的字段). 那时退化成只剩类名 —— 总比
    「连这次运行失败都报不出去」强.
    """
    try:
        return f"{type(error).__name__}: {error}"
    except Exception:  # 这里就是要兜住「什么都可能」: 连异常的字符串化都会坏
        return type(error).__name__


def close_stream(task: asyncio.Task[None], stream: RunStream) -> None:
    """把一次运行收尾: 必要时补终局事件, 然后投收尾哨兵.

    调用位置是任务的收尾回调 (见 app._finish_run), 所以它一定会跑 —— 包括
    「任务一步都没跑就被取消」那种边角 (那时协程的 finally 是空的).

    三种结束各得其所:

    | 任务怎么结束的 | 流里补什么 |
    |---------------|-----------|
    | 正常跑完 | 什么都不补 (loop 自己发过 final 了) |
    | 被取消 | error, code=cancelled |
    | 抛异常 | error, code=run_failed + 异常说明, 同时记一笔带 traceback 的日志 |

    失败**只补一个 code** (不管背后是模型挂了还是存储挂了): 客户端对这两种的处置
    是一样的 (这次运行没了), 而区分它们属于排查 —— 那一路的信息在 message (异常
    类型 + 说明) 与进程日志 (traceback) 里, 客户端按 code 决定话术就够了.

    注: 若会话没按约定发终局事件 (框架的 ChatSession 恒会发), 流仍然会干净收线
    (哨兵), 只是没有终局事件 —— 本层不替它编一个「原因」.

    Args:
        task: 这次运行的任务 (已经结束 —— 收尾回调里拿到的就是它).
        stream: 这次运行的事件流.
    """
    try:
        if task.cancelled():
            stream.emit_terminal(CANCELLED_CODE, CANCELLED_TEXT)
        else:
            # 正常结束返回 None, 失败则拿到那个异常 (取出来也顺手告诉 asyncio
            # 「这个异常有人领了」, 免得它事后打一句没人认领的警告)
            error = task.exception()
            if error is not None:
                logger.error(
                    "运行 %s 异常终止: %s",
                    stream.run_id,
                    type(error).__name__,
                    exc_info=error,
                )
                stream.emit_terminal(RUN_FAILED_CODE, _describe(error))
    finally:
        # 哨兵必须发出去: 上面那几行都可能在跟一个「坏掉的异常对象」打交道
        # (比如 str(exc) 自己抛), 但「这条流到此为止」不许被它连累 —— 否则
        # 客户端会一直等一个不来的结尾. 收尾回调里的异常照旧往上冒, asyncio
        # 会把它打出来 (不吞).
        stream.finish()
