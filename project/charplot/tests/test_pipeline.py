"""真实管道契约测试 (Issue 07 + Issue 11 知识库两轮, CONTRACT.md §1).

覆盖: 全流程 (text/file/link/kb 四形态) 产出契约图谱 (章节/知识点/依赖边/
来源引用) / 阶段事件序 / LLM 输出非法 → 重试修正 / 重试耗尽 → 异常 /
解析失败传播. LLM 与检索由 conftest 假件隔离 (不触网不调真实模型).

Issue 11 kb 形态: parse 取知识库元信息 → search 确定性概览检索 (不走
subagent) → 两轮解构 (骨架轮 + 逐知识点细化轮).
"""

import asyncio
import json
import re

import pytest
from tests.fakes import FakeChatModel

from project.charplot.api import config, django_client
from project.charplot.pipeline import PipelineInput, parsers, run_pipeline
from project.charplot.pipeline import llm as llm_mod
from project.charplot.pipeline.sources import kb_source
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


# ---------------------------------------------------------------------------
# Issue 11: 知识库驱动旅程 (kb 输入形态 + RAG 两轮解构)
# ---------------------------------------------------------------------------

KB_META_READY = {
    "id": 7,
    "name": "RAG 实战",
    "description": "企业级 RAG 全流程",
    "status": "ready",
}

KB_CHUNKS = [
    {
        "doc_id": 1,
        "title": "rag.md",
        "filename": "rag.md",
        "chunk_index": 0,
        "content": "RAG 检索增强生成: 先检索再生成, 生成约束在检索片段上",
        "score": 0.9,
    },
    {
        "doc_id": 1,
        "title": "rag.md",
        "filename": "rag.md",
        "chunk_index": 1,
        "content": "混合检索 = 稠密向量 + 稀疏 BM25 融合, rerank 精排后进 prompt",
        "score": 0.8,
    },
    {
        "doc_id": 2,
        "title": "milvus.md",
        "filename": "milvus.md",
        "chunk_index": 0,
        "content": "Milvus 是向量数据库, 支持 HNSW 索引与稀疏索引混合检索",
        "score": 0.7,
    },
]

# 骨架轮预置输出 (契约同构, prerequisites 留空)
KB_SKELETON_JSON = json.dumps(
    {
        "version": 1,
        "title": "RAG 实战",
        "chapters": [
            {
                "id": "ch_1",
                "title": "RAG 基础",
                "summary": "检索增强生成原理",
                "knowledge_points": [
                    {
                        "id": "kp_1",
                        "title": "RAG 概念",
                        "summary": "先检索再生成",
                        "prerequisites": [],
                        "sources": [],
                    },
                    {
                        "id": "kp_2",
                        "title": "混合检索",
                        "summary": "稠密+稀疏融合",
                        "prerequisites": [],
                        "sources": [],
                    },
                ],
            },
            {
                "id": "ch_2",
                "title": "向量库实践",
                "summary": "Milvus 落地",
                "knowledge_points": [
                    {
                        "id": "kp_3",
                        "title": "Milvus 索引",
                        "summary": "HNSW 与稀疏索引",
                        "prerequisites": [],
                        "sources": [],
                    },
                ],
            },
        ],
    },
    ensure_ascii=False,
)


def kb_refine_responder(text: str) -> str:
    """细化轮动态响应: 从 prompt 提取 kp id/标题, 返回补全依赖的细化 JSON."""
    m = re.search(r"知识点 id: (kp_\d+)", text)
    title_m = re.search(r"知识点标题: (.+)", text)
    kp_id = m.group(1) if m else "kp_1"
    title = title_m.group(1).strip() if title_m else "知识点"
    return json.dumps(
        {
            "id": kp_id,
            "title": title,
            "summary": f"{title} 细化摘要 (基于检索片段)",
            "prerequisites": [],
            "sources": ["1"],
        },
        ensure_ascii=False,
    )


def patch_kb_deps(monkeypatch, meta=None, chunks=None, fake=None):
    """kb 管道外部依赖注入: meta / 检索片段 / 假 LLM 三件套."""
    if meta is not None:

        async def fake_meta(kb_id):
            return meta

        monkeypatch.setattr(django_client, "fetch_kb_meta", fake_meta)
    if chunks is not None:
        monkeypatch.setattr(
            kb_source, "search_kb", lambda kb_id, query, top_k=None: chunks
        )
    if fake is not None:
        monkeypatch.setattr(llm_mod, "get_chat_model", lambda: fake)
    return fake


