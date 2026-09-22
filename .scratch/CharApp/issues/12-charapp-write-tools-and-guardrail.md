# 12 · 业务侧：8 个写工具、护栏插件、提示词清单

**Status:** done

**Type:** task

**Blocked by:** 08、11

**上游:** `../PRD.md` §4.4 / §4.6 / §4.8 / §4.9（L2）、`CharApp/docs/PLAN.md` §5、`CharApp/docs/adr/0003`

## 做什么

助手从「只能看」变成「能改数据」—— 工具从 9 个涨到 **17 个**；同时给这条新能力装上**护栏**（框架拦截点的第一条真实插件），并把提示词的版本声明从硬编码常量换成清单文件。

三件事放一片：都在 `CharApp/minimall/` 这一个包里、都是**业务的装配面**（工具集 / 插件 / 提示词），而且护栏**必须有写工具才能验** —— 拆开会让第二片变成「只能验半边」，正是 PRD §1 批评的「没人用过的接口」。

## 第一件：8 个写工具（9 → 17）

| 工具 | 参数 | 对应故事 |
|------|------|---------|
| `add_to_cart` | `slug`, `quantity` | 12 |
| `update_cart_item` | `slug`, `quantity` | 13 |
| `remove_cart_item` | `slug` | 13 的补集 |
| `clear_cart` | — | — |
| `place_order` | `address_id?`（不传 = 默认地址） | 14 |
| `cancel_my_order` | `order_no` | 与退款形成对照：无审批、即刻生效 |
| `request_refund` | `order_no`（**不带金额**） | 17 |
| `list_my_refunds` | — | 18 |

- **身份仍然不进参数表**：与 9 个只读工具同一条纪律（`build_tools(client, user_id)` 闭包），`test_identity_never_appears_in_any_tool_schema` 扩到 17 个。
- **工具说明要写清「什么时候该用」**（难点清单 #70 的要求），尤其 `cancel_my_order` 与 `request_refund` 的区别 —— 这是模型最容易混的一对。
- **每个写工具打 `annotations={"writes": True}`**（08 加的框架属性）—— 护栏插件靠它认人。
- **全部 `async def` + `httpx.AsyncClient`**：框架把同步工具扔 `to_thread`，写工具会占满线程池（issue 03 的真实教训）。

## 第二件：护栏插件（L2 的验收核心）

业务侧注册一条 `before_tool_execute` 钩子，两条规则：

1. **写操作预算**：一次运行内，写操作成功执行不超过 **8 次**
2. **单笔金额上限**：下单金额超过 **5000 元** → 拒绝，让用户去页面下单

- 放 `CharApp/minimall/`（不通用 → 业务侧，PRD §4.12 的判据）
- 拒绝原因要写成**模型看得懂的话**（走框架现成的「工具错误回填」通道，PRD §4.6 定的）
- 规则参数（8 / 5000）做成模块级常量，**并写清为什么是这两个数**：一次正常购物流程约 6–8 次写操作，8 不误伤；5000 高于在售最贵商品（4000）所以演示不误伤，但「买 100 件 × 99 = 9900」这类**被诱导的批量下单会被拦住**。

## 第三件：提示词清单

`CharApp/minimall/prompt/manifest.yaml`（`default: v1`）+ `service.py` 读它。

