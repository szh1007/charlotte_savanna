# C21 · #64 无状态化与 graceful drain 教学文档

**Status:** done（2026-10-06）

**Type:** docs

**Blocked by:** —

**上游:** `CharAgent/docs/DESIGN.md` §4 ⑬ 的 #64；`CharApp/docs/PLAN.md` §5 的「明确不做」表；`.scratch/Charlotte/PLAN.md` §5

## 为什么要有这份文档（而不是做实现）

#64 决定**不做实现** —— 理由：对面试时的技术与功能演示没有实质帮助，而它要拉进来的东西（多实例部署、容器编排、服务发现）离「一个人的作品」很远。

**但它是面试的知识考点**。所以这一票的产出是**一份能应付问答的讲解**，不是代码。

## 落在哪

`.scratch/CharApp/interview/stateless-and-drain-notes.md`

理由：那里已有 5 份同格式底稿（`INTERVIEW.md` / `context-compaction-notes.md` / `estimator-and-trigger.md` / `hitl-approval-notes.md` / `observability-notes.md`），**新底稿放一起才能被一起找到**。（若你想让收尾阶段的东西自成一处，改放 `.scratch/Charlotte/interview/` 也行 —— 但那样两份底稿要一起搬。）

## 格式（沿用既有约定）

按 `INTERVIEW.md` 的五段式：**考点 / 知识点 / 类比 / Mermaid 图 / 口述话术**，按高频 / 低频 / 少数了解三级分层。**行业侧只引一手来源**（官方文档、一手工程博客），不引二手转述。

## 必须讲到的内容

1. **为什么 agent 的无状态化比普通 web 难**
   普通请求短、无状态；agent 任务**长且有状态** —— 一次运行可能几分钟到几小时（HITL 挂起可以跨天），直接 kill 就是丢进度

2. **两条路的取舍**
   - **sticky session**：把同一会话粘到同一实例。简单，但扩容/缩容/故障时那批会话要迁移，而且实例挂了就真丢了
   - **状态外置**：状态放外部存储，任何实例都能接着跑。复杂，但扩容与故障恢复都是免费的

3. **graceful drain 的完整流程**
   `SIGTERM` → **停止接新请求** → 等存量任务完成 → 超时兜底（超过就中断，靠外置状态在下一次恢复）→ 退出

4. **关键洞察：checkpoint 让 drain 从「必须」变成「可选」**
   状态外置 + 断点续跑是**让 drain 可以不那么完美**的底座 —— 没做完的任务下次从断点接着跑，而不是从头来过
   → **这一条正好是本项目的现状**：`CharAgent/checkpoint/` 已经落了三套实现（内存 / Redis / Postgres），HITL 的挂起-恢复也证明了「跨进程接着跑」是通的

5. **进程内队列 → 分布式队列的升级路径**（#20 的三个尺度：进程内 asyncio 队列 → 分布式锁 → 限流）

6. **配套三件**：分布式锁（#21 的 SETNX + TTL + 续租 + owner 校验）· 限流（#22 的四种算法 × 三个维度）· 幂等（#17，**已经落了**，且 HITL 的 resume 重放正好是它的第一个真实调用方）

7. **本项目如果要水平扩展，具体要改哪几处**（这一节是文档的价值所在 —— 不是泛泛而谈，是**指着自己的代码**说）
   - `CharAgent/server/sessions.py` 自己写明**部署是单进程**，`SessionRegistry` 是**进程内**的（含空闲 TTL 淘汰）
   - 挂起判据**已经落在 PG**（`needs_approval AND approved_at IS NULL`）—— 这一块**天然跨进程**
   - `charagent_tool_calls` 的幂等键也在 PG —— 同样天然跨进程
   - 所以真正缺的是：**会话登记表的外置**、**运行中的 run 的归属与迁移**、**SSE 推送的跨实例路由**（客户端连到 A 实例而 run 在 B 上跑）

## 验收

- [x] 按五段式写成，含至少两张 Mermaid 图（drain 时序 + 状态外置前后的拓扑对比）
      —— 全文 7 张：Q1 拓扑对比 · **Q2 drain 时序** · Q3 取舍路径 · Q4 中断三走向 · Q5 三尺度 · Q6 幂等三态 · Q7 SSE 三路
- [x] 有「类比」段（建议：**快递分拣中心**——包裹（任务）不绑定分拣员（实例），谁有空谁处理；分拣员下班前把手上的包裹放回传送带而不是扔了）
      —— Q1 / Q3 用分拣中心，Q2 用「医院门诊下班」（停号 → 看完 → 转诊 → 关灯，正对 drain 四步），Q4 游戏存档 · Q5 食堂打饭 · Q6 发票制度 · Q7 直播 vs 播客
