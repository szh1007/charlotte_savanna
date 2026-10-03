# C05 · #38 结构化日志 + id 贯穿（含框架异常栈过脱敏出口）

**Status:** done

**Type:** feature

**Blocked by:** —

**上游:** `CharAgent/docs/DESIGN.md` §4 ⑥ 的 #38（「本册只落了 #41 的一小块」段）；`CharApp/docs/adr/0019`（日志先脱敏再写）；`.scratch/Charlotte/PLAN.md` §4 组 B

## 现状（2026-09-30 读码核实）

| 事实 | 证据 |
|---|---|
| 框架有 **6 个各自为政的 logger** | `agent/compaction.py:95` · `client/session.py:110` · `db/recorder.py:141` · `prompt/ref.py:47` · `server/app.py:228` · `server/runs.py:46` —— 全部 `logging.getLogger("charagent.<子包>")` |
| **仓里没有统一出口** | 没有任何地方 `addHandler` / `setFormatter` —— 日志长什么样由**部署方**决定 |
| **框架自己打的异常栈不过脱敏出口** | `Redactor` 协议（`redact/protocol.py`）已经在了，业务侧也注册了字段名单（`CharApp/minimall/log_redaction.py`），但框架的 logger 从不经过它 |
| 访问日志已整个关掉 | `adr/0019`（搜索词会走查询串）—— **这一条要保持，别在本次改回** |

**这一片要闭合的是这条链**：脱敏（#26，已落）→ **结构化日志（#38，本次）** → trace 回放（#41，`client/trace.py` 已落）。

---

## 一、统一出口

框架提供**一个**配置入口，业务在启动时调一次：

- 位置：新建 `CharAgent/structured_logging/`（**不要塞进 `redact/`** —— 脱敏是出口上的一道工序，不是出口本身；两者分开才讲得清「先脱敏后格式化」）
- 形状：`configure_logging(*, level, redactor, json=True, stream=None)` + 一个 `get_logger(name)` 工厂
- **零新依赖**：JSON 格式化自己写（~30 行），**不引入 structlog / loguru** —— 「零框架依赖」是这个项目立身的那句话，`pyproject.toml` 那 8 项就是它的证据，不能为了日志破例

## 二、脱敏在格式化之前

```python
class RedactFilter(logging.Filter):        # 顺序是关键
    def filter(self, record):              # 1. record.msg / record.args → redact_text
        ...                                # 2. record.__dict__ 里的结构字段 → redact_fields
        ...                                # 3. exc_info 的 traceback 文本 → redact_text
```

**三个落点都要过**，其中第三个是本次的**核心**（DESIGN #38 点名「框架自己打的异常栈还没过出口」）：

- `record.msg` 与 `record.args`（拼好的一句话）
- 结构化字段（`extra=` 传进来的 dict）
- **`exc_info`**：`logging.Formatter.formatException` 吐出来的那段文本，要**先格式化再打码再拼回去** —— 直接对 `record.exc_text` 动手

## 三、三个 id 贯穿

| id | 从哪来 | 挂到哪 |
|---|---|---|
| `thread_id` | `run_context` / checkpoint 分区键 | 每次写入自动带上 |
| `run_id` | `AgentLoop` 的 loop_id / recorder 的 run | 同上 |
| `request_id` | **server 层**从 HTTP 头取（`X-Request-Id`，没有就生成） | 同上 |

**实现用 `contextvars`，不能用 `threading.local`** —— 框架是 asyncio 的，`to_thread` 里的同步代码也要能读到（`contextvars.copy_context` 会带过去）。**这一条要在票里写死，它是本片最容易做错的地方。**

对照：`project/rag_text2sql` 里 `main.py:19` 把 `request_id` **写死成 `"charlotte"`**，于是 HTTP 链路上所有请求是同一个 id，"链路追踪"是空的 —— 那正是本片要避免的形态。

## 三·补：uvicorn 的 `color_message` 排掉（2026-10-05）