- `service.py:74-76` 的三个常量里 `PROMPT_VERSION` 退场
- **读不到清单 = 启动期错误**（`MinimallConfigError`），不静默退回上一版 —— 与 `PromptNotFoundError` 同一条纪律：静默退回会让一次「v2 的跑分」其实是 v1 的成绩，而且没有任何地方会报警
- `PyYAML` 已在根 `requirements.txt`（原来只有 OmegaConf 间接用），**不新增依赖**
- **「把实际命中的版本写进运行记录」不在本片** —— 它依赖 L3 的轨迹落库

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| 9 个只读工具的装配：`build_tools(client, user_id)` + `_BUILDERS` 表 | `tools.py:335-350` |
| 身份闭包 + schema 守卫用例 | `tests/test_provider.py::test_identity_never_appears_in_any_tool_schema` |
| 分页闭集 `PAGE_SIZE_OPTIONS`，工具级默认 100 | `tools.py:92-140` |
| 工具必须 async（框架把同步工具扔 `to_thread`） | `client.py`、issue 03 的实战教训 |
| 框架的拦截点与 `Tool.annotations` 由 08 提供 | 本目录 `08` |
| 8 个写端点由 11 提供 | 本目录 `11` |
| 提示词版本硬编码在 `service.py:74-76`，装配处 `:206` | `service.py` |
| L1b 的装配**只有一处**（`MinimallService.session_for`），CLI 与 server 共用 | issue 05 的验收 |
| 当前 9 个工具的命名约定：动词开头 + 名字里含 `my`（给模型的语言提示） | PLAN §3.3 |

## 具体任务

1. `client.py` 加 8 个方法（对齐 11 的端点）
2. `tools.py` 加 8 个工具 + `_BUILDERS` 扩到 17；每个写工具打 `annotations`
3. 新增护栏插件模块（业务侧）
4. `prompt/manifest.yaml` + `service.py` 读清单
5. **插件接到会话上 —— 装配只有一处**：CLI 与 server 都要装上，别只装一边（05 立过的旗）
6. 写测试

## 验收

- [x] CLI 里「把第二个加到购物车」→ 真加进去（随后 `get_my_cart` 读得到）—— 用例 + 真商城各核一次；**真模型那一句没跑**（要花 API，留 L4 的评估集）
- [x] 「下单，寄到我家」→ 用默认地址真下单 —— 同上（真商城核到真订单号）
- [x] 「取消订单 2026…」→ 真取消，余额回滚 —— 同上（真商城核到 `restocked_count=2`；未付款的单 `balance_returned=0.00`）
- [x] 「我要退款」→ 真建申请；「我的退款到哪了」→ 答得出状态与管理员备注 —— 同上（真商城核到订单转「退款中」）
- [x] **护栏生效且可轨迹断言**：连环下单到第 9 次写操作被拒 + **商城侧一次都没收到那个请求** + 模型收到拒绝原因并继续作答（CLI 与 HTTP 各一条）
- [x] **金额上限**：单笔 > 5000 → 被拒（单元 + CLI 端到端 + 真商城 8000 元那单）
- [x] 17 个工具的 schema 里搜不到买家 ID
- [x] 17 个工具全部是 `async def`
- [x] `manifest.yaml` 缺失 / `default` 指向不存在的文件 → **启动期报错**，不静默退回
- [x] 换一版提示词（新增一个版本文件 + 改 `default`）→ 装配取到的是新版（用例）
- [x] 既有 CLI 与 server 用例全绿；**装配仍然只有一处**（那条断言用例仍然过）
- [x] `ruff check` / `ruff format --check` 干净

## 备注

- **护栏为什么是这两条而不是别的** —— 见 PRD §4.6 的补记。要点：没有它，L2 有 8 个能改数据的工具，而**同一阶段没有任何东西拦得住模型连环下单**（HITL 挂起是 L3）；而且它把「哪些工具是写操作」从散落的 8 个业务函数里提出来，落到框架的一个统一属性上（用户故事 30 的原话：「挂在统一的点上，而不是散落在 17 个工具函数里各写一遍」）。
- **`cancel_my_order` 与 `request_refund` 的区别必须写进工具说明**：前者买家单方、即刻生效、**回滚库存**；后者要管理员审批、**不回滚库存**。这是 `CONTEXT.md` 里「取消 ≠ 退款」那条在 wire 上的落点。
- **`request_refund` 不带金额**（PRD §4.5）：买家申请时诉求是"退钱"，金额协商发生在对话框里；让模型有能力填金额 = 给模型决定权，与 §4.2 相悖。
- **三条留给 L4 的指针**（本片不做，写下来免得丢）：① 在 prompt 里要求模型**不复述**工具的真实数据（ADR-0003 的补充）；② 工具数量 A/B（17 全挂 vs 按场景裁剪）；③ 护栏收紧/放开的对照数据。
- **明确不做**：支付（PRD §6，归 L3 的挂起）、投诉（PRD §6，不做）、取消订单的"金额确认"（`cancel_order` 无需审批，没有可确认的东西）。

