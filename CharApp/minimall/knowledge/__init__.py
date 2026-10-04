"""知识库包 (L5-a): 政策语料 → Milvus 索引 → 混合检索.

一句话理解: 这是助手回答"规定是什么"那条链路的全部. 语料是**商城数据库里的
政策短文** (管理员在 Admin 手写 Markdown), 索引由一条手工命令重建, 检索在
回答政策问题时由工具调用 —— 见 `CharApp/docs/PLAN.md` 的 L5 段与 ADR-0026.

各模块分工 (从上到下就是数据流的方向):

| 模块 | 一句话 |
|------|--------|
| `documents.py` | 商城端点的行 → 一篇文档 (`# 标题` + 正文) |
| `chunking.py` | 文档 → chunk 行 (显式主键 `{slug}-{序号}`, 按把来源类型查参数) |
| `embeddings.py` | 本地 bge-m3 (稠密 + 稀疏一次编码; 只认本地模型, 拒绝静默下载) |
| `milvus.py` | 单 collection `ca_knowledge`: 全量重建 + 稠密稀疏混合检索 |
| `rerank.py` | 本地 bge-reranker-v2-m3; 三级降级分支 |
| `query_rewrite.py` | 检索前的 LLM 改写 (失败降级原查询) |
| `retriever.py` | 检索门面 (async: 同步重活丢线程池, 不堵事件循环) |
| `index.py` | 索引脚本 (`python -m CharApp.minimall.knowledge.index`) |
| `prewarm.py` | 启动预热 (服务起来后后台加载两个模型, L5-D3) |

**两条贯穿全包的纪律** (都从 charplot 那份搬来, 理由见各自的模块):

1. **模型只认本地** (`resolve_local_model_path`): 缺失时报错 (embedding) 或降级
   (rerank), 绝不触发库级自动下载.
2. **chunk 主键显式**: `{slug}-{序号}` 可反解来源, 逐句引用拿它做稳定标识.

**本包不再导出** (没有 `__all__`): 各模块自己就是入口 (`retriever.py` 给检索门面,
`index.py` 给索引脚本, `prewarm.py` 给启动预热), 而**在这里再导出会把名字搞乱** ——
`from CharApp.minimall.knowledge import prewarm` 到底是模块还是那个函数? 包属性
先命中函数, 于是拿到的东西与"看起来的模块"不是一回事 (改用例时踩过). 要用哪一件
就从哪个模块拿, 路径即名字.
"""
