"""checkpoint Redis 实现测试 (Redis 版语义, 两档模式).

默认用 tests/doubles.py 的 FakeRedisClient (实现本包用到的几条命令, 时钟可控,
于是 TTL 到期不必真等). 与真实 Redis 的对照由本文件末尾标记 `redis` 的用例补验
(需本机 Redis; 默认被 addopts 排除, 用 `pytest -m redis` 单跑).

两档模式的关注点不同:
- history (默认): 一条 Stream 记全部历史 —— 翻历史 / 按编号回溯 / 裁剪 / TTL
- latest: 一个键只留最新一帧 —— 断点续跑够用, 翻历史明确报「做不到」
"""

from __future__ import annotations

import os

import pytest
from doubles import FakeClock, FakeRedisClient
from helpers import make_checkpoint
from redis.exceptions import RedisError

from CharAgent.checkpoint.redis import (
    MODE_HISTORY,
    MODE_LATEST,
    RedisCheckpointSaver,
)
from CharAgent.checkpoint.utils.errors import (
    CheckpointCapabilityError,
    CheckpointConfigError,
    CheckpointStorageError,
)


def make_saver(mode: str = MODE_HISTORY, **overrides) -> RedisCheckpointSaver:
    """造一个注入假客户端的 saver (默认 history 模式, 前缀 test)."""
    fields = {"client": FakeRedisClient(), "key_prefix": "test", "mode": mode}
    fields.update(overrides)
    return RedisCheckpointSaver(**fields)


def make_frames(count: int = 3, *, thread_id: str = "thread-1"):
    """造一串有时刻先后的帧 (同一会话里串成一条链)."""
    frames = []
    parent = None
    for index in range(1, count + 1):
        frame = make_checkpoint(
            thread_id=thread_id,
            checkpoint_id=f"ck-{index}",
            turn_number=index,
            parent_id=parent,
            created_at=make_checkpoint().created_at.replace(minute=index),
        )
        frames.append(frame)
        parent = frame.checkpoint_id
    return frames


# ---------------------------------------------------------------------------
# 两档共用: 键名 / 存取的共同保证
# ---------------------------------------------------------------------------


def test_key_format_differs_by_mode():
    """两档键名不同: history 指向一条流, latest 指向一个字符串键.

    为什么必须不同: Redis 的键类型不能中途改, 同一个键名没法两档混用.
    """
    assert make_saver(MODE_HISTORY).key_for("t-1") == "test:ckpt:t-1"
    assert make_saver(MODE_LATEST).key_for("t-1") == "test:ckpt:latest:t-1"


def test_mode_is_readable_for_troubleshooting():
    """当前模式可读 (排查第一步: 先看它)."""
    assert make_saver(MODE_HISTORY).mode == MODE_HISTORY
    assert make_saver(MODE_LATEST).mode == MODE_LATEST


def test_unknown_mode_rejected():
    """模式名不认识: 构造期报错并列出可选值."""
    with pytest.raises(CheckpointConfigError) as excinfo:
        make_saver("streaming")

    assert "history" in str(excinfo.value)


def test_ttl_must_be_positive_integer():
    """TTL 非法 (0 / 负数 / True): 构造期报错."""
    with pytest.raises(CheckpointConfigError):
        make_saver(ttl_seconds=0)


def test_max_frames_must_be_positive_integer():
    """裁剪帧数非法: 构造期报错."""
    with pytest.raises(CheckpointConfigError):
        make_saver(max_frames=0)


def test_max_frames_rejected_in_latest_mode():
    """latest 模式下配 max_frames: 报错 (那个模式本来就只留一帧)."""
    with pytest.raises(CheckpointConfigError) as excinfo:
        make_saver(MODE_LATEST, max_frames=5)

    assert "latest" in str(excinfo.value)


def test_empty_key_prefix_rejected():
    """空前缀: 报错 (会造出 ":ckpt:t-1" 这种谁都不敢认的键)."""
    with pytest.raises(CheckpointConfigError):
        RedisCheckpointSaver(client=FakeRedisClient(), key_prefix="")