---

## 实际开发情况 2026-09-21

**一句话**：三件事全部落地（8 个写工具 + 护栏插件 + 提示词清单），CharApp 用例
**109 → 165**（+56），框架 **833 → 835**（+2，`ChatSession(hooks=)` 的前置），
`ruff check` / `ruff format --check` 干净。真商城上把 8 个写工具逐个核过一遍（见 §四）。

### 一、拍板的开放项（ticket 没定 / ticket 写错，实现时定下来的）

| 项 | ticket 说的 | 落地的 | 为什么 |
|----|------------|--------|--------|
| 护栏挂几个点 | 「业务侧注册**一条** `before_tool_execute` 钩子」 | **两个点**：`before_turn` 归类零 + `before_tool_execute` 做裁决（`WriteGuardrail.install`） | 归零若写在裁决方法里（`turn == 1` 就清账本），**同一轮里的并行调用会各清一次** —— 框架允许模型在一轮里并发发多次工具调用，它们带的 `turn` 是同一个数。第一版就是这么写的，被 `test_repeated_calls_never_reset_the_budget` 当场抓到（模型第一轮并发 8 次写操作，一次都拦不住）。`before_turn` 是每轮恰好一次，拿它当「新一轮开始」的信号才对 |
| 「写工具」几个 | 「8 个写工具……**每个**打 `annotations`」 | **7 个打标记**，`list_my_refunds` 不打 | 注解的含义是「这个工具会改数据」，而 `list_my_refunds` 是 `GET refunds/` —— 它只是**读**退款列表。打上它，买家问几句退款就白占写预算。ticket 表里的「8 个写工具」是**这一批**的名字（issue 11 的「8 个写端点」也含那条 GET），不是「8 个会改数据」 |
| 金额怎么判 | 「下单金额超过 5000 元 → 拒绝」 | 护栏**亲自问一次购物车**（`_overspend_reason` 里一次 `get_cart`） | 金额既不在参数表里（`place_order` 只收 `address_id`），又只有商城知道。让模型自己报一个金额 = 让被检查的人填检查项（它算错一个数、或被买家哄一句，这道闸就成了摆设）。一次本机 GET 换的是「这条规则真的算数」 |
| 5000 的依据 | 「高于在售最贵商品（**4000**）所以演示不误伤」 | 代码里写成**本意**：最贵的是 `iphone-17-pro` **8000** 元，它会被拦下 | 真库核过（`Product.objects.filter(is_active=True).order_by('-price')`）：8000 / 4000 / 999 / 238 / 99。ticket 那句依据不成立，但**结论可留**：5000 卡在「日常购买」（四件都在 5000 以下）与「大额消费」之间，而大额本来就该由本人拍板 —— L3 的挂起会把同一条规则升级成「挂起 → 买家点确认」。这一条**请用户确认**：若演示想买那台 8000 的，阈值要动 |
| 预算的计数口径 | 「写操作**成功执行**不超过 8 次」 | 按**放行**计（被护栏拒的不计，被商城业务拒的计） | 拦截点在**执行之前**，拿不到执行结果 —— 「成功」这个口径在那个位置上不可实现。按放行计是更保守的一侧（被商城拒的那次也占过一回额度） |
| 提示词用哪一版 | 清单 `default: **v1**` | `default: **v2**`，并**新增** `v2.prompt` | v1 是 L1a 的只读版，正文里明写着「不能替买家付款、下单、取消订单、申请退款」。不换版的话，模型会照提示词把 8 个写工具全拒掉（验收第 1 条当场失败），而**别的用例一条都不会红**。旧的 v1 原样留着（A/B 要用），`test_the_retired_version_is_still_the_retired_one` 钉住它没被就地改写 |
| 清单缺文件怎么判 | 「`manifest.yaml` 缺失 / `default` 指向不存在的文件 → 启动期报错」 | `resolve_prompt_version` **两件事一起判**（读清单 + 校验 `{版本}.prompt` 在盘上），都抛 `MinimallConfigError` | 只读清单做不到第二种：服务进程会照常起来，错误推到每个买家的第一次提问（框架 `load_prompt` 才发现文件不在）。判据合到一处，`build_service` 那句启动期预读才真的有意义 |

