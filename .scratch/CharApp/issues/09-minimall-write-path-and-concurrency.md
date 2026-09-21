# 09 · 商城侧：写路径收口与三处并发修复

**Status:** ready-for-agent

**Type:** task

**Blocked by:** 无（可与 08 并行）

**上游:** `../PRD.md` §4.3 / §4.9（L2）、`CharApp/docs/PLAN.md` §5

## 做什么

把商城的写路径变成**能被并发调用**的：购物车逻辑从视图里抽到 `services.py`（并补上事务、行锁、数量上限），修 `pay_order` / `create_order` / `cancel_order` 三处缺事务或缺锁的缺陷。

只读时期这些都碰不到；L2 的助手一旦能改数据，它们就从「理论隐患」变成「会踩的坑」。

**本片不碰 agent 面**：内部端点是 11 的活，退款是 10 的活。

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| 购物车 4 个写操作**没有 service 层**，全在视图里直接 ORM | `views_buyer.py:323`（加）/ `:363`（改）/ `:386`（删）/ `:397`（清空） |
| 那 4 个视图**无事务、无锁、无数量上限** —— 唯一的约束是库存（`min(quantity, stock)` 截断）；序列化器只有下界 `min_value=1` / `min_value=0` | 同上；`serializers.py:181`、`:186` |
| `services.py` 里**一个购物车函数都没有**（全文只有 6 个订单函数） | `services.py` |
| `pay_order` **无 `atomic`、无锁**，两次独立 `save`（扣余额 `:131-132` / 改状态 `:133-135`） | `services.py:111-135` |
| `create_order` 事务从 `:51` 起、`select_for_update` **只锁 Product 行**（`:54`）；而 `cart_items` 于 `:40-43` 在事务**外**求值，事务内 `:77` 的价格与 `:100-102` 的 `OrderItem.product_price` 用的是那份**未加锁的缓存实例** | `services.py:26-108` |
| `generate_order_no` **无唯一性重试**（`YYYYMMDDHHMMSS` + 6 位买家 ID + 4 位随机），冲突直接靠 `order_no unique=True` 抛 `IntegrityError` | `utils.py:7-11`、`models.py:321` |
| `cancel_order` 有 `atomic`，`select_for_update` **只锁 Product**；Order / Profile **未锁、未 re-fetch**，`:162` 读的是事务外传入的内存实例状态 | `services.py:138-169` |
| 对照：下单（`:51`）与 `cancel_order`（`:150`）**都**锁了 Product —— 缺的是别的行 | 同上 |
| Admin 的发货 action 自己写 `queryset.filter(status=PAID).update(...)`，**不经过 `ship_order`**；`ship_order` 生产调用点 **0 处** | `admin.py:194-198`、`services.py:172-185` |
| 缓存失效 signal 只覆盖 `Product` / `ProductImage` / `Category`，**写操作不碰缓存** | `signals.py` |

## 具体任务

1. **`services.py` 新增购物车函数**（加购 / 改数量 / 移除 / 清空），既有的 4 个写视图改成调用它。
   - 补 `transaction.atomic` + `select_for_update`（锁 Product 行做库存校验与截断）
   - 补**数量上限**常量（现在唯一的上限是库存）
   - 抽的判据是 issue 05 立过的那条：**换一个入口还要不要这段？** 要 → 共用一份。agent 与页面都要加购，所以要。

2. **修 `pay_order`**：包 `atomic` + `select_for_update` 锁 Profile 与 Order，**状态判据取锁内 re-fetch 的值**（现在两次独立 `save`，中途失败就是「钱扣了单没付」）。

3. **修 `create_order`**：把 `cart_items` 的求值挪进事务；价格与 `OrderItem.product_price` 一律取自**加锁的** `product_map`，不用事务外那份实例。

4. **给 `generate_order_no` 加唯一性重试**：有限次（如 3 次），超出则抛一条明确的业务异常 —— 不要让 `IntegrityError` 冒到用户面前。

5. **修 `cancel_order`**：锁内 re-fetch Order 与 Profile，状态判据取锁内的值。

6. **写测试。**

## 验收

- [ ] 购物车 4 个写操作**外部行为与从前一致**（既有测试全绿，未改任何断言）+ 新增「数量上限」用例
- [ ] 并发用例：同一买家并发加购同一商品 → 数量不丢更新（用 `TransactionTestCase` + 线程**真并发**）
- [ ] `pay_order`：连续两次调用（第二次状态已变）→ 第二次失败且余额**只扣一次**；并发双击同理
- [ ] `create_order`：并发改价时下单按**加锁后**的价格快照（用例要能证明「取的是锁内的值」）
- [ ] 订单号冲突 → 自动重试成功，不外泄 `IntegrityError`
- [ ] `cancel_order`：事务外传入一个过期实例 → 不误判（状态判据取锁内值）
- [ ] **退款中的订单自动不可取消**（`refunding` 不在 `cancel_order` 的白名单里）—— 本片钉一条，等 10 加上枚举后仍然成立
- [ ] 商城现有测试（97）全绿
- [ ] `ruff check` 与 `ruff format --check` 干净

## 备注

- **本片是 PRD §4.9 那句「顺带修**一处**商城支付缺事务的问题」的扩大版**。盘出来同类缺陷有 **5 处**，本片修 **3 处**（`pay_order` / `create_order` / `cancel_order`）。扩大而不是照原话只修一处的理由：三处在**同一个文件、同一类缺陷、同一种修法**（`atomic` + `select_for_update` + 锁内 re-fetch），一次收口的成本最低；而它们恰好是只读时期碰不到、写操作一进来就真会踩的三条。

- **两处已知的同类缺陷，本片明确不动 —— 写在这里，免得被当成漏了：**
  1. **Admin 发货 action**（`admin.py:194`）无事务无锁。它是**管理员路径**，agent 不经过。同一片区域里 `ship_order`（`services.py:172`）是**死代码**（生产 0 调用点，只有测试调它）—— 两者要么一起接上、要么按死代码处理，都不在本片顺手做。
  2. **购物车 4 个写视图之外**没有别的用户面写路径（`views_html.py` 全是 `TemplateView`，只 GET），所以第 1 条列完就没有了。

- **既有死代码，按「精准修改」不主动删，但别误用**：`serializers.py:225` 的 `CartSerializer` 无任何引用；`permissions.py` 的 `IsOwnerOrAdmin` / `IsAdminOrReadOnly` **全仓未被导入**，且 `IsOwnerOrAdmin` **只实现了 `has_object_permission`、没有 `has_permission`**（真用上会直接放行）。

- **并发用例的做法**：用 `TransactionTestCase` + 线程真并发，**不要用顺序调用伪装成并发** —— 顺序调用测不出锁，而本片修的正是锁。若某一条实在无法稳定复现，**宁可把它标出来也不要假装测过**。

- **不改的既有约定**：`_paginate` 的白名单与回退逻辑（`views_agent.py:37-58`）不动；缓存与 signal 不动。
