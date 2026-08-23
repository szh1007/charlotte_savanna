"""Stub 图谱生成器 (Issue 03).

确定性模板图谱: 基于输入内容派生标题, 产出 2 章节共 4 知识点的线性依赖链
(含跨章节边), 结构与 CONTRACT.md v1 一致; 真实管道 (Issue 07) 替换
run_pipeline 函数体后本模块随之退役.

与 Django 侧 derive_journey_title 保持一致的标题规则, 保证旅程标题可读.
"""


def _topic(inp):
    """主题词: 输入首行截断; file 输入 (content 空) 用通用描述."""
    content = (inp.content or "").strip()
    if not content:
        return "示例学习材料"
    return content.splitlines()[0][:20]


def generate_graph(inp) -> dict:
    """生成契约图谱 (CONTRACT.md v1): 2 章节线性依赖链, 含跨章节边."""
    topic = _topic(inp)

    def variant(suffix):
        # 由主题派生章节名, 不同输入产出不同标题
        return f"{topic} {suffix}"

    return {
        "version": 1,
        "title": topic,
        "chapters": [
            {
                "id": "ch_1",
                "title": variant("基础概念"),
                "summary": f"理解 {topic} 的前提知识与核心概念.",
                "knowledge_points": [
                    {
                        "id": "kp_1",
                        "title": f"认识 {topic}",
                        "summary": f"了解 {topic} 的基本定义与适用场景.",
                        "prerequisites": [],
                    },
                    {
                        "id": "kp_2",
                        "title": f"{topic} 的核心原理",
                        "summary": f"掌握 {topic} 的核心机制与工作方式.",
                        "prerequisites": ["kp_1"],
                    },
                ],
            },
            {
                "id": "ch_2",
                "title": variant("实践应用"),
                "summary": f"把 {topic} 应用到真实场景.",
                "knowledge_points": [
                    {
                        "id": "kp_3",
                        "title": f"{topic} 的典型应用",
                        "summary": f"了解 {topic} 在工程中的典型用法.",
                        "prerequisites": ["kp_2"],  # 跨章节依赖边
                    },
                    {
                        "id": "kp_4",
                        "title": f"{topic} 实战演练",
                        "summary": f"通过示例动手实践 {topic}.",
                        "prerequisites": ["kp_3"],
                    },
                ],
            },
        ],
    }