async def test_aclose_leaves_injected_client_open():
    """外部注入的客户端不归 saver 关 (谁建的谁关)."""
    client = FakeRedisClient()

    await make_saver(client=client).aclose()

    assert client.closed is False


# ---------------------------------------------------------------------------
# history 模式: 流式全历史
# ---------------------------------------------------------------------------


async def test_history_save_then_load_latest():
    """存一帧再取最新: 内容完全一致 (走整条记录的编码 / 解码)."""
    saver = make_saver()
    checkpoint = make_checkpoint()

    await saver.save(checkpoint)

    assert await saver.load_latest("thread-1") == checkpoint


async def test_history_lists_all_frames_in_order():
    """翻历史: 账本里的帧全在, 按时刻从早到晚."""
    saver = make_saver()
    frames = make_frames(3)
    for frame in frames:
        await saver.save(frame)

    history = await saver.list_history("thread-1")

    assert [frame.turn_number for frame in history] == [1, 2, 3]
    assert history == frames


async def test_history_latest_is_the_newest_frame():
    """最新一帧 = 最后存进去的那一帧."""
    saver = make_saver()
    for frame in make_frames(3):
        await saver.save(frame)

    latest = await saver.load_latest("thread-1")

    assert latest is not None
    assert latest.turn_number == 3


async def test_history_load_by_id_needs_thread_hint():
    """按编号取帧要一并给 thread_id (帧按会话分区存放, 只凭编号不知道翻哪本账)."""
    saver = make_saver()
    frames = make_frames(3)
    for frame in frames:
        await saver.save(frame)

    with pytest.raises(CheckpointConfigError) as excinfo:
        await saver.load(frames[0].checkpoint_id)

    assert "thread_id" in str(excinfo.value)


async def test_history_load_by_id_returns_matching_frame():
    """给了会话号: 按编号翻出那一帧 (time-travel 的入口)."""
    saver = make_saver()
    frames = make_frames(3)
    for frame in frames:
        await saver.save(frame)

    restored = await saver.load(frames[0].checkpoint_id, thread_id="thread-1")

    assert restored == frames[0]


async def test_history_load_by_id_returns_none_when_missing():
    """编号不存在: 返回 None (翻遍账本也没找到, 不是错误)."""
    saver = make_saver()
    await saver.save(make_checkpoint())

    assert await saver.load("没有这个编号", thread_id="thread-1") is None


async def test_history_limit_keeps_newest_in_oldest_first_order():
    """limit 取最近 N 帧, 返回顺序仍是从早到晚."""
    saver = make_saver()
    for frame in make_frames(3):
        await saver.save(frame)

    history = await saver.list_history("thread-1", limit=2)

    assert [frame.turn_number for frame in history] == [2, 3]


async def test_history_limit_not_positive_returns_empty():
    """limit 不是正数: 返回空列表 (不必往 Redis 跑一趟)."""
    saver = make_saver()
    await saver.save(make_checkpoint())

    assert await saver.list_history("thread-1", limit=0) == []


async def test_history_max_frames_trims_oldest():
    """配了 max_frames: 只留最近 N 帧 (精确裁剪, 裁掉最老的)."""
    saver = make_saver(max_frames=2)
    for frame in make_frames(4):
        await saver.save(frame)

    history = await saver.list_history("thread-1")

    assert [frame.turn_number for frame in history] == [3, 4]


async def test_history_ttl_is_set_and_refreshed():
    """配了 TTL: 每存一帧重新计时 (流键的过期时间不会自动往后推)."""
    clock = FakeClock()
    client = FakeRedisClient(clock=clock)
    saver = make_saver(client=client, ttl_seconds=60)
    await saver.save(make_checkpoint(checkpoint_id="ck-1"))
    clock.now += 40

    await saver.save(make_checkpoint(checkpoint_id="ck-2"))

    assert client.ttl("test:ckpt:thread-1") == 60


