# 17 · 框架侧：会话记录 + 水合 + 会话端点

**Status:** done

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

- [x] 真机：聊两句 → **重启 CharApp 进程** → `/history` 拉回原话，且**模型接着上文答**（不是只有前端回来了）
      —— **2026-09-22 跑过**（Claude 代跑，见下「六、真机验收」）
- [x] 水合撞上「工具调用没结果」的半路：补一条「结果未知」的 `tool` 结果；**后端工具一次都没被重放**（用例断言写工具没被调用）
- [x] 水合后新写的帧挂在上一条链上（`parent_id` 有值，不是新根）
- [x] `/history` 只读记录表：把一条消息标成 `hidden=True`，它**不出现在**返回里；记录表为空时返回空列表（不是 404）
- [x] `/history` 仍然**不建会话**（既有用例仍绿）
- [x] `GET /conversations` 只返回当前 `(tenant, user)` 的、`active` 且有可见消息的会话，按 `updated_at` 倒序
- [x] 别的租户 / 别的用户看不到（隔离用例）
- [x] 不传 `database=` 时**不注册**那组路由（既有 app 行为零变化）
- [x] 记录：一轮正常问答写 1 条 `runs` + 1 条 `user` + 1 条 `assistant`；工具轮与压缩摘要进 `hidden=True`
- [x] 取消 / 失败那一轮：`user` 行在、有一条「这一轮没答完」的可见 `system` 行
- [x] 记录写入失败：日志 + **这一句照样答得出来** + 下一次成功写入时先补一条可见的 `system` 提示行
      —— 唯一没照做的是「warning **事件**」：记录跑在运行收尾**之后**，那条流已被终局事件关掉（见开放项 ③）
- [x] 会话行懒创建：第一次写入才建，`title` 取首条用户消息；`updated_at` 每轮更新
- [x] `RunContext` 的 `tenant_id` / `user_id` 在框架里**只用于分区与过滤**，没有别处解释（用例或注释钉住）
- [x] `SessionRegistry` 空闲淘汰：超过 TTL 的会话被释放；**正在跑的会话不被淘汰**；淘汰后再 acquire 能水合回来
- [x] `pytest CharAgent` 全绿，**另跑** `pytest -m pg` 与 `pytest -m pg_db`；`ruff` 干净

## 备注

- **`RunContext` 加字段的位置是判断题**：给了默认值（`=""`）则向后兼容、谁都编译不过的只有"忘了填"；不给默认值则强制每个业务入口显式给出。建议**不给默认值**（装配处必须说清"这段会话属于谁"），代价是本项目之外的调用方要跟着改 —— 本项目只有 CharApp 一个调用方，可接受。CLI 入口的 `tenant_id` 用 `"minimall-cli"`（与网页端 `"minimall"` 分开，**副产品是网页左栏不显示 CLI 的会话**，这正是想要的效果）
- **`ThreadsRepository` 没有 update / delete 是本片要面对的现状**：`updated_at` 的更新与标题改写都需要新方法（issue 20 的重命名 / 删除 / 置顶也要）。本片只加**记录写入所需的最小集**（取 / 懒创建 / 刷 `updated_at`），其余留给 issue 20
- **为什么不把记录写在 `server/` 层**：CLI 入口就没记录了。也不放在业务侧：那要在框架的运行生命周期里找钩子、靠事件流重建顺序，脆
- **`checkpoints` 与 `threads` 故意不成外键**：快照是分区键、生命周期与会话行不必耦合（会话行删了，快照留在那儿也不影响续跑）；本片不动这个关系
- **与 L3 的分工**：本片把 `runs` 行建起来、把 `prompt_version` 那一列**留着空**。谁把实际命中的 prompt 版本写进去、谁算成本，是 L3 的事（PLAN §5 已写明）


## 实际开发情况 2026-09-22

**一句话**：四件事全部落地 —— 记录（`ConversationRecorder`，`db/` 的第一个生产调用方）、身份（`RunContext` 加 `tenant_id` / `user_id`）、水合（`ChatSession` 第一次提问前读回快照，含「工具调用没结果」的补回填）、端点与淘汰（`GET /conversations` + `SessionRegistry` 空闲 TTL）。**不配 `database=` / `recorder=` / 关掉 `hydrate` 时行为逐字不变**。

