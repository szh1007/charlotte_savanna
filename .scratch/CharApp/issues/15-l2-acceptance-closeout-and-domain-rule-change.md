# 15 · L2 验收收口：领域规则改判 + 三个缺陷 + 簿记

**Status:** ready-for-agent

**Type:** task

**Blocked by:** 无（可与 16、17 并行）

**上游:** `../PRD.md` §4.5 / §4.9（L2 行）、`CharApp/docs/PLAN.md` §2（L2 行）、`CharApp/CONTEXT.md`（取消 / 退款词条）

## 做什么

L2 的七片（08–14）交付完整、验收框全勾，但 **2026-09-22 的真机端到端验收**（浏览器里从下单走到「助手答出退了 70」）跑出**三条欠账**，其中两条是**真缺陷**；同时用户对**领域规则**做了一次改判。本片一次收干净。

四件事：

1. **领域规则改判**（用户 2026-09-22 拍板）：判据从「订单处于什么状态」改成「**用户说的是哪个动词**」+「**货发出去没有**」
2. **缺陷 B**（Admin 两段式动作在真实 UI 上必坏 —— 唯一一条「测试全绿但人点不动」的）
3. **缺陷 C**（被业务拒绝的操作被渲染成「已完成」）
4. **簿记**（issues 12/13/14 的 `Status` 还没改 `done`）+ PLAN §2 验收链补一步

**缺陷 A 不是缺陷，是上面第 1 条。** 定性要说准：`v2.prompt` 的术语表原文就写着「取消: 还没发货的订单 (待付款 / 已付款) 才能取消」「还没发货就取消；已经发货就说明只能申请退款」—— 真机里模型**是照 prompt 做的**，它甚至把理由说出来了（「这单还没发货,我直接帮你取消更快」）。错的是规则本身把**状态**当判据，而 `CONTEXT.md` 里「取消」与「退款」是两个**用户意图**。改 prompt + 改规则即可，不是"让模型更听话"。

## 已经替你确认过的事实

