# SPEC: CharService — 企业级电商智能客服业务层

> Status: ready-for-agent
> Type: spec
> 来源: `CharAgent/docs/adr/0008`（业务对接形态）+ `0009`（委托身份与写操作分级）+ 13 轮设计访谈（2026-09-18）
> 编制: 2026-09-18 | 关联: `CharAgent/`（框架，业务无关）、`app/minimall/`（被对接的业务系统）

---

## 1. Problem Statement

原 P1-13「客服 demo」以纯学习目的设计：工具**只读直连 minimall 的 MySQL**（演示难点 #24 最小权限），业务表自建，转人工走 Postgres。该形态能覆盖框架难点，但作为「企业级电商智能客服」的对照物不成立——**企业里 agent 从不直连业务库**，而是经内部网关调业务系统的 API；身份、限流、审计、写操作审批各有其位、各司其职。

盘点 minimall 现状后差距更明显：

| 原计划依赖的能力 | minimall 现状 |
|---|---|
| 代表用户调 API | 24 条路由全是买家自用视角（`user=request.user`），**仅 Session Cookie 认证**，无 Token/JWT |
| 商品推荐 | **无端点**（`is_featured` 字段存在但未暴露为 filter，只有 HTML 页面层在用） |
| 退款 | **无端点**（`Order.Status.REFUNDED` 枚举值存在，但全仓库无代码路径能置为该状态） |
| 物流轨迹 | **无数据源**（发货仅 Django admin 的 `queryset.update()` 批量动作，`ship_order()` 无 REST 出口） |
| 余额变动可审计 | **无账本、无幂等**；`pay_order` 无 `select_for_update`，`RechargeView` 无 `atomic()` |

**结论**：把业务 demo 从「框架里的一个任务」提升为独立子项目 `CharService/`，按企业形态重构对接方式。框架 `CharAgent/` 保持业务无关，P2 的 11 个插件不受影响（见 §4.8）。

## 2. Solution

**三层对接 + 工具集 + 前后端**，垂直切片推进（§4.8）。

```
用户渠道（模拟 App 已登录）
   │  user_id
   ▼
CharService 主服务 ─── CharAgent/server（通用运行时：会话 / run / SSE / 取消）
   │  ① 会话绑定身份 → 身份服务换委托 token
   │  ② agent loop 调工具（工具签名里没有 user_id）
   ▼
工具集（薄封装：参数校验 + 调网关 + 结果格式化）
   │  Authorization: Bearer <委托 token>
   ▼
内部 API 网关（唯一入口：认证 + 限流 + 双粒度审计 + 转发）
   │
   ▼
app/minimall /api/minimall/internal/support/*（按任务粒度设计的端点）
```

**四条不可动摇的约束**：

1. agent 不持有任何业务凭证、不知道业务系统地址——工具只能调网关
2. `user_id` 绝不进入模型可见的工具签名（防 prompt injection 越权）
3. agent 无 token 签发权（身份服务持私钥，网关只持公钥）
4. 写操作按三级分流，L2 走确认（内部审批 / 用户确认两条不同路径）

## 3. User Stories

### 渠道（模拟 App）

1. 作为渠道，我想在创建会话时声明 `user_id`（模拟用户已登录），以便 agent 知道代表谁操作。
2. 作为渠道，我想让这条身份声明**无法被模型篡改**，以便注入攻击不能越权。

### 客服用户（/chat 端）

3. 作为客服用户，我想咨询商品（有哪些、多少钱、有没有货、推荐什么），以便决定是否购买。
4. 作为客服用户，我想查询自己的订单列表与详情（含物流与售后资格），以便了解订单状态。
5. 作为客服用户，我想把商品加入购物车，以便继续购买流程。
6. 作为客服用户，我想提交退款申请，以便在符合规则时尽快拿到退款。
7. 作为客服用户，我想知道退款是「自动受理」还是「已转人工审核」，以便了解后续预期。
8. 作为客服用户，我想在终态操作（如取消订单）前看到**确认卡片**（金额、影响），点击确认后才执行，以便误操作不会造成损失。
9. 作为客服用户，我想查询退款单进度（`refund_id`），以便确认是否到账。
10. 作为客服用户，我想在无法自助解决时一键转人工，以便获得人工帮助。
11. 作为客服用户，我想在系统降级（模型挂 / 检索失败 / 网关不可用）时仍得到模板回复或 FAQ 匹配，以便服务不中断。