### 一、拍板的开放项（ticket 没定 / 与代码对不上，实现时定下来的）

| 项 | ticket 说的 | 落地的 | 为什么 |
|----|------------|--------|--------|
| `/history` 的来源怎么切 | §5「只读记录表，**不留**退回内存的兜底」 + §6「不传 `database=` 时不注册**那组**路由（既有 app 行为零变化）」 | **按装配二选一**：给了库 → 记录表；没给 → 会话内存（老路径原样保留，`conversation_of` 与 `DISPLAY_ROLES` 因此**没退役**） | 两句话合起来只能这么读：没给库时 `/history` 若也消失，那是**既有 app 的行为变化**（旧部署的三条路变两条），与「零变化」直接冲突。禁令的本意是「不许在**运行期**从一条来源退回另一条」（同一段对话刷新两次看到不一样，是最难查的一类 bug）—— 那条照旧守死：装配选定一个来源，中途不换 |
| 「发 warning 事件」 | §2「失败只记日志 + 发 warning 事件」 | **不发事件**：`logger.warning`（带 traceback）+ 记录表里那条「这里少了一轮」的可见提示行 | 记录跑在运行**收尾之后**，而那条事件流已被终局事件关掉（`stream/bus.py` 第四条不变量；`server/runs.py` 的 `RunStream` 还会把序号记账带偏）。要发就得破坏契约。给用户的信号改走提示行 —— 它比一条转瞬即逝的事件耐久（刷新 / 换台设备都还在） |
| `run()` 怎么接着写同一条链 | §4「加 `parent_id`，水合时把最新帧的编号传进去」 | 同款，**另加** `LoopResult.last_checkpoint_id` | 只做水合那一半会留下更糟的形态：第二句问话拿着**过期的** parent 写帧 = 在那棵树上凭空岔一根（看着像 time-travel）。有了这个字段，**每一句**问话都接着上一段落，不必让会话自己去查库 |
| 已跑完的运行怎么落库 | §2「`runs` 一行，`status` 由映射表给」 | 新方法 `RunsRepository.add_terminal`（直接以终态 INSERT，`finished_at` 一起写上） | 走 `add` 再 `try_transition` 要三步三次往返，而 `created → finished` 本就非法（得先 running）；更糟的是会在库里留下「状态是终态而 `finished_at` 是 NULL」的行（NULL 的语义是「还没跑到终点」）。状态机管的是**活着的**运行怎么走，这里写的是它的结局 |
| 会话列表要的新读法 | §6 只说了语义 | 加 `ThreadsRepository.list_active_with_messages`（`status` + `EXISTS 可见消息` 都在 SQL 里） | 「有可见消息」是个 EXISTS 条件，取回来再逐条查就是 N+1。租户 / 属主过滤跟 `list_for_tenant` 同一条纪律（签名里不给「不带租户查全部」） |
| `conversation_id` 是什么 | §6「业务侧叫法 = `thread_id` 的第三段」 | 照做，并把这次结构解析单独放进 `server/conversations.py` 的一个函数里 | 这是框架**唯一一次**读会话编号的结构（平时只当它是不透明主键）。放进 db 层就把它变成数据层的事；留在 wire 边界上，将来编号格式变了只动这一处 |
| `message_count` | §6 标了「可选」 | **不做** | 列表页要它就得再来一条聚合查询（或 `count_visible` 逐条算），而本片没有任何调用方需要它。等前端真要显示「几条消息」时再加 |
| 「没答完」那条说明只给取消那一轮吗 | §2 只在「取消 / 失败」那一节点了名 | **跑完却没给出可见答复也补**（guard 刹车 / 纯工具收尾） | 用户看到的是「我问了一句、页面上什么都没有」——那和取消那一轮的处境一模一样（`finished` 说的是**运行**跑完了，`content=None` 说的是**没有答复**，两件事不矛盾）。不补的话，刷新之后那一轮在记录里就凭空消失了 |
| 读记录表读不到怎么办 | 没说 | 两条只读路把 `DbError` 翻译成 **503 + `record_store_unavailable`**（与 `ServerError` 同一套信封） | 库读不了是**可用性**故障而不是 bug：不翻译就会以未处理异常的形式冒出去，客户端拿到一个没有 code 的 500，转发方只能猜「是我请求错了还是它挂了」。写入那条路的降级照旧（日志 + 提示行）—— 读与写的处置本来就不同（读不到可以让调用方重试，写不进去只该记一笔） |
| 会话编号换了属主 | 没说 | 记录员发现既有会话行属于**别人**时记一笔 warning，但**照样写** | 编号是快照与记录共用的分区键，同一段编号换了属主（业务把两段对话编到了同一个键上）记录就会串在一起。框架不替业务决定该不该写（不写就是静默丢记录），但要让这件事看得见 |
| 「压缩摘要进 `hidden=True`」怎么落 | 验收行里点名了它 | 记录员多收一个 `summary=` 参数：**新压出来的**摘要才写（隐藏的 system 行） | 摘要不在账本里（压缩只是给模型的视图），所以得单独补一行;而「新不新」只有会话判得了（比较基准是它手上那份上一轮的摘要）—— 不判的话，一段长会话每轮都会攒一条一模一样的行 |

