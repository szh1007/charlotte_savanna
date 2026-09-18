# 03 — 只读工具集铺开（L0）

**What to build:** 把 L0 只读工具铺开（本 issue **7 个**）：`search_products` / `get_product_detail` / `recommend_products` / `list_my_orders` / `get_order_detail`（01 已建）/ `search_faq`（接 Milvus 售后知识库，RAG 链路见 CharAgent P1-9，检索强制 `tenant_id` 过滤）/ `get_my_profile`（含余额）。全部保持薄封装：参数校验 + 调网关 + 结果格式化，**业务规则一律不下沉**。工具描述按 SOTA 写法（docstring 说明「何时用 / 不用 + 返回内容」，每参数 `Annotated` + `Field` 描述与约束）。工具总数到 7 个后需复核模型选择准确率——超过 20 个会显著退化，后续 L1/L2 工具加入时需持续观察。

**2026-09-18 两处调整**：

- **`track_logistics` 删除**，物流轨迹并入 `get_order_detail`（职责重叠 + 避免为它专造一套模拟承运商数据源；理由见 PRD §4.3）
- **`get_refund_status` 移到 issue 07** —— 它依赖退款单（06 才建）。原计划在 03 建一个「查占位退款单」的工具再回头对齐，是为不存在的后端先造前端，时序倒置

**Blocked by:** 02、**CharAgent P1-9**（RAG 检索链路）

**Status:** ready-for-agent

- [ ] 7 个 L0 工具全部注册可用，签名中无 `user_id`（身份经 `Injected` 的 `ctx` 取）
- [ ] `search_faq` 接通 Milvus 检索 + 引用溯源，检索失败走 FAQ 降级
- [ ] 工具描述含「何时不用」的负向指引（防误调用）
- [ ] 售前咨询场景跑通：有哪些商品 / 多少钱 / 有没有货 / 推荐什么
- [ ] 售后查询场景跑通：订单列表 / 订单详情（含物流轨迹）/ 余额
- [ ] 工具层无 minimall 地址与凭证（代码级检查）
