# 05 · 业务侧：客服服务进程

**Status:** done

**Type:** task

**Blocked by:** 04

**上游:** `../PRD.md` §4.2 / §4.9 / §4.10、`CharApp/docs/PLAN.md` §4

## 做什么

让 CharApp 的客服服务**能独立跑起来**：用 `curl` 打它（带内部令牌 + 用户 ID），问一句「我余额还有多少」，收到真实商城数据的 SSE 流。命令行入口一字不变。

这一片兑现 PRD §4.2 那句承诺 —— 「第二阶段只换『身份从哪取』这一小段，后面的代码一行不改」。

## 具体任务

### 1. 装配从 `cli.py` 抽成 CLI 与 server **共用的一处**

这是本片的第一件事，也是 L1a 已经吃过一次亏的地方：issue 03 的评审记录里，交互层当初照框架抄了一份（约 94 / 130 行逐字相同），抄完当场开始漂 —— 业务那份丢了快照帧数提示。框架侧后来的处理是把那一层上浮成公共 API，业务改成子类只覆盖四个钩子。

这次同样的接缝落在「装配」上：

| 归业务（两处共用） | 归入口（各自独有） |
|---|---|
| 身份来路 → `RunContext` | CLI：解析 argv、交互循环、终端渲染 |
| `RunContext` → 会话（工具 + 提示词 + 模型） | server：HTTP 回调、进程生命周期 |
| 进程级的商城客户端与模型装配 | |

判断标准只有一条：**换一个入口还要不要这段？** 要 → 共用一份。

### 2. `build_context` 的身份来源变成参数

现在是「读命令行参数」（`cli.py:260-277`，注释里写明了第二阶段要换成什么）。现在换：

- CLI：从命令行参数取
- server：从 Django 转发的请求头取（`X-User-Id`）

**「身份从哪来」仍然只有一处** —— 只是那一处从「一个函数体」变成「一个参数」。

### 3. 实现框架（04）的两个回调

- **认证 + 解析**：`X-Internal-Token` + `X-User-Id` → `RunContext`。令牌沿用 L1a 已建立的约定（与商城侧同值，fail closed）
- **装配**：`RunContext` → `ChatSession`。复用第 1 步抽出来的那份，**不抄第二份**

### 4. 进程里的共享与生命周期

- 商城客户端在 L1a 已经是进程级（issue 03 的实现记录第 1 条：一条连接池服务该进程里所有买家）；模型与快照后端在 server 进程里同样应当进程级共享
- 会话是 per-thread 的，由框架侧（04）管
- 进程退出时的收尾：谁建谁关

### 5. 启动脚本与配置

- `sh/charapp_backend.sh`（当时叫 `sh/charapp_minimall_server.sh`，见「收尾变更」），沿用 `sh/` 惯例（`CHARLOTTE_ROOT` + `cd` + 一条命令）
- 端口与监听地址进 `.env` / `.env.example`（`CHARAPP_SERVER_*` 段，与既有 `CHARAPP_*` 同族）

### 6. 写测试

用框架提供的 ASGI 测试入口（或 httpx 的 ASGI transport）+ 假商城（`respx`）+ `MockLLM` —— 复用 `CharApp/tests/conftest.py` 已经铺好的那套替身，不依赖真 Django、不依赖真模型。

## 验收

- [x] `curl` 打本机服务（带内部令牌 + `X-User-Id`），问「我余额还有多少」→ 收到 SSE 流，终局事件里是**真实商城的余额**
- [x] 缺令牌 / 错令牌 → 拒绝（与商城侧同一套 fail closed）
- [x] 缺 `X-User-Id` → **明确拒绝**，不是「查不到数据」（身份是装配期的错，不该伪装成业务结果）
- [x] 同一 `thread_id` 连问两句 → 第二句接得上（多轮连贯在 HTTP 上成立）
- [x] 两个不同买家并发 → 各自拿各自的数据，不串
- [x] 命令行入口行为一字不变：现有 87 个测试全绿
- [x] **装配只有一处** —— 有一条可断言的证据（比如 CLI 与 server 走的是同一个函数，而不是两份长得像的代码）
- [x] 同一条链路在**真商城 + 真模型**上真跑过一次，不只是测试里绿

## 备注

