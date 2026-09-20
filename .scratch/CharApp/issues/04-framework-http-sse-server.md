# 04 · 框架侧：HTTP + SSE 服务层

**Status:** done

**Type:** task

**Blocked by:** 无

**上游:** `../PRD.md` §4.9（L1b）、`CharApp/docs/PLAN.md` §4

## 做什么

给框架加一层 HTTP 服务：客户端问一句话，服务把这次问答的**流式事件**推回去。业务通过两个回调接进来，框架仍然不知道业务的存在。

这一片是 L1b 的地基 —— 05（业务服务）与 07（取消）都长在它上面。

**框架现在完全没有 HTTP 层**（`fastapi` / `uvicorn` / `asgi` 在 `CharAgent/` 全目录零命中），所以这是从零建，不是补完。但底下的东西比想象的窄：事件流本来就是为这一层设计的。

## 已经替你确认过的事实（别重新推一遍）

| 事实 | 出处 |
|------|------|
| 事件类型取值**就是** SSE 的 `event` 字段；`seq` 就是 SSE 的 `id` 字段 | `stream/utils/types.py:26-38` / `:55-57` |
| `StreamEvent` 里**刻意没有** `run_id` —— 注释写明「由 P1 server 在转发时注入」 | `stream/utils/types.py:58-59` |
| `EventSink` 的形状与用法都点名了本层：「传输通道（P1 server 推 asyncio.Queue / CLI 打印 / 测试收集）」 | `stream/utils/types.py:71-75` |
| **取消时框架不产终局事件** 「error(cancelled) 由 server 层负责」 | `agent/loop.py:349-351` |
| 框架 emit 的是事实（错误码 + message），**面向用户的降级话术归 server 层，框架不编造用户文案** | `docs/DESIGN.md:113`、`docs/difficulties/01-core-loop.md:20` |
| 取消的语义已经定死：`POST runs/{id}/cancel` 用 `task.cancel`，只是触发源从终端信号换成 HTTP 请求 | `client/app.py:293-294` |
| 设计文档 #20 已写明「同一 thread 不并发写」 | `docs/DESIGN.md:139` |

## 具体任务

### 1. 新增 `CharAgent/server/` 包

- **应用工厂**，不是自己起服务：业务调用它拿到 app，怎么跑（uvicorn / 测试 / 别的）由业务决定
- **接缝一 · 认证 + 解析**：业务提供「HTTP 请求 → `RunContext`」。框架只认结果，**仍然不解释 payload 里是什么**（与 L1a 的 `ToolProvider` 同一条纪律）。认证失败要能被翻译成 HTTP 状态码 —— 框架定义一个异常类，业务抛它
- **接缝二 · 装配**：业务提供「`RunContext` → `ChatSession`」。形状与 `ToolProvider` 一致：框架定协议、业务给实现，不继承基类
- **事件流**：`EventSink` → `asyncio.Queue` → SSE 响应。`EventSink` 是普通回调（同步或异步都行），不是迭代器，所以队列是本层要接的东西
- **SSE 字段映射**：`type` → `event`、`seq` → `id`、`data` → `StreamEvent.to_dict()`；`run_id` 由本层注入

### 2. 会话登记表（**这片最容易做错的一条**）

`ChatSession` 的对话历史在**对象内存**里（`client/session.py:153-155`），构造时**不从快照恢复**，而 `aclose()` 会连模型与存储一起关（`:292-295`）。直接后果：

- 会话必须按 `thread_id` **长驻** —— 每个请求新建一个的话，第二轮就看不到第一轮说过什么
- 同一 `thread_id` 的两次提问**不能并行**：`ChatSession` 没有锁，`_history` 是可变共享状态，两个 `ask()` 交错会互相污染
- 谁来建、谁来关（进程退出时的收尾）要想清楚

### 3. 取消的**留位**（完整链路在 07）

本片不暴露取消端点，但两样东西要先立起来，后加会返工：

- `run_id` 从第一次运行就分配
- 每个 run 有一个可取消的 `asyncio.Task` 句柄登记在册

