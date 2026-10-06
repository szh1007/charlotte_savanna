# C19 · 四条经历各自的 README

**Status:** done

**Type:** docs

**Blocked by:** 全部（门户写的是最终状态）

**上游:** `.scratch/Charlotte/PLAN.md` §2 组 E；§6.1 的 D3

## 现状（2026-09-30 核实）

| 目录 | README |
|---|---|
| `CharAgent/` | **没有** |
| `CharApp/` | **没有** |
| `project/rag_knowledge/` | 有，但 C02/C17 改了东西之后要更新 |
| `project/rag_text2sql/` | 有，但 C01/C15/C16 改了之后要更新 |
| `project/charplot/` | 有（自审质量很高），但 C03 修了表述之后要同步 |

**而根 `README.md` 里 grep `CharAgent|CharApp` = 0 命中** —— 一个面试官打开这个公开仓库（1119 个跟踪文件），**看不到你最好的作品的存在**。

---

## 每条 README 的统一形状

> 这一节是**模板**，四份都用它 —— 面试官点进任何一个目录，看到的都是同一套结构。

1. **一句话**：这是什么、解决什么问题
2. **架构图**：Mermaid 或 ASCII（不要贴大段代码）
3. **已实现 vs 已设计未实现对照表** ← **最要紧的一张表**
   - 理由：`CharAgent/docs/DESIGN.md` 里有 70 个难点，其中相当一部分是**设计储备不是实现**（#42-44 多智能体、#51-54 能力扩展的其余几项…）。不给这张表，**别人（或你紧张时）会把「设计过」说成「实现过」**，一问就塌
   - （2026-10-05 校：举过的例子里 **#31-33 记忆**（C12/C13/C30）与 **#50 MCP**（C14）两批已经落地，写表时按那时的实际状态列；MCP 那两条要连**边界**一起写 —— 暴露侧只读 + 单账户，见 ADR-0030）
   - 三列：能力 / 状态（✅ 已实现 · 🟡 部分 · ⬜ 未做）/ 一句话
4. **关键决策**：列 5–8 条，每条一行 + 指向 `docs/adr/` 的具体文件
5. **一行启动**：真的能复制粘贴跑起来的那种
6. **测试规模**：用例数 + 分类（哪些要外部服务）
7. **已知边界**：如实写。**记录「不做」与记录「要做」同样重要**

## 四份的具体内容

**`CharAgent/README.md`（新建）**
- 定位：从 0 手写的 agent 运行时，**零 LLM 框架依赖**（`pyproject.toml` 那 8 项依赖就是证据）
- 架构图：十三个包的分工（`agent` / `tool` / `stream` / `checkpoint` / `db` / `hooks` / `retry` / `model` / `redact` / `prompt` / `client` / `server` / `eval`）
- 决策指引：`docs/DESIGN.md`（70 个难点的地图）与 `docs/difficulties/` 十四册 —— **但要说明它是「难点地图」不是「实现清单」**，别让读者误以为全都做了
- 测试：1502 个用例；哪些需要真 Redis / PG

**`CharApp/README.md`（新建）**
- 定位：基于 CharAgent 的电商智能客服（业务验证载体）
- 架构图：浏览器 → Django BFF → CharApp 服务 → CharAgent，两条 `X-Internal-Token` 边界
- 决策指引：22 条 ADR
- **已实现 vs 未实现对照表**：L1a→L5 各阶段的落地情况
- 一行启动：`bash sh/charapp_demo.sh`（C04 建的）
- 加一节「**为什么值得看**」：三个「只有真跑过才知道」的细节（压缩视图隔轮失效 / 估算器坐标不一致 / **只裁不拒是假的**）—— 每个一行 + 指向对应 ticket

**`project/rag_knowledge/README.md`（更新）**：补 C02 与 C17 的结论、新基线数字、重跑日期

**`project/rag_text2sql/README.md`（更新）**：把「9 节点」改成「12 节点 / 9 个逻辑阶段」、§7 从「未落地」改成「已落地」并给数字、补 C16 的安全边界

**`project/charplot/README.md`（同步）**：C03 修掉的表述、补齐新状态

## 「RAG 工程」这条合并经历（D3 的落地）

