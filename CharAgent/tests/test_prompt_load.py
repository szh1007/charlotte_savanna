"""prompt 包的加载与渲染 (`CharAgent/prompt/load.py`).

三条主线:
1. **取得到**: `system.prompt` 读得出来, 占位符按传入的值替换
2. **取不到说清楚**: 文件不存在 / 占位符缺值 / 占位符写法不合法, 报错都带够定位
   的信息 (找的是哪个文件、缺的是哪个变量)
3. **错误能被一次兜住**: 两个子类同属一个基类

造边界要自己写模板文件 (仓库里的 system.prompt 只有合法占位符), 但**不往真目录
写** —— templates/ 是产品资产, 测试往里丢一次性的东西会留垃圾. 换成 monkeypatch
改模块级 `TEMPLATE_DIR` 指向临时目录.

「消费方拿到的是这个文件渲染的」那条一致性由 `test_client_session.py` 盯着, 这里
不重复.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from CharAgent.model.client_httpx import chat_model_from_env
from CharAgent.model.utils.config import DEFAULT_MODEL
from CharAgent.prompt import (
    PromptError,
    PromptNotFoundError,
    PromptVariableError,
    load_prompt,
    resolve_model_name,
)
from CharAgent.prompt import load as load_module


def _point_at(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, text: str
) -> None:
    """把 TEMPLATE_DIR 指向临时目录, 并往里写一个模板."""
    (tmp_path / f"{name}.prompt").write_text(text, encoding="utf-8")
    monkeypatch.setattr(load_module, "TEMPLATE_DIR", tmp_path)


def test_loads_the_repository_system_prompt() -> None:
    """仓库里的 system.prompt 读得出来, 且占位符被替换掉."""
    text = load_prompt("system", model_name="my-model")

    assert "my-model" in text
    assert "${model_name}" not in text, "占位符没被替换"
    assert "charlotte" in text, "作者信息在提示词里"


def test_missing_file_reports_the_path_it_looked_for() -> None:
    """文件不存在时, 报错带上**实际去找的路径** —— 只知道自己传错名字不够用."""
    with pytest.raises(PromptNotFoundError) as caught:
        load_prompt("no-such-prompt")

    assert caught.value.name == "no-such-prompt"
    assert caught.value.path.name == "no-such-prompt.prompt"
    assert "no-such-prompt.prompt" in str(caught.value)


def test_missing_variable_says_which_one_and_what_was_given() -> None:
    """占位符缺值时, 报错说清缺哪个、已给了哪些."""
    with pytest.raises(PromptVariableError) as caught:
        load_prompt("system")

    assert caught.value.name == "system"
    assert "model_name" in caught.value.detail
    assert "(一个都没给)" in str(caught.value)


def test_a_prompt_without_placeholders_needs_no_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """没有占位符的提示词不传值也能读 (以后的业务提示词多半是这样)."""
    _point_at(monkeypatch, tmp_path, "plain", "就一句话, 没有占位符.\n")

    assert load_prompt("plain") == "就一句话, 没有占位符.\n"


def test_malformed_placeholder_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """占位符写法不合法也翻译成 PromptVariableError, 不漏出裸 ValueError.

    造这种形状靠 `$` 后跟非标识符字符 (这里 `$5`) —— 提示词里出现价格是常事,
    这正是 `string.Template` 需要被包一层的原因.
    """
    _point_at(monkeypatch, tmp_path, "broken", "价格是 $5 元.\n")

    with pytest.raises(PromptVariableError) as caught:
        load_prompt("broken")

    assert "占位符写法不合法" in caught.value.detail


def test_two_error_kinds_share_one_base() -> None:
    """两个子类都能被 `except PromptError` 兜住 (调用方不必认识每个子类)."""
    assert issubclass(PromptNotFoundError, PromptError)
    assert issubclass(PromptVariableError, PromptError)


def test_model_name_follows_cli_then_env_then_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取值次序: `--model` → `.env` → 默认值."""
    monkeypatch.setenv("DEEPSEEK_MODEL_NAME", "env-model")
    assert resolve_model_name() == "env-model"

    monkeypatch.delenv("DEEPSEEK_MODEL_NAME")
    assert resolve_model_name() == DEFAULT_MODEL

    # 显式给值永远赢过 env (CLI 传的就是它)
    assert resolve_model_name("cli-model") == "cli-model"


def test_model_name_strips_the_provider_prefix() -> None:
    """LangChain 风格的 `provider:model` 前缀要剥掉."""
    assert resolve_model_name("deepseek:deepseek-flash") == "deepseek-flash"
    assert resolve_model_name("deepseek-flash") == "deepseek-flash"


def test_resolution_matches_what_the_adapter_actually_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """与适配器**实际发出去**的模型名一致 —— 直接问适配器要, 不靠人眼对.

    钉住一个真实踩过的坑: 根 .env 写的是 `deepseek:deepseek-flash`, 而两个适配器
    都过 `strip_provider_prefix` 才发出去. 解析里少了这一步, 提示词会说自己叫
    `deepseek:deepseek-flash` —— 一个 API 从没见过的名字, 而这段提示词唯一的职责
    就是照实说明身份.

    直接拿两个函数的返回值对拍 (而不是各写一份期望值): 以后适配器改了剥前缀的
    规则, 这里立刻红.
    """
    monkeypatch.setenv("DEEPSEEK_MODEL_NAME", "deepseek:deepseek-flash")

    model = chat_model_from_env(api_key="sk-not-used")  # 只构造, 不联网

    assert resolve_model_name() == model.model == "deepseek-flash"
