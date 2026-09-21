# 10 · 商城侧：退款域

**Status:** ready-for-agent

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

- [ ] 四个状态转换逐一可验：申请 / 批准（含金额）/ 打款 / 驳回
- [ ] **驳回后订单恢复到申请前的状态** —— 四个起始状态**各测一次**（`paid` / `shipped` / `received` / `completed`）
- [ ] **部分退款**：申请全退 100 → 批准 70 → 打款 → 余额只加 70，订单 `refunded`
- [ ] 金额边界：`0` 拒绝 · `> total_amount` 拒绝 · `= total_amount` 通过
- [ ] 非法转换被拒：`requested` 不能直接打款 · 已打款不能重打 · 已驳回不能批准
- [ ] **并发双提**：同一订单两个请求同时申请，只有一个成功（`select_for_update` 生效）
- [ ] **驳回后可再提**：新的一条申请能建起来
- [ ] **退款不动库存**：整条流程前后 `product.stock` 一字不变
- [ ] `Order.refunded_at` 由**打款**那步写，批准那步**不写**（断言为 `None`）
- [ ] 全程 `Decimal`（用例断言类型，别让 float 混进来）
- [ ] 商城测试全绿

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