**不新建第三个 README。** 两个项目各自站住，**在根 README 里用一节把两者串成一条经历**：

> 我做过两种形态的 RAG —— 一个是**固定管道**（摄取 → 三路召回 → RRF → 精排 → 溯源），一个是**带反馈闭环的 agent**（召回 → 生成 SQL → 真执行拿错误 → 校正 → 再执行）。管道形态的瓶颈在**摄取与排序**；agent 形态的瓶颈在**知识表示**与**闭环轮数上限**。

（这两句里的每一条都要能指向具体的报告数字或 ADR —— 别写没有依据的判断。）

---

## 验收

- [x] 五份 README（新建 2 + 更新 3）结构与 §模板 一致
      （三份既有的把七件套收进各自新增的「速览」块, 不重排既有正文 —— 见实施记录 §2）
- [x] **每份都有「已实现 vs 已设计未实现」对照表**
- [x] 每份的「一行启动」命令**真跑过一遍**
      （2026-10-06 五条逐条实测, 记录见实施记录 §3）
- [x] 每份的链接都能点开（ADR 文件名、ticket 编号、报告文件）
      （脚本化核对 6 个文件 115 条本地链接, 0 坏链; 修掉 6 处 `../.scratch/` 少一层的相对路径）
- [x] `CharAgent/README.md` 里说清 `DESIGN.md` 是**难点地图**而非实现清单
      （§2 表头前注 + §1「为什么手写」+ §5 已知边界三处写明）
- [x] 根 README 里「RAG 工程」那一节的每句判断都能指向数字或 ADR
- [x] 架构图用 Mermaid 或 ASCII，**不贴大段代码**
      （全部 ASCII/```text```; 既有 Mermaid 图原样保留）

## 改了哪些文件

**新建**
- `CharAgent/README.md` —— 定位（零框架依赖, 8 项依赖即证据）+ 十五包架构图 + 已实现对照表（按 70 个难点分组）+ 8 条关键决策 + 一行启动（实测输出）+ 测试规模（1739/1594/145）+ 已知边界
- `CharApp/README.md` —— 架构图（三条信任边界）+ L1a→L5 阶段对照表 + 8 条关键决策（ADR 指针）+ 一行启动（冷启动两个数）+ 测试规模（465）+「为什么值得看」三个真跑才现形的细节 + 已知边界

**更新**
- `project/rag_knowledge/README.md` —— 新增「速览」块（一行启动 / ASCII 架构 / 已实现表 / 6 条关键决策 / 测试 141 / 已知边界）; 修四处旧口径: §5.2-11 的标题片打折（0.7→1.0, C17 重扫）、§6.4 的「待重跑」占位与重复段落、§7.1 的「40 用例 / 4 份文档」、§1 的 rerank 模型名（v2-m3）; 目录树补 `scripts/`、`assets/eval_corpus/`、`compare.py`; 页脚
- `project/rag_text2sql/README.md` —— 新增「速览」块（含两个数字）; §6.1-2 的 AOV 旧例改掉（已修）; §8.9「无测试」改成 208 个; §5.3 评估耗时约 35 分钟; 页脚
- `project/charplot/README.md` —— 新增「速览」块（已实现表含 🟡 有意降级与 ⬜ Phase 2 / 7 条关键决策 / 测试 89+277 / 已知边界）; §5.5 补 FastAPI 侧 89; 页脚
- `README.md`（根）—— 新增「RAG 工程 —— 两种形态的 RAG」一节（对照表 + 两侧瓶颈各指向数字）; 顺带修 `### rag_knowledge` 两条自相矛盾的旧数（「50 用例」→ 83 题 / 15 篇）

**没动**（明确记录）
- 根 `CLAUDE.md`：§4.7 仍写「单图 9 节点」、§4.8 仍写「检索/解构 subagent」——两者都归 **C20**（C20 票面已认领 §4.8; §4.7 是本次读出来的同类项, 一并交给它, 见下）
- `CharAgent/docs/DESIGN.md`：④记忆 / ⑨扩展的「落地」段停在 2026-09-18（C12–C14 之后落地的那两批没记）——按「精准修改」不动, 已在 C19 报告里指出

## 实施记录

### 2026-10-06 · 开工前的口径核对（票面数字会漂, 全部按当天实测改写）

