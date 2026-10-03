# C03 · charplot 表述修错 + 低危 bug + 韧性缺口

**Status:** done

**Type:** fix

**Blocked by:** —

**上游:** `.scratch/Charlotte/PLAN.md` §2 组 0；`project/charplot/README.md` §7「已知问题与改进建议」（2026-09-08 自审）；2026-09-30 的代码审查

## 为什么这一片排在最前

charplot 在作品集里承担的是「**用主流框架把 AI 能力落成产品**」，讲述重心是**工程约束**。而它有一处**表述与代码不符**，性质是「造假」不是「夸大」—— 只要面试官点开 `agents/` 目录数一下，就当场穿帮。

其余是 README §7 自己点名的短板，其中最值得修的是**韧性缺口**（README 自评第一）。

---

## 一、表述修错（最高优先）

**`project/charplot/README.md:76`**：

```
| C. 题目生成: 知识点 + 检索片段 → 题目 JSON(讲解+来源引用) | DeepAgents 出题 subagent | ✅ |
```

**不成立。** `create_deep_agent` 全项目**只出现 1 次**（`agents/search_agent.py:53`，检索用）；出题是 `pipeline/questions.py:157` 的裸 `model.ainvoke`。

**同时改**：`:29` 的「DeepAgents = 检索/出题 subagent」、`:93` 的目录注释。

**改成**：「DeepAgents = **检索** subagent」；出题那一行如实写「LLM 单轮生成 + 结构校验」。

> **连带影响**：`CLAUDE.md` §4.8 也写着「DeepAgents = 检索/解构 subagent」，措辞同样需要复核（解构 `pipeline/stages/deconstruct.py` 也是裸 LLM 调用，不是 subagent）。归 C19 处理。

---

## 二、韧性缺口（README §7.1 自评「最值得修」）

**症状**：FastAPI 重启 → 任务在 Redis 里还在但进程没了 → 前端三个页面**卡死在 generating / indexing 态且无恢复入口**。

| 页面 | 缺口 |
|---|---|
| `QuizView.vue` | 关卡生成中断后无退出 / 重试按钮 |
| `LevelList.vue` | 卡片停在 generating，无兜底动作 |
| `KBManage.vue` | indexing 态**禁用**了重试按钮 —— 而后端其实允许超 10 分钟陈旧重抢，**UI 没给入口** |

README 承诺的「SSE 404 → 前端兜底重新生成」**只在 `JourneyDetail.vue` 实现了**。

**做法**：
1. 把 `client.ts:691` 已封装但**从未接线**的 `getTaskStatus` 接上 —— 进入这三个页面时先查一次任务状态，不存在（404）就把实体的状态机推回可重试态并给按钮
2. `KBManage.vue` 的 indexing 态放开重试按钮（后端本来就允许重抢）
3. 三个页面各补一条退出路径

**验收**：手动 kill 掉 FastAPI 再重启，三个页面都能自己恢复到可操作态，不需要刷浏览器。

---

## 三、低危 bug（README §7.2 / §7.3 已列，逐条核过）

