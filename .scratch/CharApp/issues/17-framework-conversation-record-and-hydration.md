# 17 · 框架侧：会话记录 + 水合 + 会话端点

**Status:** ready-for-agent

**Type:** task

**Blocked by:** 无（可与 15、16 并行；18 依赖本片）

**上游:** `../PRD.md` §4.9（L2.5 行）、`CharApp/docs/PLAN.md` §5（L2.5）、`CharApp/CONTEXT.md`（会话 / 运行 / 快照 / 记录 / 上下文视图）

## 做什么

四件事，把「会话」这个概念在框架里补完整：

1. **记录**（给人看的那份）：`ConversationRecorder` 在每次运行收尾把这一轮投影进 `db/` 层（`charagent_threads` / `charagent_runs` / `charagent_messages`）。那几张表与仓储**早就写好了，却从来没有一个生产调用方** —— 只有 `PostgresCheckpointSaver` 用过 `PgDatabase` 与 `checkpoints` 表（`server/` 至今零 import `db`）。本片是 `db/` 包的第一个真实调用方
2. **身份**：`RunContext` 加 `tenant_id` / `user_id` 两个 typed 字段（框架要按属主查列表）
3. **水合**：让**重启后的同一段会话**能拿回历史 —— 前端（`/history`）与**模型**都要拿得回
4. **端点与淘汰**：`GET /conversations`（列表）+ `SessionRegistry` 的空闲淘汰

