"""C17 回归: HyDE 假设答案的冻结重放.

为什么要冻: HyDE 这一路只有一处 LLM 调用 (生成假设性答案), 之后全是确定性的向量
检索. 不冻的话同一道题两次跑生成不同答案 -> 检索文本不同 -> 候选池不同 -> 精排看到
的 10 条也不同, 于是「精排开/关」两臂之差里混着 LLM 抖动, 归因不到精排头上.

这里只钉纯逻辑 (读写 / 命中 / 缺项 / 抓取), 不连 Milvus、不调真模型.
"""

from __future__ import annotations

import pytest

from app.rag_eval import dataset, runner


@pytest.fixture
def frozen_file(monkeypatch, tmp_path):
    """把冻结文件指到 tmp, 别碰仓库里的 artifacts."""
    path = tmp_path / "hyde_answers.json"
    monkeypatch.setattr(dataset, "HYDE_ANSWERS_FILE", path)
    return path


# ---------------------------------------------------------------- 读写


def test_missing_file_reads_as_empty(frozen_file):
    """没抓过 -> 空字典, 而不是报错 (抓取模式要靠这个判断)."""
    assert dataset.load_hyde_answers() == {}


def test_round_trip_keeps_content(frozen_file):
    """落盘的能被原样读回来 —— 中文不转义, 否则 diff 里全是 \\uXXXX."""
    written = {"c01": {"题目": "问句一", "假设答案": "这是假设性答案"}}
    dataset.save_hyde_answers(written)

    assert dataset.load_hyde_answers() == written
    assert "这是假设性答案" in frozen_file.read_text(encoding="utf-8")


def test_save_creates_artifacts_dir(monkeypatch, tmp_path):
    """artifacts 目录不存在时也要能落盘 (首次跑评测时它可能是空的)."""
    nested = tmp_path / "artifacts" / "hyde_answers.json"
    monkeypatch.setattr(dataset, "HYDE_ANSWERS_FILE", nested)

    dataset.save_hyde_answers({"c01": {"题目": "q", "假设答案": "x"}})

    assert nested.exists()


# ---------------------------------------------------------------- 题目变更


def test_answer_is_discarded_when_the_question_changed():
    """题目一改, 旧的假设答案就是答非所问 —— 必须作废, 不能接着用.

    这是静默失效: 答案还是那段答案, 检索照跑, 只是检索文本悄悄偏离了新问题,
    数字变差而看不出原因. 存原问题就是为了能比这一下.
    """
    frozen = {"c01": {"题目": "旧问法", "假设答案": "旧答案"}}

    assert dataset.get_frozen_answer(frozen, "c01", "新问法") is None
    assert dataset.get_frozen_answer(frozen, "c01", "旧问法") == "旧答案"


def test_never_captured_case_returns_none():
    assert dataset.get_frozen_answer({}, "c99", "问法") is None


def test_put_records_the_question_alongside_the_answer():
    frozen: dict = {}

    dataset.put_frozen_answer(frozen, "c01", "问法", "答案")

    assert frozen == {"c01": {"题目": "问法", "假设答案": "答案"}}


# ---------------------------------------------------------------- 取答案


def test_frozen_answer_is_reused_without_calling_llm():
    """命中冻结 -> 直接返回, 一次 LLM 都不该调 (这是这个机制存在的全部意义)."""
    called = []

    def fake_generate(query: str) -> str:
        called.append(query)
        return "现场生成的"

    frozen = {"c01": {"题目": "问题", "假设答案": "冻结的答案"}}

    assert (
        runner._hyde_answer_for("c01", "问题", "改写后", frozen, False, fake_generate)
        == "冻结的答案"
    )
    assert called == []


def test_frozen_answer_wins_even_in_capture_mode():
    """抓取模式也不重采已有条目 —— 增量抓, 不然每加一题就把整库的答案换一遍."""
    frozen = {"c01": {"题目": "问题", "假设答案": "冻结的答案"}}

    assert (
        runner._hyde_answer_for(
            "c01", "问题", "改写后", frozen, True, lambda q: "现场生成的"
        )
        == "冻结的答案"
    )


def test_missing_answer_raises_when_not_capturing():
    """回放模式缺项要当场报错, 不能静默现场生成 —— 那会让两组数字不可比."""
    with pytest.raises(KeyError, match="capture-hyde"):
        runner._hyde_answer_for(
            "c99", "问题", "改写后", {}, False, lambda q: "不该被调到"
        )


def test_capture_mode_generates_and_records():
    """抓取模式: 现场生成并就地记进字典 (调用方负责落盘)."""
    frozen: dict = {}

    assert (
        runner._hyde_answer_for(
            "c01",
            "数据出境怎么申报",
            "改写后",
            frozen,
            True,
            lambda q: f"针对「{q}」的假设答案",
        )
        == "针对「改写后」的假设答案"
    )
    assert frozen["c01"]["假设答案"] == "针对「改写后」的假设答案"
    assert frozen["c01"]["题目"] == "数据出境怎么申报"


def test_capture_uses_the_injected_generator_not_a_module_lookup():
    """抓取走的是**传进来的**那个函数, 不是按名字去模块上取的.

    这条是回归用: 早先的实现按名字取 `hyde_search_service._call_llm_by_rewritten_query`,
    而抓取那一趟这个名字正被 patch 成"回调本函数" —— 于是自己调自己, 一直递归到
    RecursionError. 更坏的是它被 HyDE 那一路的 isolate_route 吞掉, 表现为**静默降级
    成空召回**, 数字变差而没有任何报错.
    """
    sentinel = "由注入的函数生成"
    frozen: dict[str, str] = {}

    # 模块上放一个"如果被按名字取到就会露馅"的函数
    original = runner.hyde_search_service._call_llm_by_rewritten_query
    runner.hyde_search_service._call_llm_by_rewritten_query = lambda q: "按名字取到了"
    try:
        result = runner._hyde_answer_for(
            "c01", "问题", "改写后", frozen, True, lambda q: sentinel
        )
    finally:
        runner.hyde_search_service._call_llm_by_rewritten_query = original

    assert result == sentinel


# ---------------------------------------------------------------- 口径


def test_caveat_states_replay_and_condition_count():
    """报告里要写清"冻了多少条"和"冻的是什么" —— 数字离开口径没法判读."""
    text = runner._hyde_caveat(frozen_count=42)

    assert "冻结重放" in text
    assert "42" in text
    assert "LLM" in text


def test_capture_is_a_separate_step_not_a_mode_of_the_eval_run():
    """抓取不再挂在评测链路上.

    早先的实现让评测跑批器在逐题循环里顺手抓 —— 那条路要为每道题跑完检索 + 精排
    (实测精排 15 秒/题), 而抓取只需要一次 LLM 调用; 79 道题白烧二十分钟, 且那一趟
    的报告数字全不作数. 现在抓取是独立函数, 评测链路里只有回放.
    """
    import inspect

    assert hasattr(runner, "capture_hyde_answers")
    signature = inspect.signature(runner.run_query_eval_case)
    assert "capture_hyde" not in signature.parameters
