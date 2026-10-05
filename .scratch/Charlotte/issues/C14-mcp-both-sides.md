# C14 · MCP：消费侧接缝 + 暴露侧 server

**Status:** done

**Type:** feature

**Blocked by:** C09（暴露侧要导出的是含检索工具的完整工具集）

**上游:** `CharAgent/docs/DESIGN.md` §4 ⑨ 的 #50；`CharApp/docs/adr/0001`（内部端点信任调用方声明的 `X-User-Id`）；`.scratch/Charlotte/PLAN.md` §6.1 的 D1

## 现状（2026-09-30 读码）

| 事实 | 证据 |
|---|---|
| **SDK 已装、零 import** | `requirements.txt:169 mcp==1.29.0`、`:142 langchain-mcp-adapters==0.3.2` —— 但 `CharAgent` / `CharApp` / `app` / `demo` **全库零 import**（唯一命中是 `rag_knowledge` 里一段**被注释掉**的代码） |
| 接缝现成 | `ToolProvider` 是**单方法异步协议**（`agent/provider.py:102-119`）：`async def provide(ctx) -> Sequence[Tool]`，不继承基类、不缓存 |
| 工具可程序化构造 | `Tool` 是 `@dataclass(slots=True)`（`tool/decorator.py:52-90`），`parameters` 就是 wire JSON schema —— **MCP 的 `inputSchema` 可以直接填进去，不必用 `@tool` 装饰器** |
| 规划已在 | `DESIGN.md` #50 点名了三类能力（tools / resources / prompts）、生命周期、sampling、**多 server 的工具发现与同名仲裁** |

**两侧都要做**（D1 的结论）：消费侧证明**框架抽象装得下生态标准**；暴露侧证明**业务能力能被任何 MCP 客户端调用**，而且它能当场演示。

---

## 一、消费侧：`McpToolProvider(ToolProvider)`

```
initialize → tools/list → 每个 MCP tool 转成一个 Tool 对象 → provide() 返回
```

- **`inputSchema` 直接当 `Tool.parameters`** —— 两边都是 JSON Schema，中间不需要翻译层
- 调用时：`tools/call` 的结果转成字符串回填（`Tool` 的契约是返回 `str`）
- **同名仲裁**（DESIGN #50 点名）：多 server 场景下工具重名要有确定的规则（建议**前缀 + 拒绝静默覆盖**，重名时 fail fast 而不是后注册的赢）
- **生命周期**：连接何时建、何时关、断了怎么办 —— 与本项目的 `dirty` 纪律对齐：**不要静默降级成「工具少了一个」**
- 配置文件：消费哪个 server、用什么命令起（`command` + `args` 的既有 MCP 约定）

**消费侧的目标不是「找个业务相关的 server」**，而是**一条契约测试**：能把一个**真实**的参考 server 的工具列出来 → 转成 `Tool` → 交给 `AgentLoop` 跑通一次。为了"业务相关"硬凑一个场景，反而会稀释「抽象通用」这个论点。

## 二、暴露侧：把 `CharApp` 的工具包成 MCP server

- **进程形态**：独立进程、**stdio 传输**（这样 Claude Code / Claude Desktop 配一行就能连）
- **工具定义机械映射**：`CharApp` 的工具已经是 `name` / `description`（docstring 首段）/ `Annotated + Field` 生成的 schema —— 逐字段搬即可
- **只暴露只读那几个**（`get_my_order` / `list_my_orders` / `get_my_profile` / `search_products` / `search_knowledge`…）。**写工具（下单 / 付款 / 退款）不暴露** —— 理由：MCP 客户端没有你的确认卡机制，暴露写操作等于**绕过 HITL 直接下单**。这条要写进 ADR

### ⚠️ 信任边界：这一票最需要设计的地方

你的 `X-Internal-Token` + `X-User-Id` 模型（ADR-0001）成立的前提是「**同一个信任域内转发**」—— 浏览器 **session cookie 是唯一的真实认证点**，`X-User-Id` 只是同一域内的转发。

**MCP 客户端不在这个信任域里。** 它没有 session cookie。裸采信外部客户端声明的 `user_id`，就是把 ADR-0001 的前提拆了。

**处置（D1 的结论）**：本版按「**本地演示用**」做 ——

