# 02 — minimall internal/support 只读端点全集

**What to build:** 在 minimall 内补齐客服侧只读能力，全部落在 `/api/minimall/internal/support/*` 命名空间（**不改现有 24 条买家端点**）。商品：检索（复用 `ProductFilter`，新增 `is_featured` 过滤以支撑推荐）、详情（含库存）；用户维度：按 `user_id` 查订单列表（内部端点要能按委托身份过滤，不是 `request.user`）；订单维度：`support-view` 扩展为含**售后资格**（是否在售后期、可退金额上限——规则由 minimall 的 service 层给出，工具与网关不复制）；物流：新增物流轨迹数据源（minimall 现仅有 `shipped_at` 时间戳）与查询端点；发货：把 `ship_order()` 暴露为内部端点（现仅 Django admin 的 `queryset.update()` 批量动作，无 REST 出口，且 admin 动作**未调用该 service 函数**）。所有端点走服务身份 + 委托身份双认证，不复用买家 Session。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 商品：`search_products` / `get_product_detail` 内部端点（含 `is_featured` 推荐过滤）
- [ ] 用户维度：按 `user_id` 查订单列表（委托身份过滤，非 `request.user`）
- [ ] `support-view` 扩展：售后资格 + 可退金额上限（规则来自 service 层）
- [ ] 物流轨迹数据源 + 查询端点（模拟承运商数据）
- [ ] `ship_order()` 暴露为内部端点（并修正 admin action 绕过 service 的既有问题）
- [ ] internal 端点独立测试：权限、非法状态流转、只读边界
- [ ] 确认：现有 24 条买家端点**零改动**
