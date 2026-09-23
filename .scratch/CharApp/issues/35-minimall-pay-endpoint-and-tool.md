# 35 · 业务侧：minimall 支付内部端点 + `pay_my_order` 工具

**Status:** todo

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

- [ ] `POST /api/minimall/agent/orders/<no>/pay/` 带正确密码 → 订单 `paid`、余额扣减；带错密码 → 可区分的错误码而**不是** `order_rejected`；别人的订单 → 404
- [ ] `pay_my_order` 的 wire schema 里**没有** `payment_password`（用例断言，与"身份不在 schema 里"同一个判据）
- [ ] 闭包里没有密码时工具**不调**端点（用打桩断言端点零调用）
- [ ] 模型拿到密码错的返回值后**不会**自动重试（用 `MockLLM` 断言只调一次）
- [ ] minimall 侧既有用例全绿（含遍历 `OrderServiceError` 子类的那条）
- [ ] `ruff check` / `ruff format --check` 干净

## 备注

- **本片只做"能付"**，不做"怎么让用户输密码"（那是 issue 36）与"下单前确认"（37）。
- **`pay_order` 服务一行不改** —— 它已经把该锁的都锁了、该判的都判了（L2 的成果）。本片只是给它接一条新入口。
- **别把密码记进任何日志**（与 issue 29 一条线）：BFF 与 CharApp 两侧的请求日志都不该出现它。验收里加一条"构造一次代付，日志里搜不到密码原文"。
