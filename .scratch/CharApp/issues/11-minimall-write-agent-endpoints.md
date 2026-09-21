# 11 · 商城侧：8 个写操作内部端点

**Status:** ready-for-agent

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

- [ ] 8 个端点全部可用，返回真实数据
- [ ] 无令牌 / 错令牌 / **未配置令牌** → 全拒（与 issue 02 同一套证据，含那条最容易漏的「未配置 → 全拒」）
- [ ] **用户隔离**：以买家 A 的身份不能加进 B 的购物车、不能取消 B 的订单、不能给 B 的订单申请退款、`GET refunds/` 只看到自己的
- [ ] **写后立刻可读**：加购后马上打 `GET cart/` 能读到新条目（证明没走缓存）
- [ ] `slug` 指向不存在 / 下架商品 → 明确的错误码，不是 500
- [ ] 下单：不传 `address_id` → 用默认地址；没有默认地址 → 明确的错误码
- [ ] 业务规则错误逐条有码：余额不足 / 状态不允许取消 / 重复申请退款 / 金额越界
- [ ] 缺 `X-User-Id`、非整数 → 与 issue 02 同样拒绝（400 / 401）
- [ ] 取消一个退款中的订单 → 明确拒绝（`refunding` 不在白名单）
- [ ] 商城测试全绿

## 备注

- **本片只加端点，不加业务逻辑。** 购物车的规则在 09、退款的规则在 10。若实现时发现某个端点需要新业务逻辑，说明 09 / 10 漏了 —— **回那边补，不要在这里就地写**（就地写会让「一份逻辑」变成两份，正是 09 抽 service 要避免的）。
- **端点粒度是「业务动作」而不是「资源 CRUD」**（PRD §4.3 的原话）。加购是 `POST cart/items/`，不是「先查商品、再建 cart_item」两步 —— 模型要的是「一次调用完成一个动作」。
- **`refunds/` 的 GET 不分页**：退款单天然少，故事 18 是「问进度」，列全即可。这是本片唯一一处偏离「列表都分页」的地方，理由写进 docstring。
- **不碰的**：既有的 9 个只读端点（一行不改）、`_paginate` 的白名单与回退逻辑、缓存与 signal、买家面的 `views_buyer.py`（09 只改它调 service，不改行为）。
- 本片**不做**护栏与预算 —— 那是 12 的事。**端点本身不做限流**：拦截点在框架层（PRD §4.6 的取舍：拦截挂在统一的一点，而不是散落在 17 个工具或 8 个端点里各写一遍）。
