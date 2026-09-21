# CharApp — 电商智能客服

基于 `CharAgent` 通用 agent 框架构建的业务应用集合。当前只有一个上下文 `minimall` (包 `CharApp/minimall/`), 对接 `app/minimall` 的商城数据。

## Language

### 售后域（AfterSale）

**退款（Refund）**：
钱退回买家，不涉及货物寄回。`RefundRequest` 只表达退款。
_Avoid_：售后、退货 —— 三者含义不同，不可互换

**退货退款（Return & Refund）**：
货物寄回并经卖家签收后，钱才退回买家。需要退货地址、承运商、物流单号。
_Avoid_：退款 —— 本项目未实现退货退款，且 minimall 无任何物流字段

**售后（AfterSale）**：
退款、退货退款、换货、维修的上位概念。
_Avoid_：客服、工单 —— 本项目只用它作域名词（命名空间），不建对应模型

**投诉（Complaint）**：
买家对商品或服务不满的表达，不涉及资金流转。
**不实施**（2026-09-21 决定）—— 保留此词只为消歧：它不是售后、也不是退款。
_Avoid_：售后 —— 投诉与退款无状态耦合，是两个独立模型

### 角色

**买家（Buyer）**：
持有 `django.contrib.auth.User` 的注册用户，通过 `Profile` 持有余额，是唯一能发起下单与退款的角色。
_Avoid_：用户、客户

**管理员（Staff）**：
`User.is_staff=True` 的账号，只能在 Django Admin 中操作（含审批退款、发货）。
_Avoid_：卖家、商家 —— **本项目不存在卖家角色**，不得假设其存在

### 交易

**余额（Balance）**：
`Profile.balance`，买家预存的可支付金额，注册时赠送 10000。支付与充值都只在此字段上增减。
_Avoid_：钱包、账户余额

**支付密码（Payment Password）**：
6 位数字，与登录密码分离，仅在支付与充值时校验。
**助手不代付**（2026-09-21 决定）—— 校验需要明文经手，与「身份不进工具参数表」不可调和；
若要代付，正确做法是让用户在页面上**本人**输入，属 L3 的框架级挂起。
_Avoid_：交易密码

**取消（Cancel）**：
买家单方终止订单。仅 `pending` / `paid` 两态可取消；已支付订单取消会退回余额并回滚库存。
_Avoid_：退款 —— 取消不需要审批，退款需要

**退款申请（RefundRequest）**：
买家对已支付订单发起的退款请求。状态机 `requested → approved → refunded`，或 `requested → rejected`。
**退多少由管理员在批准时定**（协商金额，不超过订单总额）；买家申请时不带金额。
同一订单同一时刻只允许一个进行中的申请；被驳回后可以再提。
_Avoid_：退款单、售后单

**退款中（Refunding）**：
订单在退款流程进行中的状态。申请退款即进入，打款后变「已退款」，被驳回则**恢复申请前的状态**。
_Avoid_：申请中、处理中 —— 它描述的是**订单**的状态，不是退款申请的状态（后者叫 `requested`）

### 集成

**内部端点（Agent Endpoint）**：
`app/minimall/views_agent.py` 暴露给 CharApp 的粗粒度业务动作，以 `X-Internal-Token` 认证。
_Avoid_：管理 API、内部 API —— 它是「面向 agent 的业务动作」，不是用户面接口的镜像

**CharAgent**：
通用 agent 框架，对业务零知识 —— 不 import 任何 `CharApp` / Django / minimall 代码。
_Avoid_：把业务代码放进 CharAgent

**CharApp**：
基于 CharAgent 构建的业务应用。依赖方向严格单向 `CharApp → CharAgent`。