1. server 启动时从**环境变量**读一个固定的 `user_id`，绑定到单个账户
2. 工具 schema 里**不出现任何身份参数**（模型改不了）
3. 文档与 ADR 里写明边界：**这个 server 代表配置里那一个账户**，不是通用多用户网关
4. **生产化路径写进 ADR**：内部 JWT（`sub` claim）或 OAuth —— 但要说明**换的只是传输，验证点不变**

> **这条的叙述价值很高**：ADR-0001 自己写了「**什么时候必须还**」的判据 ——「信任域被拆开」。而 MCP 暴露侧**正好把信任域拆开了**。**你按自己半年前定的判据做了处置** —— 这比"我知道 MCP 有安全问题"强得多。

---

## 三、ADR

至少两条：**① MCP 暴露侧只开只读 + 固定账户的边界与生产化路径**；**② 消费侧的同名仲裁规则**（若规则不显然）。

---

## 验收

- [x] 消费侧：一条契约测试 —— 起一个真实 MCP server，`McpToolProvider` 列出它的工具、转成 `Tool`、经由 `AgentLoop` 跑通一次调用（`CharAgent/tests/test_mcp_provider.py::test_a_real_server_over_stdio_is_swallowed_by_the_loop`）
- [x] **两侧接得上，而且是自动用例**（2026-10-05 补）：`CharApp/tests/test_mcp_two_sides.py` —— 框架的 `McpToolProvider` → 真子进程 `python -m CharApp.minimall.mcp_server` → 进程内真 HTTP 假商城。此前这条链只有一次手动记录（真机那次），现在每次跑 CharApp 用例都会被验一遍
- [x] 消费侧：同名工具仲裁有测试（两个 server 同名时**不静默覆盖**；前缀是出路且路由不串台）
- [x] 暴露侧：在 Claude Code（或 Claude Desktop）里配上，问「我的订单到哪了」→ **工具真打到你的 Django 商城**，返回真数据
      —— 配置已落进**仓库根 `.mcp.json`**（`claude mcp list` 认到了它，显示 `⏸ Pending approval`：项目级 server 要本人在会话里点一次头，那是 Claude Code 的安全默认，不是故障）；
      那条 `command` / `args` / `env` **原样**从仓库外跑过一遍（`D:/__WorkSpace__/Temp` 起，只给 `PYTHONPATH`）：握手成功、列出 11 个只读工具、`get_my_profile` 拿回 savanna 的真余额；
      协议与数据那一段另有两条证据：裸 `ClientSession` 与**框架自己的 `McpToolProvider`** 各跑过一遍（`list_my_orders` → 15 笔真订单）
- [x] **否定断言**：暴露侧的工具列表里**没有**任何写工具（下单 / 付款 / 退款）—— 断两遍：名字不在（结果）+ 本地那 8 个确实带 `writes` 注解（机制）
- [x] **否定断言**：暴露侧工具的 schema 里**没有** `user_id` 之类的身份参数（另有一条：客户端想在参数里塞身份 → 本地挡下，**一个请求都没发出去**）
- [x] `AgentLoop` 零改动（`git diff --stat -- CharAgent/agent/` 为空；远端工具经的是同一条 execute_tool → 回填路径）
- [x] 两条 ADR 已记（0030 / 0031）
- [x] `CharAgent` / `CharApp` 既有用例全绿（**1594 passed** + **465 passed**，评审每一轮修完都重跑一遍）

## 开工前要定的（结果）

- **消费侧接哪个 server**：提交的契约测试对端是**本仓的 fixture server**（`CharAgent/tests/fixtures/mcp/demo_server.py`，用官方 SDK 写的低层 `Server`，真协议真子进程，**零网络零依赖**）—— 默认用例不触网是本仓纪律。对**官方参考 server**（`@modelcontextprotocol/server-filesystem`）的验证另留手动脚本 `CharAgent/tests/try_mcp_reference_server.py`（需要 node + 首次下载，故不进默认套件；本次**未实跑** —— 起它要下载并执行第三方 npm 包，权限不允许，留给人手动跑一次）
- **暴露侧 SDK 写法**：官方 SDK 的**低层 `Server`**，不用 FastMCP —— 我们的工具 schema 已经是 wire JSON Schema，低层处理器原样递出去；FastMCP 会从函数签名再推断一遍（等于把已有的答案再猜一次）。调用则走框架自己的 `execute_tool`，于是暴露出去的工具与助手手里的工具**同一条执行链**
- **消费侧放哪**：`CharAgent/mcp_client/`（框架模块，不是独立包），做成**可选依赖组** `charagent[mcp]`、**不上根门面** —— 与 `server` extra 同一个理由再加一条实测：`import mcp` 会把 starlette 拖进来

