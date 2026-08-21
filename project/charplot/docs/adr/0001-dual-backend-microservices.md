# 双后端微服务（Django 状态/数据 + FastAPI AI 能力）

自用学习项目但定位要求完整产品架构，LLM 生成管道（解析 → 搜索 → 解构 → 出题）与业务数据（账号/闯关/分析）是两类不同性质的负载，故采用双后端微服务：Django 管账号体系、学习数据、后台分析、公开分享页；FastAPI 管全部 AI 能力（知识管道、RAG 索引/检索/生成、Boss 战对话流式）。双服务通过 HTTP + 共享 MySQL 通信。

**Status**: accepted

**Considered Options**:
- Django 单体：最简单，但 AI 管道与业务耦合，流式/异步受限
- FastAPI 单体：账号体系、迁移、Admin 后台自建成本高
- 双后端微服务（采纳）：职责切分后 Django 无 LLM 依赖、FastAPI 无业务耦合，独立演进，符合本项目"实践真实产品架构"的学习目标
