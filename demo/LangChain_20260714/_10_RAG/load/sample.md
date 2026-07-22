# RAG 测试文档

## 项目简介

这是一个用于 **RAG (Retrieval-Augmented Generation)** 测试的示例文档。

## 团队成员

| 姓名 | 年龄 | 城市 | 职位 | 技能 |
|------|------|------|------|------|
| Charlotte | 28 | 北京 | 高级工程师 | Python, LangChain, 机器学习 |
| Savanna | 26 | 上海 | UI设计师 | Figma, UI设计, 用户研究 |
| Alex | 32 | 深圳 | 产品经理 | Scrum, JIRA, 路线图规划 |
| Bella | 29 | 杭州 | 数据科学家 | TensorFlow, SQL, 统计学 |
| Charlie | 35 | 广州 | 架构师 | 微服务, Kubernetes, 系统设计 |

## 技术栈

### 后端
- **Python 3.12+**
- LangChain / LangGraph
- FastAPI
- PostgreSQL + pgvector

### 前端
- React 18
- TypeScript
- Tailwind CSS

### 基础设施
- Docker
- Kubernetes
- Redis
- Kafka

## RAG 架构说明

RAG 的核心流程分为三个阶段：

1. **索引阶段 (Indexing)**：将文档分割为 chunks，通过 embedding 模型向量化后存入向量数据库
2. **检索阶段 (Retrieval)**：用户提问时，将问题向量化，在向量数据库中检索最相似的 top-k 个 chunks
3. **生成阶段 (Generation)**：将检索到的上下文拼接在 prompt 中，由 LLM 生成最终回答

## 注意事项

- 向量维度需与 embedding 模型匹配
- chunk size 通常在 500-1000 tokens 之间
- 检索时建议设置相似度阈值过滤低质量结果
- 生产环境建议使用 PostgresStore 替代 InMemoryStore
