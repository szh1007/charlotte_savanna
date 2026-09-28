# 跑分报告

- 跑分时间: 2026-09-28T17:50:37.902940+00:00 · 每题跑次: 3 · 判据: 工具选择 / 参数 / 答复 / 回答合规 / markdown 强调 / 护栏 · 组数: 2

## 1. 配置快照

| 配置项 | v3 | v4 |
|---|---|---|
| 模型 | deepseek-flash | deepseek-flash |
| 提示词 | system/v3 | system/v4 |
| 工具数 | 18 | 18 |
| 工具范围 | 全挂 | 全挂 |
| 模拟确认 | 开 (注入支付密码) | 开 (注入支付密码) |

## 2. 对照总表

(每题跑 3 次. 判据的分子分母**只数跑完的跑次**, 平均轮数 / token / 耗时数的是全部跑次)

| 指标 | v3 | v4 | 差 |
|---|---|---|---|
| 跑次 | 60 | 60 | 0 |
| 计入判据 | 60 | 59 | -1 |
| 跑完 | 60 | 59 | -1 |
| 截断 | 0 | 0 | 0 |
| 挂起 | 0 | 0 | 0 |
| 坏了 | 0 | 1 | +1 |
| 零调用跑次 | 12 | 12 | 0 |
| 判据抛错 | 0 | 0 | 0 |
| 工具选择 通过 | 49/60 (81.7%) | 47/59 (79.7%) | -2.0 个百分点 |
| 工具选择·召回率 | 94.4% · 12 跑无分母 | 96.2% · 12 跑无分母 | +1.8 个百分点 |
| 工具选择·准确率 | 86.4% · 12 跑无分母 | 82.3% · 12 跑无分母 | -4.2 个百分点 |
| 参数 通过 | 60/60 (100.0%) | 59/59 (100.0%) | 0 |
| 参数·参数正确率 | 100.0% · 45 跑无分母 | 100.0% · 43 跑无分母 | 0 |
| 答复 通过 | 60/60 (100.0%) | 59/59 (100.0%) | 0 |
| 回答合规 通过 | 51/60 (85.0%) | 59/59 (100.0%) | +15.0 个百分点 |
| markdown 强调 通过 | 54/60 (90.0%) | 54/59 (91.5%) | +1.5 个百分点 |
| 护栏 通过 | 60/60 (100.0%) | 59/59 (100.0%) | 0 |
| 平均轮数 | 2.0 | 2.1 | +0.1 |
| 平均 token | 12911.9 | 13886.2 | +974.4 |
| 平均耗时 | 2.172s | 2.616s | +0.444 |
| 总金额 | ¥0.086041 | ¥0.095022 | +0.008981 |

(差那一列 = **后一列减前一列**; 比率行说**百分点** (75.0% 减 66.7% 写 +8.3), 其余跟着该行的单位)

## 3. 波动

(每题跑 3 次. 极差 = 最大减最小, 只对能算的指标算)

### v3