### 主管 / 风控（/admin 审批台）

12. 作为主管，我想看到挂起的退款申请（含金额、订单上下文、用户历史退款次数），以便判断是否批准。
13. 作为主管，我想批准或拒绝退款申请，以便挂起的 run 从断点恢复执行或回填拒绝原因。
14. 作为主管，我想看到审批超时（默认 15 分钟）自动拒绝的记录，以便处理遗留挂起。
15. 作为**风控**，我想确认**客服坐席不能审批自己发起的退款**，以便职责分离成立。

### 人工客服（/admin 接管台）

16. 作为人工客服，我想在接管台查看转人工会话的完整历史，以便了解用户问题全貌。
17. 作为人工客服，我想在接管后直接回复用户，以便继续服务。

### 平台 / 运维方

18. 作为运维方，我想让限流与审计**不可绕过**（网关是唯一入口），以便策略真正生效。
19. 作为运维方，我想看到双粒度审计（工具调用 + 数据访问），以便回答「谁在什么时候看了谁的什么数据」。
20. 作为运维方，我想让审计标记出敏感字段命中（手机号 / 地址 / 余额），以便合规留痕与追责。
21. 作为运维方，我想在业务系统故障时熔断并降级，以便客服服务不中断。

### 学习者

22. 作为学习者，我想对照企业级形态逐个实现并打勾（内部 API / 网关 / 身份服务 / 委托 token / 写操作分级 / 双粒度审计），以便讲清「为什么企业要这么设计」而非只是「能跑通」。
23. 作为学习者，我想在面试前用 CharService 回答「agent 怎么对接已有业务系统」这一类问题，以便把架构决策讲成有取舍的故事。

## 4. Implementation Decisions

> 全部决策来源：ADR-0008 / ADR-0009。冲突时以 ADR 为准。

### 4.1 目录与进程

```
CharService/
├── api/          业务端点（审批台 / 接管台 / 工单）——通用运行时端点由 CharAgent/server 提供
├── channels/     模拟渠道网关（确定 user_id + 建立会话）
├── identity/     身份服务（RS256 签发委托 token）   独立进程 :10072
├── gateway/      内部 API 网关（认证 + 限流 + 审计 + 转发）独立进程 :10071
├── tools/        @tool 工具集（薄封装）
├── frontend/     Vue 3（/chat + /admin）           :10079
├── docs/         本子项目文档
└── tests/
```

**组装方式**：`CharAgent/server/` 以 **router 工厂**形式交付（`create_runtime_router(deps)`），CharService 主服务 `:10070` 组装成自己的 FastAPI app。对外只有一个主服务进程，网关与身份服务是独立进程（信任边界 = 进程边界）。

**端口**：主服务 10070 / 网关 10071 / 身份服务 10072 / 前端 10079（**1007x 段**，与其他子项目的 8000 / 8100 / 8200 / 9004 段分开；2026-09-18 已核实均未被占用）。

### 4.2 三层对接

| 层 | 位置 | 职责 |
|----|------|------|
| 业务系统 | `app/minimall/api/minimall/internal/support/` | 新增 `/api/minimall/internal/support/*` 命名空间，**按任务粒度**设计（`orders/{no}/support-view` 一次返回订单 + 物流 + 售后资格）；**补齐缺失能力**：退款单（含状态机）+ 余额流水 + 推荐查询 + 发货端点 + 物流轨迹数据源 |
| 内部网关 | `CharService/gateway/` | agent 的**唯一入口**：验委托 token → 限流 → 双粒度审计 → 转发。工具层代码里没有 minimall 的地址与凭证，**想绕也没东西可绕** |
| 身份服务 | `CharService/identity/` | 把会话绑定的 `user_id` 换成委托 token（`sub` / `act` / `aud` / `tenant` / `exp`），RS256 签发；agent 无签发权 |

**不做 1:1 映射**：工具的形状由**任务**决定，不由资源的 REST 形状决定。反例：让模型自己串 `get_order` → 判状态 → 算可退金额 → 调 `cancel_order`。

**业务规则归业务系统**：「能否退款」「可退多少」「什么状态可取消」一律由 minimall 的 service 层判断，网关与工具都不复制这些规则。