async def test_history_expired_key_reads_as_missing():
    """过了 TTL 再读: 当作没有 (整本账到期消失)."""
    clock = FakeClock()
    saver = make_saver(client=FakeRedisClient(clock=clock), ttl_seconds=60)
    await saver.save(make_checkpoint())
    clock.now += 61

    assert await saver.load_latest("thread-1") is None
    assert await saver.list_history("thread-1") == []


async def test_history_snapshot_is_portable_between_instances():
    """存进 Redis 的是**协议文本**: 换一个 saver 实例照样读得懂.

    这条同时证明序列化协议真的在起作用 —— 直接看 Redis 里的条目也能读出来.
    """
    client = FakeRedisClient()
    writer = make_saver(client=client)
    reader = make_saver(client=client)

    await writer.save(make_checkpoint())

    assert await reader.load_latest("thread-1") == make_checkpoint()
    entries = await client.xrange("test:ckpt:thread-1", "-", "+")
    assert '"schema_version"' in entries[0][1]["data"]


async def test_history_capabilities_declare_history():
    """能力声明: 有历史; 配了 TTL 才会过期."""
    assert make_saver().capabilities.history is True
    assert make_saver().capabilities.ttl is False
    assert make_saver(ttl_seconds=60).capabilities.ttl is True


async def test_history_storage_error_is_wrapped():
    """底层 Redis 报错: 包装成 CheckpointStorageError (带键名, 方便排查)."""

    class BrokenClient(FakeRedisClient):
        async def xadd(self, *args, **kwargs):
            raise RedisError("写超时")

    saver = make_saver(client=BrokenClient())

    with pytest.raises(CheckpointStorageError) as excinfo:
        await saver.save(make_checkpoint())

    assert "test:ckpt:thread-1" in str(excinfo.value)


# ---------------------------------------------------------------------------
# latest 模式: 只留最新一帧 (对齐 langgraph 的 ShallowRedisSaver)
# ---------------------------------------------------------------------------


async def test_latest_save_then_load_latest():
    """存一帧再取回来: 内容完全一致."""
    saver = make_saver(MODE_LATEST)

    await saver.save(make_checkpoint())

    assert await saver.load_latest("thread-1") == make_checkpoint()


async def test_latest_keeps_only_the_newest_frame():
    """只留最新: 存第二帧就把第一帧盖掉了 (白板语义)."""
    saver = make_saver(MODE_LATEST)
    await saver.save(make_checkpoint(checkpoint_id="ck-1", turn_number=1))
    await saver.save(make_checkpoint(checkpoint_id="ck-2", turn_number=2))

    latest = await saver.load_latest("thread-1")

    assert latest is not None
    assert latest.checkpoint_id == "ck-2"


async def test_latest_rejects_history_reads():
    """翻历史与按编号取: 明确报「做不到」, 而不是返回空结果糊弄调用方."""
    saver = make_saver(MODE_LATEST)

    with pytest.raises(CheckpointCapabilityError) as excinfo:
        await saver.list_history("thread-1")
    assert "history" in str(excinfo.value)

    with pytest.raises(CheckpointCapabilityError):
        await saver.load("ck-1", thread_id="thread-1")


async def test_latest_capabilities_declare_no_history():
    """能力声明: 没有历史 (调用方据此决定要不要换实现)."""
    assert make_saver(MODE_LATEST).capabilities.history is False


async def test_latest_ttl_is_passed_to_redis():
    """配了 TTL: set 带上过期秒数."""
    client = FakeRedisClient()
    saver = make_saver(MODE_LATEST, client=client, ttl_seconds=60)

    await saver.save(make_checkpoint())

    assert client.set_calls == [("test:ckpt:latest:thread-1", 60)]


async def test_latest_without_ttl_sets_no_expiry():
    """没配 TTL: set 不带过期参数 (键一直留着, 直到有人删)."""
    client = FakeRedisClient()
    saver = make_saver(MODE_LATEST, client=client)

    await saver.save(make_checkpoint())

    assert client.set_calls == [("test:ckpt:latest:thread-1", None)]


