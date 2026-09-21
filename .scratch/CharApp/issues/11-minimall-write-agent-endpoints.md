# 11 · 商城侧：8 个写操作内部端点

**Status:** done

**Type:** task

**Blocked by:** 10

**上游:** `../PRD.md` §4.3 / §4.9（L2）、`CharApp/docs/PLAN.md` §5

## 做什么

把 09 与 10 做出来的业务能力**对 agent 开门** —— 8 个写操作内部端点，与 issue 02 的 9 个只读端点同一套认证、同一套隔离纪律。

L1 的 9 个只读端点在 `products/` 这类路径下已经跑通，本片是同一套写法加 8 个。

## 端点清单

前缀沿用 `/api/minimall/agent/`（`urls_agent.py`，`app_name = "minimall_agent"`）。

| # | 路径 | 方法 | 身份 | 干什么 |
|---|------|------|------|--------|
| 1 | `cart/items/` | POST | 要 | 加购（body: `slug`、`quantity`） |
| 2 | `cart/items/<slug>/` | PATCH | 要 | 改数量（body: `quantity`） |
| 3 | `cart/items/<slug>/` | DELETE | 要 | 移除一项 |
| 4 | `cart/clear/` | DELETE | 要 | 清空 |
| 5 | `orders/` | POST | 要 | 下单（body: `address_id`，**可选** —— 不传用默认地址） |
| 6 | `orders/<order_no>/cancel/` | POST | 要 | 取消 |
| 7 | `refunds/` | POST | 要 | 申请退款（body: `order_no`，**不带金额**） |
| 8 | `refunds/` | GET | 要 | 我的退款列表（**不分页**） |

**购物车项用 `slug` 定位，不用 `cart_item_id`。** `unique_together = ["cart","product"]` 保证一个商品在车里只有一行，两者都能唯一定位；选 `slug` 是因为它是模型在商品页、搜索结果、购物车返回体里到处都能看到的稳定标识，而 `cart_item_id` 是数据库主键 —— 对模型是个没有语义的数字，模型只能靠「上一轮第几个」去猜，那是指代消解出错的高发点。（若 `get_my_cart` 的序列化器没暴露 `slug`，补上 —— 它本来就是**面向 agent 的契约**。）

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| 认证 + 身份解析的现成写法：`AgentEndpointView`（`authentication_classes = []` + `permission_classes = [IsInternalService]`）+ `_resolve_buyer` | `views_agent.py:76-84`、`:61-73` |
| fail-closed：未配置 `CHARAPP_INTERNAL_TOKEN` 时拒绝一切请求 | `permissions.py:46-49` |
| `_resolve_buyer`：`X-User-Id` 缺失 / 非数字 → 400；用户不存在 / 禁用 → **404**（不是 403，防枚举） | `views_agent.py:61-73` |
| `compare_digest` 的 str 版只接受 ASCII，已转 bytes 修好 | `permissions.py:50-56` |
| 面向 agent 的序列化契约单独成文件，显式声明「不是买家面接口的镜像」 | `serializers_agent.py` |
| 商品数据**直查 DB、不走 Redis 缓存**的理由（缓存最长约 12 分钟，助手报「还剩 3 件」时买家会当事实） | `views_agent.py`、PRD §4.3 |
| `_paginate` 的白名单 `PAGE_SIZE_OPTIONS` 与回退默认值 20 | `views_agent.py:37-58` |
| 购物车 4 个写操作的业务逻辑在 09 抽进 `services.py`；退款四态与 `refunding` 在 10 建好 | 本目录 09 / 10 |

## 具体任务

1. **8 个端点全部继承既有的 `AgentEndpointView`** —— 认证、fail-closed、身份解析、错误形状**一行不重写**。

2. **全部直查 DB / 走 service，不走 Redis 缓存。** PRD §4.3 的理由对写操作同样成立且更强：写操作必须读最新状态，用缓存判「还能不能取消」是会出真错的。