### 二、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `CharAgent/agent/provider.py` | `RunContext` + `tenant_id` / `user_id`（**不给默认值**）；模块与类 docstring 补「框架认识这两个字段，但只当分区键与过滤键」那段（含「它没破 payload 那条规矩」的说明） |
| `CharAgent/agent/loop.py` · `agent/utils/types.py` | `run(parent_id=)` 写进初始 `LoopState.last_checkpoint_id`；`LoopResult` + `last_checkpoint_id` |
| `CharAgent/db/recorder.py` | **新增**：`RunRecorder` 协议 + `ConversationRecorder`（会话行懒创建 / 运行行 / 消息行 / 刷活动时刻；取消与失败那一轮；写失败降级 = 日志 + 内存标记 + 下次补提示行）、`title_for`、三段文案常量 |
| `CharAgent/db/conversation.py` | + `recorded_transcript`（把截断续写的几段答复合回一条、正文取 `LoopResult.content`）与 `has_visible_answer`；模块 docstring 补「两个出口一个规则」 |
| `CharAgent/db/repositories/threads.py` | + `touch`（刷 `updated_at`）与 `list_active_with_messages`（会话列表那一份读法） |
| `CharAgent/db/repositories/runs.py` | + `add_terminal` |
| `CharAgent/db/__init__.py` · `CharAgent/__init__.py` | 门面导出六个新名字（根门面有防漂移用例守着） |
| `CharAgent/client/session.py` | `ChatSession` + `hydrate`（默认开）与 `recorder=`；`_hydrate_once`（惰性读回历史 + 补「结果未知」的回填 + 接着写链）、`_seal_pending_calls`、`_record` / `_record_unfinished`、`_parent_id` 维护 |
| `CharAgent/server/app.py` | `create_app` + `database=`（给了才注册 `/conversations`；`/history` 的来源也跟着它切）；`read_limit`（`limit` 查询参数） |
| `CharAgent/server/conversations.py` | **新增**：路由常量 + `conversation_id_of` + `conversation_row` |
| `CharAgent/server/history.py` | + `conversation_messages`（记录表那份投影）；`conversation_of` 留着给内存那份；模块 docstring 写清两个来源 |
| `CharAgent/server/sessions.py` | `SessionRegistry` 空闲淘汰（`DEFAULT_IDLE_TTL_SECONDS` = 30 分钟、`last_used`、`evict_idle`）；`SessionEntry` + `last_used`；模块 docstring 的「不淘汰」一段改写 |
| `CharAgent/server/__init__.py` · `server/utils/types.py` | 门面导出 + 两个插座文档里「框架不解释 payload」那段（现在认识三个结构字段了） |
| `CharAgent/tests/*` | 新增 `test_db_recorder.py`（17）· `test_server_conversations.py`（15）；`test_client_session.py` +7（水合 / 记账）· `test_checkpoint_resume.py` +3（parent 链）· `test_server_sessions.py` +7（淘汰与水合）· `test_server_history.py` +5（记录表来源 + 一条 pg_db）· `test_db_conversation.py` +4 · `test_db_store.py` +6（pg_db）；`doubles.py` + `FakeRecordDatabase`（读写都认，三个文件共用）；既有 `RunContext(...)` 调用点全部补上两个身份字段 |
| `CharApp/minimall/service.py` | `TENANT_WEB` / `TENANT_CLI`；`build_context(..., tenant_id=)`（必填 keyword）；`MinimallService.database` + `_recorder_for`；`aclose` 连库一起收 |
| `CharApp/minimall/server.py` · `cli.py` | 两个入口都建一个 `PgDatabase` 交给装配（构造不连库，库不在线也不拦启动）；网页端传 `TENANT_WEB`、命令行传 `TENANT_CLI`；`create_minimall_app` 把库交给框架（`/history` 与 `/conversations` 因此走记录表） |
| `CharApp/tests/test_recording.py` | **新增**（3）：两个入口都记账且分租户、重启后接得上上文、同一买家的两个入口是两个租户 |
| `CharAgent/tests/conftest.py` · `test_client_app.py` | 测试基建两处（见开放项 ⑤） |

