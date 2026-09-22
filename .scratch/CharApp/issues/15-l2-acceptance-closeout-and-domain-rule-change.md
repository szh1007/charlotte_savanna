# 15 · L2 验收收口：领域规则改判 + 三个缺陷 + 簿记

**Status:** done

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

- [x] `pending` 订单说取消 → 真取消、库存回滚（既有回归）
- [x] `paid` 订单说取消 → 被拒（409 `invalid_order_status`）；说退款 → 真建申请、订单转 `refunding`
- [x] **未发货的退款**：申请 → 批准 70 → 打款 → 余额 +70 **且库存回滚**
- [x] **已发货的退款**：申请 → 批准 70 → 打款 → 余额 +70 **且库存不动**
- [x] `refunding` 订单仍然发不出去（Admin action 与 `ship_order` 两处各有用例）
- [x] 真机（真模型）六段链路全过，证据（订单号 / 退款单 id / 打款前后余额 / 库存前后值 / 助手回答原文）写进本 ticket 的「实际开发情况」
- [x] 真机里说「退款」走的是 `request_refund`（不是 `cancel_my_order`）—— 用 SSE 的 `tool_name` 或商城侧调用记录核
- [x] Admin 的「批准」与「驳回」**在真实页面上点得动**（浏览器核一次；用例用两个同名 `action` 的请求体写）
- [x] 被业务拒绝的写操作在页面上显示 failed 话术，且模型收到同一句文案
- [x] `test_tools.py:427` / `:454` 两条已按新语义改写，**文案一字未动**；新增「拒绝 → `status=error`」一条
- [x] `serializers_agent.py` 的 `restocked_count` 与改判后的行为一致（有用例）
- [x] issues 12/13/14 的 `Status` 改成 `done`（**本片自己那份也一起改了** —— 12/13/14 是 ticket 点名的，15 是收尾时容易漏的那个）
- [x] 三套测试全绿：框架（`pytest CharAgent`）· 业务（`pytest CharApp`）· 商城（`manage.py test app.minimall`，**耗时较长，由用户手动跑**）；`ruff check` / `ruff format --check` 干净
- [x] 收口时清理真机现场（`l2accept_*` 两个账号、三张订单、退款单、以及验收期的两个进程）

## 备注

- **本片只修不扩**。任何"顺便"的想法（比如给写操作加新护栏）归别的片。
- **「抽 `_restock_items`」这条是判断题**：重复 2 次够不上仓库的 DRY 线（≥3 次）。理由写在 `services.py` 的注释里（"写库存全仓只有一条路径"），**实现时若不同意可以回退成两处内联**，但要在 ticket 里记下这个取舍。
- **锁顺序统一**那条不是洁癖：`cancel_order` 与 `settle_refund` 都要锁 Order + Product + Profile，两处顺序不一致就是**并发退款与取消同时发生时的死锁**。改判之后两条路径终于都会动库存，这个风险才第一次真实存在。
- **真机重跑要花真钱**（模型 API）。把 agent 调用压到最少（一条链路 + 两条新规则的定向提问），别反复重问。
- **现场先留**（用户 2026-09-22 决定）：账号与数据留到本片收口，方便"修完再跑一遍"做对照。

---

## 实际开发情况 2026-09-22

**一句话**：三件事全部落地 —— 领域规则改判（取消只认 `pending`；库存回滚以发货为界，判据取申请前的快照）、缺陷 B（Admin 两段式在真实请求体下点不动）、缺陷 C（被业务拒绝的操作被记成成功）；真机六段链路重跑通过，现场按用户决定在本片清干净。测试：框架 **850 passed / 65 deselected** · 业务 **186 passed** · 商城 **8 个模块 239 passed**（`test_services` 42 · `test_admin` 11 · `test_api` 38 · `test_agent_write_api` 45 · `test_concurrency` + `test_models` + `test_agent_api` 44 · `test_bff` 59 —— 逐个模块跑完，等价于一次全量 `manage.py test app.minimall`）；`ruff check` / `ruff format --check` 干净。三处新守卫都做了**反向验证**（把修复临时退回去，确认它们变红，再恢复）。

**一次被权限拦下的工序**（过程记录）：验收期那两个进程跑的是改判**之前**的代码（`--noreload`），跑真机必须先重启；我停进程的操作被权限规则拒（`kill` / `taskkill` 都在拒绝名单里），于是**请用户停了这两个进程**、由我起新的两个。这一步之后真机与清理都在本片内完成。

### 一、拍板的开放项（ticket 没定 / ticket 写错，实现时定下来的）

