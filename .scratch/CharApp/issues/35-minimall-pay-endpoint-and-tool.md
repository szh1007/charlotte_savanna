# 35 · 业务侧：minimall 支付内部端点 + `pay_my_order` 工具

**Status:** done

**Type:** task

**Blocked by:** issue 34（`Decision.requires_approval` 与恢复端点先存在）

**上游:** `CharApp/docs/PLAN.md` §5 的 L3b 段第 4 条；**ADR-0015**（代付的密码走一次性载荷 —— 本片是它的执行者）；`CharApp/CONTEXT.md` 的「支付密码」词条（2026-09-24 推翻"助手不代付"）

## 现状（2026-09-24 核实）

| 零件 | 状态 |
|------|------|
| `pay_order(order, payment_password)` 服务 | ✅ 在（`app/minimall/services.py:391-429`）：`transaction.atomic()` + `select_for_update()` 锁订单 + 状态必须是 `pending` + 锁 `Profile` + `check_payment_password` + 扣余额 + `status=paid` / `paid_at=now()` |
| 买家自己的付款路径 | ✅ 在（`views_buyer.py:535-550` 的 `OrderPayView` + `serializers.py:300-303` 的 `PayOrderSerializer`）—— L2 的验收链跑的就是它 |
| **内部端点** `AgentOrderPayView` | ❌ **不存在**（`views_agent.py` / `urls_agent.py` / `serializers_agent.py` 全文搜 `pay_order` / `payment_password` **零命中**，唯一命中是买家面的改支付密码） |
| **`MinimallClient.pay_order`** | ❌ 不存在（client 有 17 个方法，无 pay） |
| **`pay_my_order` 工具** | ❌ 不存在（`tools.py` 的 `_BUILDERS` 是 17 个工厂：9 读 + 8 写） |
| 消息里"付款要本人输密码" | ✅ 已在 `_place_order` 的 docstring 里（`tools.py:552-553`：「下单不等于付钱，付款要买家本人在订单页输支付密码」）—— **本片之后这句话要改**（助手现在能帮着付了） |

## 一、内部端点 `POST /api/minimall/agent/orders/<order_no>/pay/`

照 `AgentOrderCancelView`（`views_agent.py:485-497`）的形状：`_resolve_buyer` → `_own_order`（`:434-442`，**订单归属校验在这里**）→ 调服务 → 返回序列化结果。**不写 try/except** —— 业务异常由基类的 `handle_exception`（`:228-238`）统一翻。

- **body**：`{"payment_password": "..."}`，走 `_validated_data(AgentOrderPaySerializer)`（`:240-253`），serializer 照 `PayOrderSerializer` 的写法（`write_only`、恰好 6 位）
- **返回**：照 `AgentOrderCancelSerializer`（`serializers_agent.py:300`）的形状 —— 带 `status` / `status_display` / `balance_returned` 一类的字段，让模型答得出"付了多少、余额还剩多少"
- **错误码要登记**：`EXCEPTION_CODES`（`views_agent.py:155-170`）里加 `PaymentError` / `InsufficientBalanceError` / `InvalidOrderStatusError` 三条，否则会落到 `FALLBACK_CODE = "order_rejected"`（`:173`）而丢掉可区分的语义
- **注意一条既有测试**：`app/minimall/tests/test_agent_write_api` **遍历 `OrderServiceError` 的子类**保证错误码不漏（`views_agent.py:150-151` 的注释记着这件事）—— 本片新增的异常登记要让它继续绿

## 二、`MinimallClient.pay_order(order_no, payment_password)`

照既有写方法（`client.py:450-474` 一带）的形状，走 `_write`（`:254-279`，把带 `error` 体的 4xx 翻成 `MinimallRefusalError`）。

**密码进 client 的方法签名是可以的** —— PLAN §1 那条纪律说的是「`user_id` **永不进入工具的函数签名**，因此永不出现在暴露给模型的 wire schema 里」。`MinimallClient` 是业务进程**内部**的对象，**不是** wire schema。真正不能进签名的是**工具**（见下节）。

## 三、`pay_my_order(order_no)` 工具 —— schema 里**没有** `payment_password`

**这是本片的核心约束，也是 ADR-0015 的前提。**

