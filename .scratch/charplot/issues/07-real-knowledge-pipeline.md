# 07 — 真实知识管道

**Status:** done

**Blocked by:** 03 — 旅程创建与生成链路

**What to build:** 用真实 AI 管道替换 03 的 stub 图谱生成：LangGraph StateGraph 编排四阶段（归一化解析文档/链接 → 主内容分析 → 联网搜索增强 → 图谱解构），DeepAgents subagent 承担检索环节；检索源抽象可插拔（网络搜索 / Context7 官方文档 / 知识库 / 文档），材料输入也执行搜索增强（统一管道，ADR-0002）。产出图谱严格遵循 03 定义的数据契约，04-06 不受影响。

**Acceptance criteria:**
- [x] 文本 / 文档 / 网页链接输入均产出真实知识图谱（章节 + 知识点 + 依赖边）
- [x] 联网搜索增强执行（材料输入也搜），检索源抽象可插拔
- [x] 图谱质量：与输入内容相关、依赖关系合理、可支撑出题
- [x] 替换 stub 后 04/05/06 功能无回归（契约不变）
- [x] 生成任务可失败重试，SSE 阶段进度真实反映各阶段

**References:** DESIGN.md §7 步骤 07；PRD B-2；SPEC §7.1 统一管道 / ADR-0002

---

## 实现记录 (2026-08-23)

**管道结构** (`project/charplot/`):

| 模块 | 说明 |
|------|------|
| `pipeline/graph.py` | LangGraph StateGraph 四阶段编排（parse → analyze → search → deconstruct），每阶段开头 emit 阶段事件（SSE 契约不变：5 事件序 / 15-35-60-90-100） |
| `pipeline/stages/parse.py` | 归一化解析：text 归一化 / link 抓取（httpx+bs4）/ file 经 Django 内部端点取内容 + 按格式解析 |
| `pipeline/stages/analyze.py` | LLM 主内容分析：主题/摘要/概念/建议检索查询，JSON 提取 + 重试（带错误反馈） |
| `pipeline/stages/search.py` | 联网搜索增强：DeepAgents 检索 subagent 承担，材料输入也搜（统一管道） |
| `pipeline/stages/deconstruct.py` | 图谱解构：材料+分析+检索结果 → 契约图谱，本地契约校验 + 重试反馈 |
| `pipeline/contract.py` | CONTRACT.md v1 的 FastAPI 侧校验（与 Django 落库端同逻辑），sources 追加字段 |
| `pipeline/parsers.py` | txt/md/html/pdf/docx/pptx 解析 + 链接抓取（复用项目解析库） |
| `pipeline/sources/` | 检索源抽象（SearchSource 协议 + SearchResult 统一结构）+ Tavily / Context7 / 文档材料 / 知识库预留 |
| `agents/search_agent.py` | DeepAgents 检索 subagent：挂检索源工具 + ToolStrategy 结构化输出（DeepSeek 不支持 json_schema response_format，实测 400） |

**检索源可插拔**：`build_sources(material_text)` 按配置构建 —— Tavily（无 key 降级跳过）/ Context7（公开 API，失败降级）/ 文档材料（非空才挂）/ 知识库（预留，Issue 10 接入 Milvus）。新增源 = 实现协议 + 注册。

**Django 侧**：新增内部端点 `GET /api/charplot/journeys/{id}/content/`（X-Internal-Token）返回源文件 base64（CONTRACT.md §5 预留位）。

**关键实现决策**：
- DeepSeek 不支持 json_schema response_format（400）→ ToolStrategy（function calling）路径；agent 返回 dict（AgentState），结构化输出取 `result["structured_response"]`
- agent 递归上限提高到 20（默认 10 不够多轮检索）；prompt 收紧查询数与每源调用次数
- LLM 输出统一走「文本 JSON 提取 + pydantic 校验 + 重试反馈」（不依赖 provider structured output）

**验收验证**：
- FastAPI 39 测试（契约/阶段/重试/解析器/检索源）+ Django 148 测试全过；Ruff/format 全过
- 真实 LLM 端到端：`我想学 Python 装饰器` → 28s → 5 章节 12 知识点，契约校验通过，12/12 知识点带来源引用（Tavily web 检索 9 条结果真实命中）；图谱章节/依赖边结构合理
- 已知限制：Context7 匿名限速（200 req/min），超限自动降级为空结果，不影响管道
