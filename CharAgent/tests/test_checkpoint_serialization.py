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
from CharAgent.prompt.ref import (
    IDENTITY_ROLE,
    REF_NAME_KEY,
    REF_SHA_KEY,
    prompt_ref,
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
    # 搬完 state 里那三个字段就没了 (同一份数据只有一个家); 顺流升到当前版本时
    # 又补上后面几版的加法 (v4 的压缩进度 / v5 的引用与用量分解) —— 那些与这次
    # 搬家无关, 这里既验证搬家搬干净了, 也钉住「新字段确实补上了」
    assert set(upgraded["state"]) == {
        "messages",
        "summary",
        "summary_covers",
        "prompt_ref",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
    }


def test_old_snapshots_report_current_schema_version():
    """读回来的老快照在内存里已是当前版本 (升级过就不再是老的).

    v5 也进了这张名单 (2026-09-23, ticket 22): 它从「当前格式」变成了「老格式」——
    而它那份样本正好带着 v6 要改的那个键 (`run_id` = 当时的循环编号), 于是下面
    顺手把「改名之后编号没丢」也钉住.
    """
    for name in (
        "checkpoint_v1.json",
        "checkpoint_v2.json",
        "checkpoint_v3.json",
        "checkpoint_v4.json",
        "checkpoint_v5.json",
    ):
        checkpoint = DEFAULT_CODEC.decode_record(read_fixture(name))
        assert checkpoint.schema_version == SCHEMA_VERSION

    # v5 帧里那个 `run_id` 是**当时的循环编号**, v6 起它叫 `loop_id` —— 升级之后
    # 编号必须原样还在, 而新的 `run_id` (记录层那一行) 补 None (那时候还没有它)
    upgraded = DEFAULT_CODEC.decode_record(read_fixture("checkpoint_v5.json"))
    assert upgraded.loop_id == "run-v5"
    assert upgraded.run_id is None


def test_v4_progress_keeps_only_resume_inputs():
    """进度里不再有观察值 (v3), 且带上了压缩的账 (v4) 与用量/引用 (v5).

    v3 起 state 只剩「接着跑要用的」; v4 又加了 summary / summary_covers, v5 再加
    prompt_ref (读的人靠它把身份说明补回来) 与五个用量分量 (续跑接着累计) —— 这几
    样都是**恢复要用的输入**, 不是「当时答成什么样」的观察值, 所以归 state 而不是
    metadata. 这条用例钉的正是这条分界: 字段可以加, 但加的必须是这一侧的东西.
    """
    checkpoint = DEFAULT_CODEC.decode_record(read_fixture("checkpoint_v4.json"))

    assert set(checkpoint.state.__slots__) == {
        "messages",
        "turn_count",
        "total_tokens",
        "truncation_count",
        "content_parts",
        "suspension",
        "summary",
        "summary_covers",
        "prompt_ref",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
    }


def test_a_v3_snapshot_reads_back_without_a_summary():
    """老快照 (v3, 那时候还没有压缩) 原样读回: 缺的字段填「那时候没有」."""
    checkpoint = DEFAULT_CODEC.decode_record(read_fixture("checkpoint_v3.json"))

    assert checkpoint.state.summary is None
    assert checkpoint.state.summary_covers == 0
    assert checkpoint.state.messages  # 历史照旧读得回来


# ---------------------------------------------------------------------------
# 格式定稿 (快照测试, #63)
# ---------------------------------------------------------------------------


def test_serialized_record_matches_fixture():
    """序列化产物与 fixtures/checkpoint_v6.json 逐字一致 (改格式必须是有意的).

    这个用例红了怎么办: 先确认格式变更是有意为之, 再重新生成 fixture —— 顺手
    也要判断「老快照还读得回来吗」, 读不回来就得加一个迁移函数并把
    SCHEMA_VERSION +1 (见 utils/migrations.py). v1~v5 的 fixture 留着不删: 它们是
    「老快照读得回来」那些用例的样本 (见上面几条).

    它同时钉住 v6 帧的**目标形状**: 身份说明的正文不在 messages 里 (只剩 prompt_ref
    那个引用) —— 那是**造帧的人**摘的 (prompt/ref.py 的 detach_identity), 编解码器
    只负责原样写出来; 以及**两个编号各占一个键** (`loop_id` = 哪次循环执行,
    `run_id` = 记录层哪一行, 可空 —— 见 ticket 22).
    """
    expected = (FIXTURES / "checkpoint_v6.json").read_text(encoding="utf-8")
    checkpoint = DEFAULT_CODEC.decode_record(read_fixture("checkpoint_v6.json"))

    assert DEFAULT_CODEC.dumps(
        DEFAULT_CODEC.encode_record(checkpoint), indent=2
    ) == expected.rstrip("\n")


def test_the_two_ids_roundtrip_separately():
    """两个编号各走各的键, 往返都不串 (`loop_id` 必填, `run_id` 可空).

    ticket 22 之后帧上同时有两个: 循环执行那个 (必填, 老键 `run_id` 改名而来) 与
    记录层那一行 (可空, 指向 `charagent_runs`). 这里断的是**非空**的那一半 ——
    fixture 与快照都是 null, 少了这条就没人守「有值时也写得进读得出」.
    """
    checkpoint = make_checkpoint(loop_id="loop-1", run_id="run-1")

    restored = DEFAULT_CODEC.decode_record(DEFAULT_CODEC.encode_record(checkpoint))

    assert (restored.loop_id, restored.run_id) == ("loop-1", "run-1")


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


# ---------------------------------------------------------------------------
# v5: 身份说明的引用 (写侧剥离) + 用量分解
# ---------------------------------------------------------------------------

IDENTITY_TEXT = "你是商城客服, 只回答问题, 不编造事实."
IDENTITY_REF = prompt_ref("system/v2", IDENTITY_TEXT)


def test_encoding_leaves_the_identity_inlined_without_a_reference():
    """没给引用: 正文原样留在帧里 (v4 及更早的帧, 以及没配身份说明的会话)."""
    messages = [
        {"role": IDENTITY_ROLE, "content": IDENTITY_TEXT},
        {"role": "user", "content": "在吗"},
    ]

    body = DEFAULT_CODEC.encode_body(
        make_checkpoint(state=make_state(messages=messages))
    )

    assert body["state"]["messages"] == messages
    assert body["state"]["prompt_ref"] is None


def test_encoding_writes_the_messages_exactly_as_given():
    """编解码器**不改内容**: 给什么形状就写什么形状.

    剥离身份说明是**造帧的人**的事 (prompt/ref.py 的 detach_identity, 由 loop 调),
    不在这里 —— 三个后端里内存版根本不编码, 把剥离放进编码器会让「帧里存不存正文」
    随后端而变. 这条用例钉住这条边界: 连「带着引用却仍有身份说明」这种形状, 编码器
    也照写不误 (那种帧是调用方自己拼的, 不是它该管的事).
    """
    messages = [
        {"role": IDENTITY_ROLE, "content": IDENTITY_TEXT},
        {"role": "user", "content": "在吗"},
    ]

    body = DEFAULT_CODEC.encode_body(
        make_checkpoint(state=make_state(messages=messages, prompt_ref=IDENTITY_REF))
    )

    assert body["state"]["messages"] == messages
    assert body["state"]["prompt_ref"] == IDENTITY_REF


def test_a_v4_snapshot_reads_back_with_inline_identity_and_no_breakdown():
    """老快照 (v4) 读回: 引用为空 (正文内联着), 五个分量也全是「没有」.

    两个默认值说的是两件不同的事, 别读成一句「老帧不知道」:
    - prompt_ref=None = **这一帧没剥离过**, 正文仍在 messages 那边 —— 老帧仍然完全
      自描述, 读回来一个字都不用补;
    - 五个分量为 None = **那时候还没有这几个字段**. 填 0 会把「没有」说成「确实是
      零」, 而成本归因里这两句话的结论相反.
    """
    checkpoint = DEFAULT_CODEC.decode_record(read_fixture("checkpoint_v4.json"))

    assert checkpoint.schema_version == SCHEMA_VERSION  # 读回来已是当前版本
    assert checkpoint.state.prompt_ref is None
    assert (
        checkpoint.state.input_tokens,
        checkpoint.state.output_tokens,
        checkpoint.state.reasoning_tokens,
        checkpoint.state.cache_hit_tokens,
        checkpoint.state.cache_miss_tokens,
    ) == (None, None, None, None, None)


def test_usage_breakdown_roundtrips_including_the_nulls():
    """五个分量往返保真 —— 尤其要保住 None (「上游没上报」不能变成 0)."""
    state = make_state(
        input_tokens=120,
        output_tokens=None,
        reasoning_tokens=None,
        cache_hit_tokens=100,
        cache_miss_tokens=20,
    )
    codec = CheckpointCodec()

    decoded_state, _ = codec.decode_body(
        codec.loads(codec.dumps(codec.encode_body(make_checkpoint(state=state)))),
        schema_version=SCHEMA_VERSION,
    )

    assert decoded_state.input_tokens == 120
    assert decoded_state.output_tokens is None
    assert decoded_state.reasoning_tokens is None
    assert decoded_state.cache_hit_tokens == 100
    assert decoded_state.cache_miss_tokens == 20


def test_a_prompt_ref_missing_a_key_is_rejected():
    """引用少一个键 = 读的人找不回正文: 报错, 不用默认值糊过去."""
    record = read_fixture("checkpoint_v5.json")
    record["state"]["prompt_ref"] = {REF_NAME_KEY: "system/v2"}  # 少了 sha256

    with pytest.raises(CheckpointSerializationError) as excinfo:
        DEFAULT_CODEC.decode_record(record)

    assert REF_SHA_KEY in str(excinfo.value)


def test_a_prompt_ref_of_the_wrong_type_is_rejected():
    """引用不是字典 (数据被改坏了): 也报错, 不猜."""
    record = read_fixture("checkpoint_v5.json")
    record["state"]["prompt_ref"] = "system/v2"

    with pytest.raises(CheckpointSerializationError) as excinfo:
        DEFAULT_CODEC.decode_record(record)

    assert "prompt_ref" in str(excinfo.value)