```python
def _pay_my_order(client, user_id, one_shot) -> Tool:
    @tool(annotations={WRITE_ANNOTATION_KEY: True})
    async def pay_my_order(order_no: str) -> str:
        """给这一单付款。**不需要你提供密码** —— 用户会在他自己的页面上输入。"""
        ...
```

- **签名里只有 `order_no`**。`payment_password` **永远不出现在 schema 里** —— 只要它出现，模型就会自己编一个填进去，而编出来的值会走 `arguments` 落库，ADR-0015 那三个"永不"当场失效
- **密码从闭包取**：`build_tools` 要多一个一次性载荷参数（如 `build_tools(client, user_id, *, one_shot=None)`），`MinimallToolProvider.provide`（`provider.py:77-89`）从 `ctx.payload` 里取。这样将来别的一次性载荷走同一条路，不新开机制
  - `provider.py:33` 已有 `PAYLOAD_USER_ID = "user_id"`，一次性载荷的键名照它的写法加一个常量
  - **`build_tools` 的签名变化会牵动 `tests/test_provider.py`**（它断言"身份不在 schema 里"）—— 那条断言**要继续成立**，并且要**再加一条**："密码也不在 schema 里"（同一个用例、同一个判据，多一个维度）
- **密码缺失 / 过期时的行为**：闭包里没有密码 → 工具**不执行**，返回一句"这次付款没有拿到授权，请让用户重新发起"。**绝不**用空密码去撞（那会白烧一次业务侧的失败路径，还可能把账号锁进某种风控）
- **密码错 = 本次失败收场**（ADR-0015 已定）：把 `PaymentError` 翻成一句面向模型的话，**明说不要重试**。重试拿的是同一个已消失的载荷，只会撞第二次
- **护栏标记**：`pay_my_order` 打 `WRITE_ANNOTATION_KEY`（`tools.py:52`）吃 8 次写预算。**金额上限那条不适用** —— `MAX_ORDER_AMOUNT` 的检查绑死在 `PLACE_ORDER_TOOL` 上（`guardrail.py:53,133-137`），它防的是"下一个超大单"，而付款付的是**已存在**的订单

## 四、谁让它挂起

**业务侧注册的，不是框架内置的。** 判据是工具名：

```python
if tool.name == "pay_my_order":
    return Decision.requires_approval(
        prompt="这一单要付款了，需要你输一次支付密码",
        needs=("payment_password",),
    )
```

- 挂在哪里：`CharApp/minimall/guardrail.py`（L2 建的那个插件，`install` 在 `:92-99` 已挂 `BEFORE_TOOL_EXECUTE`）—— **同一条路，不新开机制**
- `Decision.requires_approval` 由 issue 34 提供；本片只消费
- **"哪些工具要人工确认"是业务知识**（换成 code agent，付款这个场景不存在），所以归业务

## 交付物

| # | 内容 |
|---|------|
| 1 | `AgentOrderPayView` + 路由 + `AgentOrderPaySerializer` + 三条错误码登记 |
| 2 | `MinimallClient.pay_order` |
| 3 | `pay_my_order` 工具（schema 无密码）+ `build_tools` / `provide` 的一次性载荷通道 |
| 4 | 护栏：`pay_my_order` 返回 `requires_approval`；写预算适用、金额上限不适用 |
| 5 | `_place_order` 的 docstring 那句"付款要买家本人在订单页输支付密码"改成新事实 |
| 6 | 用例：无 token / 错 token → 403；用户隔离（A 付不了 B 的订单）；**工具 schema 里没有 `payment_password`**；密码缺失时不执行；`PaymentError` 翻成"不要重试"的话术 |
| 7 | minimall 侧 `tests/test_agent_api.py` 风格的新用例（照既有内部端点测试的写法） |

## 验收

- [x] `POST /api/minimall/agent/orders/<no>/pay/` 带正确密码 → 订单 `paid`、余额扣减；带错密码 → 可区分的错误码而**不是** `order_rejected`；别人的订单 → 404
- [x] `pay_my_order` 的 wire schema 里**没有** `payment_password`（用例断言，与"身份不在 schema 里"同一个判据）
- [x] 闭包里没有密码时工具**不调**端点（用打桩断言端点零调用）
- [x] 模型拿到密码错的返回值后**不会**自动重试（用 `MockLLM` 断言只调一次）
- [x] minimall 侧既有用例全绿（含遍历 `OrderServiceError` 子类的那条）
- [x] `ruff check` / `ruff format --check` 干净