### 4.3 工具集（12 个，按风险分级）

| 级别 | 工具 | 说明 |
|------|------|------|
| L0 只读 | `search_products(query, category?, min_price?, max_price?)` | 商品检索 |
| L0 | `get_product_detail(product_slug)` | 商品详情 + 库存 |
| L0 | `recommend_products(context?)` | 推荐（售前） |
| L0 | `list_my_orders()` | 我的订单（隐含 current principal） |
| L0 | `get_order_detail(order_no)` | 订单 + **物流轨迹** + 售后资格，一次拿全 |
| L0 | `search_faq(query)` | 售后知识库检索（RAG，Milvus） |
| L0 | `get_my_profile()` | 账户信息（含余额） |
| L0 | `get_refund_status(refund_id)` | 退款单进度（在 issue 07 随退款链路一起建） |
| L1 可逆写 | `add_to_cart(product_id, quantity)` | 直接执行 + 回显；**不走确认卡片** |
| L2 内部审批 | `submit_refund(order_no, reason)` | 金额分层：≤ 阈值自动通过；超阈值挂起待审批 |
| L2 用户确认 | `cancel_order(order_no)` | 返回 `pending_confirmation` + `confirm_token` |
| 流程 | `escalate_to_human(reason)` | 转人工（建 Ticket + Escalation） |

**工具签名里没有 `user_id`**——它由运行时经 `Injected` 注入（ADR-0009 的防注入性质 + ADR-0010 的注入通道）：

```python
@tool
def get_order_detail(
    order_no: Annotated[str, Field(pattern=r"^\d{14}$")],
    ctx: Annotated[ToolContext, Injected()],   # ← 不进 schema，模型看不到
) -> str | Suspension: ...
```

**物流轨迹并入 `get_order_detail`（2026-09-18）**：原独立工具 `track_logistics` 被删。两个理由——① 与 `get_order_detail` 职责重叠（后者本就返回物流概要），单列会让模型在选择上产生歧义；② minimall 没有物流数据源，独立工具意味着为它专造一套「模拟承运商」数据，学习价值低（造 mock 不是 agent 工程的难点）、真实系统也不自建（对接承运商 API）。合并不减能力：仍然「按任务粒度一次拿全」。

### 4.4 委托身份与认证

- **身份在渠道层确定**：`POST /sessions` 带 `user_id`（模拟 App 已鉴权），服务端向身份服务换取委托 token 存入会话上下文
- **两跳认证**：agent 用服务凭证向身份服务认证 → 身份服务校验「agent 有权代表该用户操作」→ 签发委托 token（`sub`=用户 / `act`=support-agent / `aud`=minimall-internal / `tenant` / `exp`）
- **agent 无签发权**：私钥只在身份服务，网关只持公钥验签——即使 agent 进程被完全控制也造不出 `sub=别人` 的 token
- **token 有效期短（默认 15 分钟）→ 必须在会话中途续期**：客服会话跨越 15 分钟是常态（用户慢慢打字、来回确认），不能让工具调用在第 16 分钟开始全线 401。机制落在 **`ToolContext` 的构造时机**——它由应用在每次 run 前构造（ADR-0010），主服务持一个**凭据提供者**：构造前检查剩余有效期，不足阈值就向身份服务重换，工具执行中拿到的永远是有效凭据。**「会话建立时换一次然后锁死」是不行的**；具体阈值与重试策略在 issue 01 落地

### 4.5 写操作分级与两条确认路径

| 级别 | 判定 | 路径 |
|------|------|------|
| L0 只读 | 无副作用 | 自由调用 |
| L1 可逆写 | 可逆、无资金影响 | 直接执行 + 回显。**约束：必须有明确的用户意图**——不得在推荐商品时顺手加购 |
| L2 终态 / 资金 | 不可逆或触及资金 | 挂起，走确认（下列两条之一） |

**两条确认路径语义不同，代码上不得耦合**：

- **内部审批**（HITL）：金额分层 + **角色分离**。客服坐席只能发起，**主管 / 风控**才能批准；挂起点持久化，审批后从断点恢复执行（不重跑），超时（默认 15 分钟）自动拒绝
- **用户确认**（结构化）：工具返回 `pending_confirmation` + `confirm_token`（含操作摘要哈希 + 过期时间），前端渲染确认卡片，用户点击后带凭据再次提交，**网关校验凭据才执行**。模型无法自己「确认」，用户说「我确认」也无效