两处 ticket 之外的改动，都有一句话的理由：

- **`.gitignore` 加了一条例外**：根 `.gitignore` 有一条通用的 `*.yaml`（给 rag_text2sql 的私有配置用的），清单文件会被**挡在仓库外** —— 本地一切正常，别人克隆下来一启动就报「读不到提示词清单」。加 `!CharApp/minimall/prompt/manifest.yaml`，并用 `test_the_manifest_can_be_committed` 问一次 `git check-ignore` 守住它。
- **`ChatSession(hooks=)`**（框架侧一行参数 + 一行透传）：这是 08 §五 明确交给本片的前置 —— 不给这个参数，业务只能绕开会话自建 `AgentLoop`，那「装配只有一处」当场就破。

### 二、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `CharApp/minimall/client.py` | 8 个写方法；`MinimallRefusalError`（带 `code` + 中文 `message`）；`Refusal`（NamedTuple）；`_send` 抽出四种方法共用的传输；`_write`（业务拒绝与故障的分界）；`_refusal`（认 `{"error": {...}}`）；`_json` |
| `CharApp/minimall/tools.py` | 8 个工具（7 个打 `WRITE_ANNOTATION_KEY`）；`_act` + `_REFUSAL_HINTS`（按码补「下一步做什么」）；`_BUILDERS` 9 → 17；`SLUG_PATTERN` 进 schema |
| `CharApp/minimall/guardrail.py` | **新增**：`WriteGuardrail`（预算 8 + 金额 5000）+ `install`（两个挂载点）+ `start_run`（按运行归零） |
| `CharApp/minimall/service.py` | `resolve_prompt_version`（版本号唯一出处 + 启动期校验）；`PROMPT_VERSION` 退场；`session_for` 里挂护栏 |
| `CharApp/minimall/prompt/manifest.yaml` | **新增**（`default: v2`；被 `.gitignore` 的例外放行） |
| `CharApp/minimall/prompt/system/v2.prompt` | **新增**：能改数据的客服（8 个写工具的用法 + 取消/退款的区别 +「不能替买家付款」+ 撞上护栏怎么办） |
| `CharApp/minimall/server.py` | `build_service` 启动期先读一次清单 |
| `CharApp/minimall/cli.py` | 开场白与 `--help` 去掉「只读」；演示脚本换成 L2 的 |
| `CharApp/minimall/__init__.py` | 包门面：结构表加 `guardrail.py`，边界改写成「能改数据，但有闸」 |
| `CharAgent/client/session.py` | `hooks=` 透传给 `AgentLoop` |
| 测试 | `tests/test_guardrail.py` **新增**（17）；`test_prompt.py` 重写（18）；`test_tools.py` +写侧一节；`test_cli.py` / `test_server.py` 各加一条端到端；`conftest.py` 17 个工具名 + 8 个写端点样本 + `mock_all` 键统一成「方法 路径」 |
| `.gitignore` | `!CharApp/minimall/prompt/manifest.yaml` |

### 三、验收逐条

