"""记忆的时间衰减自检 (不需要数据库): 分值曲线的数学.

关注点是那条**曲线本身**: 「刚写下 1.0 / 每过一个半衰期减半 / 未来时间封顶」
三件事是排序与淘汰共用的口径 (仓储的 `list_for_user` 与 `_prune` 都调
`recency_score`) —— 曲线改了, 两处的行为一起变, 所以钉在函数上而不是某个调用点.

仓储的真库行为 (租户隔离 / 去重 / 软删 / 容量淘汰) 在 `test_db_store.py` 用
真库守 —— 这个文件一个数据库都不碰.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from CharAgent.db.repositories.memories import (
    DEFAULT_HALF_LIFE_DAYS,
    recency_score,
)

# 用例里的「现在」: 钉死一个时刻, 让分值是确定值 (不跟着跑用例的墙钟变)
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def test_a_fresh_memory_scores_one():
    """刚写下的记忆分值 1.0 (曲线的起点)."""
    assert recency_score(NOW, now=NOW) == 1.0


def test_the_score_halves_every_half_life():
    """每过一个半衰期, 分值减半 —— 「半衰期」这个名字就是这条."""
    halfway = recency_score(NOW - timedelta(days=DEFAULT_HALF_LIFE_DAYS), now=NOW)
    two_lives = recency_score(NOW - timedelta(days=DEFAULT_HALF_LIFE_DAYS * 2), now=NOW)

    assert halfway == 0.5
    assert two_lives == 0.25


def test_the_half_life_is_configurable():
    """半衰期可配: 同一个年龄, 半衰期越短分值越低 (衰减越快)."""
    aged = NOW - timedelta(days=7)

    slow = recency_score(aged, now=NOW, half_life_days=30)
    fast = recency_score(aged, now=NOW, half_life_days=7)

    assert fast < slow


def test_a_future_memory_is_capped_at_one():
    """未来时间 (时钟回拨 / 测试造数) 按「就是现在」算 —— 分值封顶 1.0.

    不封顶的话负年龄会让分值**大于 1**, 「未来」的记忆比刚写的还靠前 —— 那是
    个说不通的排序.
    """
    assert recency_score(NOW + timedelta(days=3), now=NOW) == 1.0