### 4.6 审计（双粒度）

| 粒度 | 位置 | 记录 |
|------|------|------|
| 工具调用 | 工具层 | 谁 / 代表谁 / 工具名 / 参数摘要（脱敏）/ 耗时 / 结果状态 |
| 数据访问 | 网关层 | 实际访问的业务资源（订单号、商品 id）+ **命中的敏感字段**（手机号 / 地址 / 余额）+ 放行决定 |

落 Postgres（`audit_logs`，与 checkpoint 同库、同 alembic 体系）。前置脱敏：写审计前 PII 已脱敏。

### 4.7 数据落点

| 数据 | 落点 | 归属 |
|------|------|------|
| Ticket / Escalation / Approval / AuditLog | Postgres（`charservice_` 前缀，**CharService 自有迁移链 + 独立版本表 `charservice_alembic_version`**） | CharService |
| Order / OrderItem / 商品 / 余额 | MySQL（minimall，**经 internal API 访问，不直连**） | minimall |
| 退款单 + 余额流水 | MySQL（minimall 侧新增——退款是它对自身数据的变更，不属于客服系统） | **minimall** |
| 退款后台 worker | **minimall 侧**（management command 或后台线程）——它要「同一事务内改余额 + 写流水 + 置状态」，必须与 MySQL 同进程；若放 CharService 就得开一个业务库连接，违反 ADR-0008 | **minimall** |
| 售后知识库向量 | Milvus（`support_knowledge`） | CharService |
| checkpoint / 会话五实体 | Postgres + Redis（CharAgent 框架，P0 已交付） | CharAgent |

**迁移归属（2026-09-18 定）**：CharService 与 CharAgent **共用同一个 PG 库，但各自独立迁移链与版本表**——业务表定义不进 `CharAgent/alembic/versions/`，两条链互不干涉，表名靠前缀避免互撞（见 `CharAgent/docs/design/06-boundaries.md` §6.6）。

### 4.8 分期（垂直切片，10 个 issue）

| # | 切片 | 验收 |
|---|------|------|
| 01 | 骨架 + 最窄切片 | 模拟渠道 → 身份服务 → agent → `get_order_detail` → 网关 → minimall internal → 返回，**一条真实链路端到端跑通** |
| 02 | minimall internal/support 只读端点全集 | 商品检索 / 详情 / 推荐 / 用户订单列表 / 物流 / 发货端点全部可用 + 测试 |
| 03 | 只读工具集铺开 | agent 能回答售前咨询 + 售后查询全场景 |
| 04 | 网关补齐：限流 + 双粒度审计 | 超限 429；审计能回答「谁看了谁的什么数据」 |
| 05 | L1 写：加购 | 加购出现在购物车；无明确意图时不调用 |
| 06 | minimall 退款能力 | refund 表 + 状态机 + 余额流水 + service + **后台 worker（在 minimall 侧，不在 CharService）**；幂等重放不产生重复退款 |
| 07 | L2 写 · 退款链路 | 小额自动通过；大额挂起待审批；拒绝回填；超时降级；Saga 补偿 |
| 08 | L2 写 · 取消订单 + 结构化用户确认 | 模型无法自己确认；凭据过期失效 |
| 09 | 转人工 + 接管台 | Ticket + Escalation + 人工接管 + reply 端到端 |
| 10 | Vue 前端（/chat + /admin） | SSE 渐进渲染 + 确认卡片 + 审批台角色分离 |

**P2 不受影响**：CharAgent 的 11 个 P2 插件一个都不改。耦合是单向的——P2 消费 P1 的产物（`CharService` 是框架的第一个真实使用方，P2 的 eval 题库、observability trace、memory 多租户终于有真实数据来源）。

### 4.9 对 CharAgent P1 issue 的影响