| 票面写的 | 实际（2026-10-06 实测） | 处置 |
|---|---|---|
| CharAgent「1502 个用例」 | **1739 收集 / 1594 默认跑 / 145 按 marker 排除**（`pytest --collect-only`） | 写实测值 |
| CharAgent「十三个包」 | **十五个**（C05 的 `structured_logging`、C14 的 `mcp_client` 在票面之后落地） | 架构图按 15 画（11 门面 + 4 不入根门面的） |
| CharApp「22 条 ADR」 | **31 条**（`docs/adr/` 0001–0031） | 写 31 |
| 其余项目测试数 | CharApp 465 / rag_knowledge 141 / rag_text2sql 208 / charplot 89 + 277 / Django 277 | 全部写进对应速览 |

### 2. 结构上的一处取舍（三份既有 README 怎么落地「模板七件套」）

既有的三份是 280–540 行的实测文档, 全文重排风险大、收益低。做法: **在开头插一个「速览」块把七件套补齐**（一行启动 / 架构图 / 已实现对照表 / 关键决策 / 测试规模 / 已知边界），既有正文一字不动地留在后面做深入节。这样「面试官点进任何一个目录看到同一套结构」成立, 而实测记录不搬家。

### 3. 「一行启动」实测记录（五条全跑）

| 项目 | 命令 | 结果 |
|---|---|---|
| CharApp | `bash sh/charapp_demo.sh --no-open` | 六步全过; Django 302（登录跳转）+ 服务 1007 `/openapi.json` 200; **冷启动 94 s, 其中服务就绪 29 s**（差额是知识库重建: 加载 bge-m3 + 重建 Milvus 集合）|
| CharAgent | `python -m CharAgent.client -q "3.5 公里换算成英里是多少"` | 真模型 + 工具调用 + Postgres 快照 2 帧, 1.7 s |
| rag_knowledge | `python -m project.rag_knowledge.app.api.server` | 8100 起; `/query/health` → `{"ok":true}` |
| rag_text2sql | `cd project/rag_text2sql && python main.py` + 一条真查询 | 8200 起; 「统计华北地区的销售总额」→ `[{"total_sales": 41099.5}]` |
| charplot | `python -m project.charplot.api.server` | 8004 起; `/ai/health` → `ok / redis ok / rerank 未降级（bge-reranker-v2-m3）` |

（前置: 本机 Docker 容器 redis / mysql:3307 / qdrant / ES / embedding / mongo / milvus 均由本次拉起。）

### 4. 评审（两轴子代理）与处置

**Standards 轴**: ① `CharAgent/README.md` 原先写「八份 fixture」实为 7 份（v1→v7）→ 已改 · ② 根 README 新节与上方 `### rag_knowledge` 的 50 用例自相矛盾 → 已改 · ③ rag_knowledge 速览把 C17 的修复标成 C18 → 已改 · ④ 两个分母（79 题诊断轮 / 83 题基线）换算处补口径注 · ⑤ `loop.py` 那句收紧（改为「四批纯加法未碰它; 改过它的都是动了循环语义的那几批」—— 核对过 `git log -- CharAgent/agent/loop.py`）· ⑥ charplot「FastAPI 7 端点」是 2026-09-08 的旧数（现 8 条路由）→ 速览里去掉该计数。

**Spec 轴**: 无范围蔓延; 数字逐项复核属实; 两条提示已处置（上面的 ②⑤）; 指路一处多余（`§7.5`）已收窄为 `§6.2-7 / §8.7`。

**交接给 C20 的清单**（本次读出来的, 不在 C19 范围）：
1. 根 `CLAUDE.md` §4.7「单图 9 节点」→「12 个节点 / 9 个逻辑阶段」（C01 已改 README, CLAUDE.md 漏改）
2. 根 `CLAUDE.md` §6.3 rag_text2sql 评估耗时 36 → 35 分钟（以 README 为准）
3. §4.8「检索/解构 subagent」→「检索 subagent」（C03 的分工, C20 票面已列）
4. 根 `README.md` 的模块表 / 作品集导览要把 `CharAgent` / `CharApp` 提到第一屏（C20 票面 §一）
5. 参考: 本次给五份 README 定的「速览」形状可以直接复用为导览的链接目标