> **先说清一件容易误解的事**：换成 Postgres 快照后端**不会**自动让历史回来。今天历史只在进程内存里，而且不只是前端看不到 —— `ChatSession` 构造时 `_history` **只有一条 system 提示**，server 路径**从不调用** `resume()`，所以模型也看不到上一进程聊过什么。换后端解决的是"快照能不能留下来"，本片解决"**有没有人去读它**"。

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| `RunContext` **不在 `server/`**，定义在 `agent/provider.py`，只有 `thread_id` 与不透明 `payload` | `CharAgent/agent/provider.py:54-77` |
| **框架生产代码没有一处读 `.payload`**（全仓 `.payload` 只出现在测试里） | 全仓 grep |
| 模块 docstring 的原话：「**框架不解释 RunContext.payload**: 里面放什么 (用户 ID / 租户 / 语言...) 是业务的事...框架唯一认识的是 `thread_id`」 | `CharAgent/server/utils/types.py:19-21` |
| `create_app(*, context_provider, session_provider)`；三条路由都是它内部的闭包；`app.state` 只挂 `session_registry` / `run_registry` | `server/app.py:119-145` |
| `SessionRegistry._entries` 是纯进程内 dict、**只增不减**；`_busy` 在 await **之前**同步置位；`release` 幂等；`entry()` 是只读查 | `server/sessions.py:139-202` |
| 它的模块 docstring 明写：「对话历史在会话对象的**内存**里...构造时**不从快照恢复**」与「**不淘汰**: 登记表只增不减」 | `sessions.py:10-12`、`:28-30` |
| `ChatSession.__init__` 的 `_history` 初始只有 `[{"role": "system", ...}]` | `client/session.py:161-163` |
| `ask()` 只是 append 提问再交给 loop；**没有任何路径恢复历史** | `session.py:243-244` |
| `resume()` 的语义是「**接着跑**」（会把欠着的工具调用补做完、再问一次模型），**不是"加载历史"** | `session.py:246-267` |
| `_reclaim_progress` 用 `len(messages) > len(self._history)` 比进度，前提是「快照的历史永远是会话历史的前缀」 | `session.py:347-355`、`:337-342` |
| `ChatSession` 有现成的只读口：`saver_name` / `saver_capabilities` / `frame_count` / `history_frames` / `history_table` | `session.py:191-285` |
| `/history` 现在读 `registry.entry(thread_id).session.history` → `conversation_of` 投影 | `server/app.py:273-278`、`server/history.py:39-64` |
| `/history` **不建会话**，有专门用例钉住这一点 | `tests/test_server_history.py:179` |
| `conversation_of` 逐条**新建** `{role, content}` 两个键（白名单）；`DISPLAY_ROLES = {user, assistant}` | `history.py:15-16`、`:36` |
| `threads` 表：`thread_id` / `tenant_id` / `user_id` / `title` / `status` / `created_at` / `updated_at` + 两个索引（`tenant+updated` / `user+updated`） | `db/schema.py:82-132` |
| `title` 的注释原文：「会话标题 (由首条用户消息生成, 前端列表用)」；`updated_at`：「会话列表按它排序 (新的在前)」 | `db/schema.py:98-104`、`:126-131` |
| `messages.hidden` 的注释原文：「内部消息: true = 不展示给前端 (**续写指令 / 压缩摘要 / 工具回填 / 带工具调用的中间轮**)」 | `db/schema.py:226-288` |
| `messages.reasoning` 的注释点明分野：「这是「给人看」, 后者是「喂模型」」 | `db/schema.py:252-258` |
| `runs` 表有 `status` / `model` / `prompt_version` / `total_tokens` / `total_cost` / `turn_count` / `error` / `finished_at` | `db/schema.py:134-224` |
| `ThreadsRepository` 只有 `add` / `get` / `list_for_tenant`，**没有** update / delete | `db/repositories/threads.py:34`、`:84`、`:89` |
| `ThreadsRepository.add` 插完**再读一次**（让库补的默认值出现在返回对象里）；重复 id 抛可读的 `DataStoreError` | `threads.py:60-82` |
| `list_for_tenant(tenant_id, *, user_id=None, limit=50)` 排序 `updated_at DESC, thread_id DESC`；`limit<=0` 返回空 | `threads.py:89-117` |
| `MessagesRepository.add_lines(*, thread_id, lines, run_id=None, noticed_at=None)` 收 `TranscriptLine` 列表；批量插入**禁止跨会话** | `db/repositories/messages.py:120-127`、`:108-113` |
| `add_turn` 写一对问答（assistant 的时间戳 +1 微秒保证顺序） | `messages.py:38-95` |
| `list_conversation(thread_id)` = 只要 `hidden IS FALSE`；`list_transcript` 不过滤 | `messages.py:162-188` |
| 「哪些消息给人看」的规则**已经写好了**：`visible_transcript` / `_is_visible` / `conversation_turns` / `count_visible` | `db/conversation.py:113`、`:167`、`:245`、`:304` |
| `_is_visible`：`user` 恒真 · `assistant` 看有没有 `tool_calls` · 其余假 | `conversation.py:177-186` |
| `RUN_STATUS_FOR_OUTCOME` 把 `LoopOutcome` 映射成 `RunStatus`；`ALLOWED_TRANSITIONS` 是现成状态机 | `db/state.py:103-110`、`:60-91` |
| `RunsRepository` 的更新写法：`update(...).where(...).values(...)` + `bool(rowcount)`；`try_transition` 是乐观锁 | `db/repositories/runs.py:108-154` |
| 事务形状统一 `async with self._session() as session:`（正常退出即提交，异常回滚） | `db/repositories/base.py:47-57` |
| `PgDatabase` 用**同步驱动 + `asyncio.to_thread`**（Windows 上 psycopg 异步连接在默认事件循环上不可用） | `db/database.py:6-19` |
| `checkpoints` 表**故意不加**到 `threads` 的外键（它是分区键） | `db/schema.py:377-441` |
| `PostgresCheckpointSaver.delete_thread` 存在，但**明确注明不属协议、不是清理入口** | `checkpoint/postgres.py:205` |
| 仓储 / 迁移用例标 `pg_db`，**默认不跑**（`addopts` 排除了）；`tests/test_db_store.py` 用真 PG + `charagent_test` schema 隔离，拿不到 PG 就 skip | `CharAgent/pytest.ini:5-10`、`tests/test_db_store.py:1-17`、`:51-102` |
| `SessionRegistry` 的用例用 `FakeSessions` 替身、**不经过 ASGI app**；`/history` 的用例用 `ToyContexts` / `ToySessions` 玩具替身驱动真 app | `tests/test_server_sessions.py:32-79`、`tests/test_server_history.py:65-91` |

