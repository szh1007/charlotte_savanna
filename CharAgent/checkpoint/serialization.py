"""序列化协议: 把快照变成能存起来的文本, 再原样读回来 (difficulties #5).

一句话理解: 快照要放进存储里, 而存储只认「一串文字」或「一张只有文字和数字的
表」. 可存档里的东西五花八门 —— 有时间 (datetime), 有嵌套的字典, 将来还可能
塞进自定义的小对象. 本文件干两件事:

1. **出门 (编码)**: 把存档翻译成 JSON 认识的样子. JSON 不认识的类型 (比如
   datetime) 就打一个「标签」, 写成:

       {"__charagent_type__": "datetime", "value": "2026-09-13T10:00:00+00:00"}

   标签就像**行李牌**: 说明「这个盒子里装的其实是一台时间, 不是普通字典」.
2. **进门 (解码)**: 反过来按行李牌认领, 把 datetime 原样变回来.

为什么不用 pickle (设计文档 §3 的选型对照): pickle 几乎什么都能装, 但它连
「Python 内部结构」一起存 —— 换个 Python 版本可能就读不回来; 更糟的是, 加载
别人给的 pickle 等于执行别人的代码 (安全风险). JSON 只认字面数据, 慢一点、要
自己写翻译, 但安全、跨语言、看得懂. 代价是「类型会丢」, 所以才需要行李牌.

**记录主体 (body)** 是本文件的核心概念: `{"state": 进度, "metadata": 观察值}`.
两种存储都先把主体拼出来, 再走同一套「拆行李牌 → 按版本翻译 → 填进对象」:

- **整条记录** (Redis 用): 身份字段平铺在顶层, 主体作为 `state` / `metadata` 两块
  —— `encode_record` / `decode_record`
- **两块分开存** (Postgres 用): 身份字段各占一列, `state` 与 `metadata` 各占一个
  JSON 列 —— 调用方自己拼主体, 用 `encode_body` / `decode_body`

读一份快照的顺序 (顺序本身就是设计的一部分):
    读文本 → 拆行李牌 (decode_payload) → 按版本翻译 (migrations) → 填进对象
先拆行李牌再翻译版本, 是因为翻译函数面对的是 Python 里的值 (datetime 已经变回
来了), 写起来直白; 反过来先翻译, 就得在老结构里手动认标签, 更绕.

与 migrations.py 的分工: 那边管「结构随版本怎么变」, 这边管「值怎么进出行李箱」.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from CharAgent.checkpoint.utils.errors import (
    CheckpointConfigError,
    CheckpointSerializationError,
)
from CharAgent.checkpoint.utils.fields import (
    read_float,
    read_int,
    read_optional_str,
    read_str,
    read_str_list,
)
from CharAgent.checkpoint.utils.migrations import migrate_body
from CharAgent.checkpoint.utils.types import (
    SCHEMA_VERSION,
    Checkpoint,
    CheckpointMetadata,
    CheckpointSource,
    CheckpointState,
    Suspension,
)
from CharAgent.model.utils.types import ModelMessage, ModelToolCall

# 行李牌上的两个键名; 一个字典**恰好**是这两个键时才被当成行李牌
# (为什么要求「恰好」: 业务数据里也可能正好有个同名字段,
# 要求「一个不多一个不少」能避免把正常数据误认成标签 —— 见 decode_payload)
TAG_KEY = "__charagent_type__"
VALUE_KEY = "value"


@dataclass(frozen=True, slots=True)
class TypeCodec:
    """一种自定义类型的翻译器: 出门 encode, 进门 decode.

    attributes:
        name: 行李牌上写的名字 (如 "datetime"), 必须唯一且稳定 —— 它是存储里
            老数据的契约, 改名等于让老快照读不出来.
        encode: 值 -> JSON 认识的东西 (标签里的 payload 部分).
        decode: 标签里的 payload -> 值.
    """

    name: str
    encode: Callable[[Any], Any]
    decode: Callable[[Any], Any]


def _encode_datetime(value: datetime) -> str:
    """datetime -> ISO 8601 文本 (带时区的会带上 +08:00 这样的偏移)."""
    return value.isoformat()


def _decode_datetime(value: Any) -> datetime:
    """ISO 8601 文本 -> datetime (认不出来就是数据坏了, 给可读错误)."""
    if not isinstance(value, str):
        raise CheckpointSerializationError(
            f"datetime 标签里应当是 ISO 8601 文本, 实际为 {value!r}"
        )
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise CheckpointSerializationError(
            f"datetime 标签里的文本不是合法 ISO 8601 时间: {value!r}"
        ) from exc


# 出厂自带的翻译器: 只管 datetime —— 它是「工具结果里最常见的时间」(#5 明文点
# 名的那一类). 其余类型用多少加多少, 见 CheckpointCodec.register_type.
DEFAULT_TYPES: dict[type, TypeCodec] = {
    datetime: TypeCodec(
        name="datetime", encode=_encode_datetime, decode=_decode_datetime
    ),
}

# JSON 原生就认识的类型 (这些直接原样通过, 不需要行李牌)
_JSON_SCALARS = (str, bool, int, float)


class CheckpointCodec:
    """快照的 JSON 编解码器: 标签机制 + 版本迁移 + 自定义类型注册 (#5).

    一个实例 = 一套翻译规则. 存储实现默认用模块末尾的 DEFAULT_CODEC; 测试想要
    干净的规则表, 自己 new 一个注入即可 (注入缝).
    """

    def __init__(self, *, types: Mapping[type, TypeCodec] | None = None) -> None:
        """
        Args:
            types: 额外注册的类型翻译器 (键是 Python 类型), 在出厂自带的基础上
                追加.
        """
        self._types: dict[type, TypeCodec] = dict(DEFAULT_TYPES)
        if types:
            for python_type, codec in types.items():
                self.register_type(
                    python_type,
                    name=codec.name,
                    encode=codec.encode,
                    decode=codec.decode,
                )

    # ------------------------------------------------------------------
    # 类型注册
    # ------------------------------------------------------------------

    def register_type(
        self,
        python_type: type,
        *,
        name: str,
        encode: Callable[[Any], Any],
        decode: Callable[[Any], Any],
    ) -> None:
        """注册一种类型的翻译器 (遇上装不进 JSON 的自定义对象时用).

        例子 (把 Decimal 存进快照)::

            codec.register_type(
                Decimal,
                name="decimal",
                encode=str,          # Decimal -> "128.00"
                decode=Decimal,      # "128.00" -> Decimal
            )

        Args:
            python_type: 要翻译的 Python 类型.
            name: 行李牌上的名字 (存储里认它, 定了就别改).
            encode / decode: 出门 / 进门的翻译函数.

        Raises:
            CheckpointConfigError: 名字为空, 或该名字已被别的类型占用.
        """
        if not name:
            raise CheckpointConfigError("类型翻译器的名字不能为空")
        for existing_type, existing in self._types.items():
            if existing.name == name and existing_type is not python_type:
                raise CheckpointConfigError(
                    f"名字 {name!r} 已被 {existing_type.__name__} 占用, "
                    f"不能再给 {python_type.__name__} (行李牌名字在存储里必须唯一)"
                )
        self._types[python_type] = TypeCodec(name=name, encode=encode, decode=decode)

    def _codec_by_name(self, name: str) -> TypeCodec | None:
        """按行李牌名字找回翻译器 (解码时用)."""
        for codec in self._types.values():
            if codec.name == name:
                return codec
        return None

    # ------------------------------------------------------------------
    # 结构遍历 (标签机制的底座)
    # ------------------------------------------------------------------

    def encode_payload(self, value: Any) -> Any:
        """把任意嵌套结构翻译成「只有 JSON 原生类型」的样子 (加行李牌).

        Args:
            value: 待编码的值 (dict / list / 标量 / 已注册类型).

        Returns:
            Any: 只含 str / int / float / bool / None / dict / list 的结构.

        Raises:
            CheckpointSerializationError: 遇到没注册翻译器的类型, 或字典的键不是
                字符串 (JSON 的键只能是文本).
        """
        if isinstance(value, dict):
            encoded: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CheckpointSerializationError(
                        f"字典的键只能是字符串 (JSON 的规矩), 实际为 "
                        f"{type(key).__name__}: {key!r}"
                    )
                encoded[key] = self.encode_payload(item)
            return encoded
        if isinstance(value, list | tuple):
            return [self.encode_payload(item) for item in value]
        if value is None or isinstance(value, _JSON_SCALARS):
            return value
        codec = self._types.get(type(value))
        if codec is None:
            raise CheckpointSerializationError(
                f"{type(value).__name__} 类型装不进 JSON, 也没注册翻译器: "
                f"{value!r}; 可以 codec.register_type({type(value).__name__}, "
                f"name=..., encode=..., decode=...) 教它怎么翻译"
            )
        return {
            TAG_KEY: codec.name,
            VALUE_KEY: self.encode_payload(codec.encode(value)),
        }

    def decode_payload(self, value: Any) -> Any:
        """把编码过的结构还原回来 (按行李牌认领).

        「恰好两个键 (TAG_KEY + VALUE_KEY)」的字典才被当成行李牌 —— 业务数据里
        如果有别的字典也带同名字段, 只要键的数量不对, 就当普通数据原样读. 这条
        规则让标签机制不必给自己留转义符.

        Args:
            value: 从存储里读出来、还没解码的结构.

        Returns:
            Any: 还原后的结构 (datetime 之类的值回到原类型).

        Raises:
            CheckpointSerializationError: 行李牌上的名字没有对应的翻译器 (数据是
                用别的规则写的, 猜着读只会读出错的东西).
        """
        if isinstance(value, dict):
            if set(value) == {TAG_KEY, VALUE_KEY}:
                name = value[TAG_KEY]
                codec = self._codec_by_name(name) if isinstance(name, str) else None
                if codec is None:
                    raise CheckpointSerializationError(
                        f"快照里有不认识的标签 {name!r}, 本代码没有对应的翻译器"
                    )
                return codec.decode(self.decode_payload(value[VALUE_KEY]))
            return {key: self.decode_payload(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.decode_payload(item) for item in value]
        return value

    # ------------------------------------------------------------------
    # 记录主体: state + metadata (两种存储共用这一层)
    # ------------------------------------------------------------------

    def encode_body(self, checkpoint: Checkpoint) -> dict[str, Any]:
        """存档 -> 主体字典 `{"state": ..., "metadata": ...}` (已加行李牌).

        Postgres 用它 (两块各进一个 JSON 列); Redis 的整条记录也复用它.
        """
        return self.encode_payload(self._body_payload(checkpoint))

    def decode_body(
        self, payload: Any, *, schema_version: int
    ) -> tuple[CheckpointState, CheckpointMetadata]:
        """主体 -> (进度, 观察值), 中间按记录里那个版本号翻译.

        Args:
            payload: 主体 `{"state": ..., "metadata": ...}` (里面的行李牌还没拆).
            schema_version: 这份记录当初写下的格式版本 (Postgres 里它是单独一列).

        Returns:
            tuple: (CheckpointState, CheckpointMetadata) —— 已升级到当前版本,
            老版本缺的字段填默认值.

        Raises:
            CheckpointSerializationError: 主体不是字典, 或字段类型不对.
        """
        if not isinstance(payload, dict):
            raise CheckpointSerializationError(
                f"存档主体应当是字典 (state + metadata), 实际为 "
                f"{type(payload).__name__}"
            )
        decoded = self.decode_payload(payload)
        return self._build_body(decoded, schema_version=schema_version)

    # ------------------------------------------------------------------
    # 整条记录 (Redis 用: 没有表结构, 一个键里塞整份)
    # ------------------------------------------------------------------

    def encode_record(self, checkpoint: Checkpoint) -> dict[str, Any]:
        """整条快照 -> 可写成 JSON 文本的字典 (Redis 的流条目 / 键值)."""
        return self.encode_payload(
            {
                "schema_version": checkpoint.schema_version,
                "checkpoint_id": checkpoint.checkpoint_id,
                "thread_id": checkpoint.thread_id,
                "run_id": checkpoint.run_id,
                "turn_number": checkpoint.turn_number,
                "parent_id": checkpoint.parent_id,
                "created_at": checkpoint.created_at,
                **self._body_payload(checkpoint),
            }
        )

    def decode_record(self, payload: Any) -> Checkpoint:
        """JSON 文本解出来的字典 -> Checkpoint (含按版本翻译).

        Args:
            payload: loads() 之后、还没解码的结构.

        Returns:
            Checkpoint: 记录对象; schema_version 恒为当前版本 (老簿已升级).

        Raises:
            CheckpointSerializationError: 顶层不是字典 / 缺版本号 / 字段类型不对.
        """
        if not isinstance(payload, dict):
            raise CheckpointSerializationError(
                f"快照文本的顶层应当是字典, 实际为 {type(payload).__name__}"
            )
        version = payload.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise CheckpointSerializationError(
                f"快照缺少整数 schema_version, 实际为 {version!r}"
            )
        decoded = self.decode_payload(payload)
        state, metadata = self._build_body(decoded, schema_version=version)
        # 读取路径不走 Checkpoint.create(): 编号与时刻来自存储, 不该在这里被重新
        # 生成; 字段类型上面已经逐个查过, 直接建对象
        return Checkpoint(
            checkpoint_id=read_str(decoded, "checkpoint_id"),
            thread_id=read_str(decoded, "thread_id"),
            run_id=read_str(decoded, "run_id"),
            turn_number=read_int(decoded, "turn_number"),
            state=state,
            metadata=metadata,
            parent_id=read_optional_str(decoded, "parent_id"),
            created_at=self._read_created_at(decoded.get("created_at")),
            schema_version=SCHEMA_VERSION,
        )

    # ------------------------------------------------------------------
    # JSON 文本
    # ------------------------------------------------------------------

    def dumps(self, payload: Any, *, indent: int | None = None) -> str:
        """结构 -> JSON 文本 (进存储的那串字符).

        Args:
            payload: 已经译好的结构 (encode_* 的产物).
            indent: 缩进层数; None 输出紧凑一行 (存储用), 传 2 输出便于人读的
                多行 (文档 / 快照对比用) —— 两种写法是同一份数据.

        Raises:
            CheckpointSerializationError: 有东西写不成 JSON 文本 (含 NaN /
                Infinity: 它们不是合法 JSON, 宁可报错也不写出别人读不懂的字).
        """
        try:
            return json.dumps(
                payload, ensure_ascii=False, indent=indent, allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise CheckpointSerializationError(
                f"快照写成 JSON 文本失败: {exc}"
            ) from exc

    def loads(self, text: str) -> Any:
        """JSON 文本 -> 结构 (拆行李牌之前的原始样子).

        Raises:
            CheckpointSerializationError: 文本不是合法 JSON.
        """
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise CheckpointSerializationError(f"快照文本不是合法 JSON: {exc}") from exc

    # ------------------------------------------------------------------
    # 内部: 主体的进出
    # ------------------------------------------------------------------

    def _body_payload(self, checkpoint: Checkpoint) -> dict[str, Any]:
        """Checkpoint -> 主体结构 (**还没**加行李牌, 给编码用).

        为什么分两层: 整条记录 (encode_record) 要一次编码到底, 中途先编码一次
        主体再跟着整条过第二遍会白走一遍大树; 于是「结构长什么样」单独一个方法.
        """
        return {
            "state": {
                "messages": checkpoint.state.messages,
                "turn_count": checkpoint.state.turn_count,
                "total_tokens": checkpoint.state.total_tokens,
                "truncation_count": checkpoint.state.truncation_count,
                "content_parts": checkpoint.state.content_parts,
                "suspension": self._suspension_payload(checkpoint.state.suspension),
            },
            "metadata": {
                "source": checkpoint.metadata.source.value,
                "turn_tokens": checkpoint.metadata.turn_tokens,
                "turn_elapsed_ms": checkpoint.metadata.turn_elapsed_ms,
                "tool_names": checkpoint.metadata.tool_names,
                "content": checkpoint.metadata.content,
                "finish_reason": checkpoint.metadata.finish_reason,
                "outcome": checkpoint.metadata.outcome,
            },
        }

    @staticmethod
    def _suspension_payload(suspension: Suspension | None) -> dict[str, Any] | None:
        """挂起点 -> 结构字典; 没挂起就是 None.

        待办的工具调用写成扁平三字段 (id / name / arguments), 不照抄上游 wire 的
        嵌套样子 —— 快照有自己的格式, 不必跟着上游协议走 (上游改字段名不该影响
        老快照). 老快照缺字段时的兜底由 fields.py 的默认值负责.
        """
        if suspension is None:
            return None
        return {
            "reason": suspension.reason,
            "pending": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in suspension.pending
            ],
            "approval_id": suspension.approval_id,
        }

    def _build_body(
        self, payload: dict[str, Any], *, schema_version: int
    ) -> tuple[CheckpointState, CheckpointMetadata]:
        """主体字典 -> (进度, 观察值): 先按版本翻译, 再逐字段取值."""
        upgraded = migrate_body(payload, from_version=schema_version)
        return (
            self._build_state(upgraded["state"]),
            self._build_metadata(upgraded.get("metadata")),
        )

    def _build_state(self, payload: Any) -> CheckpointState:
        """进度字典 -> CheckpointState."""
        if not isinstance(payload, dict):
            raise CheckpointSerializationError(
                f"state 应当是字典, 实际为 {type(payload).__name__}: {payload!r}"
            )
        return CheckpointState(
            messages=self._read_messages(payload),
            turn_count=read_int(payload, "turn_count"),
            total_tokens=read_int(payload, "total_tokens"),
            truncation_count=read_int(payload, "truncation_count"),
            content_parts=read_str_list(payload, "content_parts"),
            suspension=self._read_suspension(payload.get("suspension")),
        )

    def _build_metadata(self, payload: Any) -> CheckpointMetadata:
        """观察值字典 -> CheckpointMetadata (缺失 / None 全部按默认值填).

        老记录没有 metadata 这一块 (v2 及更早), 这里给的就是「那时候没记这些」
        的诚实默认 —— 版本翻译已经把能搬的搬过来了 (见 migrations._v2_to_v3).
        """
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise CheckpointSerializationError(
                f"metadata 应当是字典, 实际为 {type(payload).__name__}: {payload!r}"
            )
        return CheckpointMetadata(
            source=self._read_source(payload),
            turn_tokens=read_int(payload, "turn_tokens"),
            turn_elapsed_ms=read_float(payload, "turn_elapsed_ms"),
            tool_names=read_str_list(payload, "tool_names"),
            content=read_optional_str(payload, "content"),
            finish_reason=read_optional_str(payload, "finish_reason"),
            outcome=read_optional_str(payload, "outcome"),
        )

    @staticmethod
    def _read_source(payload: dict[str, Any]) -> CheckpointSource:
        """取「这一帧怎么来的」: 必须是已知取值 (见 CheckpointSource)."""
        raw = payload.get("source", CheckpointSource.LOOP.value)
        try:
            return CheckpointSource(raw)
        except ValueError as exc:
            known = ", ".join(item.value for item in CheckpointSource)
            raise CheckpointSerializationError(
                f"metadata.source 取值不认识: {raw!r}; 可选: {known}"
            ) from exc

    @staticmethod
    def _read_messages(payload: dict[str, Any]) -> list[ModelMessage]:
        """取消息历史 (必填: 没有历史就谈不上「接着跑」)."""
        messages = payload.get("messages")
        if not isinstance(messages, list) or any(
            not isinstance(item, dict) for item in messages
        ):
            raise CheckpointSerializationError(
                f"快照的 messages 应当是消息字典的列表, 实际为 {messages!r}"
            )
        return list(messages)

    @staticmethod
    def _read_suspension(payload: Any) -> Suspension | None:
        """取挂起点 (缺失 / null 表示没卡住)."""
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise CheckpointSerializationError(
                f"快照的 suspension 应当是字典或 null, 实际为 "
                f"{type(payload).__name__}: {payload!r}"
            )
        raw_pending = payload.get("pending", [])
        if not isinstance(raw_pending, list):
            raise CheckpointSerializationError(
                f"挂起点的 pending 应当是列表, 实际为 {raw_pending!r}"
            )
        pending: list[ModelToolCall] = []
        for item in raw_pending:
            if not isinstance(item, dict):
                raise CheckpointSerializationError(
                    f"挂起点的 pending 里应当都是字典, 实际为 {item!r}"
                )
            pending.append(
                ModelToolCall(
                    id=read_str(item, "id"),
                    name=read_str(item, "name"),
                    # 参数原样保留 (畸形 JSON 是自纠错路径的信号, 快照不替它修)
                    arguments=item.get("arguments")
                    if isinstance(item.get("arguments"), str)
                    else "",
                )
            )
        return Suspension(
            reason=read_str(payload, "reason"),
            pending=pending,
            approval_id=read_optional_str(payload, "approval_id"),
        )

    @staticmethod
    def _read_created_at(value: Any) -> datetime:
        """取创建时刻: v2 起是打了标签的 datetime, v1 老格式写的是裸 ISO 文本.

        两种都认 (向前兼容): 老版本没走标签机制, 直接写了文本; 读的时候多认一种
        写法, 老快照就还能读回来.
        """
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError as exc:
                raise CheckpointSerializationError(
                    f"created_at 不是合法 ISO 8601 时间: {value!r}"
                ) from exc
        raise CheckpointSerializationError(
            f"created_at 应当是时间或 ISO 8601 文本, 实际为 {value!r}"
        )


DEFAULT_CODEC = CheckpointCodec()
"""出厂编码器: 三个存储实现默认用它 (想加自定义类型就给它 register_type 登记).

注意它是一个**共享实例**: 往它注册类型等于全局生效. 只想在某个测试里临时加类型
时, 自己 new 一个 CheckpointCodec 注入给 saver, 别污染全局.
"""