| # | 位置 | 问题 | 做法 |
|---|---|---|---|
| 3.1 | `api/tasks.py:201,218` | 管道失败时 error 事件 `progress` **恒为 0**（`last_progress` 是死变量） | 报崩溃前那一阶段的进度 |
| 3.2 | `api/server.py:166-171` | `/ai/kb/search` 只 catch `ValueError`，Milvus / Django 不可达的 `RuntimeError` 直达 **500**，而 `django_client.py:291` 注释承诺转 **503** | 按注释实现；注释与实现对齐 |
| 3.3 | `services.py:1063` | 脏数据兜底抛基类 `LevelError`，而 `views_api.py:373-379` 只 catch 5 个子类 → **500 而非 400** | 改抛子类，或调用方补 catch 基类 |
| 3.4 | `views_api.py:393-399` | 重开接口只拦 `cleared`，**进行中（hearts > 0）可任意重置进度重答刷 XP** | 加 `hearts <= 0` 失败态校验（收益有界，10 级封顶，但规则上不该允许） |
| 3.5 | `services.py:699-700` vs `dashboard.py:220` | 复习衰减用 **UTC** `.date()`，仪表盘用 **`localdate()`** → 凌晨窗口复习排序与弱项清单**可能差 1 天** | 统一到 `localdate()` |
| 3.6 | `services.py:906-936` | update-in-place 尾部删题**未逐题确认无 Attempt** → 极端序列（重开答完全部旧题 + 重生成题数变少）可能连带删掉历史 Attempt | 删前逐题判 `Attempt` 存在性 |
| 3.7 | `admin.py` | `CharplotReviewReport` **未注册后台**（其余 11 个模型都注册了） | 补上 |
| 3.8 | `HeartsBar.vue:19` | 扣心动画**指错心**：`flying = max - now - 1` 是倒序索引，应为 `now` | 改索引 |
| 3.9 | `QuizView.vue:145-147` | 关卡加载失败后**残留 loading 骨架** | 失败态清骨架 |
| 3.10 | 多处 | 过时注释：`QuizView.vue:318`「来源引用将在接入真实知识源后显示」（其实已接通）、`KBManage`「stub 索引」、`Profile`「闯关答题未上线」、`SkillNode`「本期恒为空」 | 逐条改或删 |

---

## 四、死代码的处置（**要一个决定，不能两样都留着**）

| 位置 | 现状 | 建议 |
|---|---|---|
| `pipeline/stub.py`（70 行） | 全仓零 import，README §3/§7.3 自认「有意保留」 | **删** |
| `services._stub_questions:490-586`（约 97 行） | 定义后无任何调用点 | **删**（若兜底干扰词仍需要，抽成常量保留那几行） |
| `client.ts` 的 `checkDjangoHealth` / `checkAiHealth` | 未使用的导出 | 接上（健康检查面板）或删 |
| `rag/rerank.py:100-107` | README §1/§2 强调「rerank 必配」，实现却在本地模型缺失时**静默降级为 Noop**（只 warning） | **改 README 表述**为「缺失时降级不精排」，并在 `/ai/health` 里暴露 rerank 的真实状态 —— 让「架构意图」与「运行时事实」不再打架 |

> **理由**：README 说「有意保留的死代码」，在面试官眼里和「忘了删」**分不出来**。而这一片的定位就是「每个失败面都处理过」—— 留着一堆零引用文件与这句话是矛盾的。

---

## 五、顺带补一条最小的 agents 测试（可选，0.5 小时）

`agents/` 是**零测试覆盖**（`tests/conftest.py:57-60` 直接把 `run_search_agent` 换成假函数），面试官问「DeepAgents 那块你怎么测的」时没有答案。

**最小做法**：测 `agents/tools.py:24-40` 的**闭包绑定** —— 代码里有一处注释写着「显式用默认参数 `_src=src` 绑定闭包，规避循环变量捕获导致全部工具指向最后一个源」。这条正是**一个值得被测试钉住的坑**：构造 2 个源，断言两个工具各自指向正确的源。

（真实 DeepAgents 执行路径的覆盖不在这一片，属「核实后不做」——它需要真模型且输出非确定，性价比不成立。）

---

## 验收

- [x] README 的「出题 subagent」表述改掉；`agents/` 的真实职责写对
- [x] 三页能自行恢复到可操作态（用**伪造的孤儿任务态**等价复现「kill 掉 FastAPI 再重启」：Redis 里 hash 仍在 running、执行体已不在进程注册表 —— 这正是重启留下的状态。浏览器实测 `QuizView` → 「题目生成失败 / 生成任务已丢失」+ 重试与退出按钮、`LevelList` → 卡片「生成失败 · 重试」与「生成已中断」+ 重新生成；`KBManage` 浏览器侧因无 admin 凭据未跑（伪造 staff 会话被权限拦下，未强行绕过），后端侧已验：SSE 出终止帧 + Django 侧 kb → failed + 界面按钮由 `canIndex` 放开）
- [x] 三的十条逐条修完（3.1/3.2/3.3/3.4/3.5/3.6 有测试钉住并做过「把 bug 放回去」核对，3.7/3.8/3.9/3.10 见实施记录）
- [x] 四的处置逐条落定，仓库里不再有零引用的「有意保留」文件
- [x] 五的闭包测试通过
- [x] Django 侧 277 个用例（原 270 + 新增 7）、FastAPI 侧 89 个用例（原 78 + 新增 11）全绿