3. **新增面向 agent 的写侧序列化器**（`serializers_agent.py` 里加一节），与只读那批一样**显式声明这是 agent 契约**，不复用买家面那套。

4. **错误映射**：service 抛的业务异常 → 状态码 + `{"error": {"code", "message"}}`（项目既有约定）。**错误码要能被上层区分**：至少「库存不足」「状态不允许」「重复申请退款」「余额不足」「没有默认地址」「商品不存在」要各自有码 —— 12 的工具要把它们讲成不同的中文，混成一个码就只能说「出错了」。

5. **响应体要能被工具直接讲成人话**：返回足够的上下文，别只回 `{"ok": true}`。例：取消订单要回「退了多少余额、回滚了几件库存」；下单要回订单号与总额。

6. **写测试。**

## 验收

- [x] 8 个端点全部可用，返回真实数据
- [x] 无令牌 / 错令牌 / **未配置令牌** → 全拒（与 issue 02 同一套证据，含那条最容易漏的「未配置 → 全拒」）
- [x] **用户隔离**：以买家 A 的身份不能加进 B 的购物车、不能取消 B 的订单、不能给 B 的订单申请退款、`GET refunds/` 只看到自己的
- [x] **写后立刻可读**：加购后马上打 `GET cart/` 能读到新条目（证明没走缓存）
- [x] `slug` 指向不存在 / 下架商品 → 明确的错误码，不是 500
- [x] 下单：不传 `address_id` → 用默认地址；没有默认地址 → 明确的错误码
- [x] 业务规则错误逐条有码：余额不足 / 状态不允许取消 / 重复申请退款 / 金额越界
- [x] 缺 `X-User-Id`、非整数 → 与 issue 02 同样拒绝（400 / 401）
- [x] 取消一个退款中的订单 → 明确拒绝（`refunding` 不在白名单）
- [x] 商城测试全绿

## 备注

- **本片只加端点，不加业务逻辑。** 购物车的规则在 09、退款的规则在 10。若实现时发现某个端点需要新业务逻辑，说明 09 / 10 漏了 —— **回那边补，不要在这里就地写**（就地写会让「一份逻辑」变成两份，正是 09 抽 service 要避免的）。
- **端点粒度是「业务动作」而不是「资源 CRUD」**（PRD §4.3 的原话）。加购是 `POST cart/items/`，不是「先查商品、再建 cart_item」两步 —— 模型要的是「一次调用完成一个动作」。
- **`refunds/` 的 GET 不分页**：退款单天然少，故事 18 是「问进度」，列全即可。这是本片唯一一处偏离「列表都分页」的地方，理由写进 docstring。
- **不碰的**：既有的 9 个只读端点（一行不改）、`_paginate` 的白名单与回退逻辑、缓存与 signal、买家面的 `views_buyer.py`（09 只改它调 service，不改行为）。
- 本片**不做**护栏与预算 —— 那是 12 的事。**端点本身不做限流**：拦截点在框架层（PRD §4.6 的取舍：拦截挂在统一的一点，而不是散落在 17 个工具或 8 个端点里各写一遍）。

---

## 实际开发情况 2026-09-21

### 一、拍板的开放项（ticket 没定、实现时定下来的）

