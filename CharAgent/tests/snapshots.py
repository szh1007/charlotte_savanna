"""快照测试工具 (issue 09 / #63): 把「不该变的形状」钉成文件, 变了就红.

**快照测什么**: 不是「答案好不好」(那是 P2 评估体系的事), 而是「结构还是不是
原来那样」—— checkpoint 序列化格式、SSE 事件序列的逐字段载荷、真实样本回放
出的响应. 这些形状一旦被无意改动, 下游 (前端渲染 / 存储兼容 / 老快照读回)
会静默出错, 快照是把这类改动变成一条红色用例.

**用法**::

    assert_json_snapshot("event_stream_tool_path", project_events(collector.events))
    assert_text_snapshot("checkpoint_frame", DEFAULT_CODEC.dumps(record, indent=2))

**首次运行**: 快照不存在 → 写文件并让用例失败 (提示复查内容后重跑) —— 不静默
通过, 因为「新生成的快照」等于没有防线, 得有人看过一眼.

**内容确实该变**: `UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py` 覆盖,
覆盖结果须一并 review (快照改动是「有意的接口变更」的书面记录).

**每次都不一样的东西不许进快照**: 时间 / 随机编号 / 耗时这类值由调用方先用
`project_events` 或 `normalize_checkpoint_record` 归一 (换成占位符), 否则快照
用例会变成偶发 —— 那比没有快照更糟.

本文件放工具与归一函数; 用例在 test_snapshots.py (工具自测 + 事件序列快照 +
checkpoint 帧快照) 与 test_replay_contract.py (真实样本回放 + 双适配器契约).
"""

from __future__ import annotations

import difflib
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from CharAgent.stream.utils.types import StreamEvent

# 快照目录 (随仓库提交) 与覆盖开关的环境变量
SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "snapshots"
UPDATE_ENV = "UPDATE_SNAPSHOTS"

# 耗时类字段的占位符: 事件与快照里保留「这里有耗时」的事实, 但不比具体毫秒
MS_PLACEHOLDER = "<ms>"
MS_FIELDS = ("duration_ms", "elapsed_ms", "turn_elapsed_ms")
# 每次运行都不同的身份字段 (快照里换成占位符)
ID_PLACEHOLDER = "<id>"
TIMESTAMP_PLACEHOLDER = "<ts>"


def assert_json_snapshot(name: str, data: Any) -> None:
    """比对 JSON 快照 (不存在则生成并失败; 内容变了则报差异).

    Args:
        name: 快照名 (落成 `fixtures/snapshots/{name}.json`).
        data: 可 JSON 序列化的结构 (时间 / 编号请先归一).
    """
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    assert_text_snapshot(name, text)


def assert_text_snapshot(name: str, text: str) -> None:
    """比对文本快照 (JSON 之外的版式, 比如序列化器输出的紧凑 / 多行文本).

    Raises:
        pytest.fail: 快照不存在 (已生成) / 内容不符 (给出逐行差异).
    """
    path = SNAPSHOT_DIR / f"{name}.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        pytest.fail(
            f"快照不存在, 已生成 {path}: 复查内容后重新运行 (形状确实如此就"
            f"留着提交, 不对就改实现)"
        )
    expected = path.read_text(encoding="utf-8")
    if expected == text:
        return
    if _updating():
        path.write_text(text, encoding="utf-8")
        return
    pytest.fail(
        f"快照 {name} 与当前输出不一致 (改了形状? 确认无误后 "
        f"{UPDATE_ENV}=1 重跑覆盖):\n{_diff(expected, text)}"
    )


def project_events(events: Iterable[StreamEvent]) -> list[dict[str, Any]]:
    """事件序列 → 可快照的投影 (耗时字段换成占位符).

    投影保留 SSE 契约的全部字段 (type / seq / 载荷), 只把**每次运行都不同**的
    毫秒数换掉 —— 断的是「序列与载荷形状」, 不是 CPU 有多快.

    Args:
        events: 收集到的事件 (EventCollector.events).

    Returns:
        list: 逐事件的 `to_dict()` (耗时已归一).
    """
    return [_normalize_volatile(event.to_dict()) for event in events]


def normalize_checkpoint_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """checkpoint 记录 → 可快照的投影 (编号 / 时刻 / 耗时换成占位符).

    输入是 `CheckpointCodec.encode_record()` 的结果 (Redis 存的就是它, 也是
    `dumps()` 的输入) —— 于是「快照里长什么样」与「存储里长什么样」是同一份.
    loop 自己生成的编号 (uuid4) 与时刻 (now) 每次运行都不同, 属必须归一的部分;
    `parent_id` 只在**有父帧**时换占位符 —— 快照断的是「这一帧有没有上一帧」,
    不是那串 uuid 具体是多少.
    """
    payload = _normalize_volatile(dict(record))
    payload["checkpoint_id"] = ID_PLACEHOLDER
    payload["created_at"] = TIMESTAMP_PLACEHOLDER
    if payload.get("parent_id") is not None:
        payload["parent_id"] = ID_PLACEHOLDER
    return payload


def _normalize_volatile(value: Any) -> Any:
    """递归把耗时字段换成占位符 (只换数值, 保留「这个字段存在」的事实)."""
    if isinstance(value, dict):
        return {
            key: (
                MS_PLACEHOLDER
                if key in MS_FIELDS and isinstance(item, int | float)
                else _normalize_volatile(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_volatile(item) for item in value]
    return value


def _updating() -> bool:
    """是否处于「覆盖快照」模式 (UPDATE_SNAPSHOTS=1); 其余非空值也算开.

    空串与 0 / false 视为关: 环境变量被别的工具设成 "0" 时不该意外覆盖.
    """
    value = os.environ.get(UPDATE_ENV, "").strip().lower()
    return value not in ("", "0", "false", "no")


def _diff(expected: str, actual: str) -> str:
    """逐行差异 (最多 40 行; 快照整份打出来反而看不清哪里变了)."""
    lines = list(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile="快照 (已提交)",
            tofile="本次输出",
            lineterm="",
        )
    )
    if len(lines) > 40:
        lines = [*lines[:40], f"... (其余 {len(lines) - 40} 行省略)"]
    return "\n".join(lines)
