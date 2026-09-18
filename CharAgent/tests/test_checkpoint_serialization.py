"""checkpoint 序列化协议测试 (difficulties #5).

四组关注点, 对应 serialization.py 的职责:
1. 行李牌机制: datetime 之类装不进 JSON 的值怎么进出 (含注册自定义类型)
2. 进度与观察值的往返: state (接着跑要用的) 与 metadata (给人看的) 各走各的
3. 版本迁移: 老快照读得回来 (v1 裸列表 / v2 混装 → v3 分开), 未来版本拒绝读
4. 格式定稿: 序列化产物与 fixtures/checkpoint_v3.json 逐字一致 (#63 快照测试) ——
   将来谁改了字段名或标签写法, 这个用例会立刻报警, 改格式必须是有意的

样本来自 tests/helpers.py 的 make_state / make_metadata / make_checkpoint (与三个
存储实现的用例共用同一份样本, 于是「同一个进度在哪儿存都一样」的对比才有意义).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import (
    SAMPLE_CREATED_AT,
    make_checkpoint,
    make_metadata,
    make_state,
)

from CharAgent.checkpoint.serialization import (
    DEFAULT_CODEC,
    TAG_KEY,
    VALUE_KEY,
    CheckpointCodec,
    TypeCodec,
)
from CharAgent.checkpoint.utils.errors import (
    CheckpointConfigError,
    CheckpointMigrationError,
    CheckpointSerializationError,
)
from CharAgent.checkpoint.utils.migrations import migrate_body
from CharAgent.checkpoint.utils.pending import pending_tool_calls
from CharAgent.checkpoint.utils.types import (
    SCHEMA_VERSION,
    CheckpointSource,
    Suspension,
)

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> dict:
    """读一份 fixture (老格式样本 / 当前格式的锁)."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 编码: 什么能原样通过, 什么要打行李牌
# ---------------------------------------------------------------------------


def test_encode_keeps_json_native_structure_unchanged():
    """JSON 本来就认识的类型原样通过 (不需要行李牌)."""
    codec = CheckpointCodec()
    payload = {"text": "你好", "num": 3, "pi": 2.5, "flag": True, "empty": None}

    assert codec.encode_payload(payload) == payload


def test_encode_tags_datetime():
    """datetime 打上行李牌 (标签形状是存储里认的契约, 逐字断言)."""
    codec = CheckpointCodec()

    encoded = codec.encode_payload(SAMPLE_CREATED_AT)

    assert encoded == {TAG_KEY: "datetime", VALUE_KEY: "2026-09-13T10:00:00+00:00"}


def test_encode_tags_datetime_nested_in_structure():
    """藏在嵌套 dict / list 里的 datetime 也能被翻出来打标签."""
    codec = CheckpointCodec()
    moment = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)

    encoded = codec.encode_payload({"log": [{"at": moment}]})

    assert encoded["log"][0]["at"][TAG_KEY] == "datetime"


def test_encode_turns_tuple_into_list():
    """元组变成列表 (JSON 只有列表) —— 有意的类型损失, 这里钉住行为."""
    codec = CheckpointCodec()

    assert codec.encode_payload(("a", "b")) == ["a", "b"]


def test_encode_rejects_unregistered_type_with_hint():
    """没注册翻译器的类型: 报错并告诉调用方怎么注册 (别让人去猜)."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError) as excinfo:
        codec.encode_payload({"handle": object()})

    message = str(excinfo.value)
    assert "object" in message
    assert "register_type" in message


def test_encode_rejects_non_string_dict_key():
    """字典的键不是字符串: 报错 (JSON 的键只能是文本, 不能悄悄转)."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError) as excinfo:
        codec.encode_payload({1: "one"})

    assert "键" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 解码: 按行李牌认领
# ---------------------------------------------------------------------------


def test_decode_restores_datetime():
    """打了标签的值解码后回到原类型."""
    codec = CheckpointCodec()

    decoded = codec.decode_payload(
        {TAG_KEY: "datetime", VALUE_KEY: "2026-09-13T10:00:00+00:00"}
    )

    assert decoded == SAMPLE_CREATED_AT


def test_decode_leaves_lookalike_dict_as_data():
    """恰好带同名字段的业务字典不算行李牌 (键数不对就当普通数据)."""
    codec = CheckpointCodec()
    lookalike = {TAG_KEY: "datetime", VALUE_KEY: "x", "note": "业务字段碰巧同名"}

    assert codec.decode_payload(lookalike) == lookalike