- **令牌校验这会是第三处同类实现**（商城侧 `app/minimall/permissions.py` 与 charplot 那份已经是两份，issue 02 记过）。但这里是**跨进程**：CharApp 服务不能 import Django 代码，所以不能直接共享。至少要把 issue 02 发现的 `compare_digest` 非 ASCII 陷阱考虑进去 —— 或者明确记录为什么这里不适用（本处的请求头来自 Django 的 httpx 转发，不经过 WSGI 的 latin-1 解码）
- **别把「身份从哪来」散成两处**：server 侧的回调里只应该有一行「从哪个头取」，其余全是共用代码
- 命令行入口的 `_STARTUP_ERRORS` 那串启动期错误元组，server 侧大概率需要一份对应的 —— 想清楚是共用还是各写（共用优先）
- 本片**不做**取消（07）、不做 BFF（06）、不做前端

---

（实现完成后在此追加「实际开发情况」一节：验收逐条结果、与初稿的出入、计划外但必须做的、明确不处理的。）

## 实际开发情况 2026-09-20

**结论：完成。** CharApp **102 用例全绿**（原有 87 + 新增 15，都在 `tests/test_server.py`），
框架 802 / 65 deselected 照旧，**`CharAgent/` 零改动**（本片只加业务侧文件）。
真商城 + 真模型上真跑过（见下面「真跑记录」）。

### 验收逐条

| 验收项 | 结果 | 证据 |
|--------|------|------|
| 带令牌 + `X-User-Id` 问「我余额还有多少」→ SSE 流 + 真实余额 | ✅ | 真跑：`HTTP 200`、`content-type: text/event-stream`、`X-Run-Id` 在册；帧 `tool_call get_my_profile` → `tool_result {"balance": "500000.00", ...}` → `final 你的余额是 500000.00 元`；seq 连续 · 终局事件 1 个。用例：`test_a_question_comes_back_as_an_sse_stream` |
| 缺令牌 / 错令牌 → 拒绝 | ✅ | 真跑两种都是 `401 {"error":{"code":"unauthorized","message":"认证失败"}}`（**逐字相同**）。用例同上 + `test_a_service_without_a_configured_token_refuses_everything`（服务端没配令牌时，拿着**对的**令牌也拒） |
| 缺 `X-User-Id` → 明确拒绝 | ✅ | 真跑 `401 invalid_identity`；非整数同样拒。用例 `test_a_request_without_a_buyer_is_refused_explicitly`（并断言此事**没往商城发过任何请求**） |
| 同一 `thread_id` 连问两句接得上 | ✅ | 真跑：第一句「2000 块以下推荐」→ 三款；第二句「第二个多少钱」→ `get_product_detail(slug=rock-world)` → 洛克王国：世界 238.00（真查了一趟商城，不是从上下文猜的）。用例 `test_the_second_question_on_the_same_conversation_sees_the_first`（兼断「只装配一次会话」） |
| 两个买家并发各拿各的 | ✅ | 真跑：买家 #2 → 500000.00，买家 #10 → 20000.00。用例 `test_two_buyers_at_once_each_get_their_own_data`（假商城按 `X-User-Id` 回**不同**余额，并断言对方那个数字**不在**自己流里） |
| 命令行一字不变：87 全绿 | ✅ | 87 条原样全绿（未改任何断言）；`cli.main` 真跑一次也照常（余额 500000.00） |
| 装配只有一处（可断言的证据） | ✅ | `test_both_entries_go_through_the_same_assembly`：把 `MinimallService.session_for` 换成记账替身，从 **CLI 与 HTTP 各打一次**，断言两次都落在它上面（任一侧自己造 `ChatSession` 就少一条记录） |
| 真商城 + 真模型真跑过 | ✅ | 见下 |

### 真跑记录（2026-09-20，本机）

- 环境：`manage.py runserver`(8000) + `sh/charapp_backend.sh`(当时默认 8005) + 真 DeepSeek。
- **验收问句用的是**买家 `#2`（余额 500000.00）与 `#10`（20000.00）—— 本机库里只有这两个有 profile；
  一开始用 `#3` 得到的是「查不出来」，追下去是商城对不存在的买家答 404、工具如实把它当故障
  （`INTERNAL_ERROR_TEXT`），模型于是说「暂时不可用」——那是**对的**行为，不是本片的 bug。
- 客户端用的是 httpx 脚本而不是 `curl`：Git Bash 下 `-d '{"message":"我余额还有多少"}'` 里的中文
  会被编成 GBK，服务端直接回 `invalid_request`（第一次 curl 撞的就是这个）。这是**客户端侧**的编码
  问题，与 06 的 BFF（httpx 转发、UTF-8）无关，所以没有为它改任何服务端代码。