| 事实 | 出处 |
|------|------|
| `cancel_order` 的可取消白名单是**内联元组** `(PENDING, PAID)` | `app/minimall/services.py:449` |
| `REFUNDABLE_STATUSES = (PAID, SHIPPED, RECEIVED, COMPLETED)`，四个值，**唯一**使用点在 `request_refund` | `services.py:539-544`、`:577` |
| `settle_refund` 现在**完全不动库存**（只写 `Profile.balance` / `Order.status`+`refunded_at` / `RefundRequest.status`） | `services.py:679-689` |
| 库存回滚是 `cancel_order` 里的**内联 6 行**（`stock += item.quantity`）；全仓 `stock +=` **只此一处** | `services.py:459-463` |
| `OrderItem` 保存了下单时的快照（`product` FK + `product_name` / `product_price` / `quantity` / `subtotal`），回滚可以只用 `item.quantity` | `app/minimall/models.py:360-376` |
| `Order` 有时间戳 `paid_at` / `shipped_at` / `received_at` / `cancelled_at` / `refunded_at`，**没有 `completed_at`** | `models.py:339-345` |
| `RefundRequest.order_status_before` 是申请那一刻的状态快照 | `models.py:426-431` |
| `action_ship_orders` 用 `queryset.filter(status=Order.Status.PAID).update(...)` —— `refunding` 订单**本来就发不出去** | `app/minimall/admin.py:212-217` |
| `ship_order` 服务本身也拒 `refunding`，且**已有用例**逐个断言 cancel / ship / receive 全被拒 | `app/minimall/tests/test_services.py:655` |
| `v2.prompt` 的术语两段原文（要改的就是它们） | `CharApp/minimall/prompt/system/v2.prompt:57-64` |
| 同一份 prompt 的两个示例也写着旧规则 | `v2.prompt:96`（「还没发货就取消; 已经发货就说明只能申请退款」）、`:98`（「(若已发货) 已经发货了, 取消不了」） |
| 工具选择表里也有一行旧规则 | `v2.prompt:21`（「| 取消一笔还没发货的订单 | 取消订单 (即刻生效, 不用审批) |」） |
| 取消的回执话术要念「退回了多少余额、回滚了几件库存」 | `v2.prompt:34` |
| 自定义 Admin 模板把标准 `.actions` 容器 `display:none`，但**没有 disable 里面的 `<select name="action">`** | `templates/admin/change_list.html:15-19` |
| 同一模板又往表单里插了 `name="action"` 的按钮，于是请求体里**两个同名键** | `templates/admin/change_list.html:32-33` |
| Django 自己用 `getlist("action")[action_index]`（所以中间页能弹出来），项目代码用 `request.POST.get("action")` —— `QueryDict.get` 对重复键取**最后一个**（正是那个空 select） | `app/minimall/admin.py:402`；Django `contrib/admin/options.py:1612` |
| `test_admin.py` 的 `_post_action` 只发**一个** `action` 键（普通 dict），所以这个缺陷**测不到** | `app/minimall/tests/test_admin.py:71-77` |
| 中间页 `refund_action.html` 的 hidden `action` 值来自上面那个空串 → 第二次 POST 的 action 为空 → 「未选择动作」 | `templates/admin/minimall/refund_action.html:49` |
| `_act()` 把 `MinimallRefusalError` 当**正常返回值**（`return _refusal_text(exc)`，不抛） | `CharApp/minimall/tools.py:99-119`、`:140-143` |
| 于是框架把这条 tool_result 记成 `status=ok`，而 `redact()` 按 `ok` 选 `done` 话术 | `CharApp/minimall/redaction.py:142-143` |
| 8 条写工具的 failed 话术（「订单没取消成」「下单没成功」「退款申请没提交上」等）因此**全不可达** —— 它们早就在表里 | `redaction.py:101-108` |
| `_act` 实际被 **9** 个工具用（含只读的 `list_my_refunds`），不是 8 个 | `tools.py:598`（无 `WRITE_ANNOTATION_KEY`） |
| 两条用例**逐字**断言「拒绝当返回值」这个行为 | `CharApp/tests/test_tools.py:427`、`:454` |
| `serializers_agent.py` 的 `restocked_count` 按 `paid_at` 判据**重算**过（因为 `cancel_order` 只改 `status` / `cancelled_at`） | `app/minimall/serializers_agent.py:303-324` |
| 既有用例里会因规则改判而红的（至少）：`test_cancel_restores_stock` · `test_refunding_order_cannot_be_cancelled_shipped_or_received` · **`test_refund_never_touches_stock`** · `test_settle_pays_negotiated_amount` | `app/minimall/tests/test_services.py:134`、`:655`、`:673`、`:587` |
| 页面侧会红的：`test_cancel_order_restores_stock` · **`test_refund_does_not_restore_stock`** · `test_request_refund_marks_order_refunding` | `app/minimall/tests/test_api.py:309`、`:416`、`:401` |
| 内部端点侧会红的：`test_cancel_returns_balance_and_restocked_count`（用 `paid` 单断言 `balance_returned`，改判后 cancel 不再接受 `paid`） | `app/minimall/tests/test_agent_write_api.py:398` |
| issues 12 / 13 / 14 的 `Status` 还是 `ready-for-agent` | 三份文件第 3 行 |
| 真机现场的账号与数据（留着，本片收口时清理）：`l2accept_buyer`(id=26) / `l2accept_staff`(id=27) / 三张订单 / 退款单 id=5 / 余额 9971.00 | 2026-09-22 验收记录 |

## 具体任务

### 1. 领域规则（`app/minimall/services.py`）

- `cancel_order`：白名单 `(PENDING, PAID)` → **`(PENDING,)`**。付款之后一律走退款
- **抽一个私有 `_restock_items(order)`**：把 `services.py:459-463` 那 6 行（锁商品行 + 加回 `item.quantity` + `save(update_fields=["stock"])`）提出来，`cancel_order` 与 `settle_refund` 共用。**不抽的代价**：`stock +=` 会出现两处，而"写库存只有一条路径"是可审计性上值得保住的性质（PRD 的 DRY 线是重复 ≥3 次，这里是 2 次 —— 这条抽取的理由**不是** DRY，写进 docstring）
- `settle_refund`：打款时按**申请前的状态快照**判定 —— `refund.order_status_before == Order.Status.PAID` 则回滚库存，其余（`shipped` / `received` / `completed`）不动。**判据用快照字段，不用 `shipped_at` 推断**（与 `reject_refund` 恢复状态同一条纪律）
  - 锁顺序要与既有实现一致：现在 `cancel_order` 是 Order → Product → Profile，`settle_refund` 是 Order → RefundRequest → Profile。加库存回滚后要**统一成 Order → RefundRequest → Product → Profile**，并把新顺序写进 docstring（两处顺序不一致就是死锁的种子）