## 具体任务

### 1. 身份：`RunContext` 加两个字段

- `RunContext` 加 `tenant_id: str` 与 `user_id: str`（**放哪、是否给默认值，见备注里的判断题**）
- 这是框架第一次**解释**身份字段（此前 `payload` 对它完全不透明）—— 在 `agent/provider.py` 的 docstring 与 `server/utils/types.py` 那段话里都要改写说明：**框架把它们当分区键与过滤键用，不解释含义**；`payload` 继续留给业务私货
- 破例理由写进去：要「列出某个用户的会话」，框架就必须按属主过滤，而这**不能**用不透明的 `payload` 做

### 2. 记录：`ConversationRecorder`

- 放框架（判据是 PRD §4.12 那条：换到 code assistant 也能用 → 框架），业务在装配处注入一个绑定了 `(tenant_id, user_id)` 的实例
- **写入时机**：每次运行收尾**写一次**（不是每轮写，减少写入放大）
- **写什么**：
  - `charagent_runs` 一行：`status` 由 `RUN_STATUS_FOR_OUTCOME` 映射（`LoopOutcome` 已有映射表）；`model` / `prompt_version` / `total_tokens` / `total_cost` 先留空 —— **L3 的成本记账就落在这几列上**，现在建起这一行是给 L3 打地基
  - 可见消息：`user` 行 + `assistant` 终局答复（`hidden=False`）
  - hidden 消息：工具回填的中间轮、压缩摘要（`hidden=True`）—— 复用 `db/conversation.py` 的可见性规则，**不要另写一套**
  - **会话行懒创建**：首次写入时按 `thread_id` 取，没有就 `ThreadsRepository.add(...)`，`title` 取首条用户消息截断（schema 注释就是这么写的）。因此**不需要**单独的「创建会话」端点
  - `updated_at` 要跟着每一轮更新（`RunsRepository` 的 `update().values()` 写法可参考）—— 列表排序全靠它
- **取消 / 失败那一轮也写**：那一轮的 `user` 行 + 一条 `hidden=False` 的 `system` 行（「这一轮没答完」）。用户确实说过那句话、页面上也显示了它，记录里不该凭空少一轮
- **写入失败**（用户 2026-09-22 明确要求"至少让用户看到"）：
  - 当次**尽力写**；失败只记日志 + 发 warning 事件，**绝不让这一句问不出来**
  - 同时在该 recorder 上留一个「这个会话有一轮没记上」的**内存标记**，下次该会话成功写入时**先补一条提示行**再写本轮（同一事务：要么都成，要么都不成）
  - 提示行形状统一为 **`role=system` + `hidden=False`**，正文「（这中间有一轮对话没能记录下来）」
  - **诚实边界写进 docstring**：写记录失败的同一时刻数据库大概率不可用，"当次写提示"很可能也失败；标记活在内存里，进程重启会丢。这不是完美方案，是**在"只记日志"与"上分布式协调"之间的取舍**
- **两个入口都要过**（与「脱敏只挂在服务入口」那次不同）：装配只有一处（`CharApp/minimall/service.py` 的 `session_for`），CLI 与服务都会经过它

### 3. 水合（默认惰性，可关）

- 形状：`ChatSession` 加 `hydrate` 开关（默认 `True`）+ 首次 `ask()` 内部惰性水合 —— **只有在 `_history` 只有一条 system 提示、且快照有帧时**才去 `load_latest`，把 `state.messages` 灌进 `_history`
- **不要用 `resume()`**：它的语义是「接着跑」（会把欠着的工具调用补做完、**再问一次模型**），不是"加载历史"。本片要的是第三件事：**读历史，然后正常 `ask`**
- **撞上「工具调用没有结果」的半路**（L3 的挂起上线后必然出现）：**补一条 `tool` 结果，正文「本次服务中断，这一步的结果未知」**，把那条未完成的调用一起收进历史
  - 用「**未知**」而不是「未执行」：进程可能死在执行中间，说"没执行"是撒谎
  - **为什么不是"丢掉那半截"**：丢掉之后模型看到的是「我从没调过这个工具」，下一轮很可能**重发**；如果那个工具是 `place_order` / `request_refund`，重发就是重复下单。补一条"结果未知"会把模型推向先查状态（读工具）而不是重发
  - **绝不能顺手把欠着的调用执行掉**（那等于服务重启后自动重放写操作）