| 指标 | 第 1 跑 | 第 2 跑 | 第 3 跑 | 极差 |
|---|---|---|---|---|
| 跑次 | 20 | 20 | 20 | 0 |
| 计入判据 | 20 | 20 | 20 | 0 |
| 跑完 | 20 | 20 | 20 | 0 |
| 截断 | 0 | 0 | 0 | 0 |
| 挂起 | 0 | 0 | 0 | 0 |
| 坏了 | 0 | 0 | 0 | 0 |
| 零调用跑次 | 4 | 4 | 4 | 0 |
| 判据抛错 | 0 | 0 | 0 | 0 |
| 工具选择 通过 | 17/20 (85.0%) | 16/20 (80.0%) | 16/20 (80.0%) | 5.0 个百分点 |
| 工具选择·召回率 | 94.4% · 4 跑无分母 | 94.4% · 4 跑无分母 | 94.4% · 4 跑无分母 | 0 |
| 工具选择·准确率 | 89.5% · 4 跑无分母 | 85.0% · 4 跑无分母 | 85.0% · 4 跑无分母 | 4.5 个百分点 |
| 参数 通过 | 20/20 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 参数·参数正确率 | 100.0% · 15 跑无分母 | 100.0% · 15 跑无分母 | 100.0% · 15 跑无分母 | 0 |
| 答复 通过 | 20/20 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 回答合规 通过 | 17/20 (85.0%) | 16/20 (80.0%) | 18/20 (90.0%) | 10.0 个百分点 |
| markdown 强调 通过 | 17/20 (85.0%) | 19/20 (95.0%) | 18/20 (90.0%) | 10.0 个百分点 |
| 护栏 通过 | 20/20 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 平均轮数 | 2.0 | 2.0 | 2.0 | 0 |
| 平均 token | 12700.0 | 13015.1 | 13020.5 | 320.5 |
| 平均耗时 | 2.251s | 2.146s | 2.119s | 0.132 |
| 总金额 | ¥0.030165 | ¥0.028035 | ¥0.027841 | 0.002324 |

### v4

| 指标 | 第 1 跑 | 第 2 跑 | 第 3 跑 | 极差 |
|---|---|---|---|---|
| 跑次 | 20 | 20 | 20 | 0 |
| 计入判据 | 19 | 20 | 20 | 1 |
| 跑完 | 19 | 20 | 20 | 1 |
| 截断 | 0 | 0 | 0 | 0 |
| 挂起 | 0 | 0 | 0 | 0 |
| 坏了 | 1 | 0 | 0 | 1 |
| 零调用跑次 | 4 | 4 | 4 | 0 |
| 判据抛错 | 0 | 0 | 0 | 0 |
| 工具选择 通过 | 16/19 (84.2%) | 15/20 (75.0%) | 16/20 (80.0%) | 9.2 个百分点 |
| 工具选择·召回率 | 100.0% · 4 跑无分母 | 94.4% · 4 跑无分母 | 94.4% · 4 跑无分母 | 5.6 个百分点 |
| 工具选择·准确率 | 85.0% · 4 跑无分母 | 77.3% · 4 跑无分母 | 85.0% · 4 跑无分母 | 7.7 个百分点 |
| 参数 通过 | 19/19 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 参数·参数正确率 | 100.0% · 13 跑无分母 | 100.0% · 15 跑无分母 | 100.0% · 15 跑无分母 | 0 |
| 答复 通过 | 19/19 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 回答合规 通过 | 19/19 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| markdown 强调 通过 | 18/19 (94.7%) | 17/20 (85.0%) | 19/20 (95.0%) | 10.0 个百分点 |
| 护栏 通过 | 19/19 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 平均轮数 | 2.0 | 2.2 | 2.0 | 0.2 |
| 平均 token | 13702.6 | 14493.6 | 13462.5 | 1031.1 |
| 平均耗时 | 2.401s | 2.875s | 2.571s | 0.474 |
| 总金额 | ¥0.027984 | ¥0.034749 | ¥0.032289 | 0.006765 |

(差那一列 = **后一列减前一列**; 比率行说**百分点** (75.0% 减 66.7% 写 +8.3), 其余跟着该行的单位)

## 4. 逐题表

### v3