- 四个退款函数的 docstring：删掉「退款一律不动库存」那段，换成新判据，并写清**为什么回滚发生在打款而不是申请时**（申请时货还在、订单还在 `refunding`，提前放回库存等于让这一单同时占着货和钱）
- `request_refund` 的 docstring 补一句：**退款流程进行中的订单不允许发货**（现状已如此，两处都拦，写下来免得以后被当漏洞修）

### 2. 回执重算跟着改

`serializers_agent.py` 的 `restocked_count` 现在按 `paid_at` 判据重算「取消时回滚了几件」。cancel 收窄到 `pending` 之后，取消**永远**回滚全部明细 —— 判据要么简化成「全部明细的件数之和」，要么明确只对 `cancelled` 状态生效。**不改就会在回执里报错数字**。

### 3. prompt 判据改写（`CharApp/minimall/prompt/system/v2.prompt`）

五处，改完要通读一遍确认没有互相矛盾的残留：

| 位置 | 改什么 |
|------|--------|
| `:57-64` 术语两段 | 「取消」= **只有未付款能取消**，即刻生效、回滚库存；「退款」= **付款之后一律走退款**（不分发没发货），要管理员审批、退多少由管理员协商；**库存回滚以发货为界**（货没出去就回滚，出去了就不回滚） |
| `:21` 工具选择表那一行 | 「取消一笔还没付款的订单」 |
| `:34` 回执话术 | 「取消之后念回滚了几件库存」→ 加上「退款打款之后念退了多少钱、货没出去的话也念回滚了几件」 |
| `:96` 示例 | 「查订单状态 —— 还没付款就能取消；付过款了只能申请退款」 |
| `:98` 示例 | 「(若已付款) 已经付过款了, 取消不了, 只能申请退款」 |

**并且要加一条明确禁令**：「买家说"退款"就申请退款，说"取消"才取消，**不要因为"更快"就替他换一个动作**」—— 缺陷 A 的现场证据就是模型自己说了「直接帮你取消更快」。

### 4. 缺陷 B（Admin 两段式动作）

两处一起修，**根因与防御各修一次**：

- **根因（模板）**：`templates/admin/change_list.html` 的 JS 在隐藏 `.actions` 之后，把里面的 `<select>` **`disabled = true`**（浏览器就不会提交它了）。注意这是**全局**覆盖（`admin/change_list.html`），charplot 的 admin 共用同一个模板 —— 修在这里全站受益，副作用要写进 ticket 结论
- **防御（项目代码）**：`app/minimall/admin.py:402` 的 `request.POST.get("action", "")` → **`request.POST.getlist("action")[0]`**（Django 官方语义就是取第一个；`getlist[0]` 与它的 `action_index=0` 等价）
- **换掉那条假绿灯的测试写法**：新用例必须构造**两个同名 `action`** 的请求体（`action=action_approve_refunds&_selected_action=<pk>&action=`，与真实浏览器同形）。现有的 `_post_action` 只发一个键 —— 那是这个缺陷能活到真机验收的原因，**在注释里把这件事写下来**
- 顺带核一遍「批准 / 驳回」两条两段式路径都走一遍（它们共用 `_render_action_form`）

### 5. 缺陷 C（拒绝即抛错）

- `tools.py` 的 `_act()`：`except MinimallRefusalError` 那一支从 `return _refusal_text(exc)` 改成**抛一个携带同一句文案的异常**（保留 `exc.code`，便于将来有插件要看）。文案一个字不改 —— 模型收到的文本基本不变，变的是**框架给这条 tool_result 记的 status**
- 连带：`_REFUSAL_HINTS` 保留（那 7 条「下一步做什么」正是防模型重试同一动作的）
- **两条既有用例按新语义改写**（断言异常消息里含同一句文案），并**新增**一条：被拒的写操作产出的 tool_result 必须是 `status=error`（这是缺陷 C 的守卫，缺了它下次还会漂回去）
- 记录一处**命名不准**（不改）：`_act` 的注释与命名都说「写」，而它实际被 9 个工具用（含只读的 `list_my_refunds`）。只读工具走这条路后，被拒的读也会显示 failed —— 这是**正确**的，但注释要顺带说清

