"""幂等键与幂等存储测试 (difficulties #13 #17).

场景 → 断言:
- 生成: generate() 产出 32 字符 hex 随机键 (两次不同);
- 校验: parse() 接受合法键 (字母数字开头 + [A-Za-z0-9._:-]), 拒绝空串 / 超长 /
  空白 / 控制字符 / 通配符 (键会变成下游存储 key 与日志字段, 必须挡在入口);
  传进来的已是 IdempotencyKey 则原样返回 (幂等);
- 存储语义: 首次 claim = CLAIMED (拿到执行权); 未释放的重复 claim =
  IN_PROGRESS (不得重复执行); complete 后 claim = COMPLETED 且带既有结果;
  release 放行失败动作的重试 (不释放会把键永久卡死); 已完成的结果不会被
  release 抹掉;
- 组合语义 (压轴用例, #13 核心): 重试 ≠ 副作用重复执行 —— 同一幂等键重放时
  命中已完成记录, 真实动作恰好执行一次.

存储为进程内实现 (P0): 无 TTL / 无持久化 / 跨实例失效, Redis 与 PG 版本留待
后续阶段 (幂等 + Saga + 分布式锁).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from doubles import RecordingSleep

from CharAgent.model import ModelTimeoutError
from CharAgent.retry import (
    ClaimStatus,
    IdempotencyKey,
    IdempotencyKeyError,
    InMemoryIdempotencyStore,
    RetryPolicy,
    retry_async,
)

VALID_KEY = "run-2026.09.12:order_1"


# ---------------------------------------------------------------------------
# 生成与校验
# ---------------------------------------------------------------------------


def test_generate_produces_random_hex_keys() -> None:
    """生成: 32 字符 hex (uuid4), 两次调用不重复."""
    first = IdempotencyKey.generate()
    second = IdempotencyKey.generate()

    assert len(first.value) == 32
    assert first.value.isalnum()
    assert first != second


def test_parse_accepts_valid_keys_and_is_idempotent() -> None:
    """校验: 合法键通过; 传入已是键的对象原样返回 (幂等)."""
    key = IdempotencyKey.parse(VALID_KEY)
    assert key.value == VALID_KEY
    assert str(key) == VALID_KEY  # 可直接当 dict key / 日志字段
    assert IdempotencyKey.parse(key) is key


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "x" * 256,  # 超长
        "has space",
        "line\nbreak",
        "slash/inside",
        "star*",
        "-leading-dash",
        ".leading-dot",
        "中文键",
    ],
)
def test_parse_rejects_invalid_keys(raw: str) -> None:
    """非法键一律拒绝, 错误消息点名白名单 (防 key 注入下游存储)."""
    with pytest.raises(IdempotencyKeyError, match="允许字符"):
        IdempotencyKey.parse(raw)


def test_rejection_message_reports_actual_length() -> None:
    """拒绝消息带实际长度: 排查时不必去数超长键有多少字符 (消息可操作)."""
    with pytest.raises(IdempotencyKeyError, match="实际长度 256"):
        IdempotencyKey.parse("x" * 256)


def test_a_three_part_tool_call_key_is_shaped_by_the_caller_but_checked_here() -> None:
    """调用方那份三列拼出来的键: 合法拼法通过, 拼歪的在**进存储之前**就被挡住.

    键由调用方拼 (框架只约束形状, 不定义拼法): 上游每轮都从 `call_0` 重新编号,
    所以真正唯一的身份是三列 —— `run_id` + `message_id` + `tool_call_id`
    (见 `db/schema.py` 里 `charagent_tool_calls` 那段主键注释). uuid 的十六进制
    与 `:` / `-` / `.` 都在白名单里, 拼出来天然合法.

    为什么这条值得写: 拼歪了必须**在这里**就报出来, 而不是等到写库 —— 那时报错的
    地方 (某个 SQL 或主键) 离出错的地方 (谁拼的键) 已经很远了.
    """
    run_id, message_id, tool_call_id = (uuid4().hex for _ in range(3))

    key = IdempotencyKey.parse(f"{run_id}:{message_id}:{tool_call_id}")
    assert key.value.count(":") == 2

    # 夹了空白 (拼的时候用了空格当分隔符)
    with pytest.raises(IdempotencyKeyError, match="允许字符"):
        IdempotencyKey.parse(f"{run_id} {message_id} {tool_call_id}")
    # 太长的第一段把总长顶到 266 (> 255): 报的是长度, 不是字符集 (两条判据都在
    # 同一句消息里, 所以这里点名长度那一半, 免得读的人以为是空格的问题)
    with pytest.raises(IdempotencyKeyError, match="实际长度 266"):
        IdempotencyKey.parse(f"{'x' * 200}:{message_id}:{tool_call_id}")


# ---------------------------------------------------------------------------
# 幂等存储: 认领 / 完成 / 释放
# ---------------------------------------------------------------------------


async def test_first_claim_wins_and_duplicate_claim_is_in_progress() -> None:
    """首次认领拿到执行权; 未释放前的重复认领 = IN_PROGRESS (不得重复执行)."""
    store = InMemoryIdempotencyStore()
    key = IdempotencyKey.parse(VALID_KEY)

    first = await store.claim(key)
    second = await store.claim(key)

    assert first.claimed is True
    assert first.status is ClaimStatus.CLAIMED
    assert second.claimed is False
    assert second.status is ClaimStatus.IN_PROGRESS
    assert second.result is None


async def test_completed_claim_returns_the_existing_result() -> None:
    """完成后认领: COMPLETED + 既有结果 (重复请求直接返回, 不再执行 #17)."""
    store = InMemoryIdempotencyStore()
    key = IdempotencyKey.parse(VALID_KEY)
    await store.claim(key)
    await store.complete(key, {"refund_id": "r-1", "status": "accepted"})

    again = await store.claim(key)

    assert again.claimed is False
    assert again.status is ClaimStatus.COMPLETED
    assert again.result == {"refund_id": "r-1", "status": "accepted"}


async def test_release_allows_reclaim_after_failure() -> None:
    """动作失败释放认领: 后续请求可重新认领 (不释放会把键永久卡死)."""
    store = InMemoryIdempotencyStore()
    key = IdempotencyKey.parse(VALID_KEY)
    await store.claim(key)
    await store.release(key)

    again = await store.claim(key)

    assert again.claimed is True
    assert again.status is ClaimStatus.CLAIMED


async def test_release_does_not_erase_a_completed_result() -> None:
    """release 只放行「在途」记录: 已完成的结果不能被抹掉 (防结果丢失)."""
    store = InMemoryIdempotencyStore()
    key = IdempotencyKey.parse(VALID_KEY)
    await store.claim(key)
    await store.complete(key, "已受理")

    await store.release(key)

    assert (await store.claim(key)).status is ClaimStatus.COMPLETED


async def test_complete_records_result_without_prior_claim() -> None:
    """complete 直接补登完成 (动作已落地但认领记录缺失时兜底, 不抛错)."""
    store = InMemoryIdempotencyStore()
    key = IdempotencyKey.parse(VALID_KEY)

    await store.complete(key, "已受理")

    assert (await store.claim(key)).result == "已受理"


# ---------------------------------------------------------------------------
# 组合语义: 重试 ≠ 副作用重复执行 (#13 核心)
# ---------------------------------------------------------------------------


async def test_retry_with_the_same_key_executes_the_side_effect_once() -> None:
    """动作已完成但响应丢失: 重试命中 COMPLETED 直接取既有结果, 不重复执行.

    这是 #13 把「重试」与「幂等键」放在同一条的原因 —— 重试本身安全的前提是
    动作幂等: 第一次的副作用已经发生 (退款已受理), 第二次重放必须返回既有
    结果而不是再受理一次.
    """
    store = InMemoryIdempotencyStore()
    key = IdempotencyKey.generate()
    sleep = RecordingSleep()
    side_effects: list[str] = []

    async def action() -> object:
        record = await store.claim(key)
        if not record.claimed:
            return record.result  # 已完成: 直接取既有结果 (不重复执行)
        side_effects.append(key.value)  # 真实副作用 (只应发生一次)
        await store.complete(key, "退款已受理")
        raise ModelTimeoutError("响应丢失: 调用方以为这次失败")

    result = await retry_async(
        action,
        policy=RetryPolicy(max_attempts=2, jitter=0.0, sleep=sleep),
    )

    assert result == "退款已受理"
    assert sleep.delays == [0.5]  # 确实重试了一次 (瞬态错误)
    assert side_effects == [key.value]  # 但副作用只发生一次 (#13)