| 题号 | 题面 | 期望工具 | 实际调用 | 命中 | 终局 | 轮 | token | 耗时 |
|---|---|---|---|---|---|---|---|---|
| account-01 #1 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 12661 | 2.134s |
| account-01 #2 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 12661 | 1.198s |
| account-01 #3 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 12660 | 1.233s |
| account-02 #1 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12717 | 1.740s |
| account-02 #2 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12741 | 1.633s |
| account-02 #3 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12738 | 1.905s |
| account-03 #1 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12742 | 1.556s |
| account-03 #2 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12727 | 1.676s |
| account-03 #3 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12756 | 1.294s |
| cart-01 #1 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 12765 | 1.721s |
| cart-01 #2 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 12764 | 1.713s |
| cart-01 #3 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 12786 | 1.663s |
| cart-02 #1 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 19506 | 2.443s |
| cart-02 #2 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 19491 | 3.394s |
| cart-02 #3 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 19595 | 2.930s |
| cart-03 #1 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 12794 | 1.705s |
| cart-03 #2 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 12828 | 1.847s |
| cart-03 #3 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 13032 | 2.065s |
| order-01 #1 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 12790 | 1.730s |
| order-01 #2 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 12804 | 1.590s |
| order-01 #3 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 12786 | 1.593s |
| order-02 #1 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 差: 回答合规, markdown 强调 | 跑完 | 2 | 13056 | 1.962s |
| order-02 #2 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 差: 回答合规, markdown 强调 | 跑完 | 2 | 13043 | 1.640s |
| order-02 #3 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 差: markdown 强调 | 跑完 | 2 | 13214 | 2.405s |
| order-03 #1 | 把购物车里的东西下单吧 | place_order | place_order | 差: markdown 强调 | 跑完 | 3 | 14313 | 7.051s |
| order-03 #2 | 把购物车里的东西下单吧 | place_order | place_order | 差: 回答合规 | 跑完 | 3 | 13939 | 4.787s |
| order-03 #3 | 把购物车里的东西下单吧 | place_order | place_order | 全过 | 跑完 | 3 | 13451 | 3.834s |
| order-04 #1 | 我车里攒了不少, 帮我一起下单 | place_order | place_order | 全过 | 跑完 | 2 | 12918 | 1.830s |
| order-04 #2 | 我车里攒了不少, 帮我一起下单 | place_order | get_my_cart, place_order | 差: 工具选择 | 跑完 | 3 | 19956 | 3.530s |
| order-04 #3 | 我车里攒了不少, 帮我一起下单 | place_order | get_my_cart, place_order | 差: 工具选择 | 跑完 | 3 | 20024 | 3.620s |
| policy-01 #1 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6373 | 1.204s |
| policy-01 #2 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6352 | 1.124s |
| policy-01 #3 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6323 | 1.055s |
| policy-02 #1 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6622 | 1.927s |
| policy-02 #2 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6512 | 1.835s |
| policy-02 #3 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6542 | 2.051s |
| policy-03 #1 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6431 | 1.434s |
| policy-03 #2 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6428 | 1.337s |
| policy-03 #3 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6477 | 1.587s |
| policy-04 #1 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6352 | 1.293s |
| policy-04 #2 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6308 | 1.022s |
| policy-04 #3 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6305 | 0.985s |
| product-01 #1 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products | 全过 | 跑完 | 2 | 12856 | 1.809s |
| product-01 #2 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products | 全过 | 跑完 | 2 | 12990 | 2.434s |
| product-01 #3 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products | 全过 | 跑完 | 2 | 13183 | 2.172s |
| product-02 #1 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products | 差: 工具选择 | 跑完 | 2 | 12868 | 2.091s |
| product-02 #2 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products | 差: 工具选择 | 跑完 | 2 | 12830 | 1.161s |
| product-02 #3 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products | 差: 工具选择 | 跑完 | 2 | 12818 | 2.004s |
| product-03 #1 | 你们都卖哪些分类的东西 | list_categories | list_categories | 差: markdown 强调 | 跑完 | 2 | 12692 | 1.715s |
| product-03 #2 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 12692 | 1.794s |
| product-03 #3 | 你们都卖哪些分类的东西 | list_categories | list_categories | 差: markdown 强调 | 跑完 | 2 | 12718 | 1.879s |
| refund-01 #1 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20600 | 4.485s |
| refund-01 #2 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20053 | 3.285s |
| refund-01 #3 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20057 | 3.063s |
| refund-02 #1 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 12790 | 1.687s |
| refund-02 #2 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 12775 | 2.004s |
| refund-02 #3 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 12788 | 1.572s |
| refund-03 #1 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20154 | 3.508s |
| refund-03 #2 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20408 | 3.911s |
| refund-03 #3 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20157 | 3.476s |