## 改了哪些文件

**表述修错（一）**
- `project/charplot/README.md` —— 出题那行改「LLM 单轮生成 + 结构校验 (非 subagent)」；三件套分工改「DeepAgents = 检索 subagent」；目录注释补「出题/解构为裸 LLM 调用, 不在本目录」；顺带把「rerank 必配」改成如实口径
- `app/charplot/models.py` —— `CharplotQuestion.sources` 的「当前 stub 留空占位」改成「检索片段来源, 可空」

**韧性（二）**
- `project/charplot/api/tasks.py` —— 新增 `_reap_orphan_task` / `_mark_entity_failed` / `_progress_of` / `_last_progress`；`_init_task` 增写 `entity_seq`（出题任务用于定位关卡）；`event_stream` 无新事件时先做孤儿判定
- `app/charplot/services.py` —— 三个 `mark_*_failed` 加 `_stale_task_mark` 守卫（旧任务不覆盖新任务的运行）
- `project/charplot/frontend/src/api/client.ts` —— 新增 `probeTaskAlive`（三态：运行中 / 已死 / 探测失败不下结论）
- `project/charplot/frontend/src/views/QuizView.vue` —— 新增 `task_lost` / `load_error` 两个 phase；`ensureGenerated` 先探任务再决策；SSE `closed` 复活探测；生成中/失败/丢失三态都有重试 + 退出
- `project/charplot/frontend/src/views/LevelList.vue` —— `genLost` 态 + `syncGeneratingLevels`（探活 → 订阅或给按钮）；卡片加「返回地图 / 重新生成」
- `project/charplot/frontend/src/views/KBManage.vue` —— `canIndex` 放开 indexing；`taskLost` 提示 + 关闭按钮；`openDetail` / SSE `closed` 均探活

**低危 bug（三）**
- `project/charplot/api/tasks.py` —— 三个执行体统一报崩溃前进度（3.1）
- `project/charplot/api/server.py` —— `/ai/kb/search` RuntimeError → 503（3.2）
- `app/charplot/views_api.py` —— 答题视图改捕基类 `LevelError`（3.3）；重开接口加失败态校验（3.4）
- `app/charplot/services.py` —— 复习衰减改 `localdate()`（3.5）；尾部删题逐题判 Attempt（3.6）
- `app/charplot/admin.py` —— 注册 `CharplotReviewReport`（3.7）
- `frontend/src/components/HeartsBar.vue`（3.8）、`views/QuizView.vue`（3.9 骨架收敛 + 3.10 文案）、`views/Profile.vue` / `views/SkillTreeMap.vue` / `components/SkillNode.vue`（3.10 过时文案）

