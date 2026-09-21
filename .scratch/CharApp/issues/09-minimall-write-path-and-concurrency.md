# 09 · 商城侧：写路径收口与三处并发修复

**Status:** done

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

- [x] 购物车 4 个写操作**外部行为与从前一致**（既有测试全绿，未改任何断言）+ 新增「数量上限」用例
- [x] 并发用例：同一买家并发加购同一商品 → 数量不丢更新（用 `TransactionTestCase` + 线程**真并发**）
- [x] `pay_order`：连续两次调用（第二次状态已变）→ 第二次失败且余额**只扣一次**；并发双击同理
- [x] `create_order`：并发改价时下单按**加锁后**的价格快照（用例要能证明「取的是锁内的值」）
- [x] 订单号冲突 → 自动重试成功，不外泄 `IntegrityError`
- [x] `cancel_order`：事务外传入一个过期实例 → 不误判（状态判据取锁内值）
- [x] **退款中的订单自动不可取消**（`refunding` 不在 `cancel_order` 的白名单里）—— 本片钉一条，等 10 加上枚举后仍然成立
- [x] 商城现有测试（97）全绿
- [x] `ruff check` 与 `ruff format --check` 干净

## 备注

- **本片是 PRD §4.9 那句「顺带修**一处**商城支付缺事务的问题」的扩大版**。盘出来同类缺陷有 **5 处**，本片修 **3 处**（`pay_order` / `create_order` / `cancel_order`）。扩大而不是照原话只修一处的理由：三处在**同一个文件、同一类缺陷、同一种修法**（`atomic` + `select_for_update` + 锁内 re-fetch），一次收口的成本最低；而它们恰好是只读时期碰不到、写操作一进来就真会踩的三条。

- **两处已知的同类缺陷，本片明确不动 —— 写在这里，免得被当成漏了：**
  1. **Admin 发货 action**（`admin.py:194`）无事务无锁。它是**管理员路径**，agent 不经过。同一片区域里 `ship_order`（`services.py:172`）是**死代码**（生产 0 调用点，只有测试调它）—— 两者要么一起接上、要么按死代码处理，都不在本片顺手做。
  2. **购物车 4 个写视图之外**没有别的用户面写路径（`views_html.py` 全是 `TemplateView`，只 GET），所以第 1 条列完就没有了。

- **既有死代码，按「精准修改」不主动删，但别误用**：`serializers.py:225` 的 `CartSerializer` 无任何引用；`permissions.py` 的 `IsOwnerOrAdmin` / `IsAdminOrReadOnly` **全仓未被导入**，且 `IsOwnerOrAdmin` **只实现了 `has_object_permission`、没有 `has_permission`**（真用上会直接放行）。

- **并发用例的做法**：用 `TransactionTestCase` + 线程真并发，**不要用顺序调用伪装成并发** —— 顺序调用测不出锁，而本片修的正是锁。若某一条实在无法稳定复现，**宁可把它标出来也不要假装测过**。

- **不改的既有约定**：`_paginate` 的白名单与回退逻辑（`views_agent.py:37-58`）不动；缓存与 signal 不动。

---

## 实际开发情况 2026-09-21

**一句话**：写路径收口了 —— 购物车 4 个写操作抽进 `services.py`（事务 + 行锁 + 数量上限），
`pay_order` / `create_order` / `cancel_order` 三处补事务与锁（判据一律取锁内值），订单号撞车改为换号重试。
新增 **23 条用例**（商城 112 → **135**，零回归，既有断言一行未改），`ruff check` / `ruff format --check` 干净。

### 一、实现时拍板的开放项

