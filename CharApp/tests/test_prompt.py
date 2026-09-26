"""客服提示词: 它在业务目录里, 按版本落盘, 由清单声明默认版本.

提示词是这个助手的立身之本 —— 工具决定它**能**做什么, 提示词决定它**不会**做
什么. 后半句没法用单元测试断言 (那要靠评估集, L4 的事), 但下面三件事可以:

1. **落库方式**: 按 `{名字}/{版本}.prompt` 分目录存 (PLAN §3.3) —— 换一版是加一个
   文件, 不是覆盖旧文件.
2. **版本号从哪来**: 清单文件 (`prompt/manifest.yaml` 的 `default`), 而不是代码里
   的常量. 读不到清单是**启动期错误**, 不静默退回上一版.
3. **该写的写没写**: 禁则, 项目术语, 示例.

为什么钉的是「必须有哪几件事」而不是逐字比对全文: 话术会改, 改话术不该红;
但「不能替买家付款」这类禁则被删掉, 必须红.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import BUYER_ID

from CharAgent.checkpoint import InMemoryCheckpointSaver
from CharAgent.prompt import PromptNotFoundError, load_prompt
from CharAgent.tests.mock_llm import MockLLM, text_response
from CharApp.minimall import service
from CharApp.minimall.config import MinimallConfigError
from CharApp.minimall.service import (
    PROMPT_DIR,
    PROMPT_MANIFEST,
    PROMPT_NAME,
    TENANT_WEB,
    MinimallService,
    build_context,
    resolve_prompt_version,
)

# 当前声明的那一版在盘上的位置 —— 下面好几条用例都要它
CURRENT_VERSION = resolve_prompt_version()
CURRENT_PROMPT = PROMPT_DIR / PROMPT_NAME / f"{CURRENT_VERSION}.prompt"


def system_prompt(version: str | None = None) -> str:
    """按生产那条路读一遍提示词 (读法与装配一致: `{名字}/{版本}`)."""
    name = f"{PROMPT_NAME}/{version or CURRENT_VERSION}"
    return load_prompt(name, prompt_dir=PROMPT_DIR)


# ---------------------------------------------------------------------------
# 落库方式与版本
# ---------------------------------------------------------------------------


def test_the_prompt_lives_in_a_versioned_layout() -> None:
    """提示词按 `{名字}/{版本}.prompt` 落盘, 而且就在**业务自己**的目录下.

    布局带版本的理由是评估: 要比「这一版比上一版好」, 前提是两版都在. 覆盖式布局
    会静默地把上一版弄丢, 那次对比就永远做不成 (PRD §4.8).
    """
    assert CURRENT_PROMPT.is_file()
    assert sorted(path.name for path in PROMPT_DIR.iterdir()) == [
        "manifest.yaml",
        PROMPT_NAME,
    ]
    versions = sorted(path.name for path in (PROMPT_DIR / PROMPT_NAME).iterdir())
    assert f"{CURRENT_VERSION}.prompt" in versions
    assert len(versions) >= 2, "只留一版就没有 A/B 可比了 (L4 要用它)"


def test_the_retired_version_is_still_the_retired_one() -> None:
    """换版是**加一个文件**, 不是就地改旧的 —— v1 至今还是那份只读客服.

    这条守的是「评估结论能归因到具体一版」这件事的地基: 若某一版被人就地改过,
    那它当初的跑分就再也不对应任何东西, 而盘上看不出任何异常.

    v2 同样不删也不改 (issue 37 只加 v3): 它是「prompt 层确认 / 框架级确认」对照的
    素材, L4 要拿它跑 A/B —— 所以那句已经被事实推翻的「不能替买家付款」也**照旧
    留在 v2 里**, 改了它, 那次对照就没有基线了.
    """
    body = system_prompt("v1")

    assert "当前只有查询能力" in body, "v1 是 L1a 的只读版, 不该被改写"
    assert "加购" not in body

    retired = system_prompt("v2")

    assert "不能替买家付款" in retired, "v2 是 L4 的对照素材, 不该被就地改写"
    assert "不能替买家付款" not in system_prompt()


def test_the_manifest_can_be_committed() -> None:
    """清单**没被 .gitignore 挡掉** —— 根 .gitignore 里有一条通用的 `*.yaml`.

    这条守的是一种看不见的失效: 本地一切正常 (那份文件就在盘上), 别人克隆下来却
    一启动就报「读不到提示词清单」—— 而按设计它**不会**静默退回上一版. 靠人记得
    在 .gitignore 里加一条否定规则是不可靠的, 所以这里问一次 git 自己.
    """
    git = shutil.which("git")
    if git is None:  # pragma: no cover - 开发机上都装了 git
        pytest.skip("这个环境没有 git, 跳过这条仓库卫生检查")

    result = subprocess.run(
        [git, "check-ignore", "--quiet", str(PROMPT_MANIFEST)],
        cwd=PROMPT_MANIFEST.parents[3],  # 仓库根
        capture_output=True,
    )

    # check-ignore 用退出码表态: 0 = 被忽略, 1 = 没被忽略
    assert result.returncode == 1, (
        f"{PROMPT_MANIFEST.name} 被 .gitignore 挡掉了: 清单进不了仓库, "
        f"别人克隆下来助手起不来 (见根 .gitignore 里那条 `!` 例外)"
    )


def test_the_manifest_is_the_only_source_of_the_version() -> None:
    """声明用哪一版, 装配读到的就是哪一版 —— 声明与落盘对不上时当场炸.

    若读不到就悄悄退回上一版, 一次「v2 的跑分」可能其实是 v1 的成绩, 而且没有
    任何地方会报警. `PromptNotFoundError` 与 `MinimallConfigError` 都是启动期
    错误, 两个入口会报一句人话就退出.
    """
    assert system_prompt() == CURRENT_PROMPT.read_text(encoding="utf-8")

    with pytest.raises(PromptNotFoundError):
        load_prompt(f"{PROMPT_NAME}/v999", prompt_dir=PROMPT_DIR)


def test_the_declared_prompt_lets_the_model_change_data() -> None:
    """声明的那一版必须是**能改数据**的那一版.

    这条钉的是「版本号交给清单」之后的那个新风险: 把 `default` 改回只读版是一个
    字符的改动, 而后果是 8 个写工具当场全废 (模型会照提示词一律拒绝) —— 别的
    用例一条都不会红, 因为它们读的就是"声明的那一版".

    判据取正文里有没有那几个动作, **不是**写死 `== "v2"`: 版本号该由清单说了算,
    测试再去钉一个具体的版本名, 就把清单的一处改动变成了两处.
    """
    body = system_prompt()

    for must_have in ("加购", "下单", "申请退款"):
        assert must_have in body, (
            f"声明的那一版没有写「{must_have}」, 像是一份只读提示词"
        )


def test_a_missing_manifest_is_a_startup_error(tmp_path: Path) -> None:
    """清单不在 → 报错, 不猜, 不退回. 静默的后果见上一条."""
    with pytest.raises(MinimallConfigError) as excinfo:
        resolve_prompt_version(tmp_path / "manifest.yaml")

    assert "manifest.yaml" in str(excinfo.value)


@pytest.mark.parametrize(
    "content",
    [
        "",  # 空文件
        "# 只有一行注释\n",  # 解出来是 None
        "default: 2\n",  # 版本号写成了数字 (文件名是 2.prompt? 说不清)
        "- v1\n",  # 根本不是映射
        "default: [v2, v3]\n",  # 一个字段两个值, 没人知道取哪个
    ],
)
def test_a_manifest_without_a_usable_default_is_a_startup_error(
    tmp_path: Path, content: str
) -> None:
    """清单在, 但里面没有一句能用的「默认用哪一版」→ 同样是启动期错误."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(content, encoding="utf-8")

    with pytest.raises(MinimallConfigError):
        resolve_prompt_version(manifest)


