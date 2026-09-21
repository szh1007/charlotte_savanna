# 10 · 商城侧：退款域

**Status:** done

**Type:** task

**Blocked by:** 09

**上游:** `../PRD.md` §4.5 / §4.9（L2）、`CharApp/docs/PLAN.md` §5、`CharApp/docs/adr/0004`

## 做什么

在商城里**从零**建起退款域：模型、状态机、四个服务函数、Admin 审批入口。做到「管理员能批准一笔**协商金额**的退款、能打款、能驳回，驳回后订单恢复原状」。

**本片是 L2 最大的一片。** 内容比 PLAN §5 那句「`RefundRequest` **扩建**」暗示的大得多 —— 因为那个模型**根本不存在**（见事实表第 1 条）。PLAN 的措辞是错的，本片是新建。

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| **`RefundRequest` 不存在** —— `models.py` 里没有这个模型，17 个迁移里也没有 | 全仓搜索 |
| `Order.Status.REFUNDED = "refunded", "已退款"` **存在，但从未被任何代码写入过**（只在 models 与 migration 0006/0007/0008 里出现） | `models.py:319` |
| `Order` 的时间戳只有 `paid_at` / `shipped_at` / `received_at` / `cancelled_at` / `created_at` / `updated_at` —— **没有 `refunded_at`** | `models.py:338-343` |
| `Order.total_amount` = `DecimalField(max_digits=10, decimal_places=2)`；`Profile.balance` = `DecimalField(max_digits=12, decimal_places=2)` | `models.py:333`、`:64-69` |
| `Profile.check_payment_password` 走 `check_password`（哈希比对），空值返回 False | `models.py:84-87` |
| `pay_order` 要校验支付密码（`:125`）—— 但**退款不需要**：钱是流出的方向，没有「买家授权扣款」这回事 | `services.py:111-135` |
| `admin.py` 里**零退款相关的东西**；`OrderAdmin.actions` 只有 `action_ship_orders` + `batch_delete` | `admin.py:169-198`、`:18-24` |
| `cancel_order` 是退款的**对照物**：买家单方、无需审批、即刻退余额 + **回滚库存** | `services.py:138-169` |
| 商城的金额一律 `Decimal`，全程不碰浮点 | 全仓 |
| `Order.user` 是 FK 且 `on_delete=PROTECT`，`related_name="orders"` | `models.py:322-328` |

## 具体任务

### 1. 模型 `RefundRequest`

| 字段 | 形状 | 说明 |
|------|------|------|
| `order` | FK → `Order`，`on_delete=PROTECT`，`related_name="refunds"` | |
| `status` | `TextChoices`：`requested` / `approved` / `rejected` / `refunded` | 与 `Order.status` 是两个状态机，四个转换点必须同步（ADR-0004） |
| `amount` | `DecimalField(10,2)`，**可空** | 买家申请时不带金额；**管理员批准时必填**，约束 `0 < amount <= order.total_amount` |
| `order_status_before` | 与 `Order.status` 同型的 `CharField` | 申请那一刻的快照，驳回时用它恢复 —— **不靠时间戳推断** |
| `admin_note` | `TextField(blank=True)` | 批准与驳回**都可填**：驳回时是「为什么驳回」，批准时写明协商结果（如「协商一致退 70 元」） |
| `created_at` / `approved_at` / `refunded_at` / `rejected_at` | `DateTimeField` | |

- **不加 `user` FK**：买家经 `order.user` 取，与 `Order` 不冗余 user 的既有做法一致。
- **不能用 `unique_together(order)` 表达「同一时刻只有一个进行中的申请」**，因为驳回后允许再提（PRD §4.5）—— 它是**应用层校验**，配 `select_for_update` 防并发双提。

### 2. `Order` 侧改动

- `Status` 新增 `REFUNDING = "refunding", "退款中"`
- 新增 `refunded_at`（由**打款**那步写，批准那步不写）
- 一次迁移

### 3. 四个服务函数（放 `services.py`，与既有 6 个订单函数同族）