def test_decode_rejects_unknown_tag():
    """行李牌上的名字不认识: 报错 (猜着读只会读出错的数据)."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError) as excinfo:
        codec.decode_payload({TAG_KEY: "quantum", VALUE_KEY: "??"})

    assert "quantum" in str(excinfo.value)


def test_register_type_roundtrips_custom_value():
    """注册自定义类型后, 它也能进出快照 (Decimal 例子)."""
    codec = CheckpointCodec()
    codec.register_type(Decimal, name="decimal", encode=str, decode=Decimal)

    encoded = codec.encode_payload({"amount": Decimal("128.00")})

    assert encoded == {"amount": {TAG_KEY: "decimal", VALUE_KEY: "128.00"}}
    assert codec.decode_payload(encoded) == {"amount": Decimal("128.00")}


def test_register_type_accepts_types_via_constructor():
    """也可以在造 codec 时一次性带上翻译器 (构造参数与 register_type 等价)."""
    codec = CheckpointCodec(
        types={Decimal: TypeCodec(name="decimal", encode=str, decode=Decimal)}
    )

    assert codec.encode_payload(Decimal("1.50"))[TAG_KEY] == "decimal"


def test_register_type_rejects_duplicate_name():
    """同一个行李牌名字不能给两种类型 (否则读的时候谁也认不出谁)."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointConfigError) as excinfo:
        codec.register_type(Decimal, name="datetime", encode=str, decode=Decimal)

    assert "datetime" in str(excinfo.value)


def test_register_type_rejects_empty_name():
    """名字为空: 报错 (空名字在存储里没法当契约)."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointConfigError):
        codec.register_type(Decimal, name="", encode=str, decode=Decimal)


# ---------------------------------------------------------------------------
# JSON 文本
# ---------------------------------------------------------------------------


def test_dumps_and_loads_roundtrip():
    """文本进出后结构不变 (紧凑写法)."""
    codec = CheckpointCodec()
    payload = codec.encode_payload({"at": SAMPLE_CREATED_AT, "text": "中文"})

    assert codec.loads(codec.dumps(payload)) == payload


def test_dumps_indent_only_changes_layout():
    """缩进与否是同一份数据 (存储用紧凑, 文档与快照对比用多行)."""
    codec = CheckpointCodec()
    payload = {"a": [1, 2]}

    compact = codec.loads(codec.dumps(payload))
    pretty = codec.loads(codec.dumps(payload, indent=2))

    assert compact == pretty == payload


def test_loads_rejects_broken_json():
    """存的文本不是合法 JSON: 报错而不是抛裸的 JSONDecodeError."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError):
        codec.loads("{不是 JSON")


def test_dumps_rejects_nan():
    """NaN 不是合法 JSON: 宁可报错, 也不写出别人读不懂的字."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError):
        codec.dumps({"value": float("nan")})


# ---------------------------------------------------------------------------
# 进度与观察值的往返 (Postgres 用 encode_body/decode_body)
# ---------------------------------------------------------------------------


def test_body_roundtrips_state_and_metadata():
    """主体 (state + metadata) 编码 -> 文本 -> 解码后两块都原样."""
    codec = CheckpointCodec()
    checkpoint = make_checkpoint()

    body = codec.loads(codec.dumps(codec.encode_body(checkpoint)))
    state, metadata = codec.decode_body(body, schema_version=SCHEMA_VERSION)

    assert state == checkpoint.state
    assert metadata == checkpoint.metadata


def test_state_roundtrips_datetime_inside_tool_result():
    """工具结果里的 datetime 也走行李牌 (序列化协议点名的那类数据)."""
    codec = CheckpointCodec()
    moment = datetime(2026, 9, 13, 8, 30, tzinfo=UTC)
    state = make_state(
        messages=[
            {
                "role": "tool",
                "tool_call_id": "call_0",
                "content": "已发货",
                "at": moment,
            }
        ]
    )

    body = codec.loads(codec.dumps(codec.encode_body(make_checkpoint(state=state))))
    decoded, _ = codec.decode_body(body, schema_version=SCHEMA_VERSION)

    assert decoded.messages[0]["at"] == moment


def test_state_roundtrips_suspension():
    """挂起点 (还欠哪几条工具调用) 也原样往返 —— #25 恢复的底子."""
    codec = CheckpointCodec()
    pending = pending_tool_calls(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_9",
                        "type": "function",
                        "function": {
                            "name": "refund_order",
                            "arguments": '{"order_no": "20260701123456"}',
                        },
                    }
                ],
            }
        ]
    )
    state = make_state(
        suspension=Suspension(
            reason="needs_approval", pending=pending, approval_id="appr-9"
        )
    )

    body = codec.loads(codec.dumps(codec.encode_body(make_checkpoint(state=state))))
    decoded, _ = codec.decode_body(body, schema_version=SCHEMA_VERSION)

    assert decoded.suspension == state.suspension