def test_a_default_pointing_at_a_missing_version_is_a_startup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清单说用 v9, 而盘上没有 `v9.prompt` → 启动期报错 (不静默退回 v2).

    这条要的是**早报**: 清单与落盘对不上时, 服务进程就该起不来 —— 而不是等第一个
    买家来提问、框架去读文件时才发现. 所以判据落在 `resolve_prompt_version` 上
    (那句启动期预读读的就是它), 而不是 `load_prompt`.
    """
    (tmp_path / PROMPT_NAME).mkdir()
    (tmp_path / "manifest.yaml").write_text("default: v9\n", encoding="utf-8")
    monkeypatch.setattr(service, "PROMPT_DIR", tmp_path)

    with pytest.raises(MinimallConfigError) as excinfo:
        resolve_prompt_version(tmp_path / "manifest.yaml")

    assert "v9" in str(excinfo.value)


def test_a_broken_manifest_is_a_startup_error(tmp_path: Path) -> None:
    """YAML 本身写坏了也是启动期错误 (报的是「这份文件读不出」, 而不是让它变成
    一个「查不到数据」的假象)."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("default: v1\n  bad-indent: 1\n", encoding="utf-8")

    with pytest.raises(MinimallConfigError):
        resolve_prompt_version(manifest)


# ---------------------------------------------------------------------------
# 换一版: 加一个文件 + 改清单, 装配就取到新版
# ---------------------------------------------------------------------------