| 项 | 拍板 | 为什么 |
|----|------|--------|
| 成功的状态码 | **一律 200**（不用 201 / 204） | 工具读的是 body 不是状态码；8 个端点里混用 200 / 201 / 204 只会让 12 那边多几个分支 |
| 错误体 | `{"error": {"code", "message"}}` + 400 / 404 / 409 | 与 CharAgent 的 error 帧同一形状（`views_bff.py` 已经在按 code 分派），不沿用买家面的 `{"detail": str(e)}` |
| 状态码怎么分 | 404 = 你指的东西不存在 / 不属于你；409 = 状态或库存不让做；400 = 调用方填错 | 让工具层能一眼分开「重试有用 / 换个说法 / 别重试」 |
| `message` 的语言 | 纯中文一句，**不拼异常原文** | service 里那批订单异常的消息是英文，拼进去就是中英混排，而这句话会被一路念给买家；细节（还剩几件）让工具层按码再查一次 |
| 端点的请求体校验 | 只校验形状（字段在不在、类型对不对） | 业务规则一律留给 `services.py` 的锁内判据；端点这道永远不是唯一那道 |
| 购物车写操作回什么 | 动作**之后的整车**（`build_cart_payload`） | 「一次调用完成一个动作」：模型不用再补一次 GET 就能念出「车里现在有两件，一共 30 元」 |
| 下单下多少 | **整车** | 买家面要 `cart_item_ids`，模型得先查车再挑出买哪几件 —— 那正是 PRD §4.3 说的「凑几次调用才拼齐」 |
| 默认地址谁判 | **端点**（不是在 service 里） | 买家面那条路必传 `address_id`，没有「默认」这个概念；这是助手独有的入口规则 |
| 订单定位 | `_own_order`：不存在与不是你的回**同一个** 404 | 与只读详情同一条防枚举规矩 |
| 路由名 | 一个路径一个 name（`cart_item` 同时管 PATCH 与 DELETE） | 同一路径注册两个 name 会留一个永远命不中的死 pattern（Django 按路径先匹配） |
| 余额不足 / 金额越界两个码 | **立码不造入口** | 8 个端点触发不到（支付归 L3 的挂起；金额由管理员批准时定），但码是给 12 的工具写文案用的，缺了就没法区分。同样没有入口的还有 `payment_failed`（密码错误那条，与余额不足同属支付路径） |

### 二、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `app/minimall/services.py` | **只动异常类型**：拆出 `EmptyCartError` / `InvalidAddressError` / `InsufficientBalanceError` 三个子类（替换 4 处 raise + `create_order` 的 Raises），行为零变化 —— 买家侧 catch 的是基类 |
| `app/minimall/serializers_agent.py` | 新增写侧一节：4 个请求体 + 2 个返回体（`AgentRefundSerializer` / `AgentOrderCancelSerializer`） |
| `app/minimall/views_agent.py` | 错误码表 + 兜底码 + `AgentRefusalError` + 基类的 `handle_exception` / `_payload` + 8 个写端点 + 5 个解析助手 |
| `app/minimall/urls_agent.py` | 5 条新路由（8 个端点） |
| `app/minimall/tests/test_agent_api.py` | 认证表从 9 条扩到 **17 条**（带方法），三种失败方式对写端点同样各打一遍 |
| `app/minimall/tests/test_agent_write_api.py` | **新增**（45 条）：购物车 17 / 订单与取消 14 / 退款 10 / 错误码表 4 |
| `app/minimall/tests/test_services.py` | +1：`pay_order` 余额不足抛的是新子类（这条路径此前没有用例） |

### 三、验收逐条

| 验收 | 证据 |
|------|------|
| 8 个端点全部可用 | `test_agent_write_api` 全绿；每个端点都有断言真实数据的用例（车里的数量、订单号、退款单状态） |
| 无令牌 / 错令牌 / 未配置令牌 → 全拒 | `test_agent_api.AgentAuthTest` 三条 × 17 个端点（含 POST / PATCH / DELETE） |
| 用户隔离 | 加购只进自己的车 / 改别人的条目 404 / 取消别人的订单 404 / 给别人的订单申请退款 404 / `GET refunds/` 只看到自己的 |
| 写后立刻可读 | 加购 → `GET cart/` 立刻读得到；下单 → 列表与详情立刻读得到；申请退款 → 列表立刻读得到 |
| `slug` 指向不存在 / 下架 | 404 + `product_unavailable`（不是 500） |
| 下单地址 | 不传 → 默认地址；没有默认地址 → 400 `no_default_address`；传别人的地址 → 404 `invalid_address` |
| 业务规则逐条有码 | 见下面的清单。**余额不足与金额越界这两个码成立、但没有入口**：支付归 L3、金额由管理员批准时定，8 个端点触发不到（`test_unreachable_but_required_codes_exist` 明写了这件事） |
| 缺 / 非法的 `X-User-Id` | 400，与 issue 02 同一套（`ValidationError` 没动过） |
| 取消退款中的订单 | 409 + `invalid_order_status`（`refunding` 不在取消的白名单） |
| 商城测试全绿 | `manage.py test app.minimall` = **206 passed**（本片前 160，+46）；`ruff check .` / `ruff format --check .` 干净 |

