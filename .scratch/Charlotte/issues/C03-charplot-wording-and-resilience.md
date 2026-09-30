# C03 · charplot 表述修错 + 低危 bug + 韧性缺口

**Status:** todo

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

- [ ] README 的「出题 subagent」表述改掉；`agents/` 的真实职责写对
- [ ] 手动 kill FastAPI 再重启，`QuizView` / `LevelList` / `KBManage` 三页都能自行恢复到可操作态
- [ ] 三的十条逐条修完（能测的补测试，不能测的在实施记录里说明为什么）
- [ ] 四的处置逐条落定，仓库里不再有零引用的「有意保留」文件
- [ ] 五的闭包测试通过（若做）
- [ ] Django 侧 270 个用例、FastAPI 侧 78 个用例全绿

## 改了哪些文件

（实施时补）

## 实施记录

（实施时补）
