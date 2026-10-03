# C06 · #15 工具超时 + 取消（分层超时）

**Status:** done

**Type:** feature

**Blocked by:** C05（新加的异常与日志要走统一出口）

**上游:** `CharAgent/docs/DESIGN.md` §4 ② 的 #15；`adr/0017`（当初把 #15 推后、先做 #17 的理由 —— **依赖方向是单向的**，这次不再推）；`.scratch/Charlotte/PLAN.md` §4 组 B

> **2026-10-04 改判**：§三「超时回填给模型、由它决定下一步」与 §四前两条已被改判为
> **任何工具超时都中断本次运行** —— 现在生效的是文末「2026-10-04 改判」段（ADR-0024）；
> 前面那些保留为 2026-10-03 当天的事实。

## 现状（2026-09-30 读码核实）

`tool/executor.py:179 execute_tool` 的契约是**永不抛异常**：

```
解析参数(_parse_arguments) → 校验(_validate_arguments) → 调用(_invoke) → 规范化
```

**没有超时、没有循环、没有取消**。模型发起的工具调用一旦卡住，就**永远卡住** —— 整轮不会结束。

`_invoke`（`:166-171`）的分派：

```python
if inspect.iscoroutinefunction(fn):
    return await fn(**call_kwargs)          # 协程 —— 能取消
return await asyncio.to_thread(fn, **call_kwargs)   # 同步函数 —— 取消不了
```

**这两条路的取消语义不同，是本片的核心难点，不是实现细节。**

---

## 一、分层超时

DESIGN #15 定的是三层，落点如下：

| 层 | 现状 | 本次要做的 |
|---|---|---|
| **工具**（10–30s） | **没有** | 本片的主要工作 |
| **模型**（60s） | ✅ **已设**：`model/client_httpx.py:59` `timeout: float = 60.0`（`:105` 传给 `httpx.AsyncClient`），docstring 自己写着「分层超时的 model 层」 | 无事可做 —— 只把它写进文档 |
| **run 总时长** | ✅ 已有：`agent/guard.py:121` 的 `elapsed_ms / 1000 >= max_duration_seconds`（`time_source` 可注入） | **不动** —— 它的语义是「**这一轮结束后才判**」（#3），与单次调用超时不是一回事，别混 |

**所以本片真正缺的只有工具那一层** —— 模型层 60s 与 run 层 wall-clock 都已经在，DESIGN #15 的三层里只空了中间那一层。

**工具级超时要可配到单个工具**（`Tool` 上加 `timeout` 字段，缺省取全局配置），因为业务工具的真实耗时差异很大（查库 vs 调外部 API）。

## 二、取消语义（本片最容易做错的地方）

### 2.1 协程工具 —— 能取消

`asyncio.wait_for` 会在超时点 cancel 内部那层 —— 干净。

### 2.2 同步工具 —— **取消不了，要如实承认**

`asyncio.to_thread` 跑在线程池里，`wait_for` 只能**放弃等待**，**线程会一直跑到函数自己返回**。

三个选项：

- **A（推荐）**：`wait_for` 放弃等待 + 日志里**如实记一条**「这次调用已超时，但它的线程还在跑」。**在文档里写明这条边界** —— 同步工具的超时是「不再等它」，不是「它停了」；对有副作用的工具，这意味着**副作用可能仍然发生**。
- B：要求所有业务工具写成 `async def`（`CharApp` 已经这么做了，见 `PLAN.md` §3.3 的 #65 案例）→ 但这只是把问题推给工具作者，框架仍要处理同步工具
- C：同步工具用子进程隔离（能真杀）→ **过重**，本阶段不做

**选 A**，并把这条取舍写进 ADR（它满足 ADR 三条：难回退 · 反直觉 · 有真实取舍）。

### 2.3 `CancelledError` 不吞

kill switch（#3）在任意 await 点打断，`CancelledError` 属 `BaseException`，**不能被 `except Exception` 接住**，也不能被重试（#13 已定的规矩）。工具超时这条链路要和它保持一致。

## 三、超时之后回填什么

`execute_tool` 的契约是「永不抛异常、失败也回填可操作错误文本」（#2）。超时同样：