- [x] 第 7 节逐条**指向本仓的具体文件与行号**
      —— 本票原文的「第 7 节」指「必须讲到的内容」第 7 条；在底稿里**落成 §4**：4.1「已经天然跨进程」（5 行）+ 4.2「单进程假设漏出来的地方」（5 行）+ 4.3「真要做的第一步」；共 20 余处 `文件:行号`，行号按 2026-10-06 工作区记
- [x] 行业侧引用只含一手来源
      —— 8 家：12-Factor · Kubernetes（Pod Lifecycle / Disruptions）· Redis（分布式锁 / Pub/Sub）· WHATWG HTML（SSE）· uvicorn（Server Behavior + 官方源码取默认值）· LangGraph（Persistence）；原文摘录在底稿 §8，逐条附链接
- [x] 有一节「**这个项目为什么不做**」，与 `PLAN.md` §5 的结论一致
      —— §5，引用 §5 表格原话并把理由展开成三句；同节列出「已经做完的那一半」（正是 drain 要用的底座）与「真要做的顺序」（§4.3）

## 改了哪些文件

| 文件 | 改动 |
|------|------|
| `.scratch/CharApp/interview/stateless-and-drain-notes.md` | **新增**（本票交付物，508 行 / 约 64 KB（UTF-8）） |
| `.scratch/Charlotte/PLAN.md` | §5 两行的票据编号修正：多智能体「C21」→ **C22**；无状态化「C20」→ **C21**（两行都错位了一格，与本票/`C22-multiagent-notes.md` 的实际编号对不上） |
| `.scratch/CharApp/interview/hitl-approval-notes.md` | 首行目录索引「一共五份」→ **六份**，补上新底稿（同目录底稿靠这几行互相找到） |
| `.scratch/CharApp/interview/observability-notes.md` | 同上（那份写的是「一共四份」，在 L3b 底稿加入时就已过期，一并更正为六份） |

## 实施记录

**1. 先读码，再写字。** 全文的「本项目」部分逐条对着源码写，不凭印象：`server/sessions.py`（会话登记 / 空闲淘汰 / 挂起闸门 / 单进程段）· `server/runs.py`（运行表 / 事件队列 / 收尾补终局事件）· `server/sse.py`（不含断线重连那条自述）· `server/app.py`（挂起判据的装配点）· `checkpoint/`（三实现 + Redis 版 Stream 形态）· `client/session.py`（水合 / 收回进度 / `_seal_pending_calls`）· `db/schema.py` 与两个仓储 · `retry/idempotency.py` · `CharApp/minimall/server.py:416` 的 `_serve`。所有 `文件:行号` 都当场 grep 核过（并在正文写明「行号按 2026-10-06 的工作区记」，因为 PLAN 里凡带行号的描述都会漂）。

**2. 行业侧按「只引一手」执行，抓取时逐条留原文。** 2026-10-06 一轮抓取，来源与要点：12-Factor VI 的 sticky session 判词 · K8s Pod Lifecycle 的 TERM → 宽限 → SIGKILL 与「endpoints 不是立刻移除」· K8s Disruptions 的 PDB · Redis 分布式锁的 `SET NX PX` + 唯一值 + `DELEX IFEQ` + 续租 + **fencing token disclaimer** · Redis Pub/Sub 的 at-most-once · WHATWG 的断线重连与 `Last-Event-ID` · uvicorn 的优雅停机四步 + `--timeout-graceful-shutdown`（**默认值不在文档里，去官方仓库 `uvicorn/config.py` 取的 `int | None = None`，正文如实写明这一处是读源码**）· LangGraph Persistence。
> 没找到一手依据的（如 AWS ALB 的 deregistration delay 默认值）**一律不写** —— 抓取时 AWS 文档页发生了重定向，拿不到原文，于是这一份里没有它。

**3. 两个取舍**：① 全文按 `INTERVIEW.md` 的五段式 + 三级分层（高频 3 / 低频 3 / 少数了解 1），每问末尾加一段「我项目里的做法」；② **没有把 §4 写进任何一个 Q 里** —— 它是这份文档唯一「别处没有」的东西（逐条指着自己的代码说扩展要改哪里），单独立节。

**4. 顺带发现并修正**：`PLAN.md` §5 的两行票据编号错位（见「改了哪些文件」）。另外 §5 那张表里的票据编号在正文其它处一律用文件名引用（如 `C21-stateless-and-drain-notes.md`），编号改完之后不再有歧义。

**5. 没做的**：没有跑测试（本票是纯文档，不碰代码）；Mermaid 只做了语法保守化处理（节点标签全部加引号、状态图用 `state "…" as X` 别名写法），**没有在本机渲染验证**。