**错误码清单**（18 个，全部在 `views_agent.ERROR_CODES`）：

`invalid_request` · `product_unavailable` · `out_of_stock` · `insufficient_stock` · `cart_item_not_found` · `cart_empty` · `order_not_found` · `invalid_address` · `no_default_address` · `invalid_order_status` · `order_no_conflict` · `payment_failed` · `insufficient_balance` · `refund_not_allowed` · `refund_already_in_progress` · `invalid_refund_status` · `invalid_refund_amount` · `order_rejected`（兜底）

**用例的成色**：拆掉 `_own_order` 的 `user=buyer` 归属过滤 → 两条隔离用例当场红（实测），说明隔离那几条有区分度；拆掉 `_cart_item_id` 的 `cart__user=buyer` → 仍然绿，因为 service 的 `_locked_cart_item` 在锁内再查一次归属（**防御是双层的**，端点这层不是唯一那道 —— 如实记下，没有假装它测到了端点那层）。

### 四、本片明确不动的东西

| 东西 | 为什么不动 |
|------|-----------|
| 既有 9 个只读端点 | ticket 要求「一行不改」。**唯一交涉**：`orders/` 的 POST 只能挂在既有的 `AgentOrderListView` 上（同路径注册两个 view 只有第一个会命中），那个类多了一个 `post` 方法，`get` 一行没动 |
| 买家面 `views_buyer.py` / `views_html.py` | 端点只加不减，买家那条路不经过这里 |
| 缓存与 signal | 写端点全走 service，本来就绕开缓存；`_paginate` 的白名单与回退逻辑一行没碰 |
| 业务规则 | **一条没写**（ticket 的硬要求）。唯一动 `services.py` 的是异常类型，不是规则 —— 09 §五 自己写着「11 若要区分得先在 09 拆出子类」 |
| 限流 / 护栏 / 预算 | 12 的事；拦截点挂在框架层，不散落在 8 个端点里各写一遍 |
| 端点里的锁 | 不加。锁与事务全在 service（09 / 10 的契约），端点自己加锁只会变成第二套并发规则 |

### 五、留给下一片（issue 12）的接口约定

- **工具 ↔ 端点**（`CharApp/minimall/client.py` 的写方法就照这张表）：

  | 工具 | 方法 + 路径 | body | 成功回什么 |
  |------|------------|------|-----------|
  | `add_to_cart` | POST `cart/items/` | `slug`, `quantity` | 整车 |
  | `update_cart_item` | PATCH `cart/items/<slug>/` | `quantity`（0 = 移除） | 整车 |
  | `remove_cart_item` | DELETE `cart/items/<slug>/` | — | 整车 |
  | `clear_cart` | DELETE `cart/clear/` | — | 空车 |
  | `place_order` | POST `orders/` | `address_id?` | 订单详情（`order_no` + `total_amount` + 明细 + 时间线） |
  | `cancel_my_order` | POST `orders/<order_no>/cancel/` | — | 订单详情 + `balance_returned` + `restocked_count` |
  | `request_refund` | POST `refunds/` | `order_no` | 退款单（`amount` 是 null） |
  | `list_my_refunds` | GET `refunds/` | — | **裸数组**（不分页） |