def test_metadata_roundtrips_all_source_kinds():
    """观察值的来源三档 (loop / fork / suspension) 都能原样往返."""
    codec = CheckpointCodec()

    for source in CheckpointSource:
        metadata = make_metadata(source=source)
        body = codec.loads(
            codec.dumps(codec.encode_body(make_checkpoint(metadata=metadata)))
        )
        _, decoded = codec.decode_body(body, schema_version=SCHEMA_VERSION)
        assert decoded.source is source


def test_metadata_rejects_unknown_source():
    """来源取值不认识: 报错并列出可选值 (别猜「大概是哪个」)."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError) as excinfo:
        codec.decode_body(
            {"state": {"messages": []}, "metadata": {"source": "teleport"}},
            schema_version=SCHEMA_VERSION,
        )

    assert "teleport" in str(excinfo.value)


def test_decode_body_rejects_non_dict_body():
    """主体不是字典: 报错 (说明期望的形状)."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError) as excinfo:
        codec.decode_body(["不是字典"], schema_version=SCHEMA_VERSION)

    assert "state" in str(excinfo.value)


def test_decode_body_fills_defaults_for_missing_fields():
    """老一点的载荷缺字段时填默认值 (缺 = 当时还没这个概念)."""
    codec = CheckpointCodec()

    state, metadata = codec.decode_body(
        {"state": {"messages": []}}, schema_version=SCHEMA_VERSION
    )

    assert state.turn_count == 0
    assert state.total_tokens == 0
    assert state.suspension is None
    assert metadata.source is CheckpointSource.LOOP
    assert metadata.turn_tokens == 0
    assert metadata.content is None


def test_decode_state_rejects_missing_messages():
    """没有消息历史就不算一份能接着跑的快照: 报错."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError) as excinfo:
        codec.decode_body({"state": {"turn_count": 1}}, schema_version=SCHEMA_VERSION)

    assert "messages" in str(excinfo.value)


def test_decode_state_rejects_wrong_field_type():
    """字段类型不对: 报错信息说清是哪个字段、本来要什么."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError) as excinfo:
        codec.decode_body(
            {"state": {"messages": [], "turn_count": "三"}},
            schema_version=SCHEMA_VERSION,
        )

    assert "turn_count" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 整条记录 (Redis 用)
# ---------------------------------------------------------------------------


def test_record_roundtrips():
    """整条记录编码 -> 文本 -> 解码后与原记录相同."""
    codec = CheckpointCodec()
    checkpoint = make_checkpoint()

    text = codec.dumps(codec.encode_record(checkpoint))
    decoded = codec.decode_record(codec.loads(text))

    assert decoded == checkpoint