用户报的：启动 charapp 时每行 uvicorn 日志多一个 `color_message` 字段。它是 uvicorn
给自己那行**彩色**输出用的模板串，正文与写出去那句 `msg` 逐字同义 —— 在我们的 JSON 里
只是一份重复。

排它的地方只有一处：`structured_logging/record.py` 的 `IGNORED_EXTRAS`（「哪些键算
结构化字段」的权威，JSON 与纯文本两档共用那条判定）。进那张表要满足两条：不是那条消息
本身、我们没有任何地方读它 —— 宁可多看一个字段，也不凭感觉把真信息丢掉。

真机复验：起一个实例，启动那几行里 `color_message` 出现 **0** 次，而
`msg: "Uvicorn running on http://127.0.0.1:8012 (Press CTRL+C to quit)"` 一字不少 ✓。

## 四、验收

- [x] 构造一条**含手机号的框架异常**（不是业务异常），落盘日志里搜不到原文，但能搜到打码后的形状
      —— 两条各验一处：框架侧 `test_a_framework_traceback_never_reaches_the_log`（`msg` 是
      「运行异常终止: RuntimeError」而原文在 `exc` 里，正好是 ADR-0019 记的那个形状）；
      业务侧 `test_a_framework_exception_never_reaches_the_configured_log_file`（**真文件**
      + 业务自己那份名单 + 生产那个 `configure_logging`）。手机号原文两处都搜不到，
      `138****0003` 两处都在，「堆栈还得是堆栈」（`Traceback` + 函数名）也没被一起打掉。
- [x] 同一次运行的所有日志行都带 `thread_id` / `run_id` / `request_id` 三个字段
      —— 三个键**恒在**（没绑就是 `null`，见 `test_the_three_ids_are_always_present`）；
      值也真的跟得进去：`test_a_run_carries_all_three_ids`（HTTP 一次运行）与
      `test_a_run_carries_thread_and_run_id_into_the_tool_thread`（连**同步工具**里
      打的日志都带着号 —— 它跑在线程池里，是 contextvars 最容易断的那一处）。
- [x] **HTTP 路径上，两个并发请求的 `request_id` 不相同**
      —— `test_two_concurrent_requests_get_different_request_ids`：两个响应头不相同，
      且日志里两个号各自都出现过（只比响应头不够 —— 串号的形状恰恰是「头各是各的、
      日志混成一个」）。
- [x] 日志是 JSON，一事件一行，可被 `jq` 直接消费
      —— 用例每一行都 `json.loads` 过（`lines_of`）；多行 traceback 也压在一行里
      （`json.dumps` 把换行转义掉）。真机起了一次业务服务：**整个进程只有一种格式** ——
      连 uvicorn 那几行也走了同一个出口（见实施记录的 `log_config=None`）。
- [x] `pyproject.toml` 的依赖**仍然是 8 项**（没有为日志引入新依赖）
      —— `dependencies` 就是那 8 项，一个字没动；JSON 是本包自己拼的。
- [x] 访问日志仍然关着（`adr/0019` 不被回退）
      —— `access_log=False` 一个字没动（`test_server.py` 那条钉着它）；新加的
      `log_config=None` 管的是「记成什么样」，与「记不记」无关。
- [x] 现有 1502 个用例全绿
      —— 实测（2026-10-03）：框架 `pytest CharAgent` = **1394 passed / 132 deselected**；
      另跑标记集 `pytest CharAgent -m "pg or pg_db or redis"` = **120 passed**；
      业务 `pytest CharApp` = **355 passed**（含本片新增的 8 条 + 1 条）。
      合起来 1526 个用例（票据里那个 1502 是切票时的数，C01–C04 之后涨了）。
      `ruff check` / `ruff format --check` 全干净。

## 开工前要定的