顺带决定「取消时的终局事件由谁发、发什么形状」—— 框架不产它（见上表），所以是本层的责任。本片只需定契约，07 去实现。

### 4. 依赖

框架的 `pyproject.toml` 现在只有 8 个硬依赖（`alembic` / `httpx` / `openai` / `psycopg` / `pydantic` / `python-dotenv` / `redis` / `sqlalchemy`），没有 `optional-dependencies`。FastAPI / uvicorn 进来后要决定：**硬依赖还是可选依赖组**（比如 `[server]`）。把理由记进 ticket 的实现记录里。

### 5. 根门面的防漂移测试

`tests/test_root_facade.py` 有一份 `FRAMEWORK_PACKAGES` 名单，还有一条「`client` 整个包不进根门面」的断言（它是应用入口，不是库 API）。新增 `server` 包之后必须明确它算哪一类，并让测试跟着走 —— 别让它默默漂。

### 6. 写测试

## 验收

- [x] 用**框架自己的玩具业务**（两三个跟电商毫无关系的工具，与 L1a 通用性测试同一套做法）起一个真的 app，问一句，客户端收到完整事件序列，`seq` 连续、终局事件恰好一个
- [x] **同一套服务代码**装两个互不相关的业务都能跑通 —— 「通用」从形容词变成断言
- [x] 框架代码里搜不到业务词
- [x] 会话按 `thread_id` 复用：同一 thread 连问两句，第二句看得到第一句的历史
- [x] 两个不同 `thread_id` 并发跑，互不干扰
- [x] 同一 `thread_id` 的并发提问被拒绝或排队 —— **不是静默损坏历史**
- [x] 业务回调抛配置类错误 → 客户端收到明确的错误响应，不是 500 + traceback
- [x] 认证失败 → 明确的状态码，且**不泄漏**「是令牌错还是用户不存在」
- [x] 框架现有测试全绿；**`CharAgent/agent/loop.py` 零改动**（与 01 / 03 同一条约束）
- [x] 根门面防漂移测试通过

## 备注

- **事件顺序是先 sink 后 hook**（`stream/bus.py:140`），所以 sink 回调里看到的顺序是权威的
- **`seq` 不是全局编号**：`EventBus` 每次运行新建，所以每 run 从 1 起，`resume()` 之后也会重置。要跨 run 定位就得靠 `run_id` + `seq` 的组合，别指望 `seq` 单调
- 现成的测试替身别重造：`tests/doubles.py` 的 `EventCollector`（假 sink，与队列型 sink 是兄弟），`tests/snapshots.py` 的 `project_events`（事件流快照断言，把易变的毫秒值归一化了 —— 正好用来钉 SSE 载荷契约）
- **别顺手接 `db/`**：`CharAgent/db/` 里已经有 `RunsRepository` / `RunStatus` 状态机 / `run_status_for_outcome` 映射，`client/` 至今没用过。本片只要**内存里**的登记表；真需要持久化时记下来，别在这一片里顺手接上
- 框架的 server 层**不写用户文案** —— 它转发事实（错误码 + message），人话归业务侧（见上表 DESIGN.md 那条）

---

（实现完成后在此追加「实际开发情况」一节：验收逐条结果、与初稿的出入、计划外但必须做的、明确不处理的。）

## 实际开发情况 2026-09-19

**结论：完成。** 框架 **802 用例全绿**（新增 46 条），`CharAgent/agent/loop.py` **一行未动**
（`git diff --name-only CharAgent/agent/loop.py` 为空），CharApp 的 87 条照旧全绿。
本片对既有模块的**代价是零**：只加了一个包，没改任何既有模块的行为。

### 验收逐条