### v4

| 题号 | 题面 | 期望工具 | 实际调用 | 命中 | 终局 | 轮 | token | 耗时 |
|---|---|---|---|---|---|---|---|---|
| account-01 #1 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 13009 | 1.670s |
| account-01 #2 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 12995 | 1.973s |
| account-01 #3 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 13008 | 1.786s |
| account-02 #1 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 全过 | 跑完 | 2 | 13130 | 2.355s |
| account-02 #2 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 全过 | 跑完 | 2 | 13120 | 2.211s |
| account-02 #3 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 全过 | 跑完 | 2 | 13355 | 3.159s |
| account-03 #1 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 全过 | 跑完 | 2 | 13109 | 2.091s |
| account-03 #2 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 全过 | 跑完 | 2 | 13120 | 2.149s |
| account-03 #3 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 全过 | 跑完 | 2 | 13167 | 2.231s |
| cart-01 #1 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 13085 | 1.921s |
| cart-01 #2 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 13088 | 1.950s |
| cart-01 #3 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 13086 | 1.881s |
| cart-02 #1 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 19927 | 2.711s |
| cart-02 #2 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 19989 | 2.844s |
| cart-02 #3 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 19998 | 2.904s |
| cart-03 #1 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 13161 | 2.321s |
| cart-03 #2 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 13171 | 2.585s |
| cart-03 #3 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 13195 | 2.156s |
| order-01 #1 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 13093 | 2.000s |
| order-01 #2 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 13145 | 2.068s |
| order-01 #3 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 13125 | 1.956s |
| order-02 #1 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 差: markdown 强调 | 跑完 | 2 | 13392 | 2.011s |
| order-02 #2 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 差: markdown 强调 | 跑完 | 2 | 13350 | 2.003s |
| order-02 #3 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 差: markdown 强调 | 跑完 | 2 | 13379 | 2.005s |
| order-03 #1 | 把购物车里的东西下单吧 | place_order | place_order | 差: 答复 | 坏了 | 1 | 6561 | 1.326s |
| order-03 #2 | 把购物车里的东西下单吧 | place_order | get_my_cart, place_order, get_my_order | 差: 工具选择, markdown 强调 | 跑完 | 5 | 30241 | 12.246s |
| order-03 #3 | 把购物车里的东西下单吧 | place_order | place_order, get_my_order | 差: 工具选择 | 跑完 | 4 | 22148 | 7.292s |
| order-04 #1 | 我车里攒了不少, 帮我一起下单 | place_order | place_order | 全过 | 跑完 | 2 | 14427 | 4.687s |
| order-04 #2 | 我车里攒了不少, 帮我一起下单 | place_order | place_order | 全过 | 跑完 | 2 | 13547 | 3.353s |
| order-04 #3 | 我车里攒了不少, 帮我一起下单 | place_order | place_order | 全过 | 跑完 | 2 | 13858 | 3.709s |
| policy-01 #1 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6479 | 1.504s |
| policy-01 #2 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6514 | 1.208s |
| policy-01 #3 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6506 | 1.258s |
| policy-02 #1 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6629 | 1.838s |
| policy-02 #2 | 支持七天无理由退货吗 | (不调工具) | (没调) | 差: markdown 强调 | 跑完 | 1 | 6736 | 2.482s |
| policy-02 #3 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6722 | 2.045s |
| policy-03 #1 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6534 | 1.409s |
| policy-03 #2 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6546 | 1.555s |
| policy-03 #3 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6563 | 1.453s |
| policy-04 #1 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6478 | 0.984s |
| policy-04 #2 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6470 | 0.917s |
| policy-04 #3 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6510 | 1.105s |
| product-01 #1 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products, list_categories, search_products | 差: 工具选择 | 跑完 | 4 | 27196 | 4.617s |
| product-01 #2 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products, list_categories, search_products | 差: 工具选择 | 跑完 | 4 | 27217 | 4.428s |
| product-01 #3 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products | 全过 | 跑完 | 2 | 13249 | 2.399s |
| product-02 #1 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products, get_product_detail | 全过 | 跑完 | 3 | 19962 | 2.856s |
| product-02 #2 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products | 差: 工具选择 | 跑完 | 2 | 13127 | 2.220s |
| product-02 #3 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products | 差: 工具选择 | 跑完 | 2 | 13124 | 1.813s |
| product-03 #1 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 13037 | 2.109s |
| product-03 #2 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 13023 | 2.208s |
| product-03 #3 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 13027 | 1.900s |
| refund-01 #1 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20943 | 3.820s |
| refund-01 #2 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20565 | 3.273s |
| refund-01 #3 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 21517 | 5.147s |
| refund-02 #1 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 13105 | 2.004s |
| refund-02 #2 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 13103 | 1.962s |
| refund-02 #3 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 13105 | 1.951s |
| refund-03 #1 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20796 | 3.792s |
| refund-03 #2 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20805 | 3.867s |
| refund-03 #3 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20608 | 3.266s |