def test_pipeline_kb_input_two_rounds_deconstruct(monkeypatch):
    """kb 旅程全流程: 元信息解析 → 概览检索 → 骨架 + 逐知识点细化 → 契约图谱."""
    fake = FakeChatModel(
        sequence=[
            ("请为以下知识库构建图谱骨架", KB_SKELETON_JSON),
            ("请细化以下知识点的依赖边与摘要", kb_refine_responder),
        ]
    )
    patch_kb_deps(monkeypatch, meta=KB_META_READY, chunks=KB_CHUNKS, fake=fake)
    graph, stages = run(PipelineInput(journey_id=1, input_type="kb", kb_id=7))
    assert_contract_graph(graph)
    assert graph["title"] == "RAG 实战"
    # 骨架 3 个知识点 → 细化轮恰好 3 次 LLM 调用
    refine_calls = [c for c in fake.calls if "请细化以下知识点的依赖边与摘要" in c]
    assert len(refine_calls) == 3
    # 细化已回填: 摘要带"细化摘要"标记, sources 引用检索片段
    summaries = [
        kp["summary"] for ch in graph["chapters"] for kp in ch["knowledge_points"]
    ]
    assert all("细化摘要" in s for s in summaries)
    assert all(
        kp["sources"] for ch in graph["chapters"] for kp in ch["knowledge_points"]
    )
    # 阶段事件序与自输入旅程一致 (契约不变)
    assert [s for s, _ in stages] == [
        "parsing",
        "analyzing",
        "searching",
        "deconstructing",
    ]
    # 概览检索阶段未走 subagent (kb 分支确定性检索)
    assert any("RAG" in c for c in fake.calls)


def test_pipeline_kb_parse_uses_meta_and_title(monkeypatch):
    """parse 阶段: 取知识库元信息, 名称进入材料 (analyze 输入可见)."""
    fake = FakeChatModel(
        sequence=[
            ("请为以下知识库构建图谱骨架", KB_SKELETON_JSON),
            ("请细化以下知识点的依赖边与摘要", kb_refine_responder),
        ]
    )
    patch_kb_deps(monkeypatch, meta=KB_META_READY, chunks=KB_CHUNKS, fake=fake)
    run(PipelineInput(journey_id=1, input_type="kb", kb_id=7))
    # analyze prompt 含知识库名称与描述
    analyze_calls = [c for c in fake.calls if "学习材料如下" in c]
    assert analyze_calls
    assert "RAG 实战" in analyze_calls[0]
    assert "企业级 RAG 全流程" in analyze_calls[0]


def test_pipeline_kb_meta_not_ready_fails(monkeypatch):
    """知识库被下线/删除 (meta 非 ready) → 快速失败 (任务 error)."""
    meta = {**KB_META_READY, "status": "offline"}
    patch_kb_deps(monkeypatch, meta=meta, chunks=KB_CHUNKS)
    with pytest.raises(RuntimeError, match="知识库当前不可用"):
        run(PipelineInput(journey_id=1, input_type="kb", kb_id=7))


def test_pipeline_kb_skeleton_retries_with_feedback(monkeypatch):
    """骨架轮首次输出非法 → 重试带校验错误反馈 → 修正成功.

    反馈条目必须排在主关键词之前 (first-match); 细化轮仍需 responder
    条目 (细化 prompt 不含反馈词, 避免与骨架反馈关键词误匹配).
    """
    fake = FakeChatModel(
        sequence=[
            # 重试反馈关键词 = 校验错误消息特征 (模板固定含"上一轮校验失败",
            # 不能用作反馈关键词, 会首试即命中); 反馈条目须在主关键词之前
            ("未找到 JSON 对象", KB_SKELETON_JSON),
            ("请为以下知识库构建图谱骨架", "这不是 JSON"),  # 骨架首试非法
            ("请细化以下知识点的依赖边与摘要", kb_refine_responder),
        ]
    )
    patch_kb_deps(monkeypatch, meta=KB_META_READY, chunks=KB_CHUNKS, fake=fake)
    graph, _ = run(PipelineInput(journey_id=1, input_type="kb", kb_id=7))
    assert_contract_graph(graph)
    skeleton_calls = [c for c in fake.calls if "请为以下知识库构建图谱骨架" in c]
    assert len(skeleton_calls) == 2
    assert "未找到 JSON 对象" in skeleton_calls[1]


def test_pipeline_kb_refine_keeps_id(monkeypatch):
    """细化轮 id 保持校验: LLM 首次改 id → 校验错误 → 重试修正."""
    attempts = {"n": 0}

    def responder(text: str) -> str:
        m = re.search(r"知识点 id: (kp_\d+)", text)
        title_m = re.search(r"知识点标题: (.+)", text)
        kp_id = m.group(1) if m else "kp_1"
        title = title_m.group(1).strip() if title_m else "知识点"
        attempts["n"] += 1
        # 首次返回错误 id (kp_99), 触发校验错误反馈; 之后修正
        wrong_id = "kp_99" if attempts["n"] == 1 else kp_id
        return json.dumps(
            {
                "id": wrong_id,
                "title": title,
                "summary": "细化摘要",
                "prerequisites": [],
                "sources": [],
            },
            ensure_ascii=False,
        )

    fake = FakeChatModel(
        sequence=[
            ("请为以下知识库构建图谱骨架", KB_SKELETON_JSON),
            ("请细化以下知识点的依赖边与摘要", responder),
        ]
    )
    patch_kb_deps(monkeypatch, meta=KB_META_READY, chunks=KB_CHUNKS, fake=fake)
    graph, _ = run(PipelineInput(journey_id=1, input_type="kb", kb_id=7))
    assert_contract_graph(graph)
    # 3 个知识点, 首个改 id 触发 1 次重试 → 细化调用 4 次
    refine_calls = [c for c in fake.calls if "请细化以下知识点的依赖边与摘要" in c]
    assert len(refine_calls) == 4
    # 重试 prompt 带 id 保持校验错误反馈 (并发下重试位置不定, 用 any)
    assert any("禁止改名" in c for c in refine_calls)