- **失败一律** `{"error": {"code", "message"}}`（400 / 404 / 409），`message` 已经是中文，工具可以直接回填模型；要写自己的文案就 switch `code`。
- **购物车写操作回整车** —— 工具的返回体不用再加工，把 `items` / `total_count` / `total_amount` 念出来就行。
- **`slug` 从哪来**：购物车返回体里这个字段叫 `product_slug`（值就是写端点要的 `slug`）—— 12 的工具说明里点一句「标识取自购物车返回体的 `product_slug`」，模型就不用猜。
- **`GET refunds/` 是裸数组**（`[ {...} ]`，没有 `count` / `results` 外壳），12 那边别按分页形状解析。
- **身份仍然不进参数表**：8 个工具和只读那 9 个一样，`user_id` 走 `build_tools` 的闭包。
- **端点的 404 有两种含义**：「没有这笔订单」（`order_not_found`，带码）与 issue 02 那套「买家身份无效」（`{"detail"}`）。写工具只需要认前者的码。

### 六、代码审查改了什么（两轴各起了一个 sub-agent）

| 发现 | 轴 | 处理 |
|------|----|------|
| 新加注释里用了中文顿号 `、`（CLAUDE.md §4.9 要求标点一律英文） | Standards **硬违规** | **已改** 3 处（`serializers_agent.py:303-304`、`views_agent.py:503`）。同目录其它文件的同类残留是历史漂移，没顺手清 —— 本片只动自己写的行 |
| `_payload`（校验请求体）与 `_cart_payload`（购物车响应体）同名不同义；`_cart_item_id` 与 `_cart_item_ids` 只差一个字符 | Standards 判断题 | **已改**：`_validated_data` / `_cart_payload` / `_cart_item_id_by_slug` / `_all_cart_item_ids` |
| 缓存清理写在用例末尾 —— 断言一失败就跳过，脏缓存留给后面的用例 | Standards 判断题 | **已改**：搬进 `addCleanup` |
| `AgentOrderCancelSerializer` 重算了 `cancel_order` 做过的事，判据还不同（`paid_at` vs service 的 `status == PAID`；回滚件数没扣掉 service 跳过的那些） | Standards 判断题 | **未改**：改法一是让 `cancel_order` 返回三元组 —— 会牵动买家面 `views_buyer.py` 与 09 定的签名，本片明说不动那条路；二是把 service 的跳过分支抄进端点，更糟。`paid_at` 是付款那一刻写下的记录、`cancel_order` 只改 `status` / `cancelled_at`，两个判据等价；`OrderItem.product` 是 `PROTECT`，商品不可能缺失，跳过分支现在走不到。docstring 里补了这条等价关系的出处 |
| `_address_id` 把「没有默认地址 → 拒单」这条规则留在端点层 | Standards + Spec 判断题 | **未改**：买家面那条路必传 `address_id`，service 里没有「默认地址」的落点，验收第 6 条要的正是这个行为。这是本片唯一一处视图层规则，docstring 写明了理由 |
| 请求体键叫 `slug`，购物车返回体键叫 `product_slug`，模型照抄键名会吃 `invalid_request` | Spec 小疵 | **未改**：值是同一个 —— 模型从工具 schema 取参数名、从返回体取参数值，没有「照抄键名」这条路径；而 `slug` 本来就已经暴露在购物车返回体里（`product_slug`），所以没按 ticket 那句「没暴露就补上」再加一个字段。已写进 §五 提醒 12 的说明里 |
| §二 的用例分项数写错了（写 16/13/9/5，实际 17/14/10/4） | Spec (a) | **已改**（用 `ast` 数过） |
| §一 漏了同样没有入口的 `payment_failed` | Spec (a) | **已改** |

**收尾数字**：`manage.py test app.minimall` = **206 passed**（本片前 160，+46）；`ruff check .` / `ruff format --check .` 干净；`CharApp` 侧 `pytest` = 109 passed（那边没动，跑的是一次对照）。
