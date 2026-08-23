"""主内容分析 prompt (Issue 07 阶段 2: analyzing).

LLM 对归一化后的输入材料做主题提炼: 产出主题 / 摘要 / 核心概念 /
建议检索查询 (供阶段 3 搜索增强使用). 输出为 JSON 对象, 由
pipeline/stages/analyze.py 解析校验.
"""

ANALYZE_SYSTEM_PROMPT = """你是知识图谱构建管道的主内容分析器.
对用户输入的学习材料做分析, 输出严格 JSON (不要输出 JSON 之外的任何内容):

{
  "topic": "学习主题 (简短, 用作旅程标题候选)",
  "summary": "材料要点摘要 (100 字内, 中文)",
  "concepts": ["核心概念列表, 3-8 个, 取自材料本身"],
  "suggested_queries": [
    "2-4 个联网检索查询, 补全知识面与交叉验证, 覆盖材料缺失/过时部分"
  ]
}

要求:
- concepts 必须来自材料内容, 不臆造
- suggested_queries 具体可检索 (如 "Python 装饰器 语法 详解"), 不泛泛而谈
"""

ANALYZE_USER_TEMPLATE = """学习材料如下 (输入类型: {input_type}):

----- 材料开始 -----
{material}
----- 材料结束 -----
"""