### 三、顺带修掉的两处**既有**测试问题（与本片无关，但被本片跑红）

1. **`test_client_app.py` 跟着本机 `.env` 走**：那几个用例的前提是「新会话没有存档」，而后端在 `.env` 里是 `postgres` —— 跑过一次之后 `client-main` 就有帧了，于是「没有可恢复的快照」这条断言会在**第二次**跑时红。修法：文件级 autouse fixture 把后端钉成内存版（真的验「听环境变量」那条自己 `setenv`，不受影响）。
   **另**：我自己的两次全量跑往本机 Postgres 的 `charagent_checkpoints` 写了 36 帧（thread `client-main`，全部落在 23:00~23:01），已按 `thread_id='client-main'` 删掉（那张表当时只有这一批）。
2. **`db` fixture 挪进 `conftest.py`**：新增的那条 pg_db 端点用例要用同一个「独立 schema」夹具，于是把它从 `test_db_store.py` 移到共享位置（顺带 `TEST_SCHEMA` 也一起挪）。

### 四、验收逐条

| 验收 | 证据 |
|------|------|
| 真机（留给用户） | 机制：`CharApp/tests/test_recording.py::test_a_restarted_service_picks_up_the_previous_conversation`（换服务对象、同一个 saver 与会话编号，第二个模型看得到上一段）+ `test_client_session.py` 那三条水合 |
| 水合补「结果未知」且不重放 | `test_hydration_seals_a_tool_call_that_never_got_its_result`（断言 `ECHO_CALLS == []`：工具一次都没跑） |
| 水合后接着写链 | `test_a_hydrated_session_hangs_its_new_frames_on_the_old_chain` + `test_a_new_run_can_hang_its_first_frame_on_a_given_parent` |
| `/history` 只读记录表 | 端点侧 `test_the_record_table_is_the_source_when_a_database_is_given`（会话内存里那批**不参与**）· `test_a_visible_system_note_does_reach_the_reader`；真库侧 `test_the_reader_only_hands_out_the_visible_rows`（标 pg_db：`hidden=True` 那行不出现在返回里）· 空表回空列表两条 |
| `/history` 不建会话 | `test_the_record_reader_never_asks_for_a_session` + 既有的 `test_asking_for_history_does_not_start_a_conversation` |
| `/conversations` 的语义 | 真库：`test_list_active_skips_shells_internals_and_closed_threads` · `test_list_active_is_ordered_by_last_activity`；端点侧 `test_the_list_comes_back_projected_to_three_fields` · `test_the_query_carries_the_caller_identity_and_the_limit` · `test_without_a_limit_the_default_is_used` |
| 隔离 | 真库：`test_list_active_is_scoped_to_tenant_and_owner` |
| 不配库 = 零变化 | `test_the_route_is_not_registered_without_a_database` · `test_the_other_routes_still_work_without_a_database` |
| 记账的三层 | `test_a_normal_turn_writes_a_thread_a_run_and_two_messages` · `test_the_hidden_work_of_a_tool_turn_is_recorded_as_hidden` · `test_a_fresh_summary_is_recorded_as_a_hidden_line` · `test_without_a_fresh_summary_no_line_is_added` |
| 取消 / 失败也记 | `test_an_unfinished_turn_is_recorded_with_its_question[cancelled/failed]` · `test_an_unfinished_turn_still_creates_the_thread` · `test_an_interrupted_run_is_recorded_as_unfinished` |
| 写失败的降级 | `test_a_failed_write_is_logged_and_marked_instead_of_raised`（返回 False + warning 日志）· `test_the_next_write_first_adds_a_visible_notice`（先补提示行）· `test_the_notice_is_added_only_once_per_missed_turn` |
| 懒创建 / 标题 / 活动时刻 | `test_the_thread_is_created_once_and_reused_afterwards` · `test_a_long_question_is_squashed_into_a_one_line_title` · `test_every_turn_refreshes_the_thread_activity_time` · `test_title_comes_from_the_first_user_message` |
| 身份只用于分区与过滤 | `RunContext` 的 docstring 与 `agent/provider.py` 那条纪律；`test_the_two_owner_fields_have_no_default`；框架源码扫描用例（业务词一个都不许出现） |
| 空闲淘汰 | `test_an_idle_session_is_evicted_and_later_rebuilt` · `test_a_session_that_is_still_running_is_never_evicted` · `test_reading_an_entry_refreshes_its_idle_timer` · `test_evict_idle_reports_what_it_dropped_and_keeps_the_rest` · **`test_an_evicted_session_comes_back_with_its_history`**（真会话：淘汰后重新装配，历史水合回来） |
| 测试与静态检查 | 框架 **954 passed / 72 deselected**（比开工时的 892 多 62：记录员 19 · 会话列表端点 16 · 水合与记账 7 · 淘汰与水合 7 · 历史来源 6 · 分层投影 4 · parent 链 3 …）· 业务 **191 passed**（+5）· 标记集 `-m "pg or redis or pg_db"` **61 passed**（+10，含真库那 8 条）· `ruff check` / `ruff format --check` 干净 |