## 改了哪些文件

**新增**

| 文件 | 是什么 |
|---|---|
| `CharAgent/mcp_client/__init__.py` | 门面 + **目录名为什么带 `_client`**（与第三方顶包同名会把它遮蔽掉） |
| `CharAgent/mcp_client/config.py` | `McpServerSpec` + `parse_server_specs`（认 Claude Desktop / Code 那份 `mcpServers` 形状；我们多读 `tool_prefix` / `call_timeout` 两个键） |
| `CharAgent/mcp_client/client.py` | `McpClient`：一台 server 的连接（起进程 → 握手 → **翻完分页**列工具 → 换 `Tool`）与 `tools/call` 往返（文本回填 / `isError` → `ToolActionableError` / 省略的选填项不发 `null`） |
| `CharAgent/mcp_client/provider.py` | `McpToolProvider`（形状同 `ToolProvider`）+ `arbitrate`（同名仲裁）+ 收尾按 **LIFO** 关连接 |
| `CharAgent/mcp_client/utils/{__init__,errors}.py` | `McpError` / `McpConfigError` / `McpServerError` |
| `CharAgent/tests/fixtures/mcp/demo_server.py` | 契约测试对端：`add_numbers` / `echo` / `fail_softly` / `crash` / `bare` + 分页开关 |
| `CharAgent/tests/test_mcp_config.py` | 23 例（认形状 / 别家字段跳过 / 错处指名道姓） |
| `CharAgent/tests/test_mcp_provider.py` | 11 例（契约 / 保真 / 仲裁 / 起不来 / 断了 / 翻页 / 生命周期） |
| `CharAgent/tests/try_mcp_reference_server.py` | 手动脚本：打官方 `@modelcontextprotocol/server-filesystem` |
| `CharApp/minimall/mcp_server.py` | 暴露侧：低层 `Server` + 只读过滤 + 固定账户 + 握手声明边界 |
| `CharApp/tests/test_mcp_server.py` | 12 例（只读 / 一个账户 / schema 原样 / 真往返 / 上游故障 / 未知工具） |
| `CharApp/tests/test_mcp_two_sides.py` | 2 例（**两侧接上**：框架的 `McpToolProvider` → 真子进程 `python -m CharApp.minimall.mcp_server` → 进程内真 HTTP 假商城） |
| `CharApp/docs/adr/0030-mcp-exposes-read-only-tools-for-one-account.md` | 只读 + 固定账户的边界与生产化路径（兑现 ADR-0001 的判据） |
| `CharApp/docs/adr/0031-mcp-tool-name-collisions-fail-fast.md` | 同名仲裁：默认报错，前缀是唯一出路 |

**改动**

| 文件 | 改了什么 |
|---|---|
| `CharApp/minimall/config.py` | `ENV_MCP_USER_ID` + `mcp_user_id_from_env()`（没配就起不来） |
| `CharAgent/pyproject.toml` | 可选依赖组 `mcp = ["mcp>=1.29"]`（附「为什么不是硬依赖」与「目录名为什么不是 `mcp`」） |
| `CharAgent/__init__.py`、`CharAgent/tests/test_root_facade.py` | `mcp_client` 列入「刻意排除」并加断言（`__all__` 的名字不上根门面；import 根门面**不拖 MCP SDK**） |
| `.env.example` | `CHARAPP_MCP_USER_ID` 模板（`your-mall-user-id`） |
| `.env`（不提交） | `CHARAPP_MCP_USER_ID="10"`（savanna —— 演示用的那个账户，本人可改） |

**零改动**：`CharAgent/agent/`（循环核心）、`CharAgent/tool/`、`CharAgent/model/`、既有业务工具与提供者。

## 实施记录

**三个决定**（开工前那三条的结论，理由见上面「开工前要定的」）：

1. 消费侧对端 = 本仓 fixture server（真协议真子进程零依赖）+ 一个手动脚本打官方参考实现。
2. 暴露侧 = 低层 `Server`（不是 FastMCP），调用走框架 `execute_tool`。
3. 消费侧 = `CharAgent/mcp_client/` 框架模块 + 可选 extra，不上根门面。

**路上撞出来的三件事**（都留了用例或注释）：