| 开放项 | 结论 | 理由 |
|---|---|---|
| 数量上限取多少、放哪 | `MAX_CART_ITEM_QUANTITY = 99`，常量放 `services.py` | 库存决定「还能买多少」，上限决定「一次让不让买这么多」；99 是电商惯例值，真业务该由运营配置（常量集中一处便于以后挪） |
| 到上限之后 | **截断**（`min(请求量, 库存, 99)`）而不是报错 | 验收第一条要求「外部行为与从前一致」：从前页面对超库存就是静默截断（要 10 件库存 5 件 → 拿到 5 件），到上限的语义与它同类 |
| 上限的判据用哪份库存 | 加锁的 `product.stock`，不是 `item.product.stock` | 与 `create_order` 同一条规矩：事务里的判据不许来自事务外的实例（`item.product` 是懒加载读，等于绕开锁） |
| 订单号重试挂在哪 | 挂在**插入处**（`_create_order_with_unique_no`），`utils.generate_order_no` 保持不查库 | 撞不撞车只有插进去那一刻才知道（先查再插仍有竞态，唯一索引才是判官）；而且 `utils.py` 一直不依赖任何模型，查库会破坏这条边界 |
| 撞车重试的回滚范围 | 每次尝试套一层 `atomic()`（savepoint） | 撞车抛的 `IntegrityError` 只回滚这一次插入 —— 没有这层，一次撞车会把已经扣掉的库存和购物车行一起回滚 |
| 行锁顺序 | 固定 `Order → Product → CartItem → Profile`，写进模块 docstring | 两笔写操作互相等待时不会绕成环。代价是 `cancel_order` 先锁 Order 再锁 Product（状态不对也先锁一下，马上抛）—— 换来的是「顺序只有一条」 |
| 「判据取锁内值」之后，传进来的实例怎么办 | `pay_order` / `cancel_order` 返回锁内那份实例，视图改用返回值渲染（各一行） | 就地改传入实例是「服务悄悄改调用方的东西」且改不干净（传进来的可能是陈旧实例）；返回值把契约摆在 docstring 上。**这是既有测试之外唯一的行为改动面**：不这么改，支付成功后响应体里会显示 pending |
| `update_cart_item` 的 `quantity == 0` | 仍由 service 处理（返回 `None` = 已移除），不还给视图 | 「0 表示移除」是业务规则，助手入口（11）也要遵守；视图只负责把 `None` 翻成 204 |
| **没做的一处**：`Cart.objects.get_or_create` 的建车竞态 | 知道且不动，见 §四 | 与「丢更新」不是同一个机制（那是唯一索引冲突，不是丢写），修法也不同；ticket 的任务清单没有它 |

### 二、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `app/minimall/services.py` | 模块 docstring 改写成「写路径规矩」（两条：判据取锁内值 + 行锁顺序）；新增 `MAX_CART_ITEM_QUANTITY` / `ORDER_NO_MAX_ATTEMPTS` / `_cap()`；4 个新异常（`ProductUnavailableError` / `OutOfStockError` / `CartItemNotFoundError` / `OrderNumberConflictError`）；购物车 4 个函数（`add_to_cart` / `update_cart_item` / `remove_cart_item` / `clear_cart`）；`create_order` 把数量与价格的读取挪进锁内、抽出 `_create_order_with_unique_no`；`pay_order` / `cancel_order` 重写为「锁内 re-fetch + 同一事务内改状态与钱」 |
| `app/minimall/views_buyer.py` | 购物车 4 个写视图改成调 service（视图里 `CartItem` 的 import 随之删掉）；`OrderPayView` / `OrderCancelView` 改用 service 的返回值渲染 |
| `app/minimall/tests/test_services.py` | 新增 6 条订单用例（陈旧实例 ×2 · `refunding` 钉一条 · 锁内价格 · 撞车重试 ×2）+ 新的 `CartServiceTest` 11 条 |
| `app/minimall/tests/test_concurrency.py` | **新文件**：`TransactionTestCase` + 线程 + `Barrier` 的 3 条真并发的用例 |
| `app/minimall/tests/test_api.py` | 新增 3 条接口层用例（数量上限 · `PATCH quantity=0` → 204 · 清空购物车）——后两条原本**一条覆盖都没有** |

### 三、验收逐条

| 验收 | 结果 | 证据 |
|------|------|------|
| 购物车外部行为与从前一致（既有测试全绿，未改断言） | ✅ | `manage.py test app.minimall` = **135 passed**（基线 112）；既有断言行**一行未改**（新增的 3 条是追加的方法）。**一处已知差异**（代码审查指出，见 §六）：`PATCH /cart/items/{id}/` 现在先校验请求体、再找条目 —— 「非法 body + 别人的条目」这一组合从 404 变成 400，无用例覆盖；新顺序更对（400 说的是「请求本身不对」，不该先去看库），故保留 |
| 新增「数量上限」用例 | ✅ | service 层 `test_add_caps_at_max_quantity` / `..._when_accumulating`（分两次加也不许绕过）/ `test_update_caps_at_max_quantity`；接口层 `test_add_capped_at_max_quantity` |
| 并发加购不丢更新 | ✅ | `test_concurrent_add_does_not_lose_updates`（4 线程各加 1 → 车里正好 4 件）+ `test_concurrent_add_cannot_exceed_stock`（库存 2、4 线程 → 正好 2 件）。**真并发有效性已验证**：把 `add_to_cart` 的两处 `select_for_update()` 临时改成普通读 → 两条立刻红（`IntegrityError 1062` 撞 `unique_together`），说明屏障确实让 4 个请求撞在了一起 |
| `pay_order` 第二次失败且只扣一次；并发双击同理 | ✅ | 单线程：`test_pay_twice_with_stale_instance_deducts_once`（两个「待付款」实例各付一次 → 第二次 `InvalidOrderStatusError`，余额停在 9990.00）；真并发：`test_concurrent_pay_deducts_once`（2 线程同付一单 → `paid` / `rejected` 各一，余额只扣一次） |
| `create_order` 按加锁后的价格快照 | ✅ | `test_create_order_snapshot_uses_locked_price` —— 把改价卡在「事务外那次读之后、进锁之前」这个窗口里（patch 住同一窗口内的地址查询），断言订单快照取的是 20.00 而不是 10.00。**旧实现下这条是红的**（实测：`Decimal('10.00') != Decimal('20.00')`），所以它证明的正是「取的是锁内的值」 |
| 订单号冲突 → 重试成功，不外泄 `IntegrityError` | ✅ | `test_create_order_retries_when_order_no_collides`（前两次撞、第三次拿到新号）；`test_create_order_raises_when_order_no_keeps_colliding`（一直撞 → `OrderNumberConflictError`，且**整单回滚**：库存 20 原样、购物车行还在）。旧实现下前者直接抛 `IntegrityError`（实测 red） |
| `cancel_order` 过期实例不误判 | ✅ | `test_cancel_with_stale_instance_rejects_shipped`：拿一个「还停在 paid」的实例去取消已发货订单 → 拒绝，且**钱没退、库存没回滚**。旧实现下这条是红的（实测：`InvalidOrderStatusError not raised`） |
| `refunding` 订单不可取消（钉一条） | ✅ | `test_cancel_rejected_for_refunding_order`，用字面量 `"refunding"`（10 加上枚举后仍成立）。旧实现下就是绿的 —— 它是**钉子**，不是修复 |
| 商城现有测试全绿 | ✅ | 135 passed（ticket 写的 97 是旧数：06 之后还有别的片加过用例，实际基线是 112） |
| `ruff check` / `ruff format --check` 干净 | ✅ | 见 §五的收尾数字 |