## 备注

- **本片只做"能付"**，不做"怎么让用户输密码"（那是 issue 36）与"下单前确认"（37）。
- **`pay_order` 服务一行不改** —— 它已经把该锁的都锁了、该判的都判了（L2 的成果）。本片只是给它接一条新入口。
- **别把密码记进任何日志**（与 issue 29 一条线）：BFF 与 CharApp 两侧的请求日志都不该出现它。验收里加一条"构造一次代付，日志里搜不到密码原文"。

---

## 实施记录（2026-09-26）

### 交付物七条

| # | 内容 | 落点 |
|---|------|------|
| 1 | `AgentOrderPayView` + 路由 + `AgentOrderPaySerializer` + 三条错误码登记 | `app/minimall/views_agent.py` · `urls_agent.py` · `serializers_agent.py`（**错误码早就登记过了** —— 见下面那条"零改判"） |
| 2 | `MinimallClient.pay_order` | `CharApp/minimall/client.py:468`（唯一带凭据的方法，docstring 说清它与"身份不进参数表"为什么不冲突） |
| 3 | `pay_my_order` 工具（schema 无密码）+ 一次性载荷通道 | `CharApp/minimall/tools.py`（`PAYMENT_PASSWORD_FIELD` / `ONE_SHOT_FIELDS` / `_pay_my_order` / `build_tools(..., one_shot=)`）+ `provider.py` 的 `one_shot_payload` |
| 4 | 护栏：`pay_my_order` 返回 `requires_approval`；写预算适用、金额上限不适用 | `CharApp/minimall/guardrail.py`（`PAY_ORDER_TOOL` / `PAY_APPROVAL_PROMPT`，裁决排在预算之后 —— 拒绝优先于挂起） |
| 5 | `_place_order` 那句改成新事实 | `tools.py`：**下单不等于付钱；要付就再调 `pay_my_order`，那一单会停在买家确认那一步，不要去要密码**（这条**第一轮真的漏了**，是复核时抓回来的 —— 见下面「两轴复核」） |
| 6 | 用例（见下） | 新增 **21** 条（业务侧）+ **10** 条（minimall 侧） |
| 7 | minimall 侧新用例 | `app/minimall/tests/test_agent_write_api.py` 的 `AgentOrderPayTest` + `test_agent_api.py` 的 18 个端点认证表 |

### 三处改判 / 超出票据的改动（都写进了代码注释）

1. **回执只加一个字段（`balance_remaining`），不加 `paid_amount`**。票据说"带 `status` /
   `status_display` / `balance_returned` 一类的字段，让模型答得出付了多少、余额还剩多少"
   —— 「付了多少」就是同一份体里的 `total_amount`（付款付的正是整单金额）。两个字段报
   同一个数只会让模型犹豫念哪一个；真出现部分付款那天它才值得单列。
2. **密码缺失时"抛"而不是"返回"**。票据写的是"返回一句"；实现改成抛
   `ToolActionableError`（消息原文照旧回填模型，一个字没变）。理由是 2026-09-22 那次
   改判的同一件事：返回的话框架把这次执行记成成功，而页面上出现的是「这一单付好了」——
   可这一单根本没付。抛出去才走失败那条路（页面显示「付款没成功」）。
3. **`create_minimall_app` 多了一个 `idempotency=` 透传参数**（`server.py`）。它不是生产
   路径要的东西，是给用例的：假库跑不了 `PgIdempotencyStore` 那条 `INSERT ... RETURNING`，
   而不传它就没法在离线用例里走通恢复那条路。为什么透传而不是在用例里另搭一个 app ——
   挂起与恢复**是"接线"的一部分**，绕开工厂就等于绕开「业务把库交给框架了吗」那一步。

