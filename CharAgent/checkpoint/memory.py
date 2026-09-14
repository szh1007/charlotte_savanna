"""内存版快照存储: 用进程里的字典存快照 (difficulties #5, ADR-0002 的第一种语义).

一句话理解: 把快照记在**草稿纸**上 —— 写起来飞快, 翻页也方便, 但进程一重启
(相当于把草稿纸扔了) 全没了. 它存在的意义有两个:

1. 测试用: 单元测试不想为了「存一帧」先去装 Redis / Postgres, 一个字典最省事
2. 当标尺用: 它把「快照存储该有的能力」全实现了 (能存、能取最新、能翻历史、能
   按编号取), 另外两个实现各自少一截 (Redis 没历史, Postgres 要连库) —— 对着
   它就能看清「省掉的那部分换来了什么」

排序规则 (与 Postgres 的 ORDER BY 保持一致, 因为 ADR-0002 要对比两者):
- 「最新一帧」= 时刻**最大**的那帧, 不是「最后存进来的那帧」
- 时刻相同时按编号排 (顺序完全确定; 同一毫秒存两帧也不会时好时坏)
- 于是同一批快照喂内存版与 Postgres, 翻出来的顺序逐条一致

其它语义细节 (同样为了「换存储不换行为」):
- **只追加不覆盖**: 每次 save 都留一帧 (历史完整)
- **同编号重复保存是空操作**: 与 Postgres 的 ON CONFLICT DO NOTHING 对齐, 也让
  「存到一半失败后重试」不会存出两份
- **进出都深拷贝**: 存进来的对象改不到存储里的内容, 取出去的对象也改不到 ——
  两边都不许 (项目里吃过活引用的亏: 测试断言两个对象相等, 结果它们是同一个 list,
  恒真; issue 06 的 `_FakeModel` 就是这个问题)
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from CharAgent.checkpoint.utils.types import (
    Checkpoint,
    CheckpointCapabilities,
)


def _order_key(checkpoint: Checkpoint) -> tuple[datetime, str]:
    """排序键: 先按存下的时刻, 再按编号.

    直接用 datetime 比较 (而不是它的文本): 文本比较只对「时区偏移一样」的时间
    成立, 换成 +08:00 与 +00:00 混排就会排错. 框架造的快照一律带时区 (见
    types.py 的 Checkpoint.create), 所以这里能拿到正确的时间先后.
    """
    return (checkpoint.created_at, checkpoint.checkpoint_id)


class InMemoryCheckpointSaver:
    """把快照存在内存字典里 (测试与单机演示用; 进程重启即丢).

    内部只留一本账: `_frames[会话][编号] = 快照`; 「最新」与「历史顺序」都在读取
    时按 (时刻, 编号) 现算 —— 少维护一份排好序的列表, 少一处能写歪的地方.
    """

    def __init__(self) -> None:
        self._frames: dict[str, dict[str, Checkpoint]] = {}

    @property
    def capabilities(self) -> CheckpointCapabilities:
        """内存版: 有历史 (全留在字典里), 不会过期 (除非进程结束)."""
        return CheckpointCapabilities(history=True, ttl=False)

    async def save(self, checkpoint: Checkpoint) -> None:
        """存一帧 (存下的是副本; 同编号重复保存 = 空操作)."""
        frames = self._frames.setdefault(checkpoint.thread_id, {})
        if checkpoint.checkpoint_id in frames:
            return
        frames[checkpoint.checkpoint_id] = deepcopy(checkpoint)

    async def load_latest(self, thread_id: str) -> Checkpoint | None:
        """取时刻最大的那一帧 (没有则 None)."""
        frames = self._frames.get(thread_id)
        if not frames:
            return None
        newest = max(frames.values(), key=_order_key)
        return deepcopy(newest)

    async def load(
        self, checkpoint_id: str, *, thread_id: str | None = None
    ) -> Checkpoint | None:
        """按编号取一帧 (没有则 None).

        thread_id 是给「按会话分区存放的实现」(Redis 的流式历史) 用的提示
        参数: 内存版有全局编号索引, 按编号直查即可, 所以**传了也忽略**.
        """
        for frames in self._frames.values():
            found = frames.get(checkpoint_id)
            if found is not None:
                return deepcopy(found)
        return None

    async def list_history(
        self, thread_id: str, *, limit: int | None = None
    ) -> list[Checkpoint]:
        """按时刻从早到晚列出某个会话的快照 (limit 取最近 N 帧, 顺序不变)."""
        frames = self._frames.get(thread_id, {})
        if not frames:
            return []
        if limit is not None and limit <= 0:
            return []
        ordered = sorted(frames.values(), key=_order_key)
        if limit is not None:
            ordered = ordered[-limit:]
        return [deepcopy(frame) for frame in ordered]

    async def aclose(self) -> None:
        """没有连接要关 (保留方法只为与另两个实现形状一致)."""