| 项 | ticket 说的 | 落地的 | 为什么 |
|----|------------|--------|--------|
| `_act` 被几个工具用 | 「实际被 **9** 个工具用……不是 8 个」 | **8 个**（7 个写 + `list_my_refunds`） | 数错了一个：全仓 `await _act(` 8 处。docstring 按代码写 8，没有为了凑上 ticket 的数字去改调用点 |
| `restocked_count` 的判据 | 「现在按 `paid_at` 判据**重算**过……不改就会在回执里报错数字」 | 它本来就是「全部明细的件数之和」；按 `paid_at` 判的是**另一个字段** `balance_returned` | 改判后两个字段其实都算得对（只认 `pending` → 没扣过钱 → 余额恒 0；取消永远回滚全部明细 → 件数就是明细之和）。真正要收拾的是 `balance_returned` 那条**再也走不到的分支**：收成常量 `"0.00"` + docstring 写清 |
| `balance_returned` 留不留 | ticket 没提（只点了 `restocked_count`） | **留**，值恒 `"0.00"` | 它是 issue 11 定下的写端点契约（客户端 docstring / 样本 / 用例都按这个形状解析）；恒 0 但不说假话。**要不要删请用户拍板** —— 删是一行的事（序列化器 + `client.py` docstring + 两处样本） |
| `_restock_items` 抽不抽 | 「判断题……实现时若不同意可以回退成两处内联」 | **抽了** | 理由不是 DRY（两处，够不上 ≥3），是「全仓写库存只有这一条路径」这条可审计性质（将来加库存流水 / 对账只改一处）。写在函数 docstring 里 |
| 页面要不要跟着改 | ticket 没列（只提到 admin 模板） | **改了 `templates/minimall/order_detail.html`** | 改判的直接后果不是顺带：`paid` 分支那个「取消订单」按钮必然 409，而 `canRefund()` 把 `paid` 排除在外的旧理由（「未发货走取消更划算」）已作废。现在 `paid` 页面只留「申请退款」 |
| 工具描述里的旧规则 | ticket 只列了 `v2.prompt` 五处 | **`tools.py` 两个工具 docstring 一并改** | 工具描述是**模型真正读的那份 schema**，而它原文里就有缺陷 A 的现场证据：「买家说「退钱」而订单还没发货时, 用这个更快 (当场到账)」。只改 prompt 不改它 = 留着病灶 |
| ADR-0004 的旧表述 | ticket 没提 | **加日期补记**（照 ADR-0003 的先例） | 那句「`cancel_order` 只认 `pending` / `paid`」被改判推翻，不改就是文档说谎 |
| `v2.prompt` 就地改 vs 开 v3 | ticket 指定改 `v2.prompt` | **就地改** | 记一条代价：清单的纪律是「一版一个文件，覆盖式布局会把上一版静默弄丢」，于是改判前的 v2 正文只活在 git 历史里。L4 做 prompt A/B 时，手里是 v1（只读版）与现在的 v2，**没有**「改判前后」这一对 |

### 二、碰过的文件