> 错误文本要让模型**有事可做** —— 不是甩一句「timed out」，而是「这次调用超过 N 秒没有返回，你可以重试，或换一种方式达成目的」。

**不能**把超时伪装成"工具返回了空结果" —— 那会让模型以为查到了空数据。

## 四、验收

- [x] 用一个**永远不返回的假工具**（协程版）钉一条测试：整轮在预算内结束，模型收到可操作错误文本
      —— 两条各管一层。`test_a_tool_that_never_returns_does_not_stall_the_turn`（`test_loop_recovery.py`）：
      这次 run **正常收场**（`outcome is FINISHED` —— 是超时收的场，不是被 guard 刹的），模型下一轮的
      tool 消息里确实有那句话（`执行超过 0.05 秒` + `不要直接重试`），墙钟远小于 guard 的 30 秒预算。
      `test_async_tool_timeout_backfills_actionable_text`（`test_tool_executor.py`）钉执行层那三件：
      `content == ""`（不许伪装成空结果）/ `内部错误` 不在文案里（它不是意外故障）/ `exception` 是
      `ToolTimeoutError`。
- [x] 用**同步版**的假工具钉一条：`execute_tool` 能返回，且日志里有「线程仍在跑」的如实记录
      —— `test_sync_tool_timeout_leaves_its_thread_running`：返回 + 文案都是超时那条；
      **日志那行**取 JSON 记录，断言 `thread_still_running is True` 且 msg 里点到「线程」；
      最后 `await asyncio.sleep(0.7)` 看函数**自己返回**（`finished == ["done"]`）——
      「线程没停」这句话是被证出来的，不是写在注释里。
- [x] 单工具超时可配，且**不改 `Tool` 的既有构造签名**（用 keyword-only 带默认值）
      —— `Tool.timeout: float | None = field(default=None, kw_only=True)`（尾字段、只按关键字给）；
      `test_timeout_is_configured_per_tool`（显式值进得去、缺省是 None → 取全局）+

      `test_timeout_does_not_change_the_existing_tool_signature`（老七个位置参数照旧可构造，
      第八个位置参数 `TypeError`）+ `test_a_bad_timeout_is_a_registration_error`（0 / -1 / "30" 在
      **注册期**报 `ToolConfigError`）。缺省那条链另有 `test_tool_without_timeout_uses_the_global_default`
      （改全局值，行为跟着变）。
- [x] kill switch 撞上工具执行时，`CancelledError` 不被吞、不被重试
      —— `test_the_timeout_guard_does_not_swallow_the_kill_switch`：从**真取消路径**触发
      （`create_task` + 等工具开跑 + `cancel`），`execute_tool` 抛的是 `CancelledError` 而不是
      一条失败结果；**工具从头到尾只被调过一次**（`runs == ["started"]` —— 取消不是重试的
      触发点；这条断言是评审点出来补的），取消后不留后台任务。loop 层那条老用例
      （`test_interception_never_blocks_the_kill_switch`）照旧绿。
- [x] 核实并补齐**模型侧**的 httpx timeout
      —— **核实**：`chat_model_from_env` → `HttpXChatModel(timeout=60.0)` → `httpx.AsyncClient(timeout=timeout)`；
      SDK 那条同值（`AsyncOpenAI(..., timeout=timeout, max_retries=0)`）；业务侧 `build_model_for` →
      `build_model` → `chat_model_from_env(model=...)` 全程不传 timeout，落到构造缺省 —— **这一段是
      人工走查的**（业务侧没有用例钉装配，如实记下）。**补齐**：三条用例，
      `test_the_request_timeout_is_the_configured_one`（httpx 与 SDK 各一条：缺省 60 秒 + 构造
      参数优先）钉「装进了客户端」，`test_chat_model_from_env_keeps_the_model_layer_timeout`
      钉「从环境变量装配那条生产路径没有第二个地方改它」。