- 还要处理 `_reclaim_progress` 的既有前提：它靠长度比较判断进度，文档里写着「快照的历史永远是会话历史的前缀」。水合之后这条依然成立（灌的就是最新帧），但要在注释里点明**它依赖的正是水合这一步**

### 4. 会话快照树：`AgentLoop.run(parent_id=)`

- 现状：`run()` 每次从零起一个 `LoopState`，写出的帧 `parent_id=None` —— 于是服务每重启一次，同一会话在快照存储里就多**一条新根**，旧链变孤儿（内容不丢，链接断）
- 加一个 keyword-only 参数 `parent_id: str | None = None`，赋给初始 `LoopState.last_checkpoint_id`；水合时把最新帧的 `checkpoint_id` 传进去，**接着写同一棵树**
- `resume()` 那条路不变（它本来就从快照起）

### 5. `/history` 改读记录表

- 现在读进程内 `session.history` 再投影。**改成读 `charagent_messages`（`list_conversation`，即 `hidden=False`）**，身份仍从 `ContextProvider` 来（`thread_id` + 新的 `tenant_id` / `user_id`）
- **这条改动的必要性不是偏好**：issue 16 之后快照会被压缩改写，而 `/history` 若还读会话内存，用户看到的历史会跟着模型省下的成本一起变短 —— 与「记录永不压缩」直接冲突
- **只读记录表，不留「读不到退回内存」的兜底**：两条来源会给出不同结果（记录表的 `hidden` 过滤 vs `conversation_of` 的 `user`/`assistant` 过滤），"同一段对话刷新两次看到不一样"是最难查的一类 bug
- 连带：`server/history.py` 的 `conversation_of` **退役或放宽角色白名单**（现在只放行 `user` / `assistant`，会把那条可见的 `system` 提示行吞掉）；`server/__init__.py` 的门面导出跟着走；`tests/test_server_history.py` 里几条关于投影的用例要搬家或改写
- `/history` 仍然**只读、不建会话**（既有纪律，别破）

### 6. 端点：`GET /conversations`

- 形状：`create_app(..., database: Database | None = None)` —— **给了才注册这组路由，不给就不注册**（与框架既有纪律一致：不配不改行为）
- 语义：返回**当前 `(tenant_id, user_id)`** 的会话，按 `updated_at` 倒序；**只返回 `status=active` 且有可见消息**的（没聊过的空会话不进列表）；分页只给 `limit`（仓储的既有形状，不发明 offset）
- 每条给：`conversation_id`（业务侧叫法 = `thread_id` 的第三段）、`title`、`updated_at`、`message_count`（可选）
- 框架侧**不认识**「买家」这个业务概念：它只认识 `tenant_id` / `user_id` 两个字符串
- 路由写进 `create_app` 里（与另三条同一个地方）；`app.state` 上挂的东西、异常处理器跟着更新

### 7. `SessionRegistry` 空闲淘汰（面试可讲点）

- 现状：`_entries` 只增不减 + 会话长驻内存 → 持久化与水合落地后，「重启不丢」会换成「**不重启就一直涨内存**」
- 加一个空闲淘汰（TTL，建议 30 分钟；`acquire` / `entry` 碰到就刷新 `last_used`），淘汰只丢内存对象，**下次 acquire 重新水合**
- **可以淘汰的前提正是水合**：这条依赖关系要写进注释与 docstring —— 淘汰策略的正当性来自「真相在快照与记录里，内存只是缓存」。**注意与 `_busy` 的交互**：正在跑的会话绝不能被淘汰（busy 集合是现成的判据）
- `/history` 走 `entry()`（只读查）时**不要顺手建会话**（既有纪律）