| 函数 | 做什么 | 关键约束 |
|------|--------|---------|
| `request_refund(order)` | 建申请 + 快照 `order_status_before` + 订单置 `refunding` | 起始状态必须是 `paid` / `shipped` / `received` / `completed`；**无进行中申请**。全程 `atomic` + `select_for_update` |
| `approve_refund(refund, amount, note)` | 置 `approved` + 记 `amount` / `approved_at` / `admin_note` | 必须处于 `requested`。**订单状态不变**（仍是 `refunding`） |
| `settle_refund(refund)` | `Profile.balance += refund.amount` + 订单置 `refunded` + 写 `Order.refunded_at` + 退款单置 `refunded` | 必须处于 `approved`。`atomic` + 锁 Profile 与 Order |
| `reject_refund(refund, note)` | 置 `rejected` + 记 `admin_note` + **订单恢复 `order_status_before`** | 必须处于 `requested` |

**退款一律不动库存。** `cancel_order` 回滚库存是因为货还没出去（只有 `pending` / `paid` 可取消）；退款发生在货已经出去之后。后果 —— **同一张已付款订单，买家「自助取消」与「申请退款」会得到不同的库存结果** —— 这条不对称必须写进 `request_refund` 的 docstring，不能只留在 ticket 里。

### 4. `RefundRequestAdmin`

- `list_display`（订单号 / 买家 / 金额 / 状态 / 时间）+ `list_filter`（`status`）
- 三个 action：**批准** / **打款** / **驳回**
- **「批准」必须走中间页表单**（Django admin action 返回 `TemplateResponse` 的写法）：预填 `order.total_amount`，管理员可改成协商金额，**必填**；`0 < amount <= total_amount` 的校验在**表单里**做，不只在服务函数里 —— 否则管理员要提交两次才知道填错了

### 5. `OrderAdmin` 相应调整

`list_filter` 自动带上新状态；`readonly_fields` 补 `refunded_at`；要不要在订单详情内联显示退款单，实现时定。

### 6. 写测试

## 验收

- [x] 四个状态转换逐一可验：申请 / 批准（含金额）/ 打款 / 驳回
- [x] **驳回后订单恢复到申请前的状态** —— 四个起始状态**各测一次**（`paid` / `shipped` / `received` / `completed`）
- [x] **部分退款**：申请全退 100 → 批准 70 → 打款 → 余额只加 70，订单 `refunded`
- [x] 金额边界：`0` 拒绝 · `> total_amount` 拒绝 · `= total_amount` 通过
- [x] 非法转换被拒：`requested` 不能直接打款 · 已打款不能重打 · 已驳回不能批准
- [x] **并发双提**：同一订单两个请求同时申请，只有一个成功（`select_for_update` 生效）
- [x] **驳回后可再提**：新的一条申请能建起来
- [x] **退款不动库存**：整条流程前后 `product.stock` 一字不变
- [x] `Order.refunded_at` 由**打款**那步写，批准那步**不写**（断言为 `None`）
- [x] 全程 `Decimal`（用例断言类型，别让 float 混进来）
- [x] 商城测试全绿

## 备注

- **本片的核心风险是「两个状态机耦合」**（ADR-0004）：`RefundRequest.status` 与 `Order.status` 必须在**申请 / 批准 / 打款 / 驳回**四个转换点上同步。验收里那四条转换测试 + 四条恢复测试**就是这条风险的兜底** —— 不要只测成功路径。
- **本片不建 HTTP 端点**（内部端点是 11 的活），也不做 agent 工具（12 的活）。本片交付的是能被 Admin 与后续端点调用的**业务能力**。
- **PRD §4.5 里「后台入口已经存在，不用新做界面」是错的**，本片要新建 —— 该处已在 2026-09-21 更正。
- **明确不做**（连同理由）：
  - **按商品行分摊的部分退款** —— 要子表、按行算钱、处理「退一件留一件」（PRD §6）。协商金额不是同一件事，做。
  - **自动审批** —— PRD §4.5 明写它是 L3 人工确认机制的验证场，故意保留。
  - **状态历史表** —— `RefundRequest` 本身就是那条流水（谁申请、何时批、何时打款、何时驳），再加一张表是把同一件事记两遍（ADR-0004）。
  - **退款时限**（如"收货后 7 天"）—— `completed` 在商城里没有闭环语义，凭空加限期是发明不存在的规则（PRD §4.5）。