- 进程收尾也验了：`CHARAPP_SERVER_PORT=8006` 起一次、信号停掉后端口立刻释放；端口被占用时
  uvicorn 报错后**干净停机**（`Application shutdown complete`，收尾那段 finally 跑完了）。

### 与初稿的出入（以代码为准）

**1. 装配是一个对象上的方法，不是一个模块级函数**：`service.MinimallService.session_for`。
理由：服务进程本来就要**长期持有**那三件进程级零件（商城客户端 / 模型 / 快照），CLI 那侧
每次运行现攒一份 —— 一个 dataclass 同时当「零件持有者」与「唯一装配处」，两边才真的共用同一段
代码。CLI 侧多了一个 `service_for(options, model, client)` 薄适配（把 argv 选项翻译成装配参数），
**不含任何装配逻辑**。

**2. 多了一个头：`X-Conversation-Id`（可选，缺省 `web`）。** 初稿只点名 `X-Internal-Token` 与
`X-User-Id`。加它的理由：会话编号第三段（`业务:买家ID:对话ID`）总得有个来源，而 06 的验收要求
「同一买家两个标签页各聊各的」——若把第三段写死，06 就得回头改这个接缝，与本片「接缝只写一次」
的目的相反。代价：`provide` 里「从头里取」从一行变两行（身份一行、对话一行），wire 上多一个名字
（06 若不想要，改一个常量即可）。

**3. 启动期错误元组改由两入口共用**：`cli.py` 的 `_STARTUP_ERRORS` 搬到 `service.STARTUP_ERRORS`，
server 用它兜住进程启动（缺令牌 / 缺 Key / 快照后端不认识 → 一行日志 + 退出码 1）。
初稿的备注问了「共用还是各写」，答案是共用（同一批错误，同一句人话）。

**4. 提示词常量与 `build_model_for` 一并搬到 `service.py`**（`prompt_name`/`prompt_dir` 是装配的
一部分）。`tests/test_prompt.py` 的 import 跟着改了一行 —— 只改引用位置，断言一字未动。

**5. 收尾分两条路，且都写明理由**：server 用 `MinimallService.aclose()`（关商城连接池 + 模型 +
快照，一次），**不关会话** —— 会话与别的会话共用这三件资源，`ChatSession.aclose()` 会把模型与存储
一起关掉（框架 `server/sessions.py` 明文写着它从不调它）；CLI 那侧保持原有收尾
（`session.aclose()` + `client.aclose()`，关的正是同样三件）。

**6. `_configure_logging()`：** python 默认没有 root handler，业务 logger 的 INFO 会被 lastResort
（WARNING 级）丢掉 —— 启动那句地址与重试提示等于白写。只给自己包的 logger 挂输出口，不
`basicConfig`（那会把 httpx 每个请求一行的 INFO 也放进来，有用那几行会被冲走）。

### 计划外但必须做的（实现中发现）

1. **`.env` 里也补了 `CHARAPP_SERVER_*`**（初稿只说 `.env.example`）：不补则本机起服务要用默认值，
   而那正是「配置与文档不一致」的开端。
2. **`config.token_from_env` 抽出来**：令牌现在有**两个读法方向**（打商城 / 认转发件），
   名字与「空着就抛」的规则收在一处。
3. **`--max-turns` 的默认值改成读 `DEFAULT_MAX_TURNS`**：两个入口的默认轮数同源，免得一边改了
   另一边还是老数字。
4. **`ServerConfig.token` / `MinimallContexts.token` 加 `repr=False`**：共享秘密不该跟着对象被
   打印进 traceback 或日志。
5. **代码评审抓出的 5 处「搬家后没跟上的旧引用」**（都已改）：`provider.py` 模块 docstring 与
   **用户可见的报错文案**（它真的会打给使用者）、`tests/test_provider.py` 的注释、
   `minimall/__init__.py` 的装配线与结构总览表（补上 `service.py` / `server.py`）、
   `tests/test_cli.py` 里那句仍写成「第二阶段」的注释。
6. **生产接线（env → `build_service` → app）补了两条用例**：上面所有 app 用例都是直接注入
   `MinimallService` 的，这条路接错了它们一条都不会红。
7. **令牌的失败分支补了两条**：服务端空令牌一律拒绝、`server_config_from_env` 缺令牌直接抛。

### 明确不处理的（连同理由）

1. **取消端点（07）**、**断连的确定性取消（07）**：04 已经把机制闭合（`task.cancel` + 终局
   `error(cancelled)` + 收尾），本片不碰。