### 8. 测试

- 仓储 / 迁移类的用例挂 `pg_db` 标记（真 PG，`charagent_test` schema 隔离），**别忘验收时显式跑 `pytest -m pg_db`**（默认排除）
- `SessionRegistry` 的水合与淘汰：用 `FakeSessions` 替身（既有写法），不经过 ASGI
- 端点的用例照 `tests/test_server_history.py` 的玩具替身形状写（真 app + 假 provider）

## 验收

- [ ] 真机：聊两句 → **重启 CharApp 进程** → `/history` 拉回原话，且**模型接着上文答**（不是只有前端回来了）
- [ ] 水合撞上「工具调用没结果」的半路：补一条「结果未知」的 `tool` 结果；**后端工具一次都没被重放**（用例断言写工具没被调用）
- [ ] 水合后新写的帧挂在上一条链上（`parent_id` 有值，不是新根）
- [ ] `/history` 只读记录表：把一条消息标成 `hidden=True`，它**不出现在**返回里；记录表为空时返回空列表（不是 404）
- [ ] `/history` 仍然**不建会话**（既有用例仍绿）
- [ ] `GET /conversations` 只返回当前 `(tenant, user)` 的、`active` 且有可见消息的会话，按 `updated_at` 倒序
- [ ] 别的租户 / 别的用户看不到（隔离用例）
- [ ] 不传 `database=` 时**不注册**那组路由（既有 app 行为零变化）
- [ ] 记录：一轮正常问答写 1 条 `runs` + 1 条 `user` + 1 条 `assistant`；工具轮与压缩摘要进 `hidden=True`
- [ ] 取消 / 失败那一轮：`user` 行在、有一条「这一轮没答完」的可见 `system` 行
- [ ] 记录写入失败：日志 + warning 事件，**这一句照样答得出来**；下一次成功写入时先补一条可见的 `system` 提示行
- [ ] 会话行懒创建：第一次写入才建，`title` 取首条用户消息；`updated_at` 每轮更新
- [ ] `RunContext` 的 `tenant_id` / `user_id` 在框架里**只用于分区与过滤**，没有别处解释（用例或注释钉住）
- [ ] `SessionRegistry` 空闲淘汰：超过 TTL 的会话被释放；**正在跑的会话不被淘汰**；淘汰后再 acquire 能水合回来
- [ ] `pytest CharAgent` 全绿，**另跑** `pytest -m pg` 与 `pytest -m pg_db`；`ruff` 干净

## 备注

- **`RunContext` 加字段的位置是判断题**：给了默认值（`=""`）则向后兼容、谁都编译不过的只有"忘了填"；不给默认值则强制每个业务入口显式给出。建议**不给默认值**（装配处必须说清"这段会话属于谁"），代价是本项目之外的调用方要跟着改 —— 本项目只有 CharApp 一个调用方，可接受。CLI 入口的 `tenant_id` 用 `"minimall-cli"`（与网页端 `"minimall"` 分开，**副产品是网页左栏不显示 CLI 的会话**，这正是想要的效果）
- **`ThreadsRepository` 没有 update / delete 是本片要面对的现状**：`updated_at` 的更新与标题改写都需要新方法（issue 20 的重命名 / 删除 / 置顶也要）。本片只加**记录写入所需的最小集**（取 / 懒创建 / 刷 `updated_at`），其余留给 issue 20
- **为什么不把记录写在 `server/` 层**：CLI 入口就没记录了。也不放在业务侧：那要在框架的运行生命周期里找钩子、靠事件流重建顺序，脆
- **`checkpoints` 与 `threads` 故意不成外键**：快照是分区键、生命周期与会话行不必耦合（会话行删了，快照留在那儿也不影响续跑）；本片不动这个关系
- **与 L3 的分工**：本片把 `runs` 行建起来、把 `prompt_version` 那一列**留着空**。谁把实际命中的 prompt 版本写进去、谁算成本，是 L3 的事（PLAN §5 已写明）