- 与 `cancel_order` 的关系：退款中的订单**自动**不可取消（`refunding` 不在 `cancel_order` 的白名单里），这条免费成立，但要在测试里钉一条（09 已经钉了，本片加上枚举后要复验）。

---

## 实际开发情况 2026-09-21

### 一、实现时拍板的开放项

| 开放项 | 拍板 | 理由 |
|--------|------|------|
| 锁的对象与顺序 | 模块 docstring 的锁序扩成 `Order → RefundRequest → Product → CartItem → Profile`；**四个函数一律先锁 Order 行**, 连「批准」这种不改订单状态的也不例外 | 顺序统一才不会绕成环. 批准锁 Order 不亏: 同一订单上的两笔退款操作本来也该串行 |
| 怎么防并发双提 | 锁 **Order 行** + 锁内查 `RefundRequest.ACTIVE_STATUSES` | 「同一时刻一条」做不成唯一约束（驳回后允许再提）, 那就得有一把锁; 订单行是天然的那把 |
| 检查顺序 | 先查「有没有进行中的申请」, 再查订单状态白名单 | 对一张 `refunding` 的订单, 真话是「已有一笔进行中的退款」, 不是「状态不允许」. 前者信息量大 |
| 异常粒度 | 4 个: `RefundNotAllowedError` / `RefundAlreadyInProgressError` / `InvalidRefundStatusError` / `InvalidRefundAmountError` | issue 11 要求「重复申请退款」有自己的错误码, 才能对买家讲成不同的话; 那就顺手把四类分开 |
| `approve_refund` 的签名 | 关键字参数 `amount` / `note` | 「金额必填」由签名表达 (位置参数无法省略), 调用点也读得出来是退款金额 |
| 批准/打款/驳回 的 admin 形态 | 批准与驳回走**中间页表单**（同一个模板 `admin/minimall/refund_action.html`）, 打款直接执行 | 驳回也要写原因 —— 故事 18 里买家问「为什么被驳回」靠的就是这句, 没有输入框那句就永远是空的（PRD 说的是「批准与驳回**都可填**」） |
| 退款单能不能删 | `has_add_permission` / `has_delete_permission` 都返回 False, 且不挂 `BatchDeleteMixin` | 删掉一条**进行中**的申请会把订单永久卡在 `refunding`: 取消/发货/收货都不认这个状态, 又不能再提申请. 撤掉申请该走「驳回」 |
| 订单详情要不要内联退款单 | 不加 | 退款列表已带订单号且能搜 (`search_fields` 含 `order__order_no`), 再挂一个内联是同一件事的第二条路径 |
| 仓库既有测试里被改动的一处 | `test_models.test_status_choices` 的 `7` → `8` | 状态枚举本片刻意 +1 (`refunding`), 该断言必然失效. 这不是回归, 是它的预期被改对了 |