**一件"零改判"**：票据要求"错误码要登记"三条（`PaymentError` / `InsufficientBalanceError`
/ `InvalidOrderStatusError`）—— 它们**在 issue 11 就登记好了**，本片一行没加，只是终于有了
入口（`test_unreachable_but_required_codes_exist` 的 docstring 顺势改成"余额不足到本片有了
入口，只剩金额越界仍无入口"）。

### 真机（2026-09-26，真 Postgres + 真模型 + **真商城**，脚本 `D:/__WorkSpace__/Temp/hitl35_real.py`）

下面这张表是**定点代码**上最后那一趟（全过后又改过注释与用例，**主链路一行没动**才收的；
中间那几趟的数字见 `Temp/hitl35_run*.log`）。

与 34 那次不同：那次是玩具业务，这次全是生产件 —— 18 个真工具、真护栏、真商城
（Django 的 18 个内部端点，真 MySQL，脚本里走 ASGI 直连）、真装配
（`MinimallService` + `create_minimall_app`）。买家 `user_id=10`（savanna）。

| 步 | 期望 | 实际 |
|---|---|---|
| 备料 | 一笔待付款订单 | `202609260048240000108502` / 99.00 / pending；余额 19703.00 |
| 说「帮我把这单付了吧」 | 停在确认卡 | 事件 `reasoning → thinking → tool_call → approval_required`；卡上 `tool_name=pay_my_order` / `needs=["payment_password"]` / 话术「这一单要付款了, 需要你输一次支付密码」 |
| **挂起时真商城收到过付款请求吗** | 一次都没有 | 订单还是 pending、余额一分没动 |
| 查库 | 三处对上 | run `waiting_user` / `finished_at=None` / 提示词 `system/v3`；call `needs_approval`，`approval_needs=["payment_password"]`，参数 `{"order_no": "..."}`；存档帧 1 帧带 `suspension.reason=needs_approval` |
| 刷新页面 | 卡能重建 | `/history` 的 `pending_approval` 五样齐（run_id / tool_call_id / tool_name / prompt / needs） |
| 输密码 + 点确认 | 真的付掉 | 事件 `tool_call → tool_result → reasoning → final`；**订单 `paid`**（`paid_at` 有值）；**余额 19703.00 → 19604.00**（正好 −99.00）；调用行 `succeeded` + `approved_by=10` + `approved_at`；run `finished`；再刷新卡没了 |
| 模型答什么 | 答得出金额与余额 | 「付好了, 这一单 99.00 元 (進击的巨人 1 件)。付款后你的余额是 19604.00 元。」 |
| 手抖再点一次确认 | 不重放 | HTTP 404，余额不变 |
| 第二笔 + **错密码** | 拒掉且不重试 | 事件以 `final` 收尾（重试的形态会是再挂起一次）；工具 `status=error` / 页面话术「付款没成功」；那一单还是 pending；余额没动；那次运行**只发起过一次**付款调用 |
| 三个"永不" | 一处都不漏 | 消息与推理 **0 处**、工具 `arguments`/`result` **0 处**、存档帧 **0 处**、应用日志 **0 处**（正对照：扫到 90 行消息 / 18 行调用 / 27 条日志） |

**验收第 4 条的方法改了**：票据写"用 `MockLLM` 断言只调一次"，而"模型会不会重试"是模型
自己的行为 —— `MockLLM` 是脚本化的，它只会照脚本发牌，断言不出这件事。离线那侧保留的是
**能断言的那半边**（文案里明写"不要重试"，`test_a_wrong_password_says_dont_retry`），
"真的没重试"由真机这一行负责。

### 用例（业务侧 +21，minimall 侧 +10）

| 接缝 | 断了什么 |
|---|---|
| `test_tools.py`（+9） | 密码进请求体、没有密码时**零调用**（4 种形态）、密码错说"不要重试"、已付款的单说"去看订单状态"、余额不足说"去充值"、签名里只有 `order_no` |
| `test_provider.py`（+7） | schema 里搜不到密码（与身份那条同一个判据，带真密码装一遍）、一次性载荷从上下文走到闭包与请求体、凭据清单与护栏 `needs` 同源、空值不算"拿到了" |
| `test_guardrail.py`（+3） | 代付是**挂起**不是拒绝（三样都给全）、挂起不占额度、**预算用完时当场拒绝**（不弹卡） |
| `test_server.py`（+2） | 端到端：挂起 → 卡（`/history` 的 run_id）→ 带密码恢复 → **商城真收到那个密码**、订单付掉、余额对得上；拒绝那一路一分钱不动 |
| `app/minimall`（+10） | 端点：对密码 / 错密码（可区分码 + 不回显）/ 余额不足 / 已付款再付 / 别人的单 / 不存在的单 / 缺字段 / 位数不对 / 缺身份头 |