2. **BFF 与前端（06）**：本片不做用户面文案 —— 框架只转发事实（错误码 + message），
   「助手暂时不可用」这类话术归 Django 那侧。
3. **`X-Conversation-Id` 的合法性不在本层校验**：框架装配会话时会用 `checkpoint` 的标识符规则
   校验 `thread_id`，坏值会在那里报 `CheckpointConfigError`（500 + traceback，属接线 bug）。
   发号规则该由发号方（06 的页面）保证 —— 06 的 ticket 里也这么写着。
4. **会话淘汰 / 多 worker 的会话表**：登记表在框架侧（只增不减、单进程），框架已写明理由与
   升级路径，本片不引入第二套。
5. **`db/` 一行没碰**：本片只要内存里的登记表（框架提供），真需要持久化时再说。
6. **curl 里中文被 GBK 编掉**：客户端侧问题，服务端照旧只认 UTF-8 请求体（06 的 httpx 转发没问题）。
7. **验收脚本没进仓库**：临时目录里跑的（一次性核对用），可核对的版本是 `tests/test_server.py`。

### 收尾变更（2026-09-20，实现与评审之后，按用户要求）

三处命名/默认值调整，都是**改名不动行为**，改完重新验过（见下）：

| 改什么 | 从 | 到 | 动到的地方 |
|--------|----|----|-----------|
| 服务进程默认端口 | `8005` | **`1007`** | `config.DEFAULT_SERVER_PORT`、`.env`、`.env.example`、PLAN §4 架构图、测试 docstring |
| 商城地址变量名 | `CHARAPP_MINIMALL_BASE_URL` | **`CHARAPP_BASE_URL`** | `config.ENV_BASE_URL` 的值、`client.py`（含一条**用户可见**的报错文案）、`test_client.py` 两处注释、`.env` / `.env.example`、issue 03 的记录 |
| 启动脚本名 | `charapp_minimall_cli.sh` / `charapp_minimall_server.sh` | **`charapp_client.sh`** / **`charapp_backend.sh`** | `sh/` 下两个文件本身（含用法注释）、PLAN §3.5、issue 03 的记录 |

改完的复验（真商城 + 真模型，走**新脚本** `sh/charapp_backend.sh`、**新默认端口** 1007）：

```
INFO 客服服务启动中: http://127.0.0.1:1007 (POST /runs)
1. 余额: 200 text/event-stream; charset=utf-8 ['你的余额是 500000.00 元。']
2. 错令牌: 401 {"error":{"code":"unauthorized","message":"认证失败"}}
3. 缺身份: 401 invalid_identity
4. 多轮: ['进击的巨人是 99.00 元,现货 997 件。']
```

单测同轮全绿（102 条 —— 用例读的是 `ENV_*` 常量与 `DEFAULT_SERVER_PORT`，所以改名自动跟上）。

### 追加：服务端思考模式开关（2026-09-20，按用户要求）

**背景**：本片原来只让服务端走「不传 `thinking`」这一条路（上游默认开启）—— 关不掉。
CLI 有 `--no-thinking`，服务端没有对应物。

**做法**：新增环境变量 `CHARAPP_THINKING`（**服务端专用**，CLI 不看它，那边仍用 `--no-thinking`），
经 `config.thinking_from_env()` → `build_service(thinking=...)` → `MinimallService.thinking`
（装配只有一处，所以这一步天然只影响服务端那一侧的参数）。

三态，与框架那条契约对齐（`model/protocol.py` 的 `thinking`）：

| 取值 | 结果 |
|------|------|
| 不填 / 空 | `None` —— **不传该参数**，走上游默认（开启）。与从前逐字一样 |
| `1` / `true` / `yes` / `on` | `True` —— 显式开 |
| `0` / `false` / `no` / `off` | `False` —— 关（`payload["thinking"] = {"type": "disabled"}`） |
| 其它 | 启动期报错（`MinimallConfigError`）—— 这个开关决定每次请求的 token 与延迟，不替使用者猜 |

**为什么先确认了「上游默认开启」**：`thinking=None` 时字段根本不发出去（`client_httpx.py:144`），
所以要么确认上游默认开（不填 = 开，那「关」才是有意义的新增），要么这开关本来就该默认关。
证据：本片验收那次真跑的流里出现了 `reasoning` 事件（服务端从没发过 `thinking` 字段），
而录制样本里显式 `thinking=False` 的两次响应都没有 `reasoning_content` —— 两相对照，上游默认确实是开着的。

**真跑对照**（同一句需要推理的话：买两件 + 优惠券，真商城 + 真模型）：