| 验收项 | 结果 | 证据（用例） |
|--------|------|-------------|
| 玩具业务起真 app → 完整事件序列、`seq` 连续、终局事件恰好一个 | ✅ | `test_server_app.py::test_a_question_comes_back_as_a_complete_event_stream`（工具轮 `tool_call → tool_result → final`，`id` = 1,2,3） |
| 同一套服务代码装两个互不相关的业务 | ✅ | `test_the_same_server_serves_two_unrelated_businesses`（同一个 `create_app` + 同一套插座实现，差别只在参数） |
| 框架代码里搜不到业务词 | ✅ | `test_agent_provider.py::test_the_framework_never_mentions_the_business`（**扫描范围已加 `server`**，`test_the_business_scan_covers_the_framework_packages` 跟着改） |
| 会话按 `thread_id` 复用：连问两句，第二句看得到第一句 | ✅ | `test_the_second_question_sees_the_first_one`（第二个模型请求的 messages 里有第一句与它的答复；装配只发生一次） |
| 两个不同 `thread_id` 并发跑，互不干扰 | ✅ | `test_two_threads_run_at_once_without_mixing`（两个身份并发，各自的流里只有自己的工牌） |
| 同一 `thread_id` 并发提问被拒绝或排队 | ✅ | **拒绝**：`test_a_second_question_on_a_busy_thread_is_refused`（409 + `thread_busy`；被拒的那句不放开会话）+ `test_a_new_question_is_accepted_after_the_busy_one_finishes` |
| 业务回调抛配置类错误 → 明确错误响应，不是 500 + traceback | ✅ | `test_the_business_config_error_becomes_a_clean_answer`（503 + `not_configured`，响应体里没有 `Traceback`） |
| 认证失败 → 明确状态码且不泄漏 | ✅ | `test_a_failed_authentication_is_one_plain_answer`（「令牌不对」与「没带令牌」两种情况**响应体逐字相同**：`{"error": {"code": "unauthorized", "message": "认证失败"}}`） |
| 框架现有测试全绿 + `agent/loop.py` 零改动 | ✅ | `802 passed, 65 deselected`；`git diff` 对 `loop.py` 为空 |
| 根门面防漂移测试通过 | ✅ | `test_root_facade.py`：`server` 进「刻意排除」名单（第三条），并新增一条**起子进程**验「`import CharAgent` 不会把 fastapi/starlette 拖进 `sys.modules`」 |

任务清单里 ticket 另点名的三件事（不在验收清单里但要求先立起来）：

- **`run_id` 从第一次运行就分配** —— 响应头 `X-Run-Id` + **每个事件载荷**里都有（用例断言两者一致）。
- **每个 run 有可取消的 `asyncio.Task` 句柄在册** —— `test_a_run_is_registered_while_it_runs_and_dropped_when_it_ends`；
  app 层还直接验了一次真取消：`test_a_cancelled_run_ends_with_cancelled` 从 `app.state.run_registry` 取句柄 `task.cancel()`，
  断言客户端收到 `error(code=cancelled)`、恰好一个终局事件、运行出册、会话放开。
- **取消的终局事件契约** —— 定了并且**已经在跑**（见下面「与初稿的出入」第 3 条）。

### 两处决定（ticket 点名要我定，理由记在这里）

**一、依赖：可选组 `[server]`，`server` 不进根门面。**

| 选 | 理由 |
|----|------|
| `[project.optional-dependencies] server = ["fastapi>=0.139"]` | 硬依赖会让「只想用 agent loop」的人先装一个 web 框架 —— 而「零框架依赖」正是这个项目立身的那句话。做成 extra 之后，「不装 web 框架也能用这个框架」是一句能**兑现**的话（子进程用例守着）。 |
| `server` 整个包不进根门面（第三条刻意排除，与 `client` 并列但理由不同） | 根门面的承诺是「`import CharAgent` 拿到全部库 API」；`server` 要 web 栈，一旦进门面 fastapi 就变成 `import CharAgent` 的硬要求。要用 HTTP 的写 `from CharAgent.server import create_app`。 |
| **不列 uvicorn** | 框架从不 import 它（`create_app` 只交出一个 ASGI app）。口径与那八项一致：装什么 = 导入谁。 |

代价（记清楚）：多了一条「导入路径分两种」的规则，靠两条用例守着别漂（`server` 名字不许上根门面 +
根门面不许拖 web 栈）。测试套件本身仍需要装 fastapi（仓库 requirements 里本来就有），故**没有**加
`importorskip` —— 那会给「用例静默跳过」开一个口子，比多一行依赖说明更糟。