### 五、代码审查改了什么（两轴各起一个 sub-agent，2026-09-22）

**Standards 轴**（三条 HARD + 两条 smell，改掉四条）：

1. **全角标点**（`CharApp/minimall/server.py` 新增注释里的 `)` `。`）→ 改成 ASCII，与项目 CLAUDE.md §4.9 一致（顺手把那对括号配平了）。
2. **数错的说明**（`db/repositories/threads.py` 新写的「三个列方法的取舍」下面是**两**行表）→ 改成「两个列方法」。
3. **DESIGN.md 那条新硬骨头的标签**：`#12` 是「数据模型（六实体 + event sourcing + TTL）」，而记录 / 水合 / 淘汰说的是那几张实体表的**生命周期** —— 标签留着，前半句补上「说的是那几张实体表什么时候写、怎么读回、什么时候能丢」，并把它并回原来的列表（原来被空行切成了松列表）。
4. **假库里的三套映射**（`tests/doubles.py`：`_ENTITIES` / `_KEYS` / `_rows_of` + `scalars` 里一个内联三元，其中内联那支对 Run 查询会静默返回 threads 那批）→ 收成「表名 → 实体」+「实体 → 那批行」两处，`scalars`/`get`/`_remember` 都走同一个入口；`messages/threads/runs` 的注解也从 `list` 改成 `Sequence`（默认值是元组）。
5. **两个 `if self._recorder is None: return`**（judgement call，**不改**）：两处各三行，合并要靠一个「有没有记录员」的开关参数，比重复两行更难读。
6. **`_run(pending, *, question=None, since=None)` 的参数簇**（judgement call，**改了**）：那两样要么一起有（`ask`）要么一起没有（`resume`），挂成两个 Optional 等于把「同生共死」摊成两个空值加一个 `and` 判断；顺手把记账挪回它真正的概念所在（`ask`，那里同时拿着提问、`since` 与跑之前那份摘要），`_run` 回到「只接管会话状态」那一个参数。

**Spec 轴**（一条缺失 + 一条范围外 + 三条「看着实现其实有问题」，四条都处理了）：

