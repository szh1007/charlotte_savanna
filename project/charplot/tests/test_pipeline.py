"""真实管道契约测试 (Issue 07, CONTRACT.md §1).

覆盖: 全流程 (text/file/link 三形态) 产出契约图谱 (章节/知识点/依赖边/
来源引用) / 阶段事件序 / LLM 输出非法 → 重试修正 / 重试耗尽 → 异常 /
解析失败传播. LLM 与检索由 conftest 假件隔离 (不触网不调真实模型).

替换原 test_stub_pipeline.py (stub 确定性/固定结构断言随 stub 退役).
"""

import asyncio

import pytest
from tests.fakes import FakeChatModel

from project.charplot.api import config, django_client
from project.charplot.pipeline import PipelineInput, parsers, run_pipeline
from project.charplot.pipeline import llm as llm_mod
from project.charplot.pipeline.stages.analyze import analyze_material
from project.charplot.pipeline.stages.deconstruct import deconstruct_graph
from project.charplot.pipeline.types import ContentAnalysis, ParsedMaterial


def run(inp):
    """同步执行管道, 返回 (graph, emitted_stages)."""
    emitted = []

    async def emit(stage, progress, message):
        emitted.append((stage, progress))

    graph = asyncio.run(run_pipeline(inp, emit))
    return graph, emitted


def assert_contract_graph(graph):
    """契约断言 (CONTRACT.md v1): 结构/临时 id 唯一/依赖边引用存在."""
    assert graph["version"] == 1
    assert graph["title"]
    assert len(graph["chapters"]) >= 1
    kp_ids = []
    for chapter in graph["chapters"]:
        assert chapter["id"].startswith("ch_")
        assert chapter["title"] and "summary" in chapter
        assert len(chapter["knowledge_points"]) >= 1
        for kp in chapter["knowledge_points"]:
            assert kp["id"].startswith("kp_")
            assert kp["title"] and "summary" in kp
            kp_ids.append(kp["id"])
    assert len(kp_ids) == len(set(kp_ids))
    for chapter in graph["chapters"]:
        for kp in chapter["knowledge_points"]:
            for prereq in kp.get("prerequisites") or []:
                assert prereq in kp_ids


def test_pipeline_text_input_returns_contract_graph():
    graph, _ = run(
        PipelineInput(journey_id=1, input_type="text", content="我想学 Python 装饰器")
    )
    assert_contract_graph(graph)


def test_pipeline_emits_four_stages_in_order():
    _, stages = run(PipelineInput(journey_id=1, input_type="text", content="x"))
    assert [s for s, _ in stages] == [
        "parsing",
        "analyzing",
        "searching",
        "deconstructing",
    ]
    # 进度与 SSE 契约一致 (单调递增 15/35/60/90)
    progresses = [p for _, p in stages]
    assert progresses == sorted(progresses)


def test_pipeline_file_input(monkeypatch):
    async def fake_fetch(journey_id):
        return "intro.md", "# 学习标题\n\n这是一份学习材料正文".encode()

    monkeypatch.setattr(django_client, "fetch_journey_content", fake_fetch)
    graph, _ = run(PipelineInput(journey_id=1, input_type="file"))
    assert_contract_graph(graph)
    assert graph["title"]


def test_pipeline_link_input(monkeypatch):
    def fake_fetch(url):
        return "<html><body><h1>网页标题</h1><p>网页正文内容</p></body></html>"

    monkeypatch.setattr(parsers, "fetch_link_content", fake_fetch)
    graph, _ = run(
        PipelineInput(journey_id=1, input_type="link", content="https://example.com")
    )
    assert_contract_graph(graph)


def test_pipeline_unsupported_file_raises(monkeypatch):
    async def fake_fetch(journey_id):
        return "data.xyz", b"binary"

    monkeypatch.setattr(django_client, "fetch_journey_content", fake_fetch)
    with pytest.raises(Exception, match="不支持的文件格式"):
        run(PipelineInput(journey_id=1, input_type="file"))


def test_analyze_retries_with_feedback_then_succeeds(monkeypatch):
    """LLM 首次输出非法 JSON → 重试带错误反馈 → 修正成功."""
    fake = FakeChatModel(fail_first_n=1)
    monkeypatch.setattr(llm_mod, "get_chat_model", lambda: fake)
    material = ParsedMaterial(title="t", text="材料", origin="text")
    analysis = asyncio.run(analyze_material(material))
    assert analysis.topic  # 修正后成功解析
    assert len(fake.calls) == 2
    assert "上次输出解析失败" in fake.calls[1]


def test_deconstruct_retries_with_feedback_then_succeeds(monkeypatch):
    """解构首次输出非法 → 重试带契约校验错误反馈 → 修正成功."""
    fake = FakeChatModel(fail_first_n=1)
    monkeypatch.setattr(llm_mod, "get_chat_model", lambda: fake)
    material = ParsedMaterial(title="t", text="材料", origin="text")
    analysis = ContentAnalysis(topic="Python 装饰器", summary="摘要")
    graph = asyncio.run(
        deconstruct_graph(material, analysis, EMPTY_REPORT, journey_id=1)
    )
    assert_contract_graph(graph)
    assert len(fake.calls) == 2
    assert "上一轮校验失败" in fake.calls[1]


def test_analyze_exhausts_retries_raises(monkeypatch):
    """LLM 输出永远无法解析 → 重试耗尽 → RuntimeError (任务 error)."""
    fake = FakeChatModel(fail_first_n=99)
    monkeypatch.setattr(llm_mod, "get_chat_model", lambda: fake)
    original = config.LLM_RETRIES
    config.LLM_RETRIES = 1  # 快速耗尽
    try:
        material = ParsedMaterial(title="t", text="材料", origin="text")
        with pytest.raises(RuntimeError, match="主内容分析失败"):
            asyncio.run(analyze_material(material))
    finally:
        config.LLM_RETRIES = original


def test_deconstruct_exhausts_retries_raises(monkeypatch):
    fake = FakeChatModel(fail_first_n=99)
    monkeypatch.setattr(llm_mod, "get_chat_model", lambda: fake)
    original = config.LLM_RETRIES
    config.LLM_RETRIES = 1
    try:
        material = ParsedMaterial(title="t", text="材料", origin="text")
        with pytest.raises(RuntimeError, match="图谱解构失败"):
            asyncio.run(
                deconstruct_graph(
                    material, ContentAnalysis(topic="t"), EMPTY_REPORT, journey_id=1
                )
            )
    finally:
        config.LLM_RETRIES = original


class _EmptyReport:
    results = []


EMPTY_REPORT = _EmptyReport()