**二、同一 `thread_id` 的并发提问：拒绝（409）而不是排队。** 静默排队在界面上与卡死没有区别，
而队列策略（排多久 / 排到第几个 / 满了怎么办）是产品决定，框架不该替业务发明一个。拒绝是一次
可被客户端解释的失败：前端提示「上一句还在答」。

### 与初稿的出入（以代码为准）

**1. 第二个插座多收一个 `event_sink`。** ticket 写的是「`RunContext` → `ChatSession`」，实现是
`provide(context, *, event_sink)`。原因是硬约束：事件的出口在**会话构造时**就交给 `AgentLoop` 了
（`ChatSession(event_sink=...)`），而一个会话要连续服务很多次运行 —— 不把出口在装配时递进去，
后面的运行就再也接不上自己的队列。业务那侧只多一行「原样转交」（测试里的样板就是最小写法）。

**2. 收尾挂在任务的收尾回调上，不在协程的 `finally` 里 —— 这是实测踩出来的。**
最初写成「跑任务的协程 try/except/finally：取消就补终局事件、失败就补终局事件、finally 投哨兵」，
看着更自然。用例直接挂死，一查：`task.cancel()` 落在**任务一步都没跑**的时候，协程体一行不执行、
`finally` 不会跑，事件流永远等不到结尾（客户端挂死）。现在 `close_stream(task, stream)` 由
`task.add_done_callback` 调（正常 / 失败 / 取消 / 没跑起来就取消，**恰好一次**），并有一条专门的
回归用例：`test_a_task_cancelled_before_it_ever_ran_is_still_closed`。

**3. 取消的机制在本片就闭合了**（初稿说「本片只需定契约，07 去实现」）。原因也是上一条：断连路径
（生成器被关掉时叫停没人听的运行）本来就是取消，它是本层流收线的一部分，没法只留个空壳。于是：

- **04 已经做完**：`task.cancel()` 的触发点、终局事件 `error(code=cancelled)`、收尾（会话收进度
  → 登记表出册 → 会话放开），以及「取消之后能接着说『继续』」的底层（`ChatSession._reclaim_progress`，
  框架早就有了）。
- **07 剩下的**：`POST /runs/{run_id}/cancel` 端点本身、**HTTP 语义**（取消已结束的 run / 不存在的
  run 各返回什么 —— `RunRegistry.get` 已经就位，取不到就是「不存在或已结束」）、四层切穿、以及
  **断连的确定性信号**（见「明确不处理的」第 7 条）。

**4. 框架里唯一打日志的地方是 `server/runs.py`。** 框架其余部分从不打日志（失败一律上抛给调用方），
但 server 层是进程里的**最后一站**：一次运行的失败只有两个去处 —— 客户端的事件流（对方可能已经走
了）与进程日志。所以这里对「没人再保管的失败」用 `logger.error(..., exc_info=exc)` 留一笔 traceback。
这条是刻意的例外，写在模块 docstring 里。

### 计划外但必须做的（实现中发现）

1. **两张登记表挂到 `app.state` 上**（`session_registry` / `run_registry`）。原因：验收要断言「登记了
   / 放开了」，而它们是 `create_app` 的闭包变量。07 的端点也在同一个闭包里，顺手即可。
2. **HTTP 头是 ASCII** —— 测试里踩到一次：`httpx` 编头时按 ASCII 编，中文身份直接炸在客户端。真实
   业务的身份要是中文，得放进请求体（UTF-8）。这条与 issue 02 记下的 `compare_digest` 非 ASCII 陷阱
   是同一类东西（都是「HTTP 层的字节口径」），写进测试的 `headers()` 注释里提醒 05。
3. **`RunStream` 的序号记账**（`last_seq` / `closed`）。补终局事件必须「接着编号 + 恰好一个」，这两件事
   得有个地方记账 —— 放在流上最自然，也让它能单独测（不牵扯 HTTP）。