1. **目录名撞顶包**：`CharAgent/mcp/` 与第三方顶包 `mcp` 同名 —— 而 `CharAgent/` 自己会进 `sys.path`（pytest 的 rootdir 插入就是一条），那一刻 `import mcp` 解析到**我们自己**，`client.py` 的 `from mcp import ClientSession` 变成循环导入。改名 `mcp_client` 并把这条坑写进包 docstring 与 pyproject（同一条坑对任何与顶包同名的目录都成立）。
2. **两条连接的取消作用域必须 LIFO 关**：两台 server 的 `stdio_client` 作用域叠在同一个任务上，正序关第一台会让 anyio 在收尾时抛 `CancelledError`（用例先红出来的）。`McpToolProvider.aclose` 因此反序收，且 `_close_quietly` **不吞** `CancelledError` —— 它在这个位置出现就是「收尾没收干净」的信号。
3. **省略的选填参数不能变成 `null`**：executor 把模型的参数经 pydantic 模型逐字段取出（缺的会给默认值），于是给选填字段配一个哨兵默认值、调用前滤掉 —— 有 `echo` 那个工具回显原始 arguments 来钉这条（显式给 `null` 时服务端的 jsonschema 会拒，正好证明两者不是一回事）。

**验证**

| 验的是什么 | 怎么验的 | 结果 |
|---|---|---|
| CharAgent 全量 | `cd CharAgent && python -m pytest -q` | 1594 passed, 145 deselected |
| CharApp 全量 | `cd CharApp && python -m pytest -q` | 465 passed |
| ruff | `ruff check` + `ruff format --check` 新文件 | clean |
| 真机（暴露侧，评审修完后又跑了一遍） | 真 MCP 客户端（`ClientSession`）→ `python -m CharApp.minimall.mcp_server` 子进程 → 真 Django（`manage.py runserver`）：`initialize` 拿到 `minimall 0.1.0` 与那段边界说明；`list_tools` 列出 11 个只读工具；`get_my_profile` → savanna 余额 19465.00；`list_my_orders` → 15 笔真订单；`place_order` → `isError` 并列出可用工具；`{"user_id": 999}` → 被 schema 校验挡下 | 通过 |
| **两侧接上（自动用例）** | `CharApp/tests/test_mcp_two_sides.py`：框架的 `McpToolProvider` → 真子进程暴露侧 → 进程内**真 HTTP** 假商城。断三段：11 个只读工具（写的不在）、`get_my_profile` 拿回商城字段、商城看到的 `X-User-Id` 就是 `CHARAPP_MCP_USER_ID`（拿「临时把账户改成 999」验过这条断言有牙） | 通过 |
| 真机（**两侧接上**） | 用**框架自己的** `McpToolProvider`（带配置里的 `cwd`）接 CharApp 的暴露侧（真 Django）：列出同样 11 个工具、`get_my_profile` 拿回真数据、塞 `user_id` 被本地那道校验挡下（中文文案） | 通过 |
| 真机（**客户端那份配置本身**） | 照抄仓库根 `.mcp.json` 的 `command` / `args` / `env` 三项（只给 `PYTHONPATH`, 不给 `cwd`），从 `D:/__WorkSpace__/Temp` 起：握手成功、11 个工具、真余额 | 通过 |
| 官方参考 server（**第三方写的**） | `.venv/Scripts/python.exe CharAgent/tests/try_mcp_reference_server.py CharAgent/mcp_client`（本机手动跑） | **通过**：14 个工具 + 一次真调用。过程中撞出并定位了一个**启动路径**问题：经 `npx` 起它时 3 次失败（连接在 `tools/list` 或调用时断，且对端连启动横幅都没打出来），而**直连 npx 缓存里的 node 入口 5/5 全过**（横幅、14 个工具、调用全成）—— 脚本因此改成「优先直连缓存、缓存空才回退 npx」。机制没钉死（「无横幅」也可能是 npm 吞了子进程 stderr），只按可操作的结论写；排障时 `底层原因: None` 表示失败在**我方参数校验**（不是对端） |

**代码评审（2026-10-05，/code-review）抓出来并已修的十条** —— 都留了用例或注释：