| 验收 | 证据 |
|------|------|
| CLI 里「加进购物车」→ 真加进去 | `test_asking_to_add_to_cart_really_adds_it`（断言商城收到的那条请求：方法/路径/请求体/身份 + 随后 `get_my_cart` 读得到）；真商城上也核过 |
| 「下单，寄到我家」→ 用默认地址真下单 | 真商城核过（不传 `address_id` 走默认地址，回真订单号）；`place_order` 的两条参数化用例分别钉住「带地址」与「不带地址」 |
| 「取消订单」→ 真取消 | 真商城核过（`status=cancelled` + `restocked_count=2`）；回执那两个副作用数字有用例 |
| 「我要退款」→ 真建申请 | 真商城核过（订单随即「退款中」，`amount` 为 `null`）；`list_my_refunds` 读得到；重复申请被拒（`refund_already_in_progress`） |
| **护栏生效且可轨迹断言** | `test_the_write_budget_stops_the_ninth_write`（CLI）+ `test_the_web_entry_has_the_guardrail_too`（HTTP）：商城**只收到 8 次**请求、第 9 条 tool 结果是失败态且带着护栏的理由、模型据此继续作答。**比「断言工具函数未被调用」更强**：断言的是商城侧没收到 |
| **金额上限** | `test_an_order_over_the_limit_is_refused`（单元）+ `test_an_order_over_the_limit_never_reaches_the_mall`（CLI：`POST orders/` 的 `call_count == 0`，而查购物车那条**被调过一次**）+ 真商城 8000 元那单被拒 |
| 17 个工具的 schema 里搜不到买家 ID | `test_identity_never_appears_in_any_tool_schema` 扩到 17 个（`EXPECTED_PARAMS` 逐键写死），另加写工具的 `X-User-Id` 头断言 |
| 17 个工具全部 `async def` | `test_every_tool_is_async`（原样扩到 17） |
| 清单缺失 / 指向不存在的文件 → 启动期报错 | `test_a_missing_manifest_is_a_startup_error` + `test_a_default_pointing_at_a_missing_version_is_a_startup_error` + 5 种坏清单形状 + YAML 写坏 |
| 换一版 → 装配取到新版 | `test_switching_the_manifest_switches_what_the_assembly_loads`（断在**会话历史的第一条**上，不是断在 `resolve_prompt_version` 上） |
| 既有 CLI / server 用例全绿；装配仍只有一处 | 全绿；`test_both_entries_go_through_the_same_assembly` 仍过，且两侧各有一条护栏的端到端用例 |
| ruff 干净 | `ruff check` / `ruff format --check` 覆盖 `CharApp/` 与 `CharAgent/` |

**跑过的测试**：`CharApp` = **165 passed**（基线 109，在临时 worktree 上量的）；`CharAgent` = **835 passed, 65 deselected**（基线 833）。Django 侧（`manage.py test app.minimall`）本片**没碰**，未重跑。

### 四、真商城核过一遍（测试替代不了它）

起了 Django dev server，用真 `MinimallClient` + 真工具集（买家 `refund_demo` #23）走了一遍：

| 动作 | 结果 |
|------|------|
| `search_products` → `add_to_cart` → `get_my_cart` → `update_cart_item` | 车里的件数与金额是真数据（8000.00 → 16000.00） |
| `place_order` | 真订单号，`pending`（助手只能到这一步） |
| `cancel_my_order` | `cancelled` + `restocked_count=2` + `balance_returned=0.00`（未付款的订单退 0，与商城侧判据一致） |
| `request_refund` | 先在库里把这单标成已付款（付款要 L3 的挂起才有），申请成功、订单转「退款中」、`amount` 为 `null` |
| `remove_cart_item` / `clear_cart` | 车真的空了 |
| 三种**拒绝** | 空车下单 →「购物车是空的」(带「先加购再下单」)；给未付款订单申请退款 →「这个订单现在的状态不能申请退款」；重复申请 →「已有一笔退款正在处理中」(带「用 list_my_refunds 看进度」) |
| 护栏 | 车里那台 8000 元的 `iphone-17-pro` → 拒单，理由里写着两个数并让买家去结算页 |

副产品：dev 库里多了几张验证用的订单（`refund_demo` #23 名下，一张 `refunding`、若干 `pending`/`cancelled`）。

### 五、代码审查改了什么（两轴各起一个 sub-agent）