4. **没有用 `tests/snapshots.py` 的快照**（备注里提过它「正好用来钉 SSE 载荷契约」）：帧格式在
   `test_server_sse.py` 里**逐字**钉死（一帧就是那 4 行文本），事件序列在 app 用例里逐字段断言。
   快照适合「形状复杂、只想看哪儿变了」，而这里要断的**正是形状本身**，逐字断言更直接。

### 代码评审发现并修掉的四处（2026-09-19）

评审那一轮的并行子代理**跑完了，但汇总没回到我这边**（那次调用被中断）。于是按它们的核查题目
逐条自己复核了一遍：确认成立的四处已修，不成立的记进「明确不处理」。四处是同一种毛病 ——
**一条边角路径把整次运行或整条流连累掉**，代价远大于收益：

**一、收尾的两段链子要拆开（`close_stream` / `_finish_run` 各加一层 `finally`）。** 原先
「关流」与「解绑 / 放开 / 出册」是一次顺序调用：关流那一步若炸（它正在跟一个异常对象打交道），
后面的登记动作全不执行 —— 会话被永久占住（这段对话从此一直 409）。现在三件登记动作放在
`finally` 里：**生命周期的保证优先级高于「把错误原样传上去」**，而异常照旧往上冒、asyncio 照旧
打出来。

**二、异常对象自己坏了，也得把「失败了」说出去（`_describe`）。** `f"{type(exc).__name__}: {exc}"`
里 `str(exc)` 抛异常是真会发生的（自定义异常读了没初始化的字段）。那时终局事件拼不出来 → 客户端
一直等一个不来的结尾。现在退化成只剩类名，事件照发
（用例：`test_a_failure_whose_exception_is_broken_still_reports`）。

**三、孤立代理项不该在编码这一步炸掉整条流（`encode_frame`）。** 响应层用 `str.encode("utf-8")`
的默认策略，而载荷正文来自模型 —— 上游偶尔吐 U+D800 这类非法码点，那时**连接断在半路**，而且
这次运行会被当成断连取消掉（一个字符赔上整次运行）。现在自己在边界上编码，让坏字符退化成 JSON
转义（客户端解析出来还是那个字符，其余内容一字不差）。框架在别处也吃过代理项的亏
（`test_client_app.py` 记的那次是输入编码，那次修根因；这一次根因在上游，修不了，只能不让它炸）。

**四、会话装错分区要当场拦住（`_require_same_thread`）。** 登记表按 `RunContext.thread_id` 分区，
快照按 `ChatSession.thread_id` 分区 —— 业务若把会话装到别的编号上，两个不同的对话会**共用一份
快照**（读回来的历史是别人的），而且**没有任何报错**。多用户产品里这是最不该发生的一类错，
现在第一次请求就抛一条明确的 500（接线 bug，该留 traceback）。

顺带补的三处注释（都是「读者一定会问」的地方，不涉行为）：补发的终局事件**不经过 EventBus**
（那台总线随 run 结束就没了，所以不做「工具未闭合」校验 —— 被取消的运行本来就有没闭合的调用）·
失败只补一个 code 的理由（客户端处置一样，区分原因属于排查）· 本层**不含断线重连**
（`id` 字段按契约发出去，但服务端没有「从第 N 号接着推」的接口）。

复核后**没有改**的两处（免得留下错误的印象）：

- `/docs` 与 `/openapi.json` 在 `response_class=StreamingResponse` 下**正常**（实测三个路径都是 200）——
  FastAPI 的 schema 生成不吃这个 response class。
- **硬断连确实会叫停运行**：真 uvicorn + 客户端 RST 断连，服务端日志里依次是
  `ASK start` → `200 OK` → `ASK cancelled` → `ASK finally`。所以「断连即取消」这条路是通的，
  只是靠 CPython 引用计数触发的生成器回收（见「明确不处理的」第 7 条）。

### 明确不处理的（连同理由）

1. **取消端点本身**、取消「已结束 / 不存在」的 HTTP 语义 —— 归 07（见上）。
2. **会话淘汰**（LRU / TTL / 落盘）：登记表只增不减。演示规模下条目数 = 用户数，每条就是一段消息历史；
   真要淘汰是产品决定（决定「用户回来还记不记得上一轮」），不在这一片顺手做。