### 四、本片明确不动的同类缺陷（免得被当成漏了）

| 缺陷 | 为什么不动 |
|------|-----------|
| Admin 发货 action 无事务无锁（`admin.py:194`） | 管理员路径，agent 不经过 —— 与 ticket 备注同一条 |
| `ship_order` 是死代码（生产 0 调用点，已复核：只有测试调它） | 「要么一起接上、要么按死代码处理」，两件都不属于本片的修法 |
| `add_to_cart` 里 `Cart.objects.get_or_create` 的**建车竞态** | 同一买家**首次**加购**两件不同商品**（或页面与助手同时动手）时，两个请求可能同时建车 → 唯一的 `Cart.user` 索引抛 `IntegrityError`（500）。**这是本片新发现的一处**，但：机制不同（唯一索引冲突 ≠ 丢写）、修法不同（`get_or_create` 外面套 savepoint + 重查）、ticket 的任务清单也没有它。修法在 §六 有过一次修正：商品行的锁已经把「同一商品被双击」串起来了，所以这条要的是**不同商品**才成立（初稿写成「首次加购被双击」是不准的，代码审查指出） |
| `update_cart_item` 在 `stock == 0` 时把数量写成 0 | 商品卖光后 PATCH 一件车里的商品，会留下一条 `quantity = 0` 的条目 —— 而 0 在本片又是「移除」的哨兵值，语义撞车；顺着走到下单还会生成数量 0 的 `OrderItem`（`0 > 0` 为假，库存校验放行）。**老实现一模一样**（也是 `min(quantity, stock=0)`），所以不是本片引入的回归；改它又要动「外部行为与从前一致」这条验收，留作独立小片 |
| 其余只读路径（`views_agent.py` / `views_html.py`） | 本片不碰 agent 面，`views_html.py` 全是只读 `TemplateView` |

### 五、留给下一片（issue 11）的接口约定

- **service 签名**：`add_to_cart(user, *, product_id, quantity) -> (CartItem, created)` · `update_cart_item(user, *, cart_item_id, quantity) -> CartItem | None` · `remove_cart_item(user, *, cart_item_id)` · `clear_cart(user)`。**统一用 id 定位**：助手端点的自然键是 `slug`，按 ticket 的要求由端点自己解析成 id（`Product` / `CartItem` 各查一次），归属与库存**仍由 service 在锁内重新校验** —— 端点那次查询只用来把 slug 翻成 id，不做任何放行判断。
- **`pay_order` / `cancel_order` 必须用返回值**：它们判的是**锁内**那份实例，视图已经改成 `order = pay_order(order, ...)`。11 的端点若照抄 `pay_order(order, pwd)` 然后拿传进去的 `order` 序列化，响应里会显示 `pending`（而且没有测试会拦下这个错）。另外三个（`ship_order` / `receive_order` / `complete_order`）仍就地改传入实例、不返回 —— 边界写在 `services.py` 的模块 docstring 第 1 条里。
- **异常 → 错误码**（11 要把它们讲成不同的中文）：`ProductUnavailableError`（商品不存在/下架）· `OutOfStockError`（缺货）· `CartItemNotFoundError`（车里没这件）· `InsufficientStockError`（下单时库存不够）· `InvalidOrderStatusError`（状态不允许）· `OrderNumberConflictError`（订单号撞车，可重试）· `OrderServiceError("Cart is empty")` / `("Invalid shipping address")`（这两条还是裸的 `OrderServiceError`，11 若要区分得先在 09 拆出子类 —— 本片没拆，因为没有第二个消费方）。
- **收尾数字**：`manage.py test app.minimall` = **135 passed**；`ruff check .` = All checks passed；`ruff format --check .` = 全绿。