- [x] 现有全部用例全绿（切票时记为 1502 个，那是规划期的数）
      —— 实测（2026-10-03，收尾复跑）：框架 `pytest CharAgent` = **1409 passed / 132 deselected**
      （deselected 的 132 = 120 条 pg / redis / pg_db + 12 条本地 ignored 的 integration / eval，
      后者不在版本库里）；另跑标记集 `pytest CharAgent -m "pg or pg_db or redis"` = **120 passed**；
      业务 `pytest CharApp` = **355 passed**。
      **基线是量出来的、不是推的**：把 HEAD 导出到临时目录里 `--collect-only`，选中集是 **1395** 条 ——
      本片 +14 条，与逐个 ID 的 diff 一分不差（C05 记的 1394 比这个基线少一条，未深究）。
      `ruff check` / `ruff format --check` 全干净。

## 开工前要定的

- **三个默认值取多少**（对着真实耗时分布，不拍脑袋）：
  - **工具 = 30 秒**（新建 `tool/utils/config.py` 的 `DEFAULT_TOOL_TIMEOUT_SECONDS`）——
    取 DESIGN 里 10-30 那一段的**上界**，给「工具自带的超时」让路。
  - **模型 = 60 秒**：**已在**，不动（这次补了用例钉住）。
  - **run = 90 秒**：**已在**（`CharApp/minimall/service.py` 的 `DEFAULT_MAX_DURATION_SECONDS`），
    不动 —— 它管的是「这一轮结束后才判」，与单次调用超时不是一回事。
  - 依据（真机数据，不是手感）：`CharApp` 的 18 个工具**全部是 `async def`**，且都经一个
    `timeout=10.0` 的共享 HTTP client 出门（`MinimallClient`）—— **内层 10 秒先响**，
    它给的话更准（哪个下游慢 / 什么错）。所以框架这一层是**兜底**，只管「自己不设限的工具」
    （裸查库 / 裸读文件）；它必须明显大于 10 秒（别把内层更准的话盖掉）、明显小于 60 秒
    （一次卡住的工具不该吃掉整轮预算）。
    旁证两条：L4 的跑分报告里 262 次工具调用的 `duration_ms` 全在 **8 ms 以内**（中位数 1 ms；
    那是假商城的进程内传输，量的是框架侧开销）；真机 BFF 那条链路共享 client 之后是
    **2-16 ms**（ADR-0020）。
  - 一处例外要如实记下：`httpx` 的 `timeout=10.0` 是**逐阶段**的（连接 / 读 / 写 / 池各 10 秒），
    不是整次请求 10 秒 —— 「慢滴流」那种病态响应可以越过它，而框架这层正好兜住这一类。
- **`CharApp` 的哪个工具有资格拿到更长的超时**：**一个都不放宽**（付款那类也一样）。
  判据：放宽与否在真实链路里**换不来差别** —— 内层 10 秒先生效，框架给 60 秒也只是多等；
  而多一个数字就多一处要维护的判断（还要在 run 预算里给它留位）。真要长跑的工具，
  第一步是把它**异步化成任务**（#20），不是把超时调大（ADR-0023 的「什么时候该重新看」写了这条）。

## 改了哪些文件

