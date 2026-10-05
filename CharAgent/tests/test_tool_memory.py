"""长期记忆工具 (remember / recall / forget) 的离线用例.

关注点是**工具这一层自己的契约**: 报给模型的 schema 长什么样 (身份不许进去)、
拒写的判据、回给模型的文本, 以及 C30 加的三样 (recall 的编号、来源两列、
使用时刻的记账). 仓储的语义 (隔离 / 去重 / 衰减 / 淘汰 / 替换 / 前缀查) 在 db 层
的用例里守 (`test_db_store.py`), 这里用一个**记账替身**接住调用 —— 不碰数据库.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from CharAgent.db.entities import Memory, MemoryKind
from CharAgent.structured_logging import log_context
from CharAgent.tool import MAX_CONTENT_LENGTH, build_memory_tools
from CharAgent.tool.memory import (
    _SHAPE_LABELS,
    ID_DISPLAY_LENGTH,
    SENSITIVE_PATTERNS,
)
from CharAgent.tool.utils.errors import ToolActionableError


def _memory(
    content: str,
    kind: MemoryKind,
    *,
    created_at: datetime,
    memory_id: str = "m-1",
) -> Memory:
    """造一条记忆行 (与仓储读回来的形状一致, 不落库)."""
    return Memory(
        memory_id=memory_id,
        tenant_id="t-1",
        user_id="u-1",
        kind=kind.value,
        content=content,
        source_thread_id=None,
        source_run_id=None,
        created_at=created_at,
        updated_at=created_at,
        last_used_at=None,
        deleted_at=None,
    )


class RecordingStore:
    """记忆仓储的替身: 记下每次调用, 按预置的行回话."""

    def __init__(self, rows: list[Memory] | None = None) -> None:
        self.added: list[dict[str, Any]] = []
        self.rows = list(rows or [])
        self.deleted: list[str] = []
        self.touched: list[list[str]] = []

    async def add(
        self,
        *,
        tenant_id: str,
        user_id: str,
        content: str,
        kind: MemoryKind,
        source_thread_id: str | None = None,
        source_run_id: str | None = None,
        created_at: datetime | None = None,
    ) -> Memory:
        self.added.append(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "content": content,
                "kind": kind,
                "source_thread_id": source_thread_id,
                "source_run_id": source_run_id,
            }
        )
        return _memory(content, kind, created_at=created_at or datetime.now(UTC))

    async def list_for_user(self, tenant_id: str, user_id: str) -> list[Memory]:
        return list(self.rows)

    async def list_by_id_prefix(
        self, prefix: str, *, tenant_id: str, user_id: str
    ) -> list[Memory]:
        return [row for row in self.rows if row.memory_id.startswith(prefix)]

    async def soft_delete(
        self, memory_id: str, *, tenant_id: str, user_id: str, moment: Any = None
    ) -> bool:
        self.deleted.append(memory_id)
        return True

    async def touch_used(
        self,
        memory_ids: Any,
        *,
        tenant_id: str,
        user_id: str,
        moment: Any = None,
    ) -> int:
        self.touched.append(list(memory_ids))
        return len(list(memory_ids))


def _tools(
    store: RecordingStore,
    *,
    tenant_id: str = "t-1",
    user_id: str = "u-1",
    thread_id: str = "thread-1",
):
    """按名字取那三个工具 (顺序另有断言守着)."""
    built = build_memory_tools(
        store, tenant_id=tenant_id, user_id=user_id, thread_id=thread_id
    )
    return {item.name: item for item in built}


def test_the_factory_returns_remember_recall_forget():
    """工厂交出的就是那三个工具, 顺序 [remember, recall, forget] (装配顺序是契约)."""
    built = build_memory_tools(
        RecordingStore(), tenant_id="t-1", user_id="u-1", thread_id="thread-1"
    )

    assert [item.name for item in built] == ["remember", "recall", "forget"]


def test_every_rejection_shape_has_a_label():
    """拒写的形状与「中文说法」一一对应 —— redact 加第五条规则时在这里当场红.

    `SENSITIVE_PATTERNS` 从 `MASK_RULES` 长出来 (redact 加了规则它就自动跟上), 而
    `_SHAPE_LABELS` 是另一处手写的映射 —— 少一个键就是运行时的 KeyError. 与
    `redaction.TOOL_PHRASES` 那条「映射必有用例守」同一条纪律.
    """
    assert set(SENSITIVE_PATTERNS) == set(_SHAPE_LABELS)


def test_neither_tool_exposes_an_identity_parameter():
    """**本文件的核心**: 三个工具的 schema 里没有身份 —— 键名与值都搜不到.

    与业务工具同一条判据 (CharApp 的 test_provider.py 有同款): 身份走闭包,
    模型既看不见也改不了「记给谁 / 读谁的 / 删谁的」. 值那一层用的是**不会撞车**
    的怪串.
    """
    tenant_id = "tenant-9f3a2c"
    user_id = "user-7b1d0e"
    thread_id = "thread-4a5b6c"
    tools = _tools(
        RecordingStore(), tenant_id=tenant_id, user_id=user_id, thread_id=thread_id
    )

    params = {
        name: set(item.parameters.get("properties", {})) for name, item in tools.items()
    }
    assert params == {
        "remember": {"content", "kind"},
        "recall": set(),
        "forget": {"memory_id"},
    }

    import json

    for item in tools.values():
        schema = json.dumps(
            {
                "name": item.name,
                "description": item.description,
                "parameters": item.parameters,
            },
            ensure_ascii=False,
        )
        for secret in (tenant_id, user_id, thread_id):
            assert secret not in schema, f"{item.name} 的 schema 里漏了 {secret!r}"


def test_remember_schema_pins_content_and_kind():
    """remember 的两个参数各有其形: content 有长度上限, kind 是封闭的四取值."""
    remember = _tools(RecordingStore())["remember"]
    content = remember.parameters["properties"]["content"]
    kind = remember.parameters["properties"]["kind"]

    assert content["maxLength"] == MAX_CONTENT_LENGTH
    assert content["type"] == "string"
    assert kind["enum"] == ["episodic", "semantic", "style", "nickname"]
    assert set(remember.parameters["required"]) == {"content", "kind"}


async def test_remember_hands_identity_and_kind_to_the_store():
    """写入这一跳: 身份 (闭包里那两个串) 与 kind (转换后的枚举) 原样到仓储."""
    store = RecordingStore()
    remember = _tools(store, tenant_id="t-1", user_id="u-1")["remember"]

    reply = await remember.fn(content="偏好货到付款", kind="semantic")

    recorded = store.added[0]
    assert recorded["tenant_id"] == "t-1"
    assert recorded["user_id"] == "u-1"
    assert recorded["content"] == "偏好货到付款"
    assert recorded["kind"] == MemoryKind.SEMANTIC
    assert "偏好货到付款" in reply, "回话里要带上记下的那句话 (模型据此确认)"


async def test_remember_records_the_source_pair():
    """C30: 来源两列 —— thread 来自闭包 (装配期事实), run 来自日志上下文 (执行期)."""
    store = RecordingStore()
    remember = _tools(store, thread_id="thread-9")["remember"]

    with log_context(thread_id="thread-9") as trace:
        trace.bind(run_id="run-abc")
        await remember.fn(content="偏好货到付款", kind="semantic")

    assert store.added[0]["source_thread_id"] == "thread-9"
    assert store.added[0]["source_run_id"] == "run-abc"


async def test_without_a_run_context_the_run_source_is_null():
    """工具被单独调用 (没有运行在跑) 时: thread 照记, run 如实为 NULL."""
    store = RecordingStore()
    remember = _tools(store, thread_id="thread-1")["remember"]

    await remember.fn(content="偏好货到付款", kind="semantic")

    assert store.added[0]["source_thread_id"] == "thread-1"
    assert store.added[0]["source_run_id"] is None


@pytest.mark.parametrize(
    "content",
    [
        "他的手机号是 13800000003",  # 手机号形状
        "支付密码是 135791",  # 一次性密码形状 (6 位数字)
        "邮箱 buyer3@example.com",  # 邮箱形状
        "身份证 110101199003071234",  # 证件号形状
        "卡号 6222021234567890123",  # 银行卡形状
    ],
)
async def test_a_sensitive_value_is_rejected(content: str):
    """敏感值拒写: 抛可操作错误, 仓储一次都没被调 (拒在工具这一层)."""
    store = RecordingStore()
    remember = _tools(store)["remember"]

    with pytest.raises(ToolActionableError) as excinfo:
        await remember.fn(content=content, kind="semantic")

    assert store.added == [], "被拒的内容不该到达仓储"
    text = str(excinfo.value)
    assert "没有写进" in text, "要说清没写进去"
    assert "现查" in text, "要给出替代做法 (别让模型只是重复试)"


async def test_a_long_order_number_is_not_a_sensitive_value():
    """24 位订单号不误伤: 6 位数字那条规则不能从长数字里拆出子串来报.

    (顾盼 `(?<!\\d)` / `(?!\\d)` 就是为这一条加的 —— 少了它, 一个订单号会被
    拆出一段 6 位数字, 于是「记一下这单还没付」这种话都写不进去.)
    """
    store = RecordingStore()
    remember = _tools(store)["remember"]

    await remember.fn(content="这单还没付: 202609191230450000031234", kind="episodic")

    assert len(store.added) == 1


async def test_a_too_long_content_fails_the_schema_check():
    """超长内容被参数模型拦下 (schema 里有 maxLength, 校验与它同源)."""
    remember = _tools(RecordingStore())["remember"]
    model = remember.parameter_model
    assert model is not None

    with pytest.raises(ValidationError):
        model.model_validate(
            {"content": "长" * (MAX_CONTENT_LENGTH + 1), "kind": "semantic"}
        )


async def test_recall_lists_what_the_store_returns_in_order():
    """recall 的格式: 新的在前, 每条带种类标签与日期 —— 顺序由仓储给, 工具不改."""
    older = _memory(
        "上次说他换工作了",
        MemoryKind.EPISODIC,
        created_at=datetime(2026, 9, 10, 8, 0, tzinfo=UTC),
        memory_id="m-old",
    )
    newer = _memory(
        "偏好货到付款",
        MemoryKind.SEMANTIC,
        created_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
        memory_id="m-new",
    )
    recall = _tools(RecordingStore(rows=[newer, older]))["recall"]

    reply = await recall.fn()

    assert reply.index("偏好货到付款") < reply.index("上次说他换工作了"), "保序"
    assert "[事实]" in reply and "[事件]" in reply, "每条带种类标签"
    assert "2026-09-14" in reply and "2026-09-10" in reply, "每条带日期"


async def test_recall_shows_a_short_id_per_memory():
    """每条带一个短编号 (forget 按它找人) —— 长度是 ID_DISPLAY_LENGTH, 取前缀."""
    memory_id = "3f2a1b7c" + "0" * 24
    row = _memory(
        "偏好可爱",
        MemoryKind.STYLE,
        created_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
        memory_id=memory_id,
    )
    recall = _tools(RecordingStore(rows=[row]))["recall"]

    reply = await recall.fn()

    assert f"编号 {memory_id[:ID_DISPLAY_LENGTH]}" in reply


async def test_recall_marks_what_it_returned_as_used():
    """C30: 取回的行刷一次「最后被用」; 空记忆不刷 (没有东西可用)."""
    row = _memory(
        "偏好货到付款",
        MemoryKind.SEMANTIC,
        created_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
        memory_id="m-x",
    )
    store = RecordingStore(rows=[row])

    await _tools(store)["recall"].fn()

    assert store.touched == [["m-x"]]

    empty = RecordingStore()
    await _tools(empty)["recall"].fn()
    assert empty.touched == []


async def test_recall_says_so_when_the_memory_is_empty():
    """空记忆如实说空 —— 模型据此照常回答, 而不是硬编一句「我记得你」."""
    recall = _tools(RecordingStore())["recall"]

    reply = await recall.fn()

    assert "空" in reply


async def test_forget_deletes_the_one_matching_the_id():
    """forget 按编号找到那一条 → 软删 → 回话里带上被忘掉的内容."""
    memory_id = "3f2a1b7c" + "0" * 24
    row = _memory(
        "偏好可爱",
        MemoryKind.STYLE,
        created_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
        memory_id=memory_id,
    )
    store = RecordingStore(rows=[row])
    forget = _tools(store)["forget"]

    reply = await forget.fn(memory_id=memory_id[:ID_DISPLAY_LENGTH])

    assert store.deleted == [memory_id]
    assert "已忘掉" in reply and "偏好可爱" in reply


async def test_forget_accepts_the_full_id_and_any_case():
    """完整编号也能删; 大写照收 (编号是模型照抄的, 不该为大小写再卡一道)."""
    memory_id = "3f2a1b7c" + "0" * 24
    row = _memory(
        "偏好可爱",
        MemoryKind.STYLE,
        created_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
        memory_id=memory_id,
    )
    store = RecordingStore(rows=[row])

    await _tools(store)["forget"].fn(memory_id=memory_id.upper())

    assert store.deleted == [memory_id]


async def test_forget_with_an_unknown_id_says_what_to_do():
    """编号对不上任何活行: 不删, 回一句可操作的话 (先 recall 再照抄)."""
    store = RecordingStore()
    forget = _tools(store)["forget"]

    reply = await forget.fn(memory_id="ffffffff")

    assert "没有找到" in reply and "recall" in reply
    assert store.deleted == []


async def test_forget_refuses_when_the_prefix_is_ambiguous():
    """前缀撞上不止一条 (少见): **不删**, 让模型提供更长的编号.

    (8 位前缀在一个用户几十条的规模下几乎不会撞; 真撞上时宁可让模型多抄几位,
    也不能替它猜一条删掉 —— 删错的代价远大于多问一句.)
    """
    rows = [
        _memory(
            "偏好可爱",
            MemoryKind.STYLE,
            created_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
            memory_id="3f2a1b7c" + "a" * 24,
        ),
        _memory(
            "偏好高冷",
            MemoryKind.STYLE,
            created_at=datetime(2026, 9, 15, 8, 0, tzinfo=UTC),
            memory_id="3f2a1b7c" + "b" * 24,
        ),
    ]
    store = RecordingStore(rows=rows)
    forget = _tools(store)["forget"]

    reply = await forget.fn(memory_id="3f2a1b7c")

    assert "不止一条" in reply
    assert store.deleted == [], "不唯一时不删"