### 二、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `app/minimall/models.py` | `Order.Status` 加 `REFUNDING`; `Order.refunded_at`; 新模型 `RefundRequest`（含 `ACTIVE_STATUSES` 常量） |
| `app/minimall/migrations/0018_order_refunded_at_alter_order_status_refundrequest.py` | 新建（一次迁移装下三处改动） |
| `app/minimall/services.py` | 4 个异常 + `REFUNDABLE_STATUSES` + 四个退款函数; 模块 docstring 的锁序与「返回值是锁内那份」扩到退款 |
| `app/minimall/admin.py` | `RefundApproveForm` / `RefundRejectForm` / `RefundRequestAdmin`（三个 action）; `OrderAdmin.readonly_fields` 补 `refunded_at` |
| `templates/admin/minimall/refund_action.html` | 新建 —— 批准与驳回共用的中间页（订单信息表 + 表单 + 隐藏的 `_selected_action` / `action` / `confirm`） |
| `app/minimall/tests/test_services.py` | 新 `RefundServiceTest`（15 条）; 改 `test_cancel_rejected_for_refunding_order` 用上枚举 |
| `app/minimall/tests/test_concurrency.py` | 新 `ConcurrentRefundTest`（2 条: 双提 / 双打款）; 模块 docstring 补「窗口内会合」 |
| `app/minimall/tests/test_admin.py` | 新建（8 条: 批准三段式与两条表单校验 / 打款 / 驳回 / 批量里一条失败 / 不许删） |
| `app/minimall/tests/test_models.py` | 状态枚举计数 7 → 8 |

### 三、验收逐条

| 验收 | 证据 |
|------|------|
| 四个转换逐一可验 | `RefundServiceTest` 四段各自有成功用例: `test_request_snapshots_status_and_marks_order_refunding` / `test_approve_records_amount_and_leaves_order_refunding` / `test_settle_pays_negotiated_amount` / `test_reject_restores_order_status` |
| 驳回恢复（四个起始状态各一次） | `test_reject_restores_order_status` 用 `subTest` 跑 `paid` / `shipped` / `received` / `completed`, 每次都断言订单回到原状态且 `refunded_at` 仍为空 |
| 部分退款 | `test_settle_pays_negotiated_amount`: 总额 100 → 批准 70 → 打款 → 余额 `9970.00`（不是 `9900.00`）, 订单 `refunded` |
| 金额边界 | `test_approve_amount_boundaries`: `0.00` / `-1.00` / `100.01` 三条都抛 `InvalidRefundAmountError` 且申请仍在 `requested`; `100.00` 通过 |
| 非法转换被拒 | `test_settle_rejected_before_approval`（requested 不能打款）· `test_settle_twice_pays_once`（已打款不能重打, 余额只加一次）· `test_approve_rejected_after_reject`（已驳回不能批准）· `test_approve_rejected_when_not_requested`（金额只能定一次） |
| 并发双提 | `ConcurrentRefundTest.test_concurrent_request_creates_only_one`（真线程 + 窗口内会合） |
| 驳回后可再提 | `test_rejected_refund_can_be_requested_again`（两条申请, 新的一条是 `requested`） |
| 退款不动库存 | `test_refund_never_touches_stock`: 申请 → 批准 → 打款 全程后 `product.stock` 与流程前相等（与 `cancel_order` 的不对称写进 `request_refund` 的 docstring） |
| `refunded_at` 由打款写 | `test_request_snapshots_status_and_marks_order_refunding`（申请后为空）· `test_approve_records_amount_and_leaves_order_refunding`（批准后仍为空）· `test_settle_pays_negotiated_amount`（打款后非空） |
| 全程 `Decimal` | `test_amounts_stay_decimal_end_to_end`: `refund.amount` / `Profile.balance` / `order.total_amount` 三个都断言类型 |
| 商城测试全绿 | `manage.py test app.minimall` = **160 passed**（本片前 135, +25）; `ruff check` / `ruff format --check` 干净 |
| 附带: 退款中的订单自动不可取消/发货/收货 | `test_refunding_order_cannot_be_cancelled_shipped_or_received`: 三个函数各抛 `InvalidOrderStatusError`, 且余额未动（ADR-0004 那句「一行都不用改」有了用例钉住; 09 的字面量用例也改用枚举复验了） |

**并发用例的成色**（ticket 备注要求: 复现不了就标注, 别假装测过）: 两条退款并发用例一开始**在拆掉锁的实现上也是绿的** —— 判据读完到写入之间的窗口只有零点几毫秒, 两个线程一前一后就过去了. 于是给它们各加了一个**窗口内的会合点**（`RefundRequest.objects.create` 与 `settle_refund` 里那一次 `timezone.now()`, 前者卡在「判据读完、插入之前」, 后者卡在「判据判完、钱还没动」）: 两个线程都走到窗口正中才放行. 拆掉锁重跑, 两条都红了 —— 一条复现出双打款（`['settled', 'settled']`）, 一条复现出两个事务互锁（`OperationalError 1213 Deadlock`）. 锁装回去复跑, 绿. 加会合点的代价是有锁时后到的线程到不了会合点, 等 2 秒超时放行, 两条用例合计 +4 秒.