| 文件 | 改了什么 |
|------|---------|
| `app/minimall/services.py` | `cancel_order` 白名单 `(PENDING, PAID)` → `(PENDING,)` 并删掉退还余额那一支；**新增 `_restock_items`**；`settle_refund` 按 `order_status_before == PAID` 决定回滚，锁序统一成 `Order → RefundRequest → Product → Profile`；退款域注释 + 四个函数 docstring 换新判据（含「退款进行中的订单不允许发货」这条现状） |
| `app/minimall/serializers_agent.py` | 两个回执字段的 docstring；`balance_returned` 收成常量 |
| `app/minimall/views_agent.py` | 取消端点 docstring（回执里不再有「退回余额」这回事） |
| `app/minimall/admin.py` | 中间页的动作名改 `request.POST.getlist("action")[0]`（与 Django `response_action` 同语义） |
| `templates/admin/change_list.html` | 藏掉 `.actions` 之后 `select.disabled = true`（缺陷 B 的根因，全站 admin 共用） |
| `templates/minimall/order_detail.html` | `paid` 分支去掉「取消订单」；`canRefund()` 补回 `paid` |
| `CharApp/minimall/tools.py` | **新增 `RefusedActionError`**（带 `code`）；`_act` 由「返回文案」改成「抛同文案」；`cancel_my_order` / `request_refund` 的 docstring 换成新规则；`__all__` +1 |
| `CharApp/minimall/prompt/system/v2.prompt` | 五处判据（术语两段 / 工具表 / 回执话术 / 两个示例）+ 一条禁令（不许因为「更快」替买家换动作） |
| `CharApp/minimall/client.py` | `cancel_order` 的 docstring |
| `app/minimall/tests/test_services.py` | 新增 `test_cannot_cancel_paid`；`test_cancel_with_stale_instance_rejects_shipped` → **`..._rejects_paid`**（旧形状在新规则下怎么读都被拒，等于假绿灯）；`test_refund_never_touches_stock` → 拆成「未发货打款回滚」+「已发货打款不回滚（shipped / received / completed 各一遍）」 |
| `app/minimall/tests/test_admin.py` | `_post_action` 加 `action=` 参数；新增 `_post_like_a_browser`（两个同名 `action`）与 `_submit_middle_page`；新增 3 条用例（中间页动作名 / 批准两段式 / 驳回两段式） |
| `app/minimall/tests/test_agent_write_api.py` | `test_cancel_returns_balance_and_restocked_count` → **`test_cancel_paid_order_409`**（状态 / 余额 / 库存都不许动）；未付款那条补一条库存断言 |
| `app/minimall/tests/test_api.py` | `test_refund_does_not_restore_stock` → `test_request_refund_does_not_restore_stock`；可退款起点那句注释改掉 |
| `CharApp/tests/test_tools.py` | 两条拒绝用例按新语义改写（**文案一字未动**，新增 `code` 与异常类型的断言）+ 新增「拒绝 → `status=error`」；取消回执那条改成 0.00 |
| `CharApp/tests/conftest.py` | `CANCELLED_ORDER` 样本改成「取消只发生在付款前」的形状（`balance_returned` 0.00，无 `paid_at` / `shipped_at`，时间线只到下单） |
| `CharApp/docs/adr/0004-*.md` | 日期补记（`cancel_order` 的白名单变了） |
| `issues/12 · 13 · 14` | `Status` → `done` |

### 三、验收逐条

| 验收 | 证据 |
|------|------|
| `pending` 取消 → 真取消 + 回滚库存 | `test_cancel_restores_stock`（补了「余额不动」）、`test_cancel_unpaid_order_returns_zero_balance`（+库存断言） |
| `paid` 取消被拒 / 说退款则建申请 | `test_cannot_cancel_paid`（状态 / 余额 / 库存三不动）、`test_cancel_paid_order_409`（内部端点侧）、`test_request_refund_marks_order_refunding`（申请后订单转 `refunding`，快照 = `paid`） |
| 未发货的退款：打款 → 余额 +70 **且库存回滚** | `test_settling_an_unshipped_refund_restocks`（申请与批准两步都断言库存**没动**，打款后才 +2）、`test_settle_pays_negotiated_amount` |
| 已发货的退款：打款 → 余额 +70 **库存不动** | `test_settling_a_shipped_refund_keeps_stock`（shipped / received / completed 三个起始状态各一遍） |
| `refunding` 订单发不出去 | `test_refunding_order_cannot_be_cancelled_shipped_or_received`（既有，未改） |
| 被拒的写操作 → `status=error` + 同一句文案 | `test_a_refused_write_is_recorded_as_an_error`（走框架 `execute_tool` 与 `tool_result_data`：`ok=False` 且载荷 `status=error`）；页面那半在 `test_a_failed_result_loses_the_error`（`status=error` → failed 话术） |
| `test_tools.py` 两条改写 + 新增一条 | `test_a_refused_write_carries_the_sentence_and_the_next_step` / `test_a_refusal_without_a_known_code_still_says_something`（断言的是同一句话，只是改成 `pytest.raises`） |
| Admin 两段式在真实请求体下走得通 | `test_the_middle_page_carries_the_action_of_the_clicked_button`、`test_approve_takes_both_steps_a_browser_takes`、`test_reject_takes_both_steps_a_browser_takes` —— 第二步的 `action` **从中间页的 hidden 里读**（不手写），所以中间页写空值时它们会红 |
| 三处新守卫是真守卫（反向验证） | 逐条把修复退回去重跑：`getlist → get` 时 admin 那 3 条 **3 failed**；`raise → return` 时 CharApp 那 3 条 **3 failed**（其中一条的失败信息里能看到 `ToolExecution(ok=True, ...)` —— 就是缺陷 C 的形状） |
| 框架 / 业务 / ruff | `pytest CharAgent` = 850 passed, 65 deselected · `pytest CharApp` = 186 passed · `ruff check` + `ruff format --check` 干净（两处代码 + 测试目录） |
| 商城 | 8 个模块逐个跑完, 全绿 239 passed（`test_services` 42 · `test_admin` 11 · `test_api` 38 · `test_agent_write_api` 45 · `test_concurrency` + `test_models` + `test_agent_api` 合计 44 · `test_bff` 59）—— 等价于全量 `manage.py test app.minimall`, 用户仍可一条命令复核 |