- **业务侧 `CharApp/minimall/log_redaction.py` 的字段名单**：**不扩**。理由：框架那六处
  日志全是自由文本（`logger.warning("摘要生成失败: %r", exc)` 这种），走的是
  `redact_text` 那条按形状认的路 —— 名单（`redact_fields`）只有 `extra=` 的结构字段才用得上，
  而框架今天一处都没用。名单里那六个名字是照 `serializers_agent.py` 抄的**业务字段**，
  框架不看业务字段（框架那边还有一条扫源码的用例守着「框架不认识业务」）。
  真正的接法不是扩名单，而是**把名单交给出口**：业务启动时
  `configure_logging(redactor=build_redactor())` —— 那份名单从此真的在打码（此前
  `redact_fields` 那一半没有生产调用方，`DESIGN.md` ⑥ 与 ADR-0019 都记着这件事）。
- **`client/trace.py` 的 `--view` 改成 JSON**：**不做**。它是给人**在终端读**的只读入口
  （`python -m CharAgent.client.trace <run_id> --view`，一张按列对齐的表 + 视图正文），
  而结构化日志管的是**日志流**。改成 JSON 只会让「给个编号回看这一次」变难读，
  而它本来就不是日志（它是「按编号查一次运行」，与 `jq` 那条路是两件事）。

## 改了哪些文件

| 文件 | 说明 |
|---|---|
| `CharAgent/structured_logging/{__init__,config,context,filter,formatter,record}.py` + `utils/{__init__,errors}.py` | 新增：结构化日志包（出口 / 取号 / 三个 id / 打码工序 / 两种格式 / 字段判定 / 错误族） |
| `CharAgent/server/middleware.py` | 新增：`RequestIdMiddleware`（裸 ASGI：发号 + 贯穿 + 响应头回一个） |
| `CharAgent/server/utils/types.py` | 新增 `REQUEST_ID_HEADER`（wire 契约常量与别的一起放） |
| `CharAgent/server/app.py` | 装中间件；`_finish_run` 里给那一条带 traceback 的日志补上下文 |
| `CharAgent/client/session.py` | `ask` / `resume` 各绑一次号（`thread_id` 开头绑、`run_id` 开账后补绑）；新增 `_log_run_finished`（每跑完一段留一行） |
| `CharAgent/{agent/compaction,client/session,db/recorder,prompt/ref,server/app,server/runs}.py` | 六处 `logging.getLogger("charagent.x")` → `get_logger("x")`（**名字一个字没变**） |
| `CharAgent/__init__.py` | 根门面补第十一个包（`structured_logging` 的 13 个名字） |
| `CharApp/minimall/server.py` | `_configure_logging` 退休，改调框架那一处；`uvicorn_config` 加 `log_config=None` |
| `sh/charapp_backend.sh` | 日志落盘改成**按天一个文件**（`logs/charapp_server_<YYYYMMDD>.log`，`tee -a` 同时看得见终端）+ 用仓库 venv 解释器 |
| `CharAgent/structured_logging/testing.py` | 新增：测试支撑（`restore_logging` / `logging_to`，两个项目的用例共用，不进门面） |
| `CharAgent/tests/{conftest,test_logging,test_server_logging}.py` | 新增 17 + 6 条用例（共享夹具挪进 conftest） |
| `CharAgent/tests/test_root_facade.py` | 框架包名单加 `structured_logging`（十一层）+ 一条「测试支撑不进门面」的断言 |
| `CharApp/tests/test_log_redaction.py` | 新增 1 条：框架异常过**进程出口**落盘后搜不到原文 |
| `CharAgent/docs/DESIGN.md` | ⑥ 与 ③ 的落地段：#38 已落、#26/#38 的交汇点已闭合 |
| `CharApp/docs/adr/0019-*.md` | 「已知未堵」标为已堵、「重新看这条决定」的触发点标为已发生 |

## 实施记录

**包的形状**：`config`（出口 + 取号）/ `context`（三个 id）/ `record`（一条记录里哪些键是
业务放的内容）/ `filter`（打码工序）/ `formatter`（两种版式）/ `utils.errors`。
`configure_logging` 挂在**根** logger 上（一个进程一个出口 —— 框架的行与业务的行必须长得
一样，否则「一事件一行 JSON」只对一半的行成立）；顺带把 httpx / httpcore / urllib3 压到
WARNING（业务侧 `_configure_logging` 那条注释早就说过为什么：一次问答十几次工具调用，
真正有用的那几行会被冲走）。再调一次会先摘掉上一轮装的那个（幂等）。