def test_decode_record_rejects_missing_version():
    """整条记录没有版本号: 报错 (不知道按哪个版本读, 读了也是猜)."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError) as excinfo:
        codec.decode_record({"checkpoint_id": "ck-1"})

    assert "schema_version" in str(excinfo.value)


def test_decode_record_rejects_non_dict_payload():
    """整条记录的顶层不是字典: 报错."""
    codec = CheckpointCodec()

    with pytest.raises(CheckpointSerializationError):
        codec.decode_record(["不是字典"])


# ---------------------------------------------------------------------------
# 版本迁移 (向前兼容)
# ---------------------------------------------------------------------------


def test_migrate_body_upgrades_v1_list_to_structured_state():
    """v1 的裸消息列表 -> v2 的结构化字典 (只搬 messages, 其余留空)."""
    messages = [{"role": "user", "content": "你好"}]

    upgraded = migrate_body({"state": messages}, from_version=1)

    assert upgraded["state"]["messages"] == messages


def test_migrate_body_rejects_future_version():
    """快照版本比本代码新: 报错 (硬读会把字段读错位)."""
    with pytest.raises(CheckpointMigrationError) as excinfo:
        migrate_body({"state": {"messages": []}}, from_version=SCHEMA_VERSION + 1)

    assert "升级代码" in str(excinfo.value)


def test_migrate_body_rejects_missing_step():
    """中间缺一级翻译函数: 报错 (而不是把老数据当新数据用)."""
    with pytest.raises(CheckpointMigrationError) as excinfo:
        migrate_body({"state": []}, from_version=0)

    assert "迁移函数" in str(excinfo.value)


def test_v1_snapshot_reads_back_with_defaults():
    """v1 老快照能读回来: 消息历史原样, 新概念字段填默认值 (#5 向前兼容)."""
    payload = read_fixture("checkpoint_v1.json")

    checkpoint = DEFAULT_CODEC.decode_record(payload)

    assert checkpoint.state.messages == payload["state"]
    assert checkpoint.state.turn_count == 0
    assert checkpoint.metadata.source is CheckpointSource.LOOP
    assert checkpoint.created_at == datetime(2026, 8, 18, 9, 30, tzinfo=UTC)


def test_v2_snapshot_moves_observation_fields_into_metadata():
    """v2 老快照读回来时, 观察值已从 state 搬进 metadata (v2→v3 的字段搬家)."""
    payload = read_fixture("checkpoint_v2.json")

    checkpoint = DEFAULT_CODEC.decode_record(payload)

    assert set(payload["state"]) == {
        "messages",
        "turn_count",
        "total_tokens",
        "truncation_count",
        "content_parts",
        "content",
        "finish_reason",
        "outcome",
        "suspension",
    }
    assert checkpoint.metadata.source is CheckpointSource.LOOP
    assert checkpoint.metadata.turn_tokens == 0
    # v2 样本里这三个字段都是 null, 搬过去还是 null (不伪造值)
    assert checkpoint.metadata.content is None
    assert checkpoint.metadata.finish_reason is None
    assert checkpoint.metadata.outcome is None
    assert checkpoint.state.suspension is not None


def test_v2_migration_moves_non_null_observation_values():
    """v2 的观察值不是 null 时, 搬家后值原样保留 (不是只搬形状)."""
    body = {
        "state": {
            "messages": [],
            "content": "上一段答的",
            "finish_reason": "stop",
            "outcome": "finished",
        },
        "metadata": None,
    }

    upgraded = migrate_body(body, from_version=2)

    assert upgraded["metadata"]["content"] == "上一段答的"
    assert upgraded["metadata"]["finish_reason"] == "stop"
    assert upgraded["metadata"]["outcome"] == "finished"
    # 搬完 state 里那三个字段就没了 (同一份数据只有一个家)
    assert set(upgraded["state"]) == {"messages"}


def test_old_snapshots_report_current_schema_version():
    """读回来的老快照在内存里已是当前版本 (升级过就不再是老的)."""
    for name in ("checkpoint_v1.json", "checkpoint_v2.json", "checkpoint_v3.json"):
        checkpoint = DEFAULT_CODEC.decode_record(read_fixture(name))
        assert checkpoint.schema_version == SCHEMA_VERSION


def test_v3_progress_keeps_only_resume_inputs():
    """v3 起进度里不再有观察值: state 只有「接着跑要用的」那几个字段."""
    checkpoint = DEFAULT_CODEC.decode_record(read_fixture("checkpoint_v3.json"))

    assert set(checkpoint.state.__slots__) == {
        "messages",
        "turn_count",
        "total_tokens",
        "truncation_count",
        "content_parts",
        "suspension",
    }


# ---------------------------------------------------------------------------
# 格式定稿 (快照测试, #63)
# ---------------------------------------------------------------------------


def test_serialized_record_matches_fixture():
    """序列化产物与 fixtures/checkpoint_v3.json 逐字一致 (改格式必须是有意的).

    这个用例红了怎么办: 先确认格式变更是有意为之, 再重新生成 fixture —— 顺手
    也要判断「老快照还读得回来吗」, 读不回来就得加一个迁移函数并把
    SCHEMA_VERSION +1 (见 utils/migrations.py).
    """
    expected = (FIXTURES / "checkpoint_v3.json").read_text(encoding="utf-8")
    checkpoint = DEFAULT_CODEC.decode_record(read_fixture("checkpoint_v3.json"))

    assert DEFAULT_CODEC.dumps(
        DEFAULT_CODEC.encode_record(checkpoint), indent=2
    ) == expected.rstrip("\n")


def test_fixture_suspension_agrees_with_pending_extraction():
    """快照里「还欠哪些调用」的两种读法一致: 历史形状与挂起记录说得一样.

    历史末尾那条 assistant 消息 (带 tool_calls 但没结果) 是「欠着」的权威形状;
    suspension.pending 是同一件事的显式记账. 两者一致, 恢复时才不会各读各的.
    """
    checkpoint = DEFAULT_CODEC.decode_record(read_fixture("checkpoint_v3.json"))

    assert checkpoint.state.suspension is not None
    assert pending_tool_calls(checkpoint.state.messages) == (
        checkpoint.state.suspension.pending
    )
