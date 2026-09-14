"""Redis 版快照存储: 两档模式 —— 流式全历史 / 只留最新一帧 (ADR-0002 第二种语义).

一句话理解: Redis 有两个住法, 存的东西一样, 记性不一样.

**history 模式 (默认)**: 把某个会话的快照写成一条 **Stream (流水账)** —— 一个
会话一个键, 每存一帧就往账本上添一行 (`XADD`). 于是:

- 取最新一帧: 从账本尾部读一行 (`XREVRANGE ... COUNT 1`)
- 翻历史: 整本账从头读一遍 (`XRANGE`), 或倒着读最近 N 行
- 按编号回溯: 翻账本找那一行 (要一并给会话号, 因为帧按会话分区 —— 见 load)
- 控容量: `MAXLEN` 只留最近 N 帧; 控过期: `EXPIRE` 到点整本消失
- 天然有序 (按追加顺序), 且不需要任何 Redis 模块 —— 普通 Redis 就能用

**latest 模式**: 一个会话一个键, 只留最新一帧 (`SET`, 存新盖旧) —— 白板语义,
快而省, 适合「只要断点续跑、不要翻历史」的会话缓存场景. 它对应
langgraph-checkpoint-redis 里的 ShallowRedisSaver: 那种「只留最新」的做法在真实
项目里也是一档正经选项, 所以这里作为**显式模式**保留 —— 做不到的事明确报错,
不返回空结果糊弄调用方.

两档都提供的保证: 每存一帧重新计 TTL (`ttl_seconds`, 不配则不过期); 键名前缀
可配 (`key_prefix`); 存的都是**协议文本**, 换个进程换个语言照样读得懂.

存储形态 (02-data-model.md §3):
- history: 键 `{前缀}:ckpt:{thread_id}` → Stream, 每行一个字段 `data=<整条 JSON>
- latest:  键 `{前缀}:ckpt:latest:{thread_id}` → String

两档键名不同是必须的: Redis 的键类型不能中途改 (一个键要么是 Stream 要么是
String), 同一个键名没法两档混用.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from redis.exceptions import RedisError

from CharAgent.checkpoint.serialization import DEFAULT_CODEC, CheckpointCodec
from CharAgent.checkpoint.utils.errors import (
    CheckpointCapabilityError,
    CheckpointConfigError,
    CheckpointStorageError,
)
from CharAgent.checkpoint.utils.types import (
    Checkpoint,
    CheckpointCapabilities,
)

DEFAULT_KEY_PREFIX = "charagent"
DEFAULT_URL = "redis://127.0.0.1:6379/0"

MODE_HISTORY = "history"
MODE_LATEST = "latest"
MODE_NAMES = (MODE_HISTORY, MODE_LATEST)

# 流条目的字段名 (只有这一个字段, 值就是整条记录的 JSON)
_FRAME_FIELD = "data"

# latest 模式做不到的两件事: 统一说辞 (两处报错共用, 改文案只改一处)
_LATEST_MODE_REASON = (
    "当前是 latest 模式: 一个会话只保留最新一帧, 存新的就把旧的盖掉了; "
    "要翻历史 / 按编号回溯请用 mode='history' (Streams 流式全历史)"
)


def _order_key(checkpoint: Checkpoint) -> tuple[datetime, str]:
    """排序键: 先按存下的时刻, 再按编号 (与另两个实现对齐, 见 memory.py 说明)."""
    return (checkpoint.created_at, checkpoint.checkpoint_id)


class RedisCheckpointSaver:
    """把快照写进 Redis: history 模式写 Stream, latest 模式写单个键.

    连接从哪来: 不传 `client` 就按 `url` 建一个 (用 redis-py 的 asyncio 客户端);
    测试要注入替身时传 `client` —— 这时连接归调用方管, aclose 不会去关它.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        client: Any | None = None,
        mode: str = MODE_HISTORY,
        ttl_seconds: int | None = None,
        max_frames: int | None = None,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        codec: CheckpointCodec | None = None,
    ) -> None:
        """
        Args:
            url: Redis 连接串; client 为 None 时用它建客户端 (默认本机 6379).
            client: 注入的客户端 (测试替身 / 复用已有连接); 给了它就不看 url.
            mode: "history" (默认, Stream 留全历史) / "latest" (只留最新一帧).
            ttl_seconds: 快照的存活秒数 (正整数); None 表示不过期. 每存一帧都
                重新计时 —— 「最近有人来过就不过期」, 老会话自然淘汰.
            max_frames: history 模式下最多留多少帧 (正整数), None 表示不裁剪.
                裁掉的是最老的帧; 用的是**精确**裁剪, 「最多留 N 帧」是硬承诺.
            key_prefix: 键名前缀, 默认 "charagent".
            codec: 序列化编解码器, 默认出厂那一个 (DEFAULT_CODEC).

        Raises:
            CheckpointConfigError: 模式名不认识 / TTL 不是正整数 / max_frames
                不是正整数 / 在 latest 模式下给了 max_frames (没有意义) / 前缀为空.
        """
        name = (mode or "").strip().lower()
        if name not in MODE_NAMES:
            raise CheckpointConfigError(
                f"未知的 Redis 模式 {mode!r}, 可选: {', '.join(MODE_NAMES)}"
            )
        if ttl_seconds is not None and (
            isinstance(ttl_seconds, bool) or ttl_seconds <= 0
        ):
            raise CheckpointConfigError(
                f"ttl_seconds 必须是正整数 (None 表示不过期), 实际: {ttl_seconds!r}"
            )
        if max_frames is not None and (isinstance(max_frames, bool) or max_frames <= 0):
            raise CheckpointConfigError(
                f"max_frames 必须是正整数 (None 表示不裁剪), 实际: {max_frames!r}"
            )
        if max_frames is not None and name == MODE_LATEST:
            raise CheckpointConfigError(
                "max_frames 只在 history 模式下有意义 (latest 模式本来就只留一帧)"
            )
        if not key_prefix:
            raise CheckpointConfigError("key_prefix 不能为空")
        self._mode = name
        self._ttl_seconds = ttl_seconds
        self._max_frames = max_frames
        self._key_prefix = key_prefix
        self._codec = codec if codec is not None else DEFAULT_CODEC
        # 谁建的谁关: 外部注入的 client 由调用方负责关闭
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            # 延迟到用的时候才建 (构造不碰网络, 与另两个实现一致)
            from redis.asyncio import Redis

            self._client = Redis.from_url(url or DEFAULT_URL, decode_responses=True)

    @property
    def capabilities(self) -> CheckpointCapabilities:
        """能力声明: history 模式有历史; 配了 TTL 才会过期."""
        return CheckpointCapabilities(
            history=self._mode == MODE_HISTORY,
            ttl=self._ttl_seconds is not None,
        )

    @property
    def mode(self) -> str:
        """当前模式 ("history" / "latest") —— 排查时先看这个."""
        return self._mode

    def key_for(self, thread_id: str) -> str:
        """算出某个会话的键名 (排查时用: 拿这个键去 Redis 里看原文).

        history 模式指向一条 Stream (`XRANGE <键> - +`), latest 模式指向一个字符串
        键 (`GET <键>`).
        """
        if self._mode == MODE_HISTORY:
            return f"{self._key_prefix}:ckpt:{thread_id}"
        return f"{self._key_prefix}:ckpt:latest:{thread_id}"

    # ------------------------------------------------------------------
    # 存
    # ------------------------------------------------------------------

    async def save(self, checkpoint: Checkpoint) -> None:
        """存下这一帧 (history: 账本添一行; latest: 盖掉上一帧); 都会重计 TTL."""
        text = self._codec.dumps(self._codec.encode_record(checkpoint))
        key = self.key_for(checkpoint.thread_id)
        try:
            if self._mode == MODE_HISTORY:
                await self._append_frame(key, text)
            elif self._ttl_seconds is None:
                await self._client.set(key, text)
            else:
                await self._client.set(key, text, ex=self._ttl_seconds)
        except RedisError as exc:
            raise CheckpointStorageError(f"写 Redis 失败 (key={key}): {exc}") from exc

    async def _append_frame(self, key: str, text: str) -> None:
        """history 模式的一帧: XADD 追加 (+ 需要时裁掉最老的), 再重计过期时间.

        裁剪挂在 XADD 上 (而不是事后 XTRIM) 是刻意的: 一条命令做完两件事, 中间不会
        出现「刚超了一点容量」的窗口. `approximate=False` 表示**精确**裁剪 ——
        近似裁剪 (`MAXLEN ~`, Redis 的默认) 更省 CPU 但保留条数不精确.
        """
        if self._max_frames is None:
            await self._client.xadd(key, {_FRAME_FIELD: text})
        else:
            await self._client.xadd(
                key,
                {_FRAME_FIELD: text},
                maxlen=self._max_frames,
                approximate=False,
            )
        if self._ttl_seconds is not None:
            # 流键的过期时间不会因为 XADD 自动往后推, 得每次显式续上
            await self._client.expire(key, self._ttl_seconds)

    # ------------------------------------------------------------------
    # 取
    # ------------------------------------------------------------------

    async def load_latest(self, thread_id: str) -> Checkpoint | None:
        """取该会话最新一帧 (键不存在 = 没存过或已过期, 都返回 None)."""
        key = self.key_for(thread_id)
        try:
            if self._mode == MODE_HISTORY:
                entries = await self._client.xrevrange(key, "+", "-", count=1)
                return self._decode_entry(entries[0]) if entries else None
            raw = await self._client.get(key)
        except RedisError as exc:
            raise CheckpointStorageError(f"读 Redis 失败 (key={key}): {exc}") from exc
        if raw is None:
            return None
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return self._codec.decode_record(self._codec.loads(text))

    async def load(
        self, checkpoint_id: str, *, thread_id: str | None = None
    ) -> Checkpoint | None:
        """按编号取一帧 (time-travel 的入口; 没有则 None).

        **history 模式下必须一并给 thread_id**: Redis 的帧按会话分区存放 (一个会话
        一条 Stream), 只凭编号没法知道该翻哪本账 —— 要么把所有会话的账本都翻一遍
        (慢且蠢), 要么请调用方说一声. 另两个实现有全局编号索引, 这个参数传了也会
        被忽略 (协议里有它, 只是为了给「按会话分区的实现」一个说清楚的机会).

        翻账本时先做便宜的 JSON 解析只看编号, 命中了才完整解码 —— 不必把每一帧都
        还原成对象.
        """
        if self._mode != MODE_HISTORY:
            raise CheckpointCapabilityError(_LATEST_MODE_REASON)
        if thread_id is None:
            raise CheckpointConfigError(
                "Redis 的帧按会话分区存放, 按编号取帧请一并给 thread_id "
                "(从 list_history 拿到的帧都带着它)"
            )
        for text in await self._read_frames(thread_id, None, "翻账本"):
            payload = self._codec.loads(text)
            if payload.get("checkpoint_id") == checkpoint_id:
                return self._codec.decode_record(payload)
        return None

    async def list_history(
        self, thread_id: str, *, limit: int | None = None
    ) -> list[Checkpoint]:
        """按时间从早到晚列出该会话的快照 (limit 取最近 N 帧, 顺序不变).

        账本本身是有序的, 但仍按 (时刻, 编号) 重排一次 —— 三个实现的返回顺序必须
        一致, 否则「同一段经历喂不同存储, 结果一样」的对比就假了.
        """
        if self._mode != MODE_HISTORY:
            raise CheckpointCapabilityError(_LATEST_MODE_REASON)
        if limit is not None and limit <= 0:
            return []
        texts = await self._read_frames(thread_id, limit, "查快照历史")
        checkpoints = [
            self._codec.decode_record(self._codec.loads(text)) for text in texts
        ]
        return sorted(checkpoints, key=_order_key)

    # ------------------------------------------------------------------
    # 内部: 流条目的读写
    # ------------------------------------------------------------------

    async def _read_frames(
        self, thread_id: str, limit: int | None, action: str
    ) -> list[str]:
        """读流里的帧文本 (limit 取最近 N 帧; 返回顺序仍是账本顺序: 从早到晚)."""
        key = self.key_for(thread_id)
        try:
            if limit is None:
                entries = await self._client.xrange(key, "-", "+")
            else:
                newest_first = await self._client.xrevrange(key, "+", "-", count=limit)
                entries = list(reversed(newest_first))
        except RedisError as exc:
            raise CheckpointStorageError(f"{action}失败 (key={key}): {exc}") from exc
        return [entry[1][_FRAME_FIELD] for entry in entries]

    def _decode_entry(self, entry: Any) -> Checkpoint:
        """一条流条目 (条目编号, {字段: 文本}) -> Checkpoint."""
        text = entry[1][_FRAME_FIELD]
        return self._codec.decode_record(self._codec.loads(text))

    async def aclose(self) -> None:
        """关掉自己建的客户端 (外部注入的那个不动)."""
        if self._owns_client:
            await self._client.aclose()