**三个号各自的语义**（最要紧的一条）：`run_id` 取的是**记录层那一行**
（`charagent_runs.run_id`），**不是** server 层的 `new_run_id()`。判据是「拿它能干什么」：
记录层那个能 `python -m CharAgent.client.trace <run_id>` 回看这次运行（账 / 工具调用 /
视图都在），而 server 那个只活在这个进程里、管的是「这条 SSE 流」（`server/runs.py` 的
`new_run_id` docstring 本来就把两者分开写了）。`resume` 那条路两个号恰好是同一个（路径里
就是记录层那一行），`start_run` 那条不是 —— **日志里那个号以记录层为准**。

**收尾回调是个反直觉的坑**（踩到了才发现）：`task.add_done_callback(...)` 带的是**注册那一刻**
的上下文（CPython 在 `add_done_callback` 里 `copy_context()`），不是任务自己那份 —— 于是
任务里绑的号，`_finish_run` 一个都看不见。而它调的 `close_stream` 打的正是那条
「运行异常终止 + traceback」（本片要堵的那条泄漏）。解法是在那一个 `with` 里把
`thread_id`（入参）与 `run_id`（`entry.session.last_run_id` —— 同一段会话不许并发，所以
刚跑完那一次就是要收的这一笔）补上。

**中间件为什么是裸 ASGI**：`BaseHTTPMiddleware` 把下游丢进**另一个任务**里跑，而
contextvars 按任务隔离 —— 在那里绑的号传不传得下去取决于 Starlette 在哪一步派生任务。
裸 ASGI 中间件与下游在同一条调用链上（它只是 `await self.app(...)`），不赌实现细节。
号从 `X-Request-Id` 头来（认得出的才用：字母数字加 `._:-`，≤64 字符），没有就 `uuid4().hex`，
并在响应头里回同一个 —— 客户端截图上的那一串是唯一能把「他说的那一次」与「日志里那一次」
对上的东西。

**`uvicorn_config` 加 `log_config=None`**（票面没写，实施时撞出来的）：不加的话 uvicorn 装
自己那套 handler，`Started server process` 那几行是**另一种格式**，与进程其余的行混在同一个
日志文件里 —— 按行消费的那一头会在它们身上断掉（验收第 4 条就不成立了）。关掉之后
uvicorn 的 logger 向上冒到根出口，整个进程只有一种格式。真机起了一次业务服务确认。
**`access_log=False` 不受影响**（一个管「记不记」，一个管「记成什么样」）。

**`log_writer()` 一个字没动**（两道脱敏是两道，不是一道）：新的出口管「这个进程的每一行」，
`log_writer` 管「递给框架的那个 writer」；重叠在同一条话上是幂等的（打过码的串不再匹配
那四条规则）。ADR-0019 的「要不要把业务那层撤了」留到下一次真机核对时再说（撤掉会动
那条既有用例，而它守的是「写之前打码」这句话在**交接点**上成立）。

**包名从 `logging` 改成了 `structured_logging`（实施时撞出来的一个雷，当场拔掉）**：包一开始
按票面叫 `logging`，而那个名字**会遮蔽标准库** —— 谁把 `CharAgent/` 放进 `sys.path` 的
前面，谁的 `import logging` 就撞上本包，症状是 `ModuleNotFoundError: No module named
'CharAgent'` 从一行 `import logging` 里抛出来，看着莫名其妙。实测触发方式只有一个（**cwd
恰好是 `CharAgent/`**，因为 `python -m pytest` 把 cwd 放在 `sys.path[0]`），而本仓口径一直
是从仓库根跑（`pytest CharAgent`）—— 在那个口径下无影响。**但仍然改了**：一个能让人撞上
标准库的名字不该留在一个要给人跑的作品里，而代价只是一次 `mv` 加十来处 import。改成现在
这个名字之后实测：`cd CharAgent && python -c "import logging"` 拿到的是标准库，
`cd CharAgent && pytest tests/test_root_facade.py` 也跑得通（原先两条都是红的）。
**logger 名一个字没变**（还是 `charagent.agent` / `charagent.db` … 那几个）—— 换的是包名，
不是日志树。

