"""stub 管道契约测试 (Issue 03, CONTRACT.md §1).

覆盖: 输出确定性 / 结构契约 (2 章节, 临时 id 唯一, prerequisites 引用存在,
字段齐全) / file 输入 (content 空) 仍产出契约图.
"""

from project.charplot.pipeline import PipelineInput, run_pipeline


async def run(inp):
    emitted = []

    async def emit(stage, progress, message):
        emitted.append(stage)

    graph = await run_pipeline(inp, emit)
    return graph, emitted


async def test_stub_graph_contract_fields():
    graph, _ = await run(
        PipelineInput(journey_id=1, input_type="text", content="我想学 Python 装饰器")
    )
    # 顶层契约字段
    assert graph["version"] == 1
    assert graph["title"] == "我想学 Python 装饰器"
    assert len(graph["chapters"]) == 2

    kp_ids = []
    for chapter in graph["chapters"]:
        assert chapter["id"].startswith("ch_")
        assert chapter["title"] and "summary" in chapter
        assert len(chapter["knowledge_points"]) >= 1
        for kp in chapter["knowledge_points"]:
            assert kp["id"].startswith("kp_")
            assert kp["title"] and "summary" in kp
            kp_ids.append(kp["id"])
    # 临时 id 全局唯一 + prerequisites 引用存在
    assert len(kp_ids) == len(set(kp_ids))
    all_prereqs = [
        p
        for c in graph["chapters"]
        for kp in c["knowledge_points"]
        for p in kp["prerequisites"]
    ]
    assert all(p in kp_ids for p in all_prereqs)
    assert all_prereqs  # 依赖边非空 (线性链)


async def test_stub_graph_deterministic():
    inp = PipelineInput(journey_id=1, input_type="text", content="Python 装饰器")
    g1 = (await run(inp))[0]
    g2 = (await run(inp))[0]
    assert g1 == g2


async def test_stub_graph_emits_four_stages():
    _, stages = await run(PipelineInput(journey_id=1, input_type="text", content="x"))
    assert stages == ["parsing", "analyzing", "searching", "deconstructing"]


async def test_stub_graph_file_input_without_content():
    graph, _ = await run(PipelineInput(journey_id=1, input_type="file"))
    assert graph["version"] == 1
    assert graph["title"]  # file 输入无 content, 用通用描述
    assert len(graph["chapters"]) == 2


async def test_stub_graph_chapter_has_cross_chapter_edge():
    graph, _ = await run(
        PipelineInput(journey_id=1, input_type="text", content="Redis 持久化")
    )
    # 第 2 章第 1 个知识点依赖第 1 章最后知识点 (跨章节边, CONTRACT 允许)
    ch2_kp1 = graph["chapters"][1]["knowledge_points"][0]
    ch1_kp_ids = [kp["id"] for kp in graph["chapters"][0]["knowledge_points"]]
    assert ch2_kp1["prerequisites"] and ch2_kp1["prerequisites"][0] in ch1_kp_ids