| 抓到的问题 | 怎么修的 |
|---|---|
| 远端工具名（加前缀后）**没按模型侧规范校验**，带点 / 超长的名字会一路带到上游变成看不懂的 400 | `_checked_name`：本地过一遍 `[a-zA-Z0-9_-]{1,64}`，不合格当场报；补例（fixture server 加一个带点的工具，env 开关控制） |
| `parse_server_specs` 非映射输入时把 list / int 一律说成 `NoneType` | 报**传进来那个东西**的真实类型；把「整份配置是列表 / 数字」两条参数化用例的断言改成断类型名 |
| `arbitrate` 按 **server 名**判「是不是同一台」——两台同名的连接会被误报成「一台之内重复」 | 改按**连接对象本身**判；另加 `_check_unique_names`（构造期就拒重名 server，配置那份重复不了但直接构造可以） |
| `Tool.annotations` 的缺席是**静默**的（模块 docstring 却写着「不该有信息损耗」） | 模块 docstring 写明**故意不搬**远端 `readOnlyHint` 那一组、为什么，以及「按注解判断的消费者会把远端工具当没注解」这条后果；补一条用例钉住「现在没搬」 |
| 文档里那份配置的 **`cwd` 被静默丢掉**（于是 `python -m …` 那条命令在仓库外起不来，报在客户端界面上只是「server 起不来」） | `McpServerSpec` 加 `cwd`（`_spec_of` 读它、`_params` 传它）；补一条用例让 fixture server 自报工作目录 |
| 「连不上」与「工具定义转不过来」共用一句报错，把第二种人指到错的地方 | `start()` 分两段试，两种毛病两句报错 |
| `_shape_model` 造 pydantic 模型时会**静默丢掉** `_id` 这类下划线参数（MCP 里很常见），且 `model_dump` 之类名字会撞 pydantic 保护命名空间 | **整条 pydantic 路线去掉**，改成给 `**kwargs` 闭包装一份按 schema 合成的签名 —— 参数键原样转发、下划线参数可用、省略仍是省略，代码还少了一截 |
| `_close_quietly` 的 docstring 说「只记账不外抛」，但目录里没有一行日志 | 补 `logger.warning`（全局规范：不吞异常至少留痕） |
| 正则用 `.match` + `$` 会放过**尾随换行**（`"demo\n"` 能过） | 三处判形状一律改 `.fullmatch` |
| **取消（Ctrl-C / kill switch）时半开的连接没人收**：`CancelledError` 不是 `Exception`，两条 `except` 都接不住它 —— 子进程只能等 GC 在**别的任务**里退取消作用域（实测从终结器里冒一句 `Attempted to exit cancel scope in a different task`），而 `aclose()` 变成一次静默 no-op；`aclose()` 中途被打断还会把后面的连接弄丢 | `start()` 的收尾挪进 `finally`（取消也算）；`McpClient.aclose` 被打断时把连接**还回原位**；`McpToolProvider.aclose` 改成「关成功一台才从台账划掉一台」（再调一次接着收）；补一条用例：取消落在握手那一步，断言工具集仍空、能再 start、**子进程真的没了**（psutil 数子进程，缺它就跳过）—— 这条用例拿「临时关掉收尾」验过有牙（会红，且红在 `RuntimeError: Attempted to exit cancel scope in a different task`） |

评审还指出的两条**没在本票修**（都写进了对应票据，不是沉默的缺席）：

- `tool/decorator.py` 的 `_NAME_PATTERN` 有同一个 `$` 坑（既有实现，超出本票范围）。
- `read_only_tools` 是 fail-open：它筛的是注解，而「会改数据的工具都打了注解」靠人守
  —— 这条前提写进了 ADR-0030 的「代价与边界」。

**押后 / 不做**（写在这里，别让它变成沉默的缺席）：

- **resources / prompts / sampling 三类能力**：DESIGN #50 提到，本票只做 tools（消费侧也只做 tools）。真要做时消费侧的接缝是 `Tool` 之外的第二个形状，不是把 `Tool` 硬塞。
- **`tools/list_changed` 通知**：远端工具集中途变化这一版不处理（仲裁只在 `start()` 做一次），ADR-0031 的「什么时候重新看」里记着。
- **暴露侧的鉴权**：本版无鉴权、绑单账户（ADR-0030 写了边界与生产化路径）。
- **消费侧接进 CharApp**：不做 —— 为了「业务相关」硬凑一个场景会稀释「抽象装得下生态标准」这个论点（票面原话）。
- **`langchain-mcp-adapters` 没用上**：消费侧直接用官方 SDK（`langchain-mcp-adapters` 产出的是 LangChain 工具，等于多一层翻译，而我们要证的正是「**我们的**抽象装得下」）。`requirements.txt` 里那一行删不删归 C20（已在 C20 票里记下）。