**补了一块原本会让人以为「日志没生效」的空白（2026-10-03，用户反馈后）**：本片落地后，六处日志全是
「出了事才响」的（失败 / 降级 / 记账写不进去），于是**跑顺的问答在日志里一行都没有** —— 用户真机
从客服页问了几轮，终端与文件都翻遍也没找到日志，第一反应是「结构化日志没配上」。这不是 bug 而是
取舍（框架只在自己没有调用方可以上抛的地方说话），但它让一份日志**缺了「正常长什么样」那份基准**，
而 `DESIGN.md` 的 #38 本来就把「关键上下文（user_id / model / 耗时 / token）」列在要求里。
于是补了一行：`ChatSession._log_run_finished` 在**每跑完一段**时记一条 INFO，走 `extra=` 带上
`outcome` / `turns` / `tokens` / `elapsed_ms` / `truncations` / `model`（三个 id 由上下文自动带上）。
失败与取消那两条路**不在这里记**（server 层那条带 traceback 的 ERROR 已经记了，命令行那层由调用方
接住）—— 同一件事写两次只会让日志变吵。命令行入口没有出口（`configure_logging` 只在服务进程调），
所以那一行在 REPL 里不会冒出来，终端的交互输出不受影响。

**日志落到哪个文件（2026-10-03，用户要求）**：`sh/charapp_backend.sh` 原来写死
`> logs/charapp_server.log`（每次启动冲掉上一份），现在改成按天的文件
`logs/charapp_server_<YYYYMMDD>.log` + **`tee -a`**：按天的文件要**累积**当天的输出
（用 `>` 的话同一天第二次启动会把上午那份冲掉，而那一份正是「早上那次为什么失败」的
唯一材料），而 `tee` 让前台跑的时候眼睛也看得见（此前那个 `>` 让终端一片安静 —— 用户
第一次「看不到日志」有一半是它造成的）。顺带两处实撞的修补：① 裸 `python` 在没激活
venv 的终端里是系统那个，脚本以 `ModuleNotFoundError: No module named 'httpx'` 收场
（现在与 `sh/charapp_demo.sh` 同款解析仓库 venv）；② `PYTHONIOENCODING=utf-8`，不然
中文 JSON 落到文件里是 GBK（同样与装置脚本同一条理由）。`sh/charapp_demo.sh` 那两个
日志文件**没动**：它们是「每轮演示从空日志开始」的产物，不是累积型日志。

**两个被问到的「为什么不」**（真机核对后写的，面试也会问）：

- **`message_id` 不进日志**：它是**派生值** —— `message_id_for(run_id, index)` =
  `f"{run_id}:{index}"`（`db/repositories/messages.py:42`），即「哪次运行 + 第几条」。
  而日志这一层是**运行**粒度（一行 = 一次运行：outcome / turns / tokens / 耗时），一次运行有
  N 条消息 —— 往里塞一个 message_id 得先回答「哪一条」，没有答案。反过来 `run_id` 已经在
  日志里了，它才是这一层的键：`select * from charagent_messages where run_id = '<日志里那个>'`
  就把这次运行全部消息连同它们的 message_id 摆出来了。要「每条消息一行」的日志是另一个
  粒度的东西，而框架现在不产（消息落库失败是按**会话 / 运行**报的）。