async def test_switching_the_manifest_switches_what_the_assembly_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client
) -> None:
    """换一版 = 加一个版本文件 + 把清单的 `default` 指过去 (PRD §4.8 的用法).

    断在**装配的产物**上 (会话历史的第一条就是那份正文) 而不是断在
    `resolve_prompt_version` 上: 版本号读对了, 却没传进会话, 正是那种「开关解析
    了, 存下了, 但没生效」的静默失效 —— 而它骗过的是评估, 不是用户.
    """
    (tmp_path / PROMPT_NAME).mkdir()
    (tmp_path / PROMPT_NAME / "v9.prompt").write_text(
        "你是 v9 客服.\n", encoding="utf-8"
    )
    (tmp_path / "manifest.yaml").write_text("default: v9\n", encoding="utf-8")
    monkeypatch.setattr(service, "PROMPT_DIR", tmp_path)
    monkeypatch.setattr(service, "PROMPT_MANIFEST", tmp_path / "manifest.yaml")

    session = await MinimallService(
        client=client,
        model=MockLLM.fixed(text_response("好的")),
        saver=InMemoryCheckpointSaver(),
    ).session_for(
        build_context(BUYER_ID, "prompt-test", tenant_id=TENANT_WEB),
        event_sink=lambda event: None,
        # 本用例不看事件, 出口是谁都行 —— 但**说不说**是必填的 (装配处不留默认值)
        redact=False,
    )

    assert session.history[0]["content"] == "你是 v9 客服.\n"


# ---------------------------------------------------------------------------
# 内容
# ---------------------------------------------------------------------------


def test_the_prompt_is_read_from_the_business_directory() -> None:
    """读得到, 且读出来的是一份客服人设 (不是框架那份占位文本)."""
    body = system_prompt()

    assert len(body) > 200, "提示词短得不像一份客服人设, 八成读错了文件"
    assert "客服" in body


