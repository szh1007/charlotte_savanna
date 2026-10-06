# C20 · 根 `README.md` + 根 `CLAUDE.md` 更新

**Status:** done

**Type:** docs

**Blocked by:** C19（已 done, 2026-10-06 提交于 `a3bc3f8`）

**上游:** `.scratch/Charlotte/PLAN.md` §7；`CharApp/docs/PLAN.md:194-197`（当初押后的理由与解除条件）

## 为什么这一票现在才做

`CharApp/docs/PLAN.md:194-197` 记着一条决定：

> 更新根 `CLAUDE.md` 与 `README.md` → **已决意压后**：2026-09-18 的指示是「两个模块完全实现之前不要动根文档」，2026-09-21 复核为「**整个项目正式完成之后**才同步」。

**2026-09-30 解除这条** —— 理由：**门户本身是交付物**，不是收尾动作。一个公开仓库的第一屏看不见最好的作品，等于那些工作没做。

---

## 一、根 `README.md`

**现状**：写的是「个人技术学习项目」，模块表里只有 `app/minimall/` 等 —— **grep `CharAgent|CharApp` 零命中**。

改动：

1. **项目简介改写**：从「学习项目」改成能反映实际产出的描述（四条经历的存在本身改变了这个仓库的性质）
2. **模块表加两个顶层目录**：`CharAgent/`（从 0 手写的 agent 运行时）与 `CharApp/`（电商智能客服）
3. **新增一节「作品集导览」**：四条经历各一行 + 指向各自 README —— 这是面试官的第一屏，也是 D3 里「把 RAG 两个项目串成一条」的落点（具体措辞见 C19 末节）
4. **`demo/` 标明为学习区**：它不属于业务代码（根 CLAUDE.md §1.1 已经这么说，README 要跟上）—— 1119 个跟踪文件里相当一部分是教程，**不标注会让仓库显得像杂物间**
5. 技术栈徽章补充或修正（现在没有 Milvus / 向量库的位置）

## 二、根 `CLAUDE.md`

改动：

1. **§3 项目结构**：加 `CharAgent/` 与 `CharApp/` 两个顶层目录及其内部结构
2. **§4 框架特定规范**：**新增一节讲 `CharAgent` 的约定**（依赖方向 `CharApp → CharAgent` 单向、框架对业务零知识、加能力不许动 `agent/loop.py`、`pyproject.toml` 的依赖清单就是「零框架依赖」的证据）
3. **§4.8 的表述修正**（**C03 留下的指针**）：现在写着「DeepAgents = 检索/解构 subagent」——「解构」也不是 subagent（`pipeline/stages/deconstruct.py` 是裸 LLM 调用），同 C03 的口径一起改
4. **§5 当前开发状态**：加 `CharAgent` / `CharApp` 的状态表；`project/charplot/` 那节按 C03 的结果校正
5. **§6.3 开发约定**：加 `CharAgent` / `CharApp` 的启动方式（含 C04 的 `sh/charapp_demo.sh`）与 `.env` 变量段（`CHARAPP_*`）
6. **§6.6 文档同步维护**：那条「每周六 21:00 主动询问」的约定在收尾阶段可以保留，但**「最后更新」字段要刷新**

## 三、顺带

- `requirements.txt`：本阶段新增的依赖（MCP SDK **已经在里面了**，不需要动；`langchain-mcp-adapters` **C14 之后确认没用上**（消费侧直接用官方 SDK —— 它产出的是 LangChain 工具，等于多一层翻译），可以删；`mcp` 那一项对应 `CharAgent/pyproject.toml` 的可选依赖组 `charagent[mcp]`）
- `.env.example`：补 `CHARAPP_MILVUS_URL` / `CHARAPP_MODELSCOPE_ROOT` 等 C08 新增的键；**`CHARAPP_MCP_USER_ID`（C14 已加）那份注释指向 ADR-0030，写文档时别把边界那两句丢了**
- 仓库根的 **`.mcp.json`**（C14 新增，项目级 MCP 配置）：根 README 的「快速开始」值得提一句「Claude Code 打开仓库就会认到 minimall 这个只读 server」—— 它是这四条经历里**最容易被当场演示**的一个入口

---

## 验收

- [x] 根 README 的第一屏能看出四条经历是什么，且每条都点得进对应的 README
      （新增「作品集导览」表紧随项目简介；13 个链接目标逐一实测存在）
- [x] 根 README 里 `CharAgent` / `CharApp` 有命中（这条是这次改动的**最低验收线**）
      （副标题 / 模块表两行 / 导览 / 目录树 / 启动表 / 环境变量 / 文档导航, 共 20+ 处）
- [x] `demo/` 被明确标注为学习区
      （模块表「自学教程（非业务代码，可忽略）」+ 目录树「非业务代码, 可忽略」）
- [x] 根 CLAUDE.md 的目录树、状态表、启动命令、`CHARAPP_*` 段都补齐
      （§3 加 CharAgent/ CharApp/ 两个顶层块 + sh/charapp_* 四条 + .mcp.json, 并去掉已删除的 AGENTS.md/AGENTS_SYSTEM.md；§5 开头加两节主线状态表；§6.3 加 CharAgent CLI 与 charapp_demo/client/backend 三条；§6.1 加「CharApp/CharAgent 用根 .env」一条）
- [x] **§4.8 的「解构 subagent」表述改掉**
      （改为「DeepAgents = **检索** subagent（图谱解构与出题是裸 LLM 调用 + 结构校验, 不是 subagent）」, 字面 grep「检索/解构」零命中）