# ---------------------------------------------------------------------------
# 真实 Redis (需本机 Redis; 默认排除, 用 pytest -m redis 单跑)
# ---------------------------------------------------------------------------


def _real_redis_url() -> str | None:
    """本机 Redis 连接串 (与实现取的是同一组变量)."""
    return os.environ.get("CHARAGENT_CHECKPOINT_REDIS_URL") or os.environ.get(
        "REDIS_URL"
    )


class RealRedis:
    """真 Redis 上的一个隔离小场景: saver + 一个旁观的客户端.

    旁观客户端 (而不是翻 saver 的私有属性) 是有意的: 「存进去的东西到底长什么
    样」这件事, 只有从**存储那一侧**看才算证据.
    """

    def __init__(
        self, mode: str = MODE_HISTORY, ttl_seconds: int | None = None
    ) -> None:
        from redis.asyncio import Redis

        self.saver = RedisCheckpointSaver(
            url=_real_redis_url(),
            key_prefix="charagent-test",
            mode=mode,
            ttl_seconds=ttl_seconds,
        )
        self.observer = Redis.from_url(_real_redis_url() or "", decode_responses=True)

    async def __aenter__(self) -> RealRedis:
        try:
            await self.saver.load_latest("probe")
        except CheckpointStorageError as exc:
            await self.aclose()
            pytest.skip(f"本机 Redis 不可用: {exc}")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.saver.aclose()
        await self.observer.aclose()


@pytest.mark.redis
async def test_real_redis_history_roundtrip():
    """真 Redis 上: 流式历史能存、能翻、能按编号取, 存储侧确实是一条流."""
    thread_id = "real-redis-history"
    async with RealRedis() as env:
        key = env.saver.key_for(thread_id)
        try:
            frames = make_frames(3, thread_id=thread_id)
            for frame in frames:
                await env.saver.save(frame)

            history = await env.saver.list_history(thread_id)
            restored = await env.saver.load(
                frames[0].checkpoint_id, thread_id=thread_id
            )
            raw = await env.observer.xrange(key, "-", "+")
        finally:
            await env.observer.delete(key)

    assert [frame.turn_number for frame in history] == [1, 2, 3]
    assert restored == frames[0]
    assert len(raw) == 3  # 存储侧: 一条流里三条条目
    assert '"schema_version"' in raw[0][1]["data"]


@pytest.mark.redis
async def test_real_redis_trim_and_ttl():
    """真 Redis 上: 裁剪与 TTL 都真的生效."""
    thread_id = "real-redis-trim"
    async with RealRedis(ttl_seconds=120) as env:
        key = env.saver.key_for(thread_id)
        saver = RedisCheckpointSaver(
            url=_real_redis_url(),
            key_prefix="charagent-test",
            max_frames=2,
            ttl_seconds=120,
        )
        try:
            for frame in make_frames(4, thread_id=thread_id):
                await saver.save(frame)

            remaining = await env.saver.list_history(thread_id)
            ttl = await env.observer.ttl(key)
        finally:
            await env.observer.delete(key)
            await saver.aclose()

    assert [frame.turn_number for frame in remaining] == [3, 4]
    assert 0 < ttl <= 120


@pytest.mark.redis
async def test_real_redis_latest_mode_roundtrip():
    """真 Redis 上: latest 模式存一帧取一帧 (存的就是那串 JSON)."""
    thread_id = "real-redis-latest"
    async with RealRedis(mode=MODE_LATEST) as env:
        checkpoint = make_checkpoint(thread_id=thread_id)
        key = env.saver.key_for(thread_id)
        try:
            await env.saver.save(checkpoint)
            loaded = await env.saver.load_latest(thread_id)
            raw = await env.observer.get(key)
        finally:
            await env.observer.delete(key)

    assert loaded == checkpoint
    assert isinstance(raw, str) and '"schema_version"' in raw