### 六、代码审查改了什么（两轴各起了一个 sub-agent）

Spec 轴那一个**不只是读**：它在临时 worktree 里把本片的新用例逐个跑在 `HEAD` 的旧代码上，
四条「旧实现下是红的」声明的确为真（价格快照 `10.00 != 20.00`、陈旧支付 / 陈旧取消 `InvalidOrderStatusError not raised`、订单号直接漏 `IntegrityError`），
并复核了「既有断言一行未改」与 135 这个数。

| 审查发现 | 处理 |
|---|---|
| **[Standards·硬]** `CartItemNotFoundError` 的抛出与「锁内取条目」那段在 3 处重复（DRY：≥3 次抽取） | 抽成 `_locked_cart_item(user, cart_item_id)`（归属过滤 + 加锁 + 抛异常一处写死），`update` / `remove` 改调它 |
| **[Standards + Spec·硬]** 模块 docstring 把规矩写宽了：「状态类判断**一律**锁内 re-fetch」「**每个函数**都按同一顺序加锁」——而 `ship_order` / `receive_order` / `complete_order` 既不加锁也不 re-fetch | docstring 收窄到**会动到钱与库存的判断**（下单/付款/取消），并点名那三个函数为什么可以松（只推一步状态，重复执行最多把时间戳再写一次）。**没有**顺手给它们加锁：ticket 没要，10 会重访这片 |
| **[Standards·硬]** 新增行里的顿号 `、` 与英文逗号混用（CLAUDE.md §4.9「标点一律英文」） | 新增行里的 `、` 全改成 `, `（14 处）。`「」` 与 `——` 保留 —— 该条列举的是逗号/句号/括号/冒号，且全仓早已这么写 |
| **[Standards·判断]** `update_cart_item` / `remove_cart_item` / `clear_cart` 缺 `Args:`，而兄弟函数 `add_to_cart` 有 | 前两个补上（前者补 `quantity` 的 0 = 移除语义）；`clear_cart` 只有一个参数，不加 |
| **[Standards·判断]** 用例 patch 了自家代码 `app.minimall.services.generate_order_no`（§5「不 Mock 自己的代码」） | 保留，但把理由写进用例 docstring：随机撞车等不出来（要跑上万单），这里验的是「撞了以后怎么办」 |
| **[Spec]** `PATCH /cart/items/{id}/` 的 404 → 400 顺位差异 | 保留新顺序，差异记在 §三（无用例覆盖；新顺序 = 先判请求本身） |
| **[Spec]** §四 里「首次加购被双击 → 建车竞态」写得不准 | 改了：商品行的锁已经把同一商品串起来了，这条要**两件不同商品**并发才成立 |
| **[Spec]** `stock == 0` 时 PATCH 会留下 `quantity = 0` 的条目（与本片的「0 = 移除」哨兵撞语义） | 老实现一样，不是本片引入的回归；记进 §四 的「不动的同类缺陷」 |
| **[Standards·判断]** Feature Envy：service 直接改 `Profile.balance`，金额动作或许该在模型上（`Profile.debit()/credit()`） | 不改：钱的动作必须在 service 的事务与锁里完成，搬到模型只是多一层转手（规则仍在 service 里）。`Profile` 保留它自己的状态（密码、头像） |
| **[Standards·判断]** 返回形状不齐：`add_to_cart` 给 `(条目, 是否新建)`、`update_cart_item` 给 `CartItem \| None`（None = 已移除） | 不改：两者都是「视图要什么给什么」（201 vs 200 / 204 vs 200）；为两个函数发明一个结果类型是过度设计，语义都写在 docstring 与视图的注释里 |
| **[Standards·判断]** 用例拿生产常量 `MAX_CART_ITEM_QUANTITY` 做断言两侧 | 保留：99 是**业务参数**（可以合法地改成 50），要钉的是性质（上限生效且优先于库存）。`test_add_caps_at_stock` + `test_add_caps_at_max_quantity` 两条合起来把 `min()` 的行为钉死了 |
| **[Standards·判断]** `clear_cart` 是一行转发（Middle Man） | 保留：ticket 要的就是「4 个写操作都在 service 里」，一行也是那一层的门，助手入口要走同一份 |