- **`charagent_runs.request_id` 不填日志里那个 request_id**：两个东西**同名不同义**。
  库里那一列是**幂等键**（#17：客户端重试时故意带同一个值 → 返回已有那一行、不重跑，
  有唯一约束 `uq_charagent_runs_request_id`），而日志里那个是**一次 HTTP 请求**的号
  （每次天然不同）。语义正好相反（一个要复用、一个不复用），拿后者填前者等于把幂等键
  变成「每次请求都不同的号」，幂等直接失效。**而且那一列至今没接线**：`begin()` 只传
  `thread_id` / `status` / `created_at`（真机核对：本机库 `charagent_runs` 1 行、
  `request_id` 非空 0 行）—— 框架备好了 `get_by_request_id()` 与唯一约束，但 `POST /runs`
  还不接受幂等键。**「从请求号反查运行」不需要它**：日志就是那个索引（request_id → 同一行
  的 run_id → 库）；要真在库里也存 HTTP 请求号，正确做法是加一列（加列要一条迁移，见 ADR-0006）。
  两处代码里各留了一句「同名不同义」的说明（`structured_logging/context.py` 的 `TraceIds`
  与 `db/schema.py` 那一列上方的 Python 注释 —— **不是** `comment=`：注释进 DDL，改它会被
  `test_no_difference_between_code_and_migrated_schema` 拦下，为一句提醒加一条迁移不划算）。

**留了一条没做的事**：`client/trace.py` 的 `--view` 保持人读格式（理由见「开工前要定的」）。

**评审后的修补**（两轴评审：标准 / 票面，各一个子代理并行跑；下面这些是改掉的，其余
逐条判断后保留 —— 理由写在各自的位置）：

| 发现 | 处置 |
|---|---|
| `CharApp/tests/test_server.py` 两处还写着「日志口是 `_configure_logging` 挂的」并 `logger.handlers.clear()` —— 那个函数这次删了，而出口也搬到了**根**上 | 删掉那两行与过时的说明；改成 `with restore_logging():` 包住 `main()`（`main` 一进门就调 `configure_logging`，不还原就会把指着 capsys 那个流的 handler 留在进程里） |
| 同一段「存下根 logger、跑完还原」的样板在**三个**地方各抄一遍（两个项目） | 新增 `CharAgent/structured_logging/testing.py`（`restore_logging` + `logging_to`），两边共用一份。**住在包里**而不是某个 `tests/` 下：`CharAgent/tests/*` 在本仓可 import，但**装出来的包里没有它**（`namespaces = false`）—— 跨项目共用的支撑得随包走（与 `db/testing.py` 同一个理由）；它也**不进门面**（根门面那条用例顺带钉住） |
| `configure_logging` 的返回值没有任何调用方用 | 去掉返回值（与 `logging.basicConfig` 一致），docstring 的 Returns 换成一句「要接日志用 `logging_to`」 |
| `_finish_run` 里那个 `with log_context(...)` 只包住 `close_stream`，而 `_settle_approvals` 是**在 finally 里派的**任务 —— 它复制的是派它的那一刻的上下文，于是那条路的日志没有号 | 把 `with` 包住整段（含 finally） |
| 并发那条用例「两个号都出现过」在**号被对调**时照样通过 | 按会话编号分堆比对（两次运行用不同的 thread_id，响应头与日志行一比，串号无处可藏） |
| 公共 API 里混着两个只有本包用的名字（`iso_time` / `extras_of`） | 收成模块私有 / 移出门面（15 → 13 个名字） |
| 扫业务词那条用例钉着扫描范围，但名单里漏了新包（与 `redact`） | 两个都补进去 —— 「扫漏了比扫出错更危险」 |
| `RichFormatter` 的骨架键与 `RESERVED_KEYS` 要一起改（纯文本那档会露馅） | 留一句注释把这件事写在字面量旁边（合成一份要把四个值各有各算法的东西硬拧在一起，不划算） |
| `_echoing` 这个名字看不出是在响应头里回号 | 改名 `_echoing_request_id` |

**保留没改的**：`filter.py` 里给 `stack_info` 也过一遍打码（评审说「今天没人用」）——
它与 traceback 是同一类东西（一段代码路径文本），留一条路不遮，迟早要在某次排查里
被发现；两行换「不静默地漏」。同样保留的还有全角顿号（项目规范写的是「标点一律英文」，
但框架既有文件里遍地是它，跟着周围写；评审也同意这是事实上的 house style）。