| 文件 | 说明 |
|---|---|
| `CharAgent/tool/utils/config.py` | 新增：`DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0` + 那个数字怎么来的（不分层的三段各自在哪） |
| `CharAgent/tool/executor.py` | 新增 `_invoke_with_timeout`（`asyncio.timeout` + 槽位日志 + `ToolTimeoutError`）；调用那一行改走它；模块与 `execute_tool` 的 docstring 写清两条取消语义 |
| `CharAgent/tool/decorator.py` | `Tool.timeout`（尾字段, keyword-only, 缺省 None → 全局）+ `@tool(timeout=...)` + 注册期校验 `_validate_timeout` |
| `CharAgent/tool/utils/errors.py` | 新增 `ToolTimeoutError(ToolActionableError)` —— 与「作者说不」同一条回填路，但日志 / 轨迹 / 插件分得清这两种失败 |
| `CharAgent/tool/utils/messages.py` | 新增 `timeout_error_text(name, seconds)`：说清发生了什么 + 下一步 + 「会改数据就不要直接重试」 |
| `CharAgent/tool/__init__.py` · `CharAgent/__init__.py` | `ToolTimeoutError` 进两处门面（根门面的防漂移用例守着） |
| `CharAgent/tests/test_tool_executor.py` | 新增 10 条（协程超时 / 同步线程 / 全局缺省 / 工具自己的 TimeoutError / kill switch / 工具级配置 / 签名不变 / 坏值注册期报错 ×3） |
| `CharAgent/tests/test_loop_recovery.py` | 新增 1 条：卡住的工具不再拖死整轮（run 正常收场 + 模型看到文案） |
| `CharAgent/tests/doubles.py` | 新增 `hang_forever`：永不返回的协程载体 —— 执行层与 loop 层两条用例共用一份（评审后从两处各抄一份收成一处） |
| `CharAgent/retry/idempotency.py` | 两处「工具执行无超时」的现状描述改为「当时没有, 2026-10-03 补上」+ 落地形态与取舍指针（原文是**现在时**, 不改就成了假话） |
| `CharAgent/tests/test_model_client_httpx.py` · `test_model_client_sdk.py` | 各新增 1 条：模型层 60 秒真的装进客户端（构造参数优先） |
| `CharAgent/tests/test_model_protocol.py` | 新增 1 条：**从环境变量装配**那条路也没改小 60 秒（评审后补） |
| `CharAgent/docs/DESIGN.md` | §② 硬骨头补 #15 落地段（三层里只空了中间那层 + 两条取消语义 + 两条边界）；§② 的「落地」行补 `tool/` |
| `CharAgent/docs/difficulties/02-stability.md` | #15 行补 `• 落地：` |
| `CharApp/docs/adr/0023-*.md` | 新增：工具超时是「不再等它」不是「它停了」（+ 分层取值 + 不考虑的六条替代方案） |
| `CharApp/docs/adr/0017-*.md` | 「下次被想起」那句标为已发生；补记 #15 与幂等的关系 |

## 实施记录

**超时的形状：一层 `asyncio.timeout` + 一个「这到底是不是我掐的」的判据。**
放在 `_invoke` 外面（解析 / 校验是微秒级，掐它们没有意义），用的是 `asyncio.timeout(limit)`
上下文管理器而不是 `wait_for`：3.12 起 `wait_for` 本来就是它实现的，而它能直接拿到
`guard.expired()`。**这个判据是必须的** —— 工具自己抛的 `TimeoutError`（socket / 连接池超时）
落在同一个 except 分支里，没有它就会被谎报成「框架等够 N 秒放弃了」。`expired()` 走的是
`_State`（EXPIRING / EXPIRED），只在**它自己触发取消**时置位，于是「外部 kill switch」
与「工具自己的超时」都从这条支路漏下去，原样上抛（后者走「意外异常」回填通用内部错误文案，
有一条用例钉着）。

**同步工具那条路：日志字段是 `thread_still_running: True`，msg 里点明「线程仍在后台运行到
函数自己返回, 副作用可能仍然发生」。** 走 `get_logger("tool")`（C05 的出口，于是这行也带三个
id、也过打码）与 `extra=`（结构化字段，`jq` 能直接筛）。**协程那条不记日志**：它真的被 cancel
了，失败本身会落在工具调用那一行记录里（`charagent_tool_calls`）—— 只有「还在跑」这件事是
看不见的，所以只记它。

**为什么 `ToolTimeoutError` 是 `ToolActionableError` 的子类**：两个都是「有话说给模型听」，
回填的路完全一样（`execute_tool` 里 `except ToolActionableError` 原文透传），于是**主流程
一行没改**；单独一个类型是给日志 / 轨迹 / 插件用的（认得出「不是工具说不行，是它根本没回来」）。
它也因此上了两处门面 —— 根门面那条防漂移用例会拦住「子包加了、根上忘了」。

**「30 秒」这个数的由来写进了 `config.py`**（不是写在某个 docstring 的角落里）：三层各自在
哪、为什么取这一段的上界、为什么不给「不设超时」这个选项。业务侧（`CharApp`）**一个字没改**：
18 个工具全是协程、全经一个 10 秒的 HTTP client 出门 —— 内层先响，框架这层是兜底
（判据与旁证写在「开工前要定的」那一节）。