**测试总量**：`CharApp` = 228 passed（本片 +21）；`CharAgent` = 1283 passed（搬了
`PendingAwareDatabase` 到 `doubles.py`，用例数不变）；`manage.py test app.minimall` =
298 passed。`ruff check` / `ruff format --check` 干净。

### 残留（本片不做，或本片发现）

1. **仓库里的提示词 v2 还写着「不能替买家付款」** —— 那句话的改写是 **issue 37** 的交付物
   （它的交付物 #3 就是 `v3.prompt`）。**但 37 的票据只点了"删掉下单前确认那句"，没点这句**
   —— 这是本片发现的一处**计划缺口**：v3 必须同时把付款那一节改成 ADR-0015 的新事实，
   否则代付到 37 之后仍然走不通（模型会照旧拒绝）。真机用的临时提示词
   （`Temp/hitl35prompt/system/v3.prompt`）就是那一节的草稿，直接搬即可。
   **已把这条补进 37 的票据**（交付物 #3 与验收各加了一句），免得它跟着本片一起翻篇。
2. ~~**`app/minimall/apps.py` 的首次请求预热在 ASGI 下会炸**~~ —— **当场修了**（用户
   2026-09-26 发话"现在就修"）。见下面「收尾后补修」一节。
3. **测试里"模型不会重试"只能靠真机**（见上）：`MockLLM` 是脚本化的，断言不出模型的自主
   行为。要离线断言这一类，得引 L4 的评估集。
4. **真机在开发库里留了数据**：本片跑了五趟，每趟各下两笔（每趟一笔已付款 + 一笔仍待付款），
   订单号依次是 `202609260018590000103602` / `202609260019020000109503`（第一趟）、
   `202609260022570000104346` / `202609260023010000109287`（第二趟）、
   `202609260024520000104297` / `202609260024570000109854`（第三趟）、
   `202609260048240000108502` / `202609260048280000109063`（第四趟）、
   `202609260053120000100514` / `202609260053160000100050`（补修 ASGI 那趟）—— 余额
   20000.00 → **19505.00**（共付掉 5 笔 × 99.00），另有 5 笔停在待付款。清理按老规矩
   （先把这几行导到 `Temp` 再删）。
5. **`turn_count` 在恢复那一段记的是累计轮次**（本片真机：`1 → 3`）—— ticket 22 的口径，
   不是本片引入的；列在这里只为下次别把它当成新问题。

### 两轴复核（`/code-review`，2026-09-26）

两个轴各一个子代理，审的是本节这 19 个文件相对 `HEAD` 的 diff（另外三个片的改动不在
这次的 diff 范围里）。

**规范轴**——3 条硬伤 + 5 条判断项，全部修掉：

