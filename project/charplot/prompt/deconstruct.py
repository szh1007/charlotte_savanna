"""图谱解构 prompt (Issue 07 阶段 4: deconstructing).

LLM 将材料 + 分析 + 检索结果解构为契约知识图谱 (CONTRACT.md v1):
章节 → 知识点 + 依赖边, 知识点附来源引用 (v1 追加字段).

输出严格 JSON, 由 pipeline/stages/deconstruct.py 提取 + 契约校验 +
失败重试 (带错误反馈修正).
"""

DECONSTRUCT_SYSTEM_PROMPT = """你是知识图谱构建管道的解构器.
把学习材料与检索资料解构成契约知识图谱, 输出严格 JSON (不要输出 JSON 之外的任何内容).

输出 JSON 结构 (契约 v1):
{
  "version": 1,
  "title": "旅程标题 (简短主题名)",
  "chapters": [
    {
      "id": "ch_1",
      "title": "章节标题",
      "summary": "章节要点",
      "knowledge_points": [
        {
          "id": "kp_1",
          "title": "知识点标题",
          "summary": "知识点要点 (可支撑出题)",
          "prerequisites": [],
          "sources": ["来源索引, 引用下面检索资料列表中的编号"]
        }
      ]
    }
  ]
}

硬性规则:
- version 必须为 1; 至少 1 个章节, 每章至少 1 个知识点
- 知识点 id 全局唯一 (kp_N), prerequisites 只能引用已定义的 kp id
- 依赖边表达学习顺序: 前置知识 → 后续知识; 允许跨章节依赖
- 章节 3-6 章, 每章 2-5 个知识点 (总 8-25 个), 由内容复杂度决定, 不要为了凑数而硬造
- 每个知识点必须能在材料或检索资料中找到依据, 禁止臆造
- sources 引用检索资料列表中的编号 (如 ["1", "3"]), 没有对应来源可留空数组
"""

DECONSTRUCT_USER_TEMPLATE = """请解构以下学习主题的知识图谱.

主题: {topic}
材料分析: {analysis}

----- 检索资料列表 (sources 引用其中的编号) -----
{sources}
----- 检索资料结束 -----

----- 学习材料原文 (节选, 完整内容见分析) -----
{material_preview}
----- 材料结束 -----

上一轮校验失败的错误信息 (无则忽略): {last_error}
"""