- [x] `.env.example` 与代码里实际读的键**逐条对齐**
      （脚本按「代码实际读的键 vs 模板（含注释示例）」逐文件核对：根模板 0 缺；charplot 26/26；rag_knowledge 补 `RK_BGE_M3` 一档；video_downloader 补 `FREE_SUMMARY_DAILY` / `FREE_QA_DAILY` 与 `DEEPSEEK_*` 回退键。§三 点名要补的 `CHARAPP_MILVUS_URL` / `CHARAPP_MODELSCOPE_ROOT` 在 HEAD 已存在（C08/C14 已补）, `CHARAPP_MCP_USER_ID` 的 ADR-0030 指针与两句边界均在）
- [x] 「最后更新」字段刷新 → 2026-10-06

## 改了哪些文件

| 文件 | 说明 |
|---|---|
| `README.md` | 副标题与徽章（+CharAgent / PostgreSQL / Qdrant / Elasticsearch）· 项目简介改写 + 模块表加两行主线 · **新增「作品集导览」**（四条经历, D3 落点）· 目录树加 CharAgent//CharApp//.mcp.json · 快速开始（环境要求加 PG、启动表加 CharAgent/CharApp 两行、`.mcp.json` 一句）· 环境变量（加「主线专用（根 .env）」条）· 文档导航加两份主线 README · 顺带修 minimall 小节的模型数（9→11, 含 RefundRequest/KnowledgeArticle）与 API/页面描述 |
| `CLAUDE.md` | §1 概述与模块表（加两条主线）· §2 技术栈（+PG、+CharAgent 行）· §3 目录树（两主线块 / sh/ 四条 / .mcp.json / .scratch 两阶段 / minimall 子树补齐 / 去 AGENTS*）· §4.7 12 节点 · §4.8 检索 subagent · **新增 §4.10 CharAgent 约定**（依赖单向 / 零框架依赖 / 不许改 loop.py / 框架给协议业务给内容 / 四个包不进根门面 / 测试）· §5 开头加两节主线状态表 + 5.1/5.5/5.6/5.7 陈旧数字修正 · §6.1 根 .env 一段 · §6.3 CharAgent/CharApp 启动三条 + 评估 35 分钟 · §6.4 核心依赖 + 删依赖两头一起 · §5.9 基础设施行 · §7 Domain docs 指向 CharApp/CONTEXT.md + 31 条 ADR · 页脚日期 |
| `project/rag_knowledge/.env.example` | 补 `RK_BGE_M3`（本地路径与默认之间的中间档）一行注释示例 |
| `project/video_downloader/.env.example` | 补免费档每日配额两个键（`FREE_SUMMARY_DAILY` / `FREE_QA_DAILY`）与 `DEEPSEEK_*` 回退键（原先只在注释里提过） |

## 实施记录

### 2026-10-06 · `langchain-mcp-adapters` 的处置（用户决定：留着）

票面 §三 说它「可以删」（全仓确认零引用：只有 C14 的票据提过它，代码里一处没用 —— 消费侧走的是官方 SDK）。实施时：`requirements.txt` 的行删掉了，但 `pip uninstall -y langchain-mcp-adapters` 被权限拦下（动的是 venv 里已装的包），成了「文件删了、venv 还装着」的半状态。

**用户当天拍板：带上无所谓，不需要移除。** 于是两处都还原 —— `requirements.txt` 放回 `langchain-mcp-adapters==0.3.2`，为解释半状态临时加进 CLAUDE.md §6.4 的那句「删依赖要两头一起」也一并撤掉。**requirements.txt 最终不在本票改动清单里。**

### 两处编号上的取舍（避免打翻既有引用）

- **§4 新增的 CharAgent 一节编号为 §4.10**（排在 §4.9 代码质量之后）：§4.9 被多张票据引用为「注释标点一律半角」的出处，不重编号。
- **§5 的两节主线状态表用不编号标题**（插在 §5 导语与 §5.1 之间）：5.1–5.9 不动，票据里对 §5.6/§5.7 的引用继续有效。

### 评审（两轴子代理）与处置

**Spec 轴**：7 个验收框全部达标（`CharAgent`/`CharApp` 命中、`.env.example` 逐键核对、§4.8 字面复核、链接目标逐一存在、C19 移交的 5 条全部落到位）。抓到一条措辞过宽：README「每条经历的 README 采用同一套结构」对经历 4（deep_search/menu/video_downloader 仍是旧结构）不成立 → 已改为「经历 1–3 的 README」。

**Standards 轴**抓到两条硬事实错误 + 三条判断项，均已修：
① minimall 模型数：实为 **11 个**（9 + `RefundRequest` + `KnowledgeArticle`）,  README 与 CLAUDE.md 两处「9 个」→ 已改并补齐 agent 接入一行；
② README 导览措辞（同上）；
③ §5 CharAgent 行的「限流 / 分布式锁」引用外延过大（PLAN §5 未覆盖）→ 改指 `DESIGN.md` #20–#22；
④ 两份目录树把 `client/eval` 也说成「可选依赖组」→ 改为「可选依赖组 server / mcp 按需装」；
⑤ video_downloader 回退说明与新注释重复 → 合并成一段。

### 与 C19 的关系

C19 的记录里给 C20 留了 5 条交接（§4.7 节点数 / §6.3 耗时 / §4.8 措辞 / README 第一屏 / 速览形状）—— 已全部落地。C19 提交于 `a3bc3f8`（用户手动提交），本票在其之上。