1. **业务侧从没验证 `/history` 读记录表**（CharApp 的 app 用例永远不传 `database`）→ 补 `test_server.py::test_the_web_history_reads_the_record_table`（经 HTTP 问一句 → 从记录表读回）+ `test_the_tenant_comes_from_the_entry_not_from_the_request`（租户由入口定死）；为此让假库也认「库里已有 + 刚写进去的」消息行，并模拟**一条**过滤规则（会话历史只给可见的行，真过滤在 SQL 里，由 pg_db 用例守）。
2. **跑完没答复也补「没答完」行属范围外**（spec 只在取消 / 失败那节点过名）→ 保留，但写进开放项（理由见上）。
3. **读不到记录表 = 未处理的 500**（两条只读路读的是库，而 `DbError` 不是 `ServerError`，没有任何处理器接它；写入侧的降级不覆盖读取）→ 补 `_record_store_error_response`（503 + `record_store_unavailable`）+ 两条用例。
4. **属主隔离只做在读上**（记录员按 `thread_id` 复用既有会话行，不比对属主；业务拿 `--conversation-id web` 打字面就能撞进网页端那一段）→ 记录员加一笔 warning（照样写，理由见开放项）+ 一条用例。
5. **`conversation_id_of` 取的是最后一段，而 spec 写「第三段」** → 行为不变（编号恰好三段时等价，而写死下标 2 会假设前两段不含分隔符），把 docstring 改成同时说清这两件事。

### 六、真机验收（2026-09-22 晚，Claude 代跑）

环境：Django `runserver 8000 --noreload`（新实例）+ CharApp 服务 `python -m CharApp.minimall.server`（新实例）+ 本机 Postgres/MySQL/Redis + 真 DeepSeek；会话编号 `acceptance`，脚本见当时的临时目录（不进仓库）。

| 步骤 | 结果 |
|------|------|
| ① 第 1 句（种一个只有靠上文才答得出的信息）：「请记住：我的幸运数字是 47，我叫林小满。只回复「记住了」」 | `final → '记住了'`；`charagent_runs` 落 1 行 (finished, turn_count=1, total_tokens=5680) |
| ② 第 2 句「我余额还有多少？」（买家 1 在库里不存在 → 工具 404） | 模型据实回「系统出了点问题」；**工具行与中间轮以 `hidden=True` 落库**，`/history` 只回可见那两条 —— 工具失败的降级链在真机上也走得通 |
| ③ **重启 CharApp 进程**（先确认 1007 只剩 TIME_WAIT、没有 LISTENING，再起新进程） | 新进程、内存全空 |
| ④ 第 3 句「我叫什么名字？我的幸运数字是几？」 | `final → '林小满47'` —— **只可能来自上一段进程的历史**，证明水合真的把历史喂给了模型（不是只有前端回来了） |
| ⑤ `/history` | 三轮原话逐条拉回（读的是记录表） |
| ⑥ 换真实买家（user 2）问「我余额还有多少？」 | 工具真打到商城 → `final → '你的账户余额是 **600000.00 元**。'`；库里可见两行 + 隐藏两行 + 1 条 runs |
| ⑦ 查库 | 会话行 / 运行行 / 消息行三张表都对：`tenant_id` 网页端是 `minimall`、命令行是 `minimall-cli`；可见行 `hidden=False`、工具与中间轮 `hidden=True`；消息行带 `run_id` |

**这一跑还抓出一个真问题（已修）**：业务的两处用例真的会跑 `cli.main`（`test_cli.py` 全体 + `test_server.py::test_both_entries_go_through_the_same_assembly`），而 CLI 从本片起会记账、用的又是真 `PgDatabase` —— 于是每跑一次业务测试就往**真库**写一轮 MockLLM 的假对话（本机 PG 里攒到 300 行，`minimall:3:cli` / `minimall:7:cli`）。这既违反业务测试「离线可跑、不碰外部服务」的约定（`CharApp/tests/conftest.py` 开头那条），也会让「记录表里有什么」这种排查完全失真。修法照这个文件已有的做法（它本来就 monkeypatch `build_saver_for` 换掉真存储）：CLI 多一个 `build_database()` 注入缝，两处 fixture 各把它换成 `FakeRecordDatabase`；残留行已按 `thread_id` 删干净，再跑两套测试记录表里不再多一行。