3. **跨进程 / 多 worker 的会话登记与互斥**：会话表与运行表都在进程内（`_entries` / `_busy` /
   `RunRegistry`），**部署必须单进程**。多 worker 下同一个 `thread_id` 的两个请求落到不同进程，
   各有各的登记表 —— 并发拦截当场失效，而且两边各建一个会话同时写同一段快照（读回来的历史是混的）。
   跨进程要分布式的登记与锁（DESIGN #20 的升级路径：进程内队列 → 分布式锁）；框架里
   `retry/idempotency.py` 的幂等 store 是同一个前提。**代码里已写明**（`sessions.py` 模块
   docstring 的「单进程」一段），免得只躺在 ticket 里。
4. **`db/` 一行不碰**：`RunsRepository` / `RunStatus` / `run_status_for_outcome` 都在，但本片只要**内存里**
   的登记表（ticket 明说别顺手接）。真需要持久化时再说。
5. **HTTP 的 `run_id` 与 loop 写进快照的 `run_id` 不打通**：打通的代价是给 `ChatSession.ask` 加参数
   （那会碰业务侧 87 条用例的路），收益要等可观测层（L3：按 run 查「当时它看到了什么」）。留给 L3。
6. **不开 CORS、不做鉴权中间件、不做限流**：本层面向服务端（上游转发层），不直接面向浏览器。
7. **客户端硬断连的「确定性取消」**：会叫停（上面实测过），但 Starlette 在 ASGI `spec_version < 2.4`
   （uvicorn 0.48 报的就是 2.3）走 anyio 任务组竞速，硬断连时**不 aclose 生成器**（直接抛
   `ClientDisconnect`）—— 那条路上 finally 要等生成器被回收才跑。CPython 引用计数下这通常就发生在
   本次请求收尾时（实测正是如此），但**不是保证**。不影响正确性：登记表的出册挂在任务自己的收尾回调
   上，运行结束就出册，不会留幽灵条目。**要确定性就得上 `receive` 监听 `http.disconnect`**
   （07 的验收里有这条，届时一并做）。
8. **队列不设上限、不做背压**：事件产出受模型 / 工具节奏约束（每个事件背后是一次几百毫秒到几秒的
   等待），消费端在同进程逐条转发。真限流是服务治理的事。
9. **`uvicorn` 不进 extra**（见决定一）。

### 产物

新增（框架侧 7 个源文件 + 4 个测试文件）：

```
CharAgent/server/__init__.py            门面 (13 个名字, 刻意不上根门面)
CharAgent/server/app.py                 create_app + POST /runs + 错误翻译 + 任务收尾
CharAgent/server/sse.py                 sse_frame (字段映射) + encode_frame + sse_stream
CharAgent/server/sessions.py            EventRouter + SessionEntry + SessionRegistry
CharAgent/server/runs.py                RunStream + RunHandle + RunRegistry + close_stream
CharAgent/server/utils/types.py         两个插座协议 + wire 契约常量
CharAgent/server/utils/errors.py        ServerError 一族 (自带状态码)
CharAgent/tests/test_server_app.py       16 条 (ASGI 端到端)
CharAgent/tests/test_server_sessions.py  10 条 (登记表与事件路由)
CharAgent/tests/test_server_runs.py      12 条 (流的记账 + 收尾 + 在册)
CharAgent/tests/test_server_sse.py        6 条 (帧格式 + 边跑边推 + 关流叫停)
```

修改 4 个：`CharAgent/__init__.py`（docstring 改三条排除）· `CharAgent/pyproject.toml`（+`[server]` extra）
· `CharAgent/tests/test_root_facade.py`（排除名单 + 子进程用例）· `CharAgent/tests/test_agent_provider.py`（扫描 +`server`）。

**框架既有模块零改动**（`agent/` `stream/` `checkpoint/` `client/` `db/` `model/` `tool/` `hooks/` `retry/` `prompt/` 全部未动）。