def test_the_prompt_forbids_the_things_it_must_forbid() -> None:
    """禁则一条都不能少 (本人点头 / 查别人 / 编造 / 许诺).

    与 v1 的差别: **「不能下单 / 取消 / 退款」那条退场了** —— L2 起助手真的能改
    数据, 再留着它, 模型会一律拒绝.

    与 v2 的差别有两条 (issue 37):
    - v2 第 1 条写的是「**不能替买家付款** …… 你没有、也不该有他的支付密码」.
      代付 (issue 35) 把前半句变成了**假话** —— 模型读到它就会拒绝付款, 那条链路
      在 v3 里仍然走不通. 所以改成「下单与付款都由买家本人点头」, 并把「不要在
      对话里再问一句」写成明文 (不写的话模型会先问一句"要不要", 买家说好之后
      才调工具 → 卡又弹一次, 一个人被问两遍).
    - 那句被推翻的话**必须不在**声明的那一版里: 留着它等于把代付关掉.
    """
    body = system_prompt()

    for must_say in (
        "都由买家本人点头",
        "不要在对话里再问一句",
        "不要向他索要支付密码",
        "不能查别人的",
        "不编造",
        "不许诺",
    ):
        assert must_say in body, f"提示词里少了这条禁则: {must_say}"

    assert "不能替买家付款" not in body, (
        "代付已经做出来了, 这句话与事实相反 —— 模型读到它会拒绝付款"
    )


def test_the_prompt_hands_the_confirmation_over_to_the_card() -> None:
    """下单与付款都不该在对话里再问一句: 那句"要不要"由确认卡替模型问 (issue 37).

    守的是**用户被问两次**那种失效 —— 模型先问一句"要下单吗", 买家说"好", 模型
    才去调工具, 于是卡又弹一次.

    说清这条用例钉的是什么: **v2 里并没有"下单前问一句确认吗"那样的要求** (那句
    话只活在 PRD §4.7 的第一阶段计划里), 所以本片落成的是一条**正向指令**, 而不是
    "删掉一句". 判据取那三个字面量 (卡替他问过了 / 不要在对话里再问一句 / 示例里
    弹出的那张卡) —— 示例是行为最强的锚, 流程改了而示例照旧, 模型会照旧答.
    """
    body = system_prompt()

    assert "卡替他问过了" in body
    assert "不要在对话里再问一句" in body
    assert "我弹了一张确认卡" in body, "示例也要按新流程写, 否则模型照着旧示例答"


def test_the_prompt_tells_the_model_to_obey_the_guardrail() -> None:
    """被护栏拦下时该怎么办, 提示词里要有一句.

    具体阈值 (8 次 / 5000 元) **不写进提示词** —— 那是 `guardrail.py` 里那两个
    常量的事, 写两处迟早对不上. 提示词只管一件事: 撞上之后**不要重试, 不要拆单**,
    让买家自己去页面 (护栏回填的那句话里也这么说).
    """
    body = system_prompt()

    assert "金额上限" in body
    assert "不要反复重试" in body and "也不要拆成几单" in body
    # 查「5000 元」而不是裸的 5000: 示例里的订单号 (...1230450000031234) 恰好含着
    # 那四个字符, 裸查会假红
    assert "5000 元" not in body, "阈值只在 guardrail.py 里有一处, 别抄进提示词"


def test_the_prompt_uses_the_project_vocabulary() -> None:
    """术语按 CONTEXT.md 用: 取消 ≠ 退款, 且**不存在卖家角色**.

    这三条在 PRD §7.2 里被点名为最容易写错的 —— 写错了模型就会对买家说出商城里
    并不存在的东西 (比如「我帮你联系商家」).

    说清楚这条用例的边界: 它只断「这几个词在不在」, **断不了「用的是不是对的
    意思」** (让模型去做退货退款, 这里照样绿). 语义对不对要靠 L4 的评估集, 不是
    单元测试能兜的 —— 所以这几行不是配方的全部, 只是防「整段被删掉」.
    """
    body = system_prompt()

    assert "取消" in body and "退款" in body
    assert "退货退款" in body, "要明确说清商城不涉及寄回, 否则模型会自己编一套退货流程"
    assert "没有卖家" in body
    assert "回滚库存" in body, "取消与退款的关键差别 (回不回库存) 必须写出来"


def test_the_prompt_carries_examples() -> None:
    """示例直接写在模板里 (PRD §4.8: 第一阶段不做示例检索, 那是跟知识库一起做的事)."""
    body = system_prompt()

    assert "买家:" in body and "回答:" in body, "示例要写进模板, 且看得出是示例"