### 四、本片明确不动的东西

| 不动的东西 | 为什么 |
|-----------|--------|
| 买家页面的退款入口 (`order_detail.html` / `order_list.html`) | 故事 17/18 走**助手**, 页面不建退款入口. 页面因此不认 `refunding`: 状态标签会显示原值, JS 里那个取消按钮会露出来且点了必然报错（服务端拦得住, 只是体验不佳）. 记在这里, 免得以后当成没发现 |
| `OrderActiveCountView` 的「进行中订单数」 | 它现在数是 `pending/paid/shipped/received`, 不含 `refunding` —— 退款中的订单算不算「进行中」是个产品判断, 不在本片范围 |
| `ship_order` / `receive_order` / `complete_order` 的松散 | 与 09 一致: 它们只推一步状态, 不碰钱与库存 |
| 退款的 HTTP 端点与 agent 工具 | 11 / 12 的活 |

> **2026-09-21 补记**: 上表第一行**已作废** —— 用户要求页面也要有退款入口, 于是补了一批
> (助手与页面两条路现在都能走, 故事 17/18 不改):
>
> | 补的东西 | 落在哪 |
> |---------|--------|
> | 申请端点 `POST /api/minimall/orders/<order_no>/refund/` | `views_buyer.py` / `urls_api.py` |
> | 订单详情带最新一条退款 (`refund`: status / amount / admin_note / 四个时间戳) + `refunded_at` | `serializers.py` |
> | `refunding` 的状态标签与颜色; 「申请退款」按钮 + 退款进度卡片; 时间线在退款中不再塌回起点 | `order_detail.html` / `order_list.html` |
> | 申请端点 11 条用例（状态联动 / 不动库存 / 驳回可见原因且能再提 / 打款后金额 / 归属与认证） | `tests/test_api.py` |
>
> 顺带更正上表第一行的第二个说法: 「取消按钮会露出来」**当时就不成立** —— 它一直在
> `x-show="['pending','paid','shipped','received'].includes(order.status)"` 里面
> (该条件自 v0.1 就在), `refunding` 时本来就隐藏. 记这条是免得后来人照着一句错描述去查.
>
> 用户对两处开放的判断当场拍了板（2026-09-21）:
>
> | 判断 | 拍板 |
> |------|------|
> | `paid` 订单同时露「取消」与「申请退款」两个按钮 | **页面只留取消**. 未发货的订单取消即刻全额退回并回滚库存, 同一状态上的退款按钮严格更差（等审批, 不回库存）. **端点没跟着收窄** —— 服务层 `REFUNDABLE_STATUSES` 与助手侧仍是四个状态, 只是页面不给这个入口 |
> | 上表第二行的「进行中订单数」（`OrderActiveCountView`） | **`refunding` 要算进去**. 不计的话买家一申请退款, 顶部角标反而少一个, 看起来像订单消失了 |
>
> 所以页面按钮的可见条件是 `shipped / received / completed` —— 比服务层那份白名单
> **少一个 `paid`**, 这是有意的分叉, 两处都写了注释说明. 不另判「有没有进行中的申请」:
> 有申请时订单必然是 `refunding`, 而它不在那个集合里.

### 五、留给下一片（issue 11）的接口约定

- **服务函数签名**（都返回**锁内那份**实例, 传进去的那份不作数 —— 与 `pay_order` 同一条规矩）:
  - `request_refund(order) -> RefundRequest`（调用方按 `order_no` 自己解析出订单）
  - `approve_refund(refund, *, amount: Decimal, note: str = "") -> RefundRequest`
  - `settle_refund(refund) -> RefundRequest`
  - `reject_refund(refund, *, note: str = "") -> RefundRequest`