**死代码处置（四）**
- 移除 `project/charplot/pipeline/stub.py` 与 `app/charplot/services.py::_stub_questions`（含 `_STUB_DISTRACTORS` / `_STUB_QUESTION_COUNT`）—— 归档到仓库外 `D:\__WorkSpace__\Temp\charplot-c03\`（未真删）
- `project/charplot/pipeline/{__init__,graph,contract}.py` —— 三处随之过时的 stub 注释改写
- `client.ts` 的 `checkDjangoHealth` / `checkAiHealth` → 新组件 `components/HealthPill.vue` + 接入 `App.vue` 导航
- `project/charplot/rag/rerank.py` —— 降级决策收成唯一来源 `_resolve_local_model`，新增 `rerank_status()`；`api/server.py` 的 `/ai/health` 暴露 `rerank` 字段

**测试**
- 新增：`project/charplot/tests/test_task_reaper.py`（5）、`tests/test_search_tools.py`（2）
- 扩充：`tests/test_kb_rag.py`（503 + health rerank 2 例）、`tests/test_pipeline_api.py`（error 进度 1 例）、`app/charplot/tests/test_quiz.py`（2）、`test_level_generation.py`（3）、`test_knowledge_base.py`（1）、`test_review_report.py`（1）

**文档**
- `project/charplot/README.md` §4.3 补孤儿回收语义、§6 更新 stub 清理结论、§7 按处置结果重写（含新增的「单进程前提」风险条）、计数更新
- 根 `CLAUDE.md` —— 仅同步会漂的计数（测试文件数 / 用例数 / 任务系统补「孤儿任务回收」）；**§4.8「DeepAgents = 检索/解构 subagent」的措辞按本票分工留给 C19**

## 实施记录

### 2026-10-03

**一处与票面做法不同的地方（重要）**：票面第 1 条写「进入页面查一次任务状态，不存在（404）就推回可重试态」。但 **FastAPI 重启不会删 Redis 里的任务 hash**（24h TTL），所以重启后 `GET /ai/tasks/{id}` 仍是 200 + `status: running`，404 那条路径**碰不到本票描述的主场景**（页面依旧空转 0%）。按用户决策补了后端**孤儿任务回收**：订阅时若「无新事件 + 执行体不在进程注册表 + 状态仍 running」→ 判定为重启遗留 → 写终止 error 事件 + 经既有内部端点把实体推回 `failed`。这样前端点的「重试」立刻真跑（不必等 10 分钟陈旧锁），并且 `getTaskStatus` 探测随后看到的是 `error`。判据以**单进程部署**为前提（`uvicorn.run` 单进程，README §7.4 已记风险条：将来加 worker 要换成跨进程心跳）。

**顺带修的（同一缺陷类，票面只点了管道一处）**：`_run_level_generation_task` / `_run_kb_index_task` 的 error 事件原本也恒 0，一并改为报崩溃前进度。附带加了 `_stale_task_mark`：旧任务的失败标记不覆盖新任务（回收器会并发触发这个竞态，是本票新引入的失败面）——否则孤儿回滚可能把正在跑的新任务标记成失败。

**「把 bug 放回去」核对**（两侧都做，确认用例不是空转）：

| 放回什么 | 结果 |
|---|---|
| error 事件 progress 写死 0 | `test_pipeline_error_reports_last_stage_progress` 红 |
| 去掉 `/ai/kb/search` 的 RuntimeError 分支 | `test_search_api_dependency_failure_503` 红（500） |
| 去掉孤儿回收里的实体回滚调用 | 4 条孤儿用例红（`test_live_task_not_reaped` 照常绿，反例不重复） |
| 复习衰减换回 `.date()` | `test_review_decay_uses_local_date` 红（排序翻转） |
| 尾部删题不判 Attempt | `test_save_tail_questions_with_attempts_kept` 红（历史被连带删） |
| 重开去掉失败态校验 | `test_restart_in_progress_level_rejected` 红 |
| 答题视图收窄回只捕 2 个子类 | 新增的脏数据用例红 + 既有 `test_answer_replay_rejected_400` 也红（正好反证捕基类的必要性） |

**3.7 ~ 3.10 为什么不补测试**：3.7（admin 注册）补了 1 条 `admin.site.is_registered` 断言；3.8/3.9/3.10 属前端动效与文案，仓库无前端测试运行器（`package.json` 只有 dev/build/preview），以 `vue-tsc -b` 类型检查 + 浏览器实测兜底，不新增测试基建（YAGNI）。

**真机验收（浏览器）**：起 Django 8000 / FastAPI 8004 / Vite 9004，新建临时旅程与知识库（未动 savanna 既有数据），伪造死任务后实测：

| 场景 | 结果 |
|---|---|
| QuizView（死任务 + SSE 有历史事件） | 自动落到「题目生成失败」，出「重试生成 / 返回关卡列表」；DB 侧 level → `failed`，任务 hash → `error` 且 `progress=60`（不再 0） |
| LevelList（同上，换新死任务走它自己的探测） | 卡片「题目生成中 60%」→ 数秒内自行变「题目生成失败 · 重试」 |
| LevelList（任务 id 根本不存在 → 404） | 卡片「生成已中断」+「返回地图 / 重新生成」（探测路径生效，且未再发起 SSE 订阅） |
| QuizView（任务不存在） | 新增的「生成任务已丢失」视图 + 两个按钮 |
| 顶部健康指示 | `业务 状态 ok, db ok, redis ok` / `AI 状态 ok, redis ok, rerank 精排`（`/ai/health` 新字段读数正确） |
| 浏览器控制台 | 仅一条预期的 404（探测本身），无 JS 报错 |
| KBManage | 浏览器侧未验（无 admin 凭据，伪造 staff 会话被权限拦下）；后端侧 curl 实测：SSE 出终止帧 → Django 侧 kb → `failed` |

夹具（journey 18 / kb 2 + 伪造的 Redis 任务键）先导出到 `D:\__WorkSpace__\Temp\charplot-c03\fixture_export.json` 再删除；savanna 的既有数据未触碰。

### 代码评审（两轴）带回的东西

**Standards 轴**（含一条会挂 pre-commit 的硬伤）：

| 发现 | 处置 |
|---|---|
| `api/tasks.py` 出题 error 那行 91 > 88 字符（E501，`ruff format --check` 也会改它） | 已 `ruff format`（拆行）；`ruff check` + `format --check` 全绿 |
| 根 `CLAUDE.md` 页脚日期未刷新（§6.6 要求同步时刷新） | 已改 2026-10-03 |
| `_stale_task_mark` 三处重复守卫形状（判定 + warning + return） | 抽成 `_is_stale_mark`（命中即 warning 并返回 bool），三处只留一行 `if …: return <entity>`；顺带改名（原名字像名词、实际答是否） |
| `rerank.py` 用 `reason.startswith("本地模型未找到")` 做控制流（字符串当类型用） | 新增 `_LocalModel` NamedTuple（path / reason / configured），是否告警由 `configured` 决定，不再猜文案 |
| 三个页面的「SSE 被拒 → 探测 → 判丢失」逐字重复 | 收敛为 `client.ts::subscribeTaskEvents(taskId, {onEvent, onLost})`，三页共用（重试/退出面板文案仍各页自持 —— 状态语义不同，不强抽） |
| `HealthPill` 的 `BackendHealth.label` 字段恒为常量却还要兜底重述 | 去掉该字段，label 在 pill 构造处直接给 |

**Spec 轴**（两条真缺陷）：

| 发现 | 处置 |
|---|---|
| `KBManage` 的 `taskLost` 跨知识库泄漏：先开一个死任务的库，再开 draft/ready 的库仍显示「索引任务已中断」（复位点只有 `beginSse`） | 已修：`openDetail` 开头复位（丢失态只属于当前库） |
| `QuizView` 把「任务其实已成功」误判为丢失：探测只认 `status === 'running'`，任务刚 done 时会落到 `startLevelGeneration` 重复出题（且 phase 从 answering 闪回 generating） | 已修：重拉后若已是 `ready` 直接进答题 |
| 孤儿回收在**两个并发订阅**下可能各自回收一次（非原子） | **判为可接受的良性竞态**，不做 SETNX/WATCH：两次写入的是同一个终态（status=error + 同一 message），实体标记幂等且 `_stale_task_mark` 保证同任务重复标记无副作用；改用「先占标记再写帧」反而引入「占了标记但帧没写成 → 任务再也回不了收」的新失败面。代价记在此处：并发订阅会各收到一帧 error（客户端收到第一帧即终止流，无实际影响） |
| 3.10 的同类过时注释漏了两处：`services.py::_kp_status` 文档「本期无关卡数据时传空集」、`test_skill_tree.py` 模块注释与用例注释「本期无 Level 数据恒为 0, 待关卡数据流入」 | 已按现状改写（关卡行已存在，注释以「测试不建关卡时计数为 0」表述） |
| 根 `CLAUDE.md` §4.8 仍写「rerank（必配链路）」 | 已补「本地模型缺失时降级不精排，`/ai/health` 暴露真实状态」（与 README 同口径） |
| KBManage 浏览器侧未跑 | 已在验收与上表如实标注，不假称已验 |

**遗留 / 交接**：
- `CLAUDE.md` §4.8 的「检索/解构 subagent」措辞按票面分工归 C19；`README.md`（根）未涉及本片内容。
- 本片新引入的单进程前提已写进 `project/charplot/README.md` §7.4，若将来要上多 worker 需先换判据。