| 发现 | 轴 | 处理 |
|------|----|------|
| **「default 指向不存在的文件 → 启动期报错」只做了一半**：`resolve_prompt_version` 只读清单，不校验 `{版本}.prompt` 在不在 | Spec **硬伤** | **已改**：清单与文件一起判（都抛 `MinimallConfigError`），并补 `test_a_default_pointing_at_a_missing_version_is_a_startup_error`。这条同时让 `build_service` 的启动期预读真的有意义 |
| **ticket 说「5000 高于在售最贵商品（4000）」与真库不符**（最贵 8000） | Spec 事实错 | **已改**：ticket 那句在 §一拍板表里更正；代码 docstring 改写成「卡在日常购买与大额消费之间」并注明最贵那件会被拦。**结论请用户确认** |
| ticket 把 8 个都叫「写工具」并要求都打注解，`list_my_refunds` 其实只读 | Spec 计数 | **保留 7 个**（见 §一），并在 `conftest.py` / `tools.py` / 本 ticket 写明这条取舍 |
| 新增行里有 **120 处全角标点**（`。` / `、`），项目 CLAUDE.md §4.9 要求标点一律英文 | Standards **硬违规** | **已改**：只改**本片新增的行**（106 行），历史漂移没顺手清 —— 与 issue 11 同一条规矩 |
| `_overspend()` 名字像谓词，实际返回拒绝理由 | Standards 判断题 | **已改**：`_overspend_reason()`，调用处的变量也跟着叫 `reason` |
| `WRITE_ANNOTATION` 装的是注解的**键**，而同批的 `MANIFEST_DEFAULT_KEY` 带 KEY | Standards 判断题 | **已改**：`WRITE_ANNOTATION_KEY` |
| `_refusal()` 用裸 `tuple[str, str]` 背两样东西，再由 `MinimallRefusalError(*refusal)` 按位置拆 | Standards 判断题 | **已改**：`Refusal` NamedTuple（两样都是字符串，顺序反了没人看得出来 —— 而 `MinimallRefusalError` 的参数顺序正好是反的） |
| `_fetch` 与 `_act` 同形，注释只解释了语义之差、没解释共有的尾巴为何写两遍 | Standards 判断题 | **已改 docstring**：共用的只有一行 `json.dumps`，合并会换来两个可选参数与两个半用的分支 |
| 测试里的轨迹推导式出现 3 次（只差 `seen(N)`）；`mock_all` 的键混用两种形状 | Standards 判断题 | **已改**：提 `backfilled(model, turn)`；`mock_all` 的键统一成 `"方法 路径"`（GET 也带方法），调用点跟着改 |
| `test_prompt.py` 把 `CURRENT_VERSION == "v2"` 钉死 —— 清单说它是唯一出处，测试却要跟着改 | Standards 判断题 | **已改**：换成 `test_the_declared_prompt_lets_the_model_change_data`（判据是**正文里有没有那几个动作**，不是版本名） |
| `__all__` 导出了没有消费者的常量（`SLUG_PATTERN` / `MANIFEST_DEFAULT_KEY`） | Standards 判断题 | **已改**：两个都退出门面（`WRITE_ANNOTATION_KEY` 留着 —— 护栏与测试真的要 import 它） |
| 范围外的两处（`.gitignore` 例外、`ChatSession(hooks=)`） | Spec (b) | **保留**：前者有 `test_the_manifest_can_be_committed` 背书、后者是 08 §五 交给本片的前置 |

### 六、留给下一片的（本片不做）

- 三条 L4 的指针照旧（prompt 里要求不复述真实数据 / 工具数量 A/B / 护栏阈值的对照数据）。
- **拆单能绕过金额上限**（一单 9000 拆成两单 4500）：真要堵住得按运行累计金额，而那是阈值问题，该由 L4 的对照数据决定。护栏的拒绝理由里已经写明「不要拆成几单」。
- **会话级总额度**：本片的预算管的是「一次运行」，同一买家连着问十句就是十轮，额度各算各的。逐笔确认是 L3 的挂起。