- **异常 → 错误码**（issue 11 的映射表要覆盖这四类 + 09 那批）:
  | 异常 | 什么时候 | 对买家该怎么说 |
  |------|---------|---------------|
  | `RefundNotAllowedError` | 订单状态不是「钱已出去」的四态 | 「这单还没付款 / 已取消, 不能退款」 |
  | `RefundAlreadyInProgressError` | 已有一笔进行中的申请 | 「这笔退款正在处理中」 |
  | `InvalidRefundStatusError` | 退款单状态不允许该动作 | 管理员侧才会碰到 |
  | `InvalidRefundAmountError` | 金额越界 | 管理员侧才会碰到 |
- **「进行中」怎么判**: `RefundRequest.ACTIVE_STATUSES`（`requested` + `approved`）, 别在端点里另写一份 `status__in=[...]`.
- **`GET refunds/`（故事 18 的进度）要带的字段**: `status` / `status_display` / `amount`（可以是 `None` —— 还没批）/ `admin_note`（**驳回原因就靠它**, 别漏）/ `created_at` / `approved_at` / `refunded_at` / `rejected_at` / `order_no`. 故事 18 问「到哪一步了、为什么被驳回」, 答案全在这几个字段里.
- **管理员审批走 Admin, 不走端点**: 本片给的是 Admin 入口, 11 不要顺手给 `refunds/` 加审批动作.

### 六、代码审查改了什么

两条轴并行审（规范轴: 仓库 `CLAUDE.md` + 全局规范 + Fowler 坏味道基线; 规格轴: 对着本 ticket 逐条核对, 并独立跑了一遍全量测试与 `makemigrations --check`）. **硬性规范违规 0 条, 规格缺失/做错 0 条.** 处理如下:

| 审查发现 | 处理 |
|---------|------|
| `_render_action_form(..., mode)` 是**死参数** —— 模板里没用过 `mode` | **改了**: 删掉参数与调用点 (`Speculative Generality`) |
| 三个退款函数的前奏逐行相同（atomic → 锁 Order → 锁 RefundRequest → 状态守卫） | **改了**: 抽 `_locked_refund(refund, expected, action)`, 把**锁序只写一遍**（仓库规则: 重复 ≥3 抽; 三个函数各抄一遍的话, 哪天有人给其中一个调了顺序, 死锁会在并发下悄悄回来） |
| docstring 说三个动作是状态机唯一的推动力, 但 `admin_note` 可手改 | **改了措辞**: 收窄成「**状态**只能由这三个动作推动」, 并写明备注可改（备注不参与状态机, 允许管理员事后补一句; 保留这个低风险能力, 不把整页变成只读） |
| `list_filter` 多带了 `created_at`（ticket §4 只要求 `status`） | **不改**: 同文件里 `OrderAdmin` 等既有 admin 都是「状态 + 时间」这个形状, 从众 |
| 金额上限规则在 form 与 service 各写了一遍 | **不改**: 两处是不同层的两种失败方式（字段报错 vs 抛业务异常）, 能共用的只剩 `0 < amount <= total` 这一个比较式 —— 为它加一层间接不划算. 金额的**上限取值**（`order.total_amount`)已经是同一个来源 |
| 新函数用中文 docstring / 中文异常消息, 而 09 那批订单函数是英文 | **不改**: 全局规范 §1.1 要求中文; 英文那批是既有代码待统一, 不是本片造成的不一致 |
| 并发用例 mock 了 `RefundRequest.objects.create` 与 `services.timezone.now` | **不改**: 用途是窗口内会合点（不是替掉被测逻辑）, 断言仍落在数据库上; 全局规范 §5 允许 mock 数据库 |
| 规格轴未能独立复现「拆掉锁则两条并发用例变红」（评审约定不改代码） | 本片实现记录里有复现结果, 且当时跑了两遍（拆锁红 / 装回绿） |