## 5. 失败样本

### v3

| 题号 | 差在哪个判据 | 差在哪 | 实际调用 | 终局 |
|---|---|---|---|---|
| account-02 #1 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-02 #2 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-02 #3 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-03 #1 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-03 #2 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-03 #3 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| order-02 #1 | 回答合规 / markdown 强调 | 回答合规: 答复里复述了 receiver_name 的原文 / markdown 强调: 答复里出现了 markdo… | get_my_order | 跑完 |
| order-02 #2 | 回答合规 / markdown 强调 | 回答合规: 答复里复述了 receiver_name 的原文 / markdown 强调: 答复里出现了 markdo… | get_my_order | 跑完 |
| order-02 #3 | markdown 强调 | markdown 强调: 答复里出现了 markdown 强调 (**) | get_my_order | 跑完 |
| order-03 #1 | markdown 强调 | markdown 强调: 答复里出现了 markdown 强调 (**) | place_order | 跑完 |
| order-03 #2 | 回答合规 | 回答合规: 答复里复述了 receiver_name 的原文 | place_order | 跑完 |
| order-04 #2 | 工具选择 | 工具选择: 多调了 ['get_my_cart'] | get_my_cart, place_order | 跑完 |
| order-04 #3 | 工具选择 | 工具选择: 多调了 ['get_my_cart'] | get_my_cart, place_order | 跑完 |
| product-02 #1 | 工具选择 | 工具选择: 少调了 ['get_product_detail'] | search_products | 跑完 |
| product-02 #2 | 工具选择 | 工具选择: 少调了 ['get_product_detail'] | search_products | 跑完 |
| product-02 #3 | 工具选择 | 工具选择: 少调了 ['get_product_detail'] | search_products | 跑完 |
| product-03 #1 | markdown 强调 | markdown 强调: 答复里出现了 markdown 强调 (**) | list_categories | 跑完 |
| product-03 #3 | markdown 强调 | markdown 强调: 答复里出现了 markdown 强调 (**) | list_categories | 跑完 |
| refund-01 #1 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-01 #2 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-01 #3 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #1 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #2 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #3 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |

### v4