| # | 发现 | 修法 |
|---|------|------|
| 1 | `tools.py` 的模块 docstring：抬头写「四条」而正文成了 5 条，新增那条还编重复了 `4.` | 改成「五条」并重排（第 5 条） |
| 2 | **漏改的计数**：`test_agent_api.py`「全部 17 个端点」、`test_agent_write_api.py`「8 个写操作端点」、`test_redaction.py`「17 个工具一个不少」、`config.py:70`「17 个工具 schema」 | 逐个改到 18 / 9；`guardrail.py` 那句改成不带数字（「在每个写工具里各写一遍判断」），免得下次再漂 |
| 3 | `test_the_credential_list_matches_what_the_guardrail_asks_for` **名不副实**：它承诺「与护栏的 `needs` 是同一批」，却没 import 护栏，只断言「常量等于它自己」—— 护栏换成别的字面量它照样绿 | 改成真去问一次护栏（拿那条裁决的 `needs` 比对）；顺带删掉被包含的多余断言、修那句描述不准的注释 |
| 4 | `test_provider.py` 抬头错字「身份的密码都进不了参数表」 | 「身份与密码都进不了参数表」 |
| 5 | 同一密码 `135791` 在三个测试文件里各写一份 | 提到 `conftest.py` 的 `PAYMENT_PASSWORD`（与 `BUYER_ID` / `ORDER_NO` 同一处） |
| 6 | `AgentOrderPayTest` 里同一段代付调用抄了 5 遍 | 收成一个 `pay(order_no, password=..., *, user=...)` |
| 7 | `AgentOrderPaySerializer` 与买家面的 `PayOrderSerializer` 逐字相同 | **保留**（那是本模块自己写明的规矩：「这是 agent 的契约，不复用买家面那套」—— repo 标准压过基线气味） |
| 8 | `doubles.py` 抬头「替身清单（六个）」被我改成不标个数、而清单涨到 7 | 保留不标个数，并把理由写进那句话（数与代码同步是平白多一处会漂的地方） |

**规格轴**——3 条缺失/偏差，其中一条是**真漏项**：

| # | 发现 | 修法 |
|---|------|------|
| 1 | **交付物 5 没做**：`_place_order` 的 docstring 仍写着「付款要买家本人在商城的订单页输支付密码」，而上面那张交付物表却写着"已改" —— **表里那一行是错的** | 改成新事实（下单不等于付钱 → 要付就调 `pay_my_order`，那一单会停下等他输密码，**不要去要密码**） |
| 2 | 备注要的「日志里搜不到密码原文」只有真机扫了一遍，离线没有用例；BFF 那半要等 issue 36 | 离线补两条**能离线断言的那半边**：回填给模型的成功文本里没有密码、错误消息（会进日志与轨迹的那句）里没有密码 |
| 3 | 「`pay_my_order` 吃 8 次写预算」只被**门禁**、挂起那一刻不**占用**（恢复那段是新装的护栏，补做时记一次） | 语义正确（挂起那次没执行，运行又就此结束），把这条口径写进下面「预算的口径」一段 |
| 4 | 我把 `approval_required` 并进了共用的 `terminal_events()` —— 等于把十几条既有用例的「恰好一个终局」判据悄悄放宽 | 还原那条 helper；新加 `terminal_of()`（三种终局都算）只给代付这两条用例用 |

**两轴之外，这一轮还改了一处结构**：`PendingAwareSession` / `PendingAwareDatabase` 从
`test_server_approval.py` 搬进 `doubles.py`（业务侧的端到端也要它，而它本来就与
`FakeRecordDatabase` 是同一族；顺带把 `test_server_approval.py` 的 import 收干净）。

### 预算的口径（补一条，评审问到的）

代付对写预算的用法是**门禁 + 补做时记一次**：挂起那一刻**不记账**（`_used` 记的是
真做过的写操作，而这一条还没做，整次运行也就此结束）；恢复那一段会重新装配会话
（一次性载荷要进闭包），护栏账本因此是新的，补做真正执行时记一次。所以「一次代付
吃掉一次写预算」这句在意图上成立，差别只在**记在哪本账上** —— 而这一点不可观测
（挂起那本账当次就结束了）。

### 收尾后补修：ASGI 下的首次请求（2026-09-26，用户发话"现在就修"）

**缺陷**：`app/minimall/apps.py` 的 `_warmup_on_first_request(sender, environ, **kwargs)`
把预热挂在 `request_started` 上，而那个信号**两种处理器递的字段不一样** —— WSGI 递
`environ`（对得上），ASGI 递 `scope`。于是真按 ASGI 部署时**第一个请求必 500**
（`TypeError: missing 1 required positional argument: 'environ'`），而**只有第一个
请求**会撞上（之后 `_warmed` 已是 True，症状自己消失）。仓库里 `charlotte_savanna/asgi.py`
是在的，也就是说 uvicorn / daphne 一起就是开局 500。

**修法**（`apps.py`）：签名改成 `(sender, **kwargs)` —— 不是"两个字段都收下"，而是
**都不收**：这个函数关心的是"来了一次请求"，不是"那次请求长什么样"。