**模型层与 run 层刻意没动**：60 秒早在构造参数上（这次补两条用例钉住，两个适配器各一条），
run 层是 `guard.check_after_turn` 的墙钟预算 —— 它是**轮后判定**（#3），与单次调用超时不是
一回事。把两者混起来改，会把「温和刹车」变成「随机时刻打断」。

**没做的事（都是判过之后不做的）**：
- **不给 kill switch 那条路补同样的「线程还在跑」日志**：那不是本片的验收项，属 #18
  （流式中断 / 取消）—— 记在 ADR-0023 的「代价与边界」里，免得下次读的人以为是漏了。
- **不做超时的降级话术**（「超时了就跟用户说 X」）：框架不编造用户文案（#4 的既有分界），
  回填给模型的话是「你可以怎么继续」，最后由模型自己组织回答。
- **不给任何工具单独放宽**（付款那类也一样），判据见「开工前要定的」。
- **不加环境变量开关**：真实差异是工具级的，一个全局数字按不住；要全局改就在装配处给每个
  工具传同一个值。

**评审后的修补**（两轴评审：标准 / 票面，各一个子代理并行跑；下面这些是改掉的，其余
逐条判断后保留 —— 理由写在各自的位置）：

| 发现 | 处置 |
|---|---|
| 票面文件行尾多一个空格（pre-commit 的 `trailing-whitespace` 会拦） | 删掉 |
| 「永不返回的协程载体」在执行层与 loop 层两个测试文件各抄一份（函数体逐字相同） | 收进 `tests/doubles.py` 的 `hang_forever`，两处 import（仓里的规矩：跨文件共用的替身随「共享支撑」走，与 `EventCollector` 同一个理由） |
| 新写的 `_log_records` 是仓里日志行解析器的**第三份**拷贝（`test_logging.py` / `test_server_logging.py` 各有一份 `lines_of`） | 那条用例只用一次 —— 索性内联两行，不新增共享件，也不留第三份 |
| 局部变量 `guard` 与仓里的 `LoopGuard` 撞名 | 改名 `timeout_guard`，并在原地留一句「仓里的 `guard` 一律指 LoopGuard，这个是超时那道闸」 |
| 「两条路的取消语义不同」在五处近义复述 | **保留**：其中四处各有分工（模块 docstring 指路 / 实现处写全 / 文案解释为何不分叉 / 配置表给数字），只把 `Tool.timeout` 那一条压短并指向实现处 —— 本仓的开发文档惯例就是「就地写 why」，砍到一处会让读者跳文件 |
| ADR-0017 里「本 ADR **第 29 行**说的……」自引用行号 | 改成文字指代（行号会随编辑漂） |
| 验收 4 的「工具只跑了那一次」当时无用例支撑（只证了取消传播干净） | 补断言：`runs == ["started"]` |
| 验收 5 的「业务侧装配一个字没漏」只有人工核对，无用例钉住 | 补一条**工厂路径**的用例（`chat_model_from_env` 那条生产路径也没改小 60 秒）；业务侧 `build_model` 那一跳仍是无用例的人工走查 —— 票面已如实标注，不假装有证据 |
| 验收 6 的「1502」与实测 1883/1884 对不上 | 票面写清 1502 是**切票时的规划数**，实测数字另列 |
| （自查，评审未点名）`DESIGN.md` 与 `retry/idempotency.py` 里「工具执行……无超时」是**现在时**陈述 | 两处都改成「当时没有，2026-10-03 补上」并指到 ADR-0023 —— 不改就成了假话 |

**保留没改的**（判过之后留着的）：`guard.expired()` 那一层区分（票面 §2.3 只点名
`CancelledError`，但 `except TimeoutError` 不加它就等于把「工具自己超时」谎报成「框架超时」）；
新增公共类型 `ToolTimeoutError`（票面只要求回填契约，但日志 / 轨迹 / 插件要分得清两种失败）；
`asyncio.timeout` 而不是票面举例的 `wait_for`（3.12 起前者是后者的实现，且能直接拿
`expired()`）。

---

## 2026-10-04 改判：超时不再交回模型，直接中断本次运行

> §三（超时之后回填什么）与 §四的前两条在本日被改判（本片落地才一天）。上面那些
> 保留为 2026-10-03 当天的事实；**现在生效的是本段**。ADR：`CharApp/docs/adr/0024`
> （改判 0023 第 3 条，其余各条保留）。