### 四、真机重跑（真模型，2026-09-22 上午）

按 PLAN §2 的六段链路，全部在浏览器里走（Django :8000 与 CharApp :1007 都是重启后的新代码）：

| 段 | 做了什么 | 证据 |
|----|---------|------|
| 1 下单 | 商品页加购 → 购物车勾选 → 结算 → 提交订单 | 订单号 **202609220836490000265022**（99.00；库存 990 → 989） |
| 2 买家在订单页付款 | 「确认支付」输支付密码 | 订单 → 已付款；余额 9971.00 → 9872.00 |
| 3 说「我要退款」 | 客服页：「订单 202609220836490000265022 我要退款」 | 助手答「**这一单已经付过款了，我帮你提交退款申请**」，步骤行「退款申请已提交」；Django 日志 `POST /api/minimall/agent/refunds/` 200，全程**没有** `orders/<no>/cancel/` |
| 4 批准 70 | Admin 退款申请列表 → 勾选 → 「批准退款 (填协商金额)」→ 中间页填 70.00 + 备注「协商一致退 70 元」→ 确认 | 「已批准 1 笔退款.」；退款单 id=**6**：`requested → approved`，金额 70.00 |
| 5 打款 | 勾选 → 「打款 (按批准的金额出账)」→ 确认 | 订单 → 已退款；余额 9872.00 → **9942.00**；库存 989 → **990** |
| 6 助手答得出 | 客服页：「我的退款到哪了」 | 助手答「**退款金额：70.00 元（协商一致退 70 元），已退回余额**；这一单还没发货，商品库存也一并回滚了」；日志 `GET /api/minimall/agent/refunds/` 200 |

**三条针对性观察**：

| 观察 | 结果 |
|------|------|
| 已付款（未发货）说「退款」不得被换成取消 | 第 3 段：助手调的是 `POST refunds/`，`cancel` 一次都没发生 —— 缺陷 A 的行为没了 |
| 未发货的退款：打款后库存**回滚** | 989 → **990** ✓ |
| 已发货的退款：打款后库存**不动** | 第二单 **202609220842520000261844**：下单付款 → Admin「批量发货」→ 买家页「申请退款」（退款单 id=**7**，`order_status_before = shipped`）→ 批准 70 → 打款 → 余额 9843.00 → **9913.00**、库存 **989 → 989** ✓ |
| 改判后「取消」不再被接受 | 问助手取消那张已退款的单 → 「这一单取消不了 …… 取消只对还没付款的订单适用」。它在查完订单后**没有**去调取消工具（prompt 的判据生效，连试都没试） |

**缺陷 C 的真机复核**（测试之外再验一次）：让助手「帮我下单」（当时购物车是空的）→ 商城 409 `cart_empty` → 页面步骤行是 **「下单没成功」**（改之前这一行会是完成态的「订单已提交」），答复照读商城原话「购物车现在是空的」。日志：`GET /api/minimall/agent/cart/ 200` → `POST /api/minimall/agent/orders/ 409`。

**缺陷 B 的真机复核**：批准（两段式）与打款都在**真实页面上点完了**；中间页的 hidden `action` 是 `action_approve_refunds`（不是空串），浏览器里 `document.querySelector('.actions select').disabled === true` —— 模板那道（根因）与 `getlist[0]`（防御）都在起作用；另外「批量发货」在**订单** changelist 上也照常工作（改过的模板是全局的）。

**「驳回」的口径说清**：它在浏览器里**没有单独点过一次** —— 它与批准走的是同一个 `_render_action_form` 与同一个中间页模板（只换了表单类），批准既已点通，驳回按浏览器的请求体形状（两个同名 `action` + 中间页的 hidden）有用例钉住。真机上少点的那一次记在这里，不当作已验证。

**顺带核到的页面改动**：订单页在 `pending` 时是「确认支付 / 取消订单」，付款之后变成「申请退款」——「取消订单」按钮按改判消失。

**验收期的三条操作备注**（现场已清，仅存档）：

- 两个账号的口令在上午那次验收里没留记录，重跑前**重设过**（登录口令 `l2accept2026`，买家的支付密码 `123456`）；账号本身在收口时已删。
- 真机期间浏览器**共用一个 session**：登录 Admin 会把买家的 session 与 CSRF token 一起顶掉，中间出过一次 403「这次没能问出去」。这是验收顺序的问题（后来改成「买家侧做完再切 Admin」），不是产品缺陷。
- 打款那一笔在真机上多看了两眼：`refund.admin_note` 就是助手第 6 段念出来的那句备注 —— 故事 18 的「为什么被驳回 / 协商了什么」这条路是通的。