```
CHARAPP_THINKING=false  事件序列: ['thinking', 'tool_call', 'tool_call', 'tool_result', 'tool_result', 'final']   有 reasoning: False
不填 (上游默认)          事件序列: ['reasoning', 'tool_call', 'tool_result', 'reasoning', 'final']                  有 reasoning: True
```

**测试**：+2 条（`test_the_thinking_switch_is_three_state` 覆盖三态与写错值；`test_turning_thinking_off_reaches_the_model`
断言 `thinking=False` 真的落到模型的请求参数上 —— 防「解析了、存下了、但没生效」那类静默失效）。
`build_service` 的接线用例里也顺手断了一次 env → 零件。全绿 **104 条**。

**明确不做**：`max_turns` 仍只有默认值（没有 env 开关）—— 它不像 thinking 那样直接影响成本与延迟，
等真有需求再加。

### 追加：编辑器解析不到跨目录的测试替身（2026-09-20，用户报的）

**现象**：`tests/test_cli.py` 与 `tests/test_server.py` 里的 `from mock_llm import ...` /
`from trace_assertions import ...` 在编辑器里报「未解析的导入」。

**为什么 pytest 全绿却没人报**：这两行靠 `CharApp/pytest.ini` 的 `pythonpath` 生效（业务侧复用框架
的测试替身，不抄第二份「假大脑」，PRD §5 接缝一）—— pytest 读那份配置，所以**运行时**一直是对的；
而 ruff（pre-commit 里跑的）只做语法/风格/未使用导入，**不做导入解析**，项目也没配 mypy / pyright ——
这类「编辑器视角」的错误在工具链里没有任何出口。（test_cli.py 那两行是 issue 03 就有的老问题，
test_server.py 这两行是本期新写的同一个问题。）

**修法（按用户指的路子，最省事也最不容易漂）**：替身改成**完整路径导入**
`from CharAgent.tests.mock_llm import ...` / `from CharAgent.tests.trace_assertions import ...`
—— `CharAgent/tests/` 虽然没有 `__init__.py`，但它是**命名空间子包**，照样能导入；这样编辑器、
lint、读代码的人看到的是同一件事，不必知道 pytest 配置里挂了什么路径。
`CharApp/pytest.ini` 的 `pythonpath` 相应收回到只剩 `..`（`CharAgent/tests` 那条不再需要 —— 留着反而
会多出「裸写法也能跑」这条路，同名的两份模块对象并存更难查）。
同目录的 `conftest` 保持 `from conftest import ...`：它本来就能解析，而且 pytest 自己就是按那个名字
加载它的，改成完整路径会造出第二份 conftest 模块对象。

**过程中我犯的错（记下来免得再犯）**：最初我改的是 `.vscode/settings.json`（加 `extraPaths`）——
那是**编辑器侧**的办法，而且我写之前用 `ls -la .vscode/ | head -5` 看目录，被 `head` 截掉了最后一行
`settings.json`，误以为文件不存在，**直接把用户原有的编辑器配置覆盖了**。已用 `git checkout --`
从 HEAD 原样恢复（该文件在版本控制里，`git status` 干净），改走上面的导入路径方案后不再需要动
编辑器配置。教训：写文件前用 `git status` / `git ls-files` 确认它是不是被跟踪的，别信一条被截断的 `ls`。

### 产物

新增（业务侧 3 个源文件 + 1 个脚本 + 1 个测试文件）：

```
CharApp/minimall/service.py        两入口共用的装配: build_context + MinimallService.session_for
CharApp/minimall/server.py         服务进程: 两个插座 + create_minimall_app + 进程入口
CharApp/tests/test_server.py       15 条 (ASGI 端到端 + 身份/令牌 + 会话隔离 + 生产接线)
sh/charapp_backend.sh              启动脚本 (沿用 sh/ 惯例; 当时的名字见「收尾变更」)
```

修改 4 个业务源文件：`CharApp/minimall/cli.py`（只剩终端那一半）· `CharApp/minimall/config.py`
（`token_from_env` + `ServerConfig`）· `CharApp/minimall/__init__.py`（结构总览补两个新模块）·
`CharApp/minimall/provider.py`（注释与用户可见文案各一处）；3 个测试文件：
`tests/test_prompt.py`（import）· `tests/test_cli.py`、`tests/test_provider.py`（各一条注释）；
外加 2 个环境文件 `.env` / `.env.example`（`CHARAPP_SERVER_*` 段）。

**框架零改动**：`CharAgent/` 全部未动（`git diff CharAgent/` 为空）。