**证据三层**：

| 层 | 做法 | 结果 |
|---|---|---|
| 用例 | 新文件 `app/minimall/tests/test_apps.py`（3 条）：真走一次 ASGI 处理器（`ASGITransport` + `get_asgi_application()`，形状由 Django 自己递）断言**不是 500**；两种递法各调一次；只热一次 | 3 passed |
| **伪证** | 把签名改回 `(sender, environ, **kwargs)` 再跑 | **3 条全红**，报的就是真机那条 `TypeError`（说明这 3 条真的守着它，不是摆设） |
| 真机 | 把 35 真机脚本里那段 `request_started.disconnect(...)` 绕行**删掉**，重跑 | `EXIT=0` **全过** —— 商城那一跳第一次就通了（修之前同一个脚本死在第一个请求上） |

**顺带说明**：`warmup_cache()` 自己还有一个"只热一次"的标记（`cache._warmed_up`），
两层都在，所以"每个请求都热一次"这种更贵的错不会发生；新用例把预热函数打了桩
（`app.minimall.cache.warmup_cache`），免得到测试库跑一遍还去写那套 Redis 键。

### 改了哪些文件

| 文件 | 改了什么 |
|------|---------|
| `app/minimall/serializers_agent.py` | `AgentOrderPaySerializer`（请求体，write_only 恰好 6 位）+ `AgentOrderPaidSerializer`（回执 = 订单详情 + `balance_remaining`） |
| `app/minimall/views_agent.py` | `AgentOrderPayView` + 模块抬头 / 错误码那段的注释 |
| `app/minimall/urls_agent.py` | 一条路由 `orders/<no>/pay/`（name `order_pay`） |
| `app/minimall/tests/test_agent_api.py` | 认证覆盖表加代付那条（18 个端点） |
| `app/minimall/tests/test_agent_write_api.py` | 新用例类 `AgentOrderPayTest`（10 条）+ 错误码那段 docstring 改到新事实 |
| `app/minimall/apps.py` | **收尾后补修**：首次请求预热的签名改成两种处理器都吃（见上） |
| `app/minimall/tests/test_apps.py` | **新增**：3 条用例守着那个签名（含一条真走 ASGI 处理器的） |
| `CharApp/minimall/client.py` | `pay_order` + 头部/写操作那两处计数 |
| `CharApp/minimall/tools.py` | `PAYMENT_PASSWORD_FIELD` / `ONE_SHOT_FIELDS` / `_NO_AUTHORIZATION_TEXT` / `_pay_my_order` / `build_tools(..., one_shot=)` / 两条 refusal 提示 / 模块抬头与约定（新增第 5 条）/ `_place_order` 那句 |
| `CharApp/minimall/provider.py` | `one_shot_payload` + `provide` 传载荷 + 抬头 |
| `CharApp/minimall/guardrail.py` | `PAY_ORDER_TOOL` / `PAY_APPROVAL_PROMPT` + 挂起那一条裁决 + 三条规则的说法 |
| `CharApp/minimall/redaction.py` | 代付那三种页面话术 |
| `CharApp/minimall/server.py` | `create_minimall_app(..., idempotency=)` 透传 |
| `CharApp/minimall/__init__.py` | 包抬头（装配线 / 文件表 / 两条边界） |
| `CharApp/tests/conftest.py` | 工具名表 18 / 写工具 8 / 代付端点样本 / `PAYMENT_PASSWORD` |
| `CharApp/tests/test_tools.py` | 代付那一节 9 条 + 抬头与计数 |
| `CharApp/tests/test_provider.py` | 密码维度守卫 7 条 + 抬头 |
| `CharApp/tests/test_guardrail.py` | 挂起 3 条 + 抬头 |
| `CharApp/tests/test_server.py` | 端到端 2 条 + `serving_with_approvals` + `terminal_of` |
| `CharAgent/tests/doubles.py` | `PendingAwareSession` / `PendingAwareDatabase` 搬进来 |
| `CharAgent/tests/test_server_approval.py` | 改成从 `doubles` 取那两个替身 |
| `.scratch/CharApp/issues/37-....md` | 补记「v3 还要改付款那一节」（本片发现的计划缺口） |
