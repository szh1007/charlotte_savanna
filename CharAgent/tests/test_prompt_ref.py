"""prompt 包的引用机制 (`CharAgent/prompt/ref.py`, 帧 v5).

三条主线:
1. **引用说得清**: 名字 + 渲染后正文的哈希 —— 哈希算的是**渲染后**的那份, 不是盘上
   的模板 (`${model_name}` 换一个值就是另一份正文)
2. **还原搬得回来**: 按引用取回正文补回历史; 补完把引用**重算**成与补回的那份相符
   (帧里那个引用是当初算的, 而正文可能已经不是它了 —— 不重算的话, 下一次落盘时
   sha 校验会把一次版本切换变成「这段会话再也存不下快照」)
3. **两种情况分得清**: 正文与引用对不上 -> 用当前文件 + 一条 warning (不阻断);
   文件干脆不在 -> 抛 (编不出正文还继续, 等于让这段会话带着别人的身份说话)

造提示词一律写临时目录 (`prompt_dir=tmp_path`), 不往真 `templates/` 里丢东西.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from CharAgent.model.utils.types import ModelMessage
from CharAgent.prompt import (
    PromptNotFoundError,
    PromptRefMismatchError,
    deref_prompt,
    detach_identity,
    identity_message,
    load_prompt,
    prompt_ref,
    ref_name,
    restore_identity,
)

# 哈希算法与两个键名是**引用格式的内部件** (只给帧的编解码器用), 门面不上浮它们
# (与 `TEMPLATE_DIR` / `ENV_MODEL_NAME` 同一条口径), 所以从子模块取
from CharAgent.prompt.ref import sha256_text

# 本模块要收日志的那个 logger 名 (与 ref.py 里 `getLogger` 的参数一致)
PROMPT_LOGGER = "charagent.prompt"


def _write(root: Path, name: str, text: str) -> None:
    """往临时目录写一份提示词 (名字里带 `/` 时按目录层级建, 如 `system/v2`)."""
    path = root / f"{name}.prompt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_reference_hashes_the_rendered_text(tmp_path: Path) -> None:
    """哈希算的是**渲染后**的正文, 不是盘上的模板."""
    _write(tmp_path, "system", "你的底层模型是 ${model_name}.")

    text = load_prompt("system", prompt_dir=tmp_path, model_name="m-1")
    ref = prompt_ref("system", text)

    assert ref == {"name": "system", "sha256": sha256_text("你的底层模型是 m-1.")}
    assert ref["sha256"] != sha256_text("你的底层模型是 ${model_name}."), (
        "算成模板的哈希了 —— 那样换模型名就发现不了正文变了"
    )


def test_ref_name_reads_the_name_or_none() -> None:
    """名字读得出来; 没有引用时是 None (内联 / 根本没配)."""
    assert ref_name(prompt_ref("system/v2", "正文")) == "system/v2"
    assert ref_name(None) is None


def test_identity_message_is_the_first_system_message() -> None:
    """身份说明那条 wire 消息的形状只有一处定义."""
    assert identity_message("正文") == {"role": "system", "content": "正文"}


def test_deref_returns_the_text_the_reference_was_built_from(tmp_path: Path) -> None:
    """原样往返: 引用 -> 正文 (文件没动过时逐字一致)."""
    _write(tmp_path, "system/v2", "你是客服.")
    ref = prompt_ref("system/v2", load_prompt("system/v2", prompt_dir=tmp_path))

    assert deref_prompt(ref, prompt_dir=tmp_path) == "你是客服."


def test_deref_warns_but_continues_when_the_file_was_edited(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """文件被**就地改过**: 用当前这份继续, 但留一条 warning.

    报错会让一次误编辑变成「所有旧会话打不开」; 一声不吭又会让「当时它看到什么」
    悄悄失真 —— 中间那条路是唯一既不阻断又留痕的.
    """
    _write(tmp_path, "system/v2", "原版正文.")
    ref = prompt_ref("system/v2", "原版正文.")
    _write(tmp_path, "system/v2", "被人改过的正文.")

    with caplog.at_level(logging.WARNING, logger=PROMPT_LOGGER):
        text = deref_prompt(ref, prompt_dir=tmp_path, context="thread=t-1")

    assert text == "被人改过的正文."
    assert len(caplog.records) == 1
    assert "system/v2" in caplog.text
    assert "thread=t-1" in caplog.text, "日志要带上定位上下文"


def test_deref_raises_when_the_prompt_is_gone(tmp_path: Path) -> None:
    """文件不在盘上: 抛 —— 不猜也不退回别的版本."""
    ref = prompt_ref("system/gone", "那段会话用的正文")

    with pytest.raises(PromptNotFoundError):
        deref_prompt(ref, prompt_dir=tmp_path)


def test_restore_prepends_the_identity(tmp_path: Path) -> None:
    """剥离过的历史补回身份说明, 并给出与之相符的引用."""
    _write(tmp_path, "system/v2", "你是客服.")
    ref = prompt_ref("system/v2", "你是客服.")

    messages, restored_ref = restore_identity(
        [{"role": "user", "content": "在吗"}], ref, prompt_dir=tmp_path
    )

    assert messages == [
        {"role": "system", "content": "你是客服."},
        {"role": "user", "content": "在吗"},
    ]
    assert restored_ref == ref


def test_restore_is_idempotent_when_the_identity_is_already_there(
    tmp_path: Path,
) -> None:
    """已经补过就不再补 (水合与收回进度两条路可能都走到这里).

    补两遍会把一份身份说明变成两份 —— 模型会看到两条互相矛盾的人设.
    """
    _write(tmp_path, "system/v2", "你是客服.")
    ref = prompt_ref("system/v2", "你是客服.")
    messages = [
        {"role": "system", "content": "你是客服."},
        {"role": "user", "content": "在吗"},
    ]

    restored, restored_ref = restore_identity(messages, ref, prompt_dir=tmp_path)

    assert restored == messages
    assert restored_ref == ref


def test_restore_resyncs_the_reference_to_what_it_actually_used(
    tmp_path: Path,
) -> None:
    """正文与引用对不上时, 返回的引用指向**实际用的那份**正文.

    这条是「换一版提示词之后接着聊」不炸的前提: 帧里那个老引用说的还是旧正文, 照它
    留在新帧里, 下一次落盘就会被 sha 校验拦下.
    """
    _write(tmp_path, "system/v2", "原版正文.")
    ref = prompt_ref("system/v2", "原版正文.")
    _write(tmp_path, "system/v2", "被人改过的正文.")

    messages, restored_ref = restore_identity(
        [{"role": "user", "content": "在吗"}], ref, prompt_dir=tmp_path
    )

    assert messages[0]["content"] == "被人改过的正文."
    assert restored_ref == prompt_ref("system/v2", "被人改过的正文.")
    assert restored_ref != ref, "老引用必须被换掉, 否则新帧与正文对不上"


def test_restore_does_nothing_without_a_reference(tmp_path: Path) -> None:
    """没有引用 (v4 及更早的帧): 正文内联着, 一个字都不用补."""
    messages = [
        {"role": "system", "content": "内联的身份说明"},
        {"role": "user", "content": "在吗"},
    ]

    restored, restored_ref = restore_identity(messages, None, prompt_dir=tmp_path)

    assert restored is messages
    assert restored_ref is None


def test_detach_removes_the_identity_message_when_the_text_matches() -> None:
    """落帧时摘掉身份说明那条 (正文换成了引用)."""
    messages: list[ModelMessage] = [
        {"role": "system", "content": "你是客服."},
        {"role": "user", "content": "在吗"},
    ]

    detached = detach_identity(messages, prompt_ref("system/v2", "你是客服."))

    assert detached == [{"role": "user", "content": "在吗"}]


def test_detach_leaves_everything_alone_without_a_reference() -> None:
    """没有引用: 一个字节都不动 (那一帧靠正文自描述)."""
    messages: list[ModelMessage] = [
        {"role": "system", "content": "你是客服."},
        {"role": "user", "content": "在吗"},
    ]

    assert detach_identity(messages, None) is messages


def test_detach_is_idempotent_on_an_already_detached_history() -> None:
    """已经摘过的历史再摘一次: 原样返回 (搬帧不会炸)."""
    messages: list[ModelMessage] = [{"role": "user", "content": "在吗"}]

    detached = detach_identity(messages, prompt_ref("system/v2", "你是客服."))

    assert detached is messages


def test_detach_refuses_a_reference_that_does_not_match_the_text() -> None:
    """引用与第 0 条的正文对不上: 报错, 不挑一个凑合用.

    照它存下去, 这段会话从此每一帧都记着一个**错的**身份说明 —— 而读的人只看到
    「名字 + 哈希」, 没有任何线索知道它错了.
    """
    messages: list[ModelMessage] = [
        {"role": "system", "content": "另一份身份说明"},
        {"role": "user", "content": "在吗"},
    ]

    with pytest.raises(PromptRefMismatchError) as caught:
        detach_identity(messages, prompt_ref("system/v2", "你是客服."))

    assert caught.value.name == "system/v2"
    assert caught.value.expected != caught.value.actual