### 6. 簿记与文档

- `issues/12`、`13`、`14` 的 `**Status:**` 改 `done`
- PLAN §2 的 L2 行：验收链补「**买家在订单页付款**」这一步（助手的 17 个工具里没有付款工具，PRD §6 明文归 L3；原链路文字缺了它就不可能成立）。**PLAN 与 PRD 已在规划期改好，本片只核对**

### 7. 真机重跑（真模型，必须）

按 PLAN §2 的链路重跑一遍，**六段**：浏览器下单 → 买家在订单页付款 → 说「我要退款」→ Admin 批准 70 → Admin 打款 → 助手答得出退了 70。另外**专门观察两条新规则**：

- 已付款（未发货）订单说「退款」→ 助手必须走 `request_refund`，**不得替换成取消**
- 未发货的退款打款后 → **库存回滚了**；已发货的退款打款后 → 库存不动

## 验收

- [ ] `pending` 订单说取消 → 真取消、库存回滚（既有回归）
- [ ] `paid` 订单说取消 → 被拒（409 `invalid_order_status`）；说退款 → 真建申请、订单转 `refunding`
- [ ] **未发货的退款**：申请 → 批准 70 → 打款 → 余额 +70 **且库存回滚**
- [ ] **已发货的退款**：申请 → 批准 70 → 打款 → 余额 +70 **且库存不动**
- [ ] `refunding` 订单仍然发不出去（Admin action 与 `ship_order` 两处各有用例）
- [ ] 真机（真模型）六段链路全过，证据（订单号 / 退款单 id / 打款前后余额 / 库存前后值 / 助手回答原文）写进本 ticket 的「实际开发情况」
- [ ] 真机里说「退款」走的是 `request_refund`（不是 `cancel_my_order`）—— 用 SSE 的 `tool_name` 或商城侧调用记录核
- [ ] Admin 的「批准」与「驳回」**在真实页面上点得动**（浏览器核一次；用例用两个同名 `action` 的请求体写）
- [ ] 被业务拒绝的写操作在页面上显示 failed 话术，且模型收到同一句文案
- [ ] `test_tools.py:427` / `:454` 两条已按新语义改写，**文案一字未动**；新增「拒绝 → `status=error`」一条
- [ ] `serializers_agent.py` 的 `restocked_count` 与改判后的行为一致（有用例）
- [ ] issues 12/13/14 的 `Status` 改成 `done`
- [ ] 三套测试全绿：框架（`pytest CharAgent`）· 业务（`pytest CharApp`）· 商城（`manage.py test app.minimall`，**耗时较长，由用户手动跑**）；`ruff check` / `ruff format --check` 干净
- [ ] 收口时清理真机现场（`l2accept_*` 两个账号、三张订单、退款单、以及验收期的两个进程）

## 备注

- **本片只修不扩**。任何"顺便"的想法（比如给写操作加新护栏）归别的片。
- **「抽 `_restock_items`」这条是判断题**：重复 2 次够不上仓库的 DRY 线（≥3 次）。理由写在 `services.py` 的注释里（"写库存全仓只有一条路径"），**实现时若不同意可以回退成两处内联**，但要在 ticket 里记下这个取舍。
- **锁顺序统一**那条不是洁癖：`cancel_order` 与 `settle_refund` 都要锁 Order + Product + Profile，两处顺序不一致就是**并发退款与取消同时发生时的死锁**。改判之后两条路径终于都会动库存，这个风险才第一次真实存在。
- **真机重跑要花真钱**（模型 API）。把 agent 调用压到最少（一条链路 + 两条新规则的定向提问），别反复重问。
- **现场先留**（用户 2026-09-22 决定）：账号与数据留到本片收口，方便"修完再跑一遍"做对照。