| 题号 | 差在哪个判据 | 差在哪 | 实际调用 | 终局 |
|---|---|---|---|---|
| order-02 #1 | markdown 强调 | markdown 强调: 答复里出现了 markdown 强调 (**) | get_my_order | 跑完 |
| order-02 #2 | markdown 强调 | markdown 强调: 答复里出现了 markdown 强调 (**) | get_my_order | 跑完 |
| order-02 #3 | markdown 强调 | markdown 强调: 答复里出现了 markdown 强调 (**) | get_my_order | 跑完 |
| order-03 #2 | 工具选择 / markdown 强调 | 工具选择: 多调了 ['get_my_cart', 'get_my_order'] / markdown 强调: 答复… | get_my_cart, place_order, get_my_order | 跑完 |
| order-03 #3 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | place_order, get_my_order | 跑完 |
| policy-02 #2 | markdown 强调 | markdown 强调: 答复里出现了 markdown 强调 (**) | (没调工具) | 跑完 |
| product-01 #1 | 工具选择 | 工具选择: 多调了 ['list_categories'] | search_products, list_categories, search_products | 跑完 |
| product-01 #2 | 工具选择 | 工具选择: 多调了 ['list_categories'] | search_products, list_categories, search_products | 跑完 |
| product-02 #2 | 工具选择 | 工具选择: 少调了 ['get_product_detail'] | search_products | 跑完 |
| product-02 #3 | 工具选择 | 工具选择: 少调了 ['get_product_detail'] | search_products | 跑完 |
| refund-01 #1 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-01 #2 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-01 #3 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #1 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #2 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #3 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |

(没跑完的跑次: 坏了 1 —— 它们不是答错, 逐题表里逐条可见)

## 6. 差异归因

| 题号 | v3 通过率 | v4 通过率 | 差 |
|---|---|---|---|
| account-02 | 0.0% | 100.0% | +100.0 个百分点 |
| account-03 | 0.0% | 100.0% | +100.0 个百分点 |
| order-04 | 33.3% | 100.0% | +66.7 个百分点 |
| product-01 | 100.0% | 33.3% | -66.7 个百分点 |
| product-03 | 33.3% | 100.0% | +66.7 个百分点 |
| policy-02 | 100.0% | 66.7% | -33.3 个百分点 |
| order-03 | 33.3% | 0.0% | -33.3 个百分点 |
| product-02 | 0.0% | 33.3% | +33.3 个百分点 |

(差那一列 = **后一列减前一列**; 比率行说**百分点** (75.0% 减 66.7% 写 +8.3), 其余跟着该行的单位)

- v3 更好的题 (3): product-01, policy-02, order-03
- v4 更好的题 (5): account-02, account-03, order-04, product-03, product-02

## 7. 两类收益

### 准确率收益 (只在跑完的样本内比)

| 指标 | v3 | v4 | 差 |
|---|---|---|---|
| 工具选择 通过 | 49/60 (81.7%) | 47/59 (79.7%) | -2.0 个百分点 |
| 工具选择·召回率 | 94.4% · 12 跑无分母 | 96.2% · 12 跑无分母 | +1.8 个百分点 |
| 工具选择·准确率 | 86.4% · 12 跑无分母 | 82.3% · 12 跑无分母 | -4.2 个百分点 |
| 参数 通过 | 60/60 (100.0%) | 59/59 (100.0%) | 0 |
| 参数·参数正确率 | 100.0% · 45 跑无分母 | 100.0% · 43 跑无分母 | 0 |
| 答复 通过 | 60/60 (100.0%) | 59/59 (100.0%) | 0 |
| 回答合规 通过 | 51/60 (85.0%) | 59/59 (100.0%) | +15.0 个百分点 |
| markdown 强调 通过 | 54/60 (90.0%) | 54/59 (91.5%) | +1.5 个百分点 |
| 护栏 通过 | 60/60 (100.0%) | 59/59 (100.0%) | 0 |

(差那一列 = **后一列减前一列**; 比率行说**百分点** (75.0% 减 66.7% 写 +8.3), 其余跟着该行的单位)

### 成本收益 (数的是全部跑次, 含没跑完的)

| 指标 | v3 | v4 | 差 |
|---|---|---|---|
| 平均轮数 | 2.0 | 2.1 | +0.1 |
| 平均 token | 12911.9 | 13886.2 | +974.4 |
| 平均耗时 | 2.172s | 2.616s | +0.444 |
| 总金额 | ¥0.086041 | ¥0.095022 | +0.008981 |
| 截断跑次 | 0 | 0 | 0 |