| Issue | 影响 |
|-------|------|
| P1-1 server | **拆分**：通用运行时端点（会话 / run / SSE / 取消）留 CharAgent，以 router 工厂交付；业务端点去 CharService |
| P1-3 熔断 / P1-8 降级 | 小改：新增「网关 / 下游业务系统故障」分支（业务侧消费方是 issue 09 的「降级路径汇入转人工」，故 09 的 `Blocked by` 含这两个） |
| P1-4 幂等 / Saga / 锁 | 加强：异步退款是 Saga 的真实施展场景 |
| P1-5 限流 | 位置移到网关侧（框架仍交付 `RateLimiter` 实现） |
| P1-6 沙箱 / P1-7 HITL 脱敏审计 | **方案改写**：沙箱从「只读 MySQL」改为「服务身份 + 委托 token + 网关唯一入口」；审批加角色分离与金额分层；审计加数据访问粒度 |
| P1-2 状态机 | 小改：新增两种挂起类型（内部审批 / 用户确认） |
| P1-13 客服 demo / P1-14 前端 | **整个移交 CharService** |
| P1-9 / P1-10 / P1-11 / P1-12 | 不动 |

## 5. Testing Decisions

> 原则同 CharAgent：测试测「代码对不对」（确定性，可 mock）；评估测「输出好不好」（非确定性，P2）。

### 5.1 测试切入点

| Seam | 位置 | 用途 |
|------|------|------|
| **网关 HTTP 边界** | gateway app | 工具层测试打桩网关；网关自身测试打桩 minimall |
| **身份服务签发边界** | identity app | 主服务测试打桩签发；身份服务测「agent 无权代某用户」「token 过期」「签名错误」 |
| **minimall internal API** | Django 侧 | internal 端点独立测试（含权限、幂等、状态机非法流转） |
| **ChatModel 协议** | CharAgent `model.py` | 沿用 P0 的 MockLLM 三模式，agent 侧零改动 |
| **工具注册边界** | `@tool` | 工具函数直调测试（参数校验、结果格式化） |

### 5.2 必须覆盖的安全用例

- **越权**：持用户 A 的 token 访问用户 B 的订单 → 网关拒绝（不是业务系统兜底）
- **伪造**：agent 侧自签 token → 网关验签失败
- **注入**：对话中诱导模型填 `user_id` → 工具签名里没有该参数，无处可填
- **确认绕过**：模型尝试自行「确认」→ 无 `confirm_token` 则拒绝
- **重复退款**：同 `request_id` 重放 → 返回已有退款单，不产生第二次副作用
- **限流绕过**：工具层直连 minimall → 地址与凭证不存在，物理不可达
- **职责分离**：客服角色调审批端点 → 403

### 5.3 分层

单元（分层规则 / token 校验 / 限流算法 / 脱敏）→ 集成（网关 ↔ minimall internal）→ E2E（渠道 → agent → 工具 → 网关 → minimall，mock LLM + 打桩外部服务）→ 前端（Vitest 可选）。

## 6. Out of Scope

- **下单 / 代客下单**：明确不做（涉及支付与合同成立），引导用户自己在商城完成
- **真实支付渠道**：退款走 minimall 余额退回，不接支付宝 / 微信 / 银行
- **OAuth2 授权服务器**（Keycloak 等）：双主体语义用自签 JWT claim 表达即可
- **服务网格 / 现成网关**（Istio / Kong / APISIX）：网关自研，学习重心在实现
- **真实承运商对接**：物流轨迹由本地模拟数据源提供
- **多租户管理后台**：`tenant` 进 token 与 Milvus 过滤字段，但不做租户开通 / 配置界面
- **容器编排 / 水平扩展**：单机多进程；无状态化与 drain 属 CharAgent P2-10
- **修改 minimall 的买家端点**：只新增 internal/support 命名空间，不动既有 24 条路由
- **前端视觉规范**：只定路由与承载能力

## 7. Further Notes

- **前置服务**：MySQL（minimall 库）、Postgres、Redis、Milvus —— 均已有；`.env` 沿用根 `.env.example` 约定，新增 `CHARSERVICE_*` 前缀
- **启动**：主服务 `sh/charservice_backend.sh`（:10070）、网关与身份服务随主服务脚本一并拉起、前端 `sh/charservice_frontend.sh`（:10079）
- **依赖**：复用根 `.venv`；新增 `PyJWT`（RS256 签发验签）
- **文档体系**：本 PRD + `.scratch/CharService/issues/`（10 个垂直切片）+ `CharService/docs/`（实施中按需建 CONTEXT / DESIGN）
- **实施起点**：issue 01（骨架 + 最窄切片）