**改判的理由**：超时意味着**结果未知**（写操作可能已经生效）。原设计把决定权交回模型
（回填一句「不要直接重试」），而那只是一句**请求**，不是一道闸 —— 模型换参数再发一次
就是全新动作（幂等键只管同一次动作的重放，0023 自己写明了这句）。用户裁决：**任何工具
超时都中断本次运行**，不做读写区分、不加工具级开关（框架被防漂移用例禁止认识业务注解
`writes`，也不值得为「只读工具保留自纠错」维护第二套标记）。

**落地形状**：`tool/executor.py` 的 `ToolExecution.timed_out`（只认框架那道闸；工具自己
抛的内置 `TimeoutError` 不算，`ToolTimeoutError` 类型即声明）→ `agent/loop.py` 的
`_handle_tool_turn` / `_complete_pending_turn` 据此置 `outcome=interrupted`、`done=True`
→ 终局事件 `error(code="interrupted")` → BFF 换成用户话术。

### 验收

- [x] 协程工具永不返回 → 本次运行**中断**收场：`outcome is INTERRUPTED`、`content is None`、
      模型只被问过一次、那条 tool 消息在历史里（含「执行超过 0.05 秒 / 结果未知 / 不要直接
      重试」）、墙钟远小于 guard 预算 —— `test_a_tool_that_never_returns_interrupts_the_run`
      （`test_loop_recovery.py`，改写自旧用例）。
- [x] 一批里「超时 + 成功」：两条 tool 消息按调用顺序回填、成功的原文保留、两个 `tool_result`
      事件都在终局的 `error` **之前**（`stream/bus.py` 的不变量：未闭合的 tool_call 不许发
      终局事件）—— `test_a_timeout_still_backfills_every_call_in_the_batch`。
- [x] 中断**优先于挂起**：同批「要人批 + 超时」→ `approval is None`、要批那条按
      `RUN_INTERRUPTED_TEXT` 失败回填、帧 `suspension is None`、`metadata.outcome` 是
      interrupted —— `test_a_timeout_beats_a_suspension_in_the_same_batch`。
- [x] HITL 恢复段补做的那条超时 → 第二段就地中断、模型零次调用、那一帧仍是 `SUSPENSION`
      来源（只是 outcome 记 interrupted）—— `test_an_approved_call_that_times_out_interrupts_the_resume`。
- [x] 执行层的标记只认框架那道闸：`timed_out is True`（协程超时 / 同步超时）、
      `timed_out is False`（工具自己抛内置 `TimeoutError`）、`timed_out is True`
      （工具自己抛 `ToolTimeoutError`：类型即声明）—— `test_tool_executor.py` 三条。
- [x] 映射三处 + 终局文案：`db/state.py` → `RunStatus.FAILED`（`test_db_state.py` 新增一条）·
      `eval/utils/types.py` → `BROKEN`（`test_eval_runner.py` 加断言）· `client/render.py`
      中文短语（枚举遍历用例守着）· `TERMINAL_ERROR_TEXT["interrupted"]`（完整性用例的集合
      加了它）。
- [x] BFF 自己的用户话术（不是兜底那一句，且与 `stream_interrupted` 分开）——
      `test_a_tool_timeout_interruption_gets_its_own_copy`（`app/minimall/tests/test_bff.py`）。
- [x] 全量用例：框架 `pytest CharAgent` = **1414 passed / 132 deselected**（基线 1409，
      本段净 +5 = 新增 5 条；另改写 1 条旧超时用例）；业务 `pytest CharApp` = **355 passed**；
      BFF `manage.py test app.minimall.tests.test_bff` = **135 passed**（含新增 1 条）；
      `ruff check` / `ruff format --check` 全干净。

### 改了哪些文件（本段）