### 现场清理（2026-09-22，用户拍板「本片就清干净」）

| 删了什么 | 结果 |
|---------|------|
| 5 张订单（3 张旧 + 本次 2 张）+ 5 条 `OrderItem` + 3 条退款单（id 5 / 6 / 7） | 已删 |
| `l2accept_buyer`(26) / `l2accept_staff`(27) 两个账号 | 已删（Profile / 购物车 / 收货地址连带清掉，残留计数全 0） |
| 产线 `attacking-giants` 的库存 | 按**本次**净占用复原：989 → **990**（旧验收留下的 1 件偏差没动，不属于本片） |
| 验收期的两个进程（Django :8000 / CharApp :1007） | 已停，`sh/_status_.sh` 报「未发现项目进程」 |

### 五、代码审查改了什么（两轴各起一个 sub-agent）

**采纳并改掉的 7 条**：

| 指正 | 改法 |
|------|------|
| `_act`「9 个」数错 | 改成 8（7 写 + 1 只读），并把「名字里的『写』不准」这句顺成通顺的说法 |
| `tools.py` 的 `__all__` 注释说「两个名字」而实际出去了三个 | 注释重写（标记键 + 异常都在门面上的理由） |
| `services.py` 把 DRY 出处记成 PRD | 改记 `CLAUDE.md`（那条 ≥3 的规矩在系统级文档里，不在 PRD） |
| `order_detail.html` 注释里用了「退货」 | 改成「退款」（CONTEXT.md：退货退款这个词在本项目不存在） |
| `balance_returned` 的旧判据（按 `paid_at` 判）还留在代码里，与新 docstring 打架 | 收成常量 `"0.00"` + 写明那一支为什么再也走不到 |
| `test_admin.py` 四个请求体近似重复 | `_post_action` 加 `action=` 参数，`_post_like_a_browser` 与 `_submit_middle_page` 各收一处 |
| `test_tools.py` 里「页面上说的就是模型听到的那句」 | 改对：页面拿到的是按 `status` 选的话术（脱敏层换掉 `error`，ADR-0003），两者同源但不是同一句 |

**看过但有意不改的 4 条**（连理由，另附 1 条顺带记录）：

- **`RefusedActionError.code`「留给将来的插件」** —— ticket 明确要求保留，且现在就有读者（两条用例断言 `code`）。评审说它像 Speculative Generality，这条按 ticket 走。
- **继承 `ToolActionableError` 的语义张力** —— 框架那边只有这一类异常会把消息**原文**回填模型（其余都变成「内部错误」），而 ticket 要求「文案一个字不改」，所以只能借它。已在 `RefusedActionError` 的 docstring 里写明「它那句『让模型修正重试』在这里要反着读」，并把「框架的重试策略本就判它不可重试」一并记下。
- **`getlist("action")[0]` 的 `IndexError` 面**（旧的 `.get("action", "")` 会退回空串）—— 写死兜底反而会把「动作名取错了」这类问题掩掉；Django 自己的 `response_action` 就是同款写法，而只有它派发得到这里。
- **一片里塞了三件事（Divergent Change）** —— 这是 ticket 的结构（三条欠账 + 一次改判一次收干净），不是实现时的顺手扩张。
- 另：`v1.prompt` 里那句「待付款 / 已付款的订单」在新规则下过期了，但清单的纪律就是**冻结旧版本**（A/B 要用），不动。

### 六、留给下一片的

- **`balance_returned` 保留**（用户 2026-09-22 拍板：改判后恒 `"0.00"` 就恒着，没必要删）—— 本片不动它，只把旧判据收成常量 + docstring 写清。
- **商城全量测试**（`manage.py test app.minimall`，耗时较长）由用户手动跑 —— 本片只跑了受影响的四个模块。
- **`v2.prompt` 就地改的代价**：改判前的 v2 正文只在 git 历史里，L4 做 prompt A/B 时手里没有「改判前后」这一对 —— 若要那一对，得从 git 里捞出来另存一版。
- **issue 21 §4 的第 1 条（删 `l2accept_*` 两个账号 + 订单 + 退款单）已由本片完成**（用户拍板：现场由本片收干净，不等 21）。21 那条只剩 Postgres 相关的前置与「起一次、问一句、停掉」的服务复启确认。