| 文件 | 说明 |
|---|---|
| `CharAgent/tool/executor.py` | `ToolExecution.timed_out`（新字段）+ `execute_tool` 置位 + 三处 docstring |
| `CharAgent/tool/utils/messages.py` | `timeout_error_text` 改写：删「换个方式/调整参数再试」，补「结果未知」；只陈述调用层事实 |
| `CharAgent/agent/utils/types.py` | `LoopOutcome.INTERRUPTED = "interrupted"` + 枚举 docstring |
| `CharAgent/agent/utils/messages.py` | `RUN_INTERRUPTED_TEXT`（同批未执行调用的回填，与 `APPROVAL_ALREADY_PENDING_TEXT` 并列） |
| `CharAgent/agent/loop.py` | `_handle_tool_turn`（中断判定 + 优先于挂起 + 降级回填）· `_complete_pending_turn`（恢复段就地中断）· 模块 docstring（四种停法 → 五种） |
| `CharAgent/agent/utils/events.py` | `TERMINAL_ERROR_TEXT["interrupted"]` + 模块 docstring |
| `CharAgent/db/state.py` | 映射 → `FAILED` + 注释段改写 |
| `CharAgent/eval/utils/types.py` | 映射 → `BROKEN` + 四分类表的成因 |
| `CharAgent/client/render.py` | `_OUTCOME_TEXT` 中文短语 |
| `app/minimall/views_bff.py` | `ERROR_COPY["interrupted"]`（用户话术，与「连接断了」分开） |
| `CharAgent/tests/test_loop_recovery.py` · `test_loop_suspension.py` · `test_tool_executor.py` · `test_db_state.py` · `test_eval_runner.py` · `test_loop_events.py` | 改写 1 条 + 新增 4 条 + 断言 / 集合更新（见验收） |
| `app/minimall/tests/test_bff.py` | 新增 1 条 |
| `CharApp/docs/adr/0024-*.md` | 新增：工具超时即中断本次运行 |
| `CharApp/docs/adr/0023-*.md` · `0017-*.md` · `CharApp/docs/PLAN.md` · `CharAgent/docs/DESIGN.md` · `CharAgent/docs/difficulties/01-core-loop.md` · `02-stability.md` · `CharAgent/retry/idempotency.py` | 指针 / 改判补记（旧文里「回填给模型自己决定」是现在时的，不改就成了假话） |

### 实施记录（本段）

**中断 ≠ 立即停，也不是取消**：同一批的兄弟调用在超时之前就开了，`gather` 会等它们跑完
（副作用照常、回填与事件照常），不做「取消兄弟」（那是 #18 那一族）。

**每一条调用都必须回填**，包括因中断而没执行的「要人批」那条 —— 三条理由：wire 配对
（带 tool_calls 的 assistant 后面必须有 tool 消息）、`stream/bus.py` 的状态机（未闭合的
tool_call 发不出终局事件）、落库那两拍（否则 `charagent_tool_calls` 留 PENDING 孤儿）。
降级必须在补消息**之前**完成，否则 `_facts_of` 会把那条记成 `needs_approval`（库里留幽灵卡）。

**`state.approval` 必须保持 None**：`emit_terminal` 先判 approval —— 残留会让终局发成
`approval_required`，用户看到一张永远不会被处理的确认卡。

**文案分两层**：工具层（`timeout_error_text`）只说调用层事实（结果未知 / 不要直接重试 /
先核实）—— 它也能被单独调用（演示 / 单测），写死「本次运行已中断」就是假话；运行级那句
由终局事件与 BFF 话术承担。本 run 里模型读不到这条 tool 消息（运行已结束），它写给的是
日后读到这段历史的人与模型（下一轮提问 / 显式 resume / 复盘）。

**没做的事**（判过之后不做的）：
- **不给工具级开关**（读写区分）：框架被防漂移用例禁止认识 `writes`，维护「两套标记焊死」
  的纪律只为让只读工具保留自纠错，不值（四个替代方案见 ADR-0024）。
- **不动 `ToolCallFact` / 库表**：`charagent_tool_calls` 记 `failed` + 文本（有稳定前缀可
  匹配）；要不要加超时状态是独立决定，边界记在 ADR-0024。
- **不给中断帧加新事件类型**：`error(code="interrupted")` + 前一条 `tool_result` 足够前端
  重建现场，事件类型集合是跨版本契约。
- **不拦显式 resume**：从那一帧接着跑是人的决定（CLI `--resume` / 会话层），边界记在
  ADR-0024「代价与边界」。
