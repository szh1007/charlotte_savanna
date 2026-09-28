# 跑分报告

- 跑分时间: 2026-09-28T15:31:42.011551+00:00 · 每题跑次: 3 · 判据: 工具选择 / 参数 / 答复 / 回答合规 / 护栏 · 组数: 2

## 1. 配置快照

| 配置项 | 全挂 | 按题裁剪 |
|---|---|---|
| 模型 | deepseek-flash | deepseek-flash |
| 提示词 | system/v3 | system/v3 |
| 工具数 | 18 | 18 |
| 工具范围 | 全挂 | 按题裁剪 |
| 模拟确认 | 开 (注入支付密码) | 开 (注入支付密码) |

## 2. 对照总表

(每题跑 3 次. 判据的分子分母**只数跑完的跑次**, 平均轮数 / token / 耗时数的是全部跑次)

| 指标 | 全挂 | 按题裁剪 | 差 |
|---|---|---|---|
| 跑次 | 60 | 60 | 0 |
| 计入判据 | 60 | 60 | 0 |
| 跑完 | 60 | 60 | 0 |
| 截断 | 0 | 0 | 0 |
| 挂起 | 0 | 0 | 0 |
| 坏了 | 0 | 0 | 0 |
| 零调用跑次 | 12 | 12 | 0 |
| 判据抛错 | 0 | 0 | 0 |
| 工具选择 通过 | 46/60 (76.7%) | 47/60 (78.3%) | +1.7 个百分点 |
| 工具选择·召回率 | 98.1% · 12 跑无分母 | 96.3% · 12 跑无分母 | -1.9 个百分点 |
| 工具选择·准确率 | 79.1% · 12 跑无分母 | 80.0% · 12 跑无分母 | +0.9 个百分点 |
| 参数 通过 | 60/60 (100.0%) | 60/60 (100.0%) | 0 |
| 参数·参数正确率 | 100.0% · 43 跑无分母 | 100.0% · 44 跑无分母 | 0 |
| 答复 通过 | 60/60 (100.0%) | 60/60 (100.0%) | 0 |
| 回答合规 通过 | 52/60 (86.7%) | 53/60 (88.3%) | +1.7 个百分点 |
| 护栏 通过 | 60/60 (100.0%) | 60/60 (100.0%) | 0 |
| 平均轮数 | 2.2 | 2.2 | -0.1 |
| 平均 token | 14242.4 | 8865.0 | -5377.4 |
| 平均耗时 | 2.192s | 2.251s | +0.059 |
| 总金额 | ¥0.097837 | ¥0.099859 | +0.002022 |

(差那一列 = **后一列减前一列**; 比率行说**百分点** (75.0% 减 66.7% 写 +8.3), 其余跟着该行的单位)

## 3. 波动

(每题跑 3 次. 极差 = 最大减最小, 只对能算的指标算)

### 全挂

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
| 工具选择 通过 | 16/20 (80.0%) | 15/20 (75.0%) | 15/20 (75.0%) | 5.0 个百分点 |
| 工具选择·召回率 | 100.0% · 4 跑无分母 | 94.4% · 4 跑无分母 | 100.0% · 4 跑无分母 | 5.6 个百分点 |
| 工具选择·准确率 | 78.3% · 4 跑无分母 | 81.0% · 4 跑无分母 | 78.3% · 4 跑无分母 | 2.7 个百分点 |
| 参数 通过 | 20/20 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 参数·参数正确率 | 100.0% · 14 跑无分母 | 100.0% · 15 跑无分母 | 100.0% · 14 跑无分母 | 0 |
| 答复 通过 | 20/20 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 回答合规 通过 | 17/20 (85.0%) | 18/20 (90.0%) | 17/20 (85.0%) | 5.0 个百分点 |
| 护栏 通过 | 20/20 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 平均轮数 | 2.2 | 2.1 | 2.2 | 0.1 |
| 平均 token | 14557.5 | 13687.4 | 14482.2 | 870.2 |
| 平均耗时 | 2.306s | 2.045s | 2.224s | 0.261 |
| 总金额 | ¥0.034790 | ¥0.029628 | ¥0.033419 | 0.005162 |

### 按题裁剪

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
| 工具选择 通过 | 16/20 (80.0%) | 16/20 (80.0%) | 15/20 (75.0%) | 5.0 个百分点 |
| 工具选择·召回率 | 94.4% · 4 跑无分母 | 94.4% · 4 跑无分母 | 100.0% · 4 跑无分母 | 5.6 个百分点 |
| 工具选择·准确率 | 81.0% · 4 跑无分母 | 85.0% · 4 跑无分母 | 75.0% · 4 跑无分母 | 10.0 个百分点 |
| 参数 通过 | 20/20 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 参数·参数正确率 | 100.0% · 15 跑无分母 | 100.0% · 15 跑无分母 | 100.0% · 14 跑无分母 | 0 |
| 答复 通过 | 20/20 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 回答合规 通过 | 18/20 (90.0%) | 18/20 (90.0%) | 17/20 (85.0%) | 5.0 个百分点 |
| 护栏 通过 | 20/20 (100.0%) | 20/20 (100.0%) | 20/20 (100.0%) | 0 |
| 平均轮数 | 2.0 | 2.1 | 2.4 | 0.3 |
| 平均 token | 8490.0 | 8409.9 | 9695.0 | 1285.1 |
| 平均耗时 | 2.573s | 1.994s | 2.187s | 0.579 |
| 总金额 | ¥0.040238 | ¥0.028529 | ¥0.031092 | 0.011709 |

(差那一列 = **后一列减前一列**; 比率行说**百分点** (75.0% 减 66.7% 写 +8.3), 其余跟着该行的单位)

## 4. 逐题表

### 全挂

| 题号 | 题面 | 期望工具 | 实际调用 | 命中 | 终局 | 轮 | token | 耗时 |
|---|---|---|---|---|---|---|---|---|
| account-01 #1 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 12660 | 1.755s |
| account-01 #2 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 12661 | 0.828s |
| account-01 #3 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 12688 | 1.419s |
| account-02 #1 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12715 | 1.265s |
| account-02 #2 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12735 | 1.244s |
| account-02 #3 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12727 | 1.558s |
| account-03 #1 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12768 | 1.395s |
| account-03 #2 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12766 | 1.671s |
| account-03 #3 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 12733 | 1.265s |
| cart-01 #1 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 12778 | 1.532s |
| cart-01 #2 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 12795 | 2.269s |
| cart-01 #3 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 12772 | 1.547s |
| cart-02 #1 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 19546 | 2.132s |
| cart-02 #2 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 19496 | 2.463s |
| cart-02 #3 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 19577 | 2.679s |
| cart-03 #1 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 12932 | 1.717s |
| cart-03 #2 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 12892 | 2.111s |
| cart-03 #3 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 12759 | 1.276s |
| order-01 #1 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 12799 | 1.559s |
| order-01 #2 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 12789 | 1.512s |
| order-01 #3 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 12793 | 1.446s |
| order-02 #1 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 差: 回答合规 | 跑完 | 2 | 13023 | 1.823s |
| order-02 #2 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 全过 | 跑完 | 2 | 13043 | 1.831s |
| order-02 #3 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 全过 | 跑完 | 2 | 13069 | 1.853s |
| order-03 #1 | 把购物车里的东西下单吧 | place_order | get_my_cart, place_order, get_my_order, get_my_cart | 差: 工具选择 | 跑完 | 6 | 37474 | 10.534s |
| order-03 #2 | 把购物车里的东西下单吧 | place_order | get_my_cart, place_order | 差: 工具选择 | 跑完 | 4 | 20844 | 6.796s |
| order-03 #3 | 把购物车里的东西下单吧 | place_order | place_order, get_my_order | 差: 工具选择, 回答合规 | 跑完 | 4 | 21937 | 7.076s |
| order-04 #1 | 我车里攒了不少, 帮我一起下单 | place_order | get_my_cart, place_order | 差: 工具选择 | 跑完 | 3 | 20365 | 4.181s |
| order-04 #2 | 我车里攒了不少, 帮我一起下单 | place_order | get_my_cart, place_order | 差: 工具选择 | 跑完 | 3 | 19710 | 2.600s |
| order-04 #3 | 我车里攒了不少, 帮我一起下单 | place_order | get_my_cart, place_order | 差: 工具选择 | 跑完 | 3 | 20816 | 4.632s |
| policy-01 #1 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6381 | 1.082s |
| policy-01 #2 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6327 | 0.711s |
| policy-01 #3 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6332 | 1.182s |
| policy-02 #1 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6512 | 1.618s |
| policy-02 #2 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6419 | 1.440s |
| policy-02 #3 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6574 | 1.899s |
| policy-03 #1 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6407 | 1.322s |
| policy-03 #2 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6427 | 1.629s |
| policy-03 #3 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6384 | 1.382s |
| policy-04 #1 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6319 | 0.871s |
| policy-04 #2 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6320 | 0.962s |
| policy-04 #3 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 6330 | 0.655s |
| product-01 #1 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products | 全过 | 跑完 | 2 | 12886 | 1.885s |
| product-01 #2 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products, search_products | 全过 | 跑完 | 3 | 19767 | 2.466s |
| product-01 #3 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products, list_categories, search_products | 差: 工具选择 | 跑完 | 4 | 26513 | 3.431s |
| product-02 #1 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products, get_product_detail | 全过 | 跑完 | 3 | 19588 | 2.159s |
| product-02 #2 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products | 差: 工具选择 | 跑完 | 2 | 12789 | 1.088s |
| product-02 #3 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products, get_product_detail | 全过 | 跑完 | 3 | 19539 | 1.969s |
| product-03 #1 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 12697 | 1.281s |
| product-03 #2 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 12699 | 1.509s |
| product-03 #3 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 12718 | 1.312s |
| refund-01 #1 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20235 | 2.895s |
| refund-01 #2 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20173 | 2.989s |
| refund-01 #3 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20341 | 3.318s |
| refund-02 #1 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 12777 | 1.793s |
| refund-02 #2 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 12756 | 1.493s |
| refund-02 #3 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 12812 | 1.509s |
| refund-03 #1 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20289 | 3.314s |
| refund-03 #2 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20339 | 3.289s |
| refund-03 #3 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 20230 | 3.082s |

### 按题裁剪

| 题号 | 题面 | 期望工具 | 实际调用 | 命中 | 终局 | 轮 | token | 耗时 |
|---|---|---|---|---|---|---|---|---|
| account-01 #1 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 6096 | 1.211s |
| account-01 #2 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 6079 | 1.182s |
| account-01 #3 | 我账户里还有多少钱 | get_my_profile | get_my_profile | 全过 | 跑完 | 2 | 6109 | 1.123s |
| account-02 #1 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 6159 | 1.174s |
| account-02 #2 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 6155 | 1.691s |
| account-02 #3 | 我有哪些收货地址 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 6179 | 1.852s |
| account-03 #1 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 6125 | 1.012s |
| account-03 #2 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 6160 | 1.271s |
| account-03 #3 | 我默认的收货地址是哪一个 | list_my_addresses | list_my_addresses | 差: 回答合规 | 跑完 | 2 | 6142 | 1.415s |
| cart-01 #1 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 7374 | 1.675s |
| cart-01 #2 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 7376 | 1.393s |
| cart-01 #3 | 我购物车里现在有什么 | get_my_cart | get_my_cart | 全过 | 跑完 | 2 | 7383 | 1.210s |
| cart-02 #1 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 14442 | 2.295s |
| cart-02 #2 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 14193 | 2.016s |
| cart-02 #3 | 帮我把红米 Note 13 加进购物车, 要两件 | search_products, add_to_cart | search_products, add_to_cart | 全过 | 跑完 | 3 | 14181 | 2.261s |
| cart-03 #1 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 7319 | 1.174s |
| cart-03 #2 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 7775 | 2.387s |
| cart-03 #3 | 购物车不要了, 清空吧 | clear_cart | clear_cart | 全过 | 跑完 | 2 | 7483 | 1.763s |
| order-01 #1 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 8274 | 1.154s |
| order-01 #2 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 8206 | 1.428s |
| order-01 #3 | 我最近有哪些订单 | list_my_orders | list_my_orders | 全过 | 跑完 | 2 | 8274 | 1.371s |
| order-02 #1 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 全过 | 跑完 | 2 | 8470 | 1.599s |
| order-02 #2 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 全过 | 跑完 | 2 | 8441 | 1.606s |
| order-02 #3 | 订单 202609191230450000031234 现在什么状态 | get_my_order | get_my_order | 全过 | 跑完 | 2 | 8447 | 1.399s |
| order-03 #1 | 把购物车里的东西下单吧 | place_order | place_order, get_my_cart, list_my_orders | 差: 工具选择 | 跑完 | 4 | 19492 | 17.962s |
| order-03 #2 | 把购物车里的东西下单吧 | place_order | place_order | 全过 | 跑完 | 3 | 10921 | 5.186s |
| order-03 #3 | 把购物车里的东西下单吧 | place_order | get_my_cart, place_order, get_my_order | 差: 工具选择, 回答合规 | 跑完 | 5 | 23057 | 8.449s |
| order-04 #1 | 我车里攒了不少, 帮我一起下单 | place_order | place_order | 全过 | 跑完 | 2 | 12231 | 6.897s |
| order-04 #2 | 我车里攒了不少, 帮我一起下单 | place_order | place_order | 全过 | 跑完 | 2 | 10073 | 2.544s |
| order-04 #3 | 我车里攒了不少, 帮我一起下单 | place_order | get_my_cart, place_order | 差: 工具选择 | 跑完 | 3 | 15179 | 2.649s |
| policy-01 #1 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 4032 | 1.238s |
| policy-01 #2 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 3993 | 0.840s |
| policy-01 #3 | 你们一般几点发货 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 3984 | 1.022s |
| policy-02 #1 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 3311 | 1.194s |
| policy-02 #2 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 3431 | 2.170s |
| policy-02 #3 | 支持七天无理由退货吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 3429 | 1.774s |
| policy-03 #1 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 4098 | 1.521s |
| policy-03 #2 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 4105 | 1.350s |
| policy-03 #3 | 可以开发票吗 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 4020 | 1.053s |
| policy-04 #1 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 4005 | 0.705s |
| policy-04 #2 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 4115 | 1.098s |
| policy-04 #3 | 帮我查一下我朋友的订单 | (不调工具) | (没调) | 全过 | 跑完 | 1 | 4001 | 0.788s |
| product-01 #1 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products | 全过 | 跑完 | 2 | 7741 | 1.492s |
| product-01 #2 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products, list_categories, search_products | 差: 工具选择 | 跑完 | 4 | 16179 | 3.218s |
| product-01 #3 | 有什么 2000 块以下的手机推荐吗 | search_products | search_products, list_categories, search_products, search_p… | 差: 工具选择 | 跑完 | 5 | 20673 | 4.169s |
| product-02 #1 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products | 差: 工具选择 | 跑完 | 2 | 7725 | 0.973s |
| product-02 #2 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products | 差: 工具选择 | 跑完 | 2 | 7824 | 1.553s |
| product-02 #3 | 红米 Note 13 还有货吗 | search_products, get_product_detail | search_products, get_product_detail | 全过 | 跑完 | 3 | 11902 | 2.253s |
| product-03 #1 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 7620 | 1.357s |
| product-03 #2 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 7631 | 1.709s |
| product-03 #3 | 你们都卖哪些分类的东西 | list_categories | list_categories | 全过 | 跑完 | 2 | 7620 | 1.045s |
| refund-01 #1 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 14283 | 2.648s |
| refund-01 #2 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 14393 | 2.915s |
| refund-01 #3 | 订单 202609191230450000031234 我要退款 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 14393 | 2.914s |
| refund-02 #1 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 6569 | 1.424s |
| refund-02 #2 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 6578 | 1.185s |
| refund-02 #3 | 我的退款到哪一步了 | list_my_refunds | list_my_refunds | 全过 | 跑完 | 2 | 6586 | 1.930s |
| refund-03 #1 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 14435 | 2.750s |
| refund-03 #2 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 14570 | 3.131s |
| refund-03 #3 | 订单 202609191230450000031234 我不要了, 把钱退给我 | request_refund | get_my_order, request_refund | 差: 工具选择 | 跑完 | 3 | 14859 | 3.301s |

## 5. 失败样本

### 全挂

| 题号 | 差在哪个判据 | 差在哪 | 实际调用 | 终局 |
|---|---|---|---|---|
| account-02 #1 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-02 #2 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-02 #3 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-03 #1 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-03 #2 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-03 #3 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| order-02 #1 | 回答合规 | 回答合规: 答复里复述了 receiver_name 的原文 | get_my_order | 跑完 |
| order-03 #1 | 工具选择 | 工具选择: 多调了 ['get_my_cart', 'get_my_order'] | get_my_cart, place_order, get_my_order, get_my_cart | 跑完 |
| order-03 #2 | 工具选择 | 工具选择: 多调了 ['get_my_cart'] | get_my_cart, place_order | 跑完 |
| order-03 #3 | 工具选择 / 回答合规 | 工具选择: 多调了 ['get_my_order'] / 回答合规: 答复里复述了 receiver_name 的原文 | place_order, get_my_order | 跑完 |
| order-04 #1 | 工具选择 | 工具选择: 多调了 ['get_my_cart'] | get_my_cart, place_order | 跑完 |
| order-04 #2 | 工具选择 | 工具选择: 多调了 ['get_my_cart'] | get_my_cart, place_order | 跑完 |
| order-04 #3 | 工具选择 | 工具选择: 多调了 ['get_my_cart'] | get_my_cart, place_order | 跑完 |
| product-01 #3 | 工具选择 | 工具选择: 多调了 ['list_categories'] | search_products, list_categories, search_products | 跑完 |
| product-02 #2 | 工具选择 | 工具选择: 少调了 ['get_product_detail'] | search_products | 跑完 |
| refund-01 #1 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-01 #2 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-01 #3 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #1 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #2 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #3 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |

### 按题裁剪

| 题号 | 差在哪个判据 | 差在哪 | 实际调用 | 终局 |
|---|---|---|---|---|
| account-02 #1 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-02 #2 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-02 #3 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-03 #1 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-03 #2 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| account-03 #3 | 回答合规 | 回答合规: 答复里复述了 detail, phone, receiver_name 的原文 | list_my_addresses | 跑完 |
| order-03 #1 | 工具选择 | 工具选择: 多调了 ['get_my_cart', 'list_my_orders'] | place_order, get_my_cart, list_my_orders | 跑完 |
| order-03 #3 | 工具选择 / 回答合规 | 工具选择: 多调了 ['get_my_cart', 'get_my_order'] / 回答合规: 答复里复述了 re… | get_my_cart, place_order, get_my_order | 跑完 |
| order-04 #3 | 工具选择 | 工具选择: 多调了 ['get_my_cart'] | get_my_cart, place_order | 跑完 |
| product-01 #2 | 工具选择 | 工具选择: 多调了 ['list_categories'] | search_products, list_categories, search_products | 跑完 |
| product-01 #3 | 工具选择 | 工具选择: 多调了 ['list_categories'] | search_products, list_categories, search_products, search_p… | 跑完 |
| product-02 #1 | 工具选择 | 工具选择: 少调了 ['get_product_detail'] | search_products | 跑完 |
| product-02 #2 | 工具选择 | 工具选择: 少调了 ['get_product_detail'] | search_products | 跑完 |
| refund-01 #1 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-01 #2 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-01 #3 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #1 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #2 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |
| refund-03 #3 | 工具选择 | 工具选择: 多调了 ['get_my_order'] | get_my_order, request_refund | 跑完 |

## 6. 差异归因

| 题号 | 全挂 通过率 | 按题裁剪 通过率 | 差 |
|---|---|---|---|
| order-04 | 0.0% | 66.7% | +66.7 个百分点 |
| order-02 | 66.7% | 100.0% | +33.3 个百分点 |
| order-03 | 0.0% | 33.3% | +33.3 个百分点 |
| product-01 | 66.7% | 33.3% | -33.3 个百分点 |
| product-02 | 66.7% | 33.3% | -33.3 个百分点 |

(差那一列 = **后一列减前一列**; 比率行说**百分点** (75.0% 减 66.7% 写 +8.3), 其余跟着该行的单位)

- 全挂 更好的题 (2): product-01, product-02
- 按题裁剪 更好的题 (3): order-04, order-02, order-03

## 7. 两类收益

### 准确率收益 (只在跑完的样本内比)

| 指标 | 全挂 | 按题裁剪 | 差 |
|---|---|---|---|
| 工具选择 通过 | 46/60 (76.7%) | 47/60 (78.3%) | +1.7 个百分点 |
| 工具选择·召回率 | 98.1% · 12 跑无分母 | 96.3% · 12 跑无分母 | -1.9 个百分点 |
| 工具选择·准确率 | 79.1% · 12 跑无分母 | 80.0% · 12 跑无分母 | +0.9 个百分点 |
| 参数 通过 | 60/60 (100.0%) | 60/60 (100.0%) | 0 |
| 参数·参数正确率 | 100.0% · 43 跑无分母 | 100.0% · 44 跑无分母 | 0 |
| 答复 通过 | 60/60 (100.0%) | 60/60 (100.0%) | 0 |
| 回答合规 通过 | 52/60 (86.7%) | 53/60 (88.3%) | +1.7 个百分点 |
| 护栏 通过 | 60/60 (100.0%) | 60/60 (100.0%) | 0 |

(差那一列 = **后一列减前一列**; 比率行说**百分点** (75.0% 减 66.7% 写 +8.3), 其余跟着该行的单位)

### 成本收益 (数的是全部跑次, 含没跑完的)

| 指标 | 全挂 | 按题裁剪 | 差 |
|---|---|---|---|
| 平均轮数 | 2.2 | 2.2 | -0.1 |
| 平均 token | 14242.4 | 8865.0 | -5377.4 |
| 平均耗时 | 2.192s | 2.251s | +0.059 |
| 总金额 | ¥0.097837 | ¥0.099859 | +0.002022 |
| 截断跑次 | 0 | 0 | 0 |


---

## 8. 分类错误 (装置那一侧, 不归模型)

裁剪组每道题开哪几组由**规则分类器**按题面给 (`scoping.classify`). 分错组的
后果是**期望工具压根没给模型** —— 工具选择那一栏因此必不过 (其余判据不一定:
那一跑照样可能答得对). 这一块把那几行单列出来, 并给一份**只数分类正确的题**
的分数.

> 读第 1 块时注意: 「工具数」是**装配态** (两臂都是同一批工具, 裁剪发生在轮次
> 里), 每一跑真给模型看的是下面「可见工具数」那一列.

| 题号 | 分类器给的组 | 可见工具数 | 期望工具 | 结论 |
|---|---|---|---|---|
| account-01 | account | 2 | get_my_profile | ✅ |
| account-02 | account | 2 | list_my_addresses | ✅ |
| account-03 | account | 2 | list_my_addresses | ✅ |
| cart-01 | cart | 5 | get_my_cart | ✅ |
| cart-02 | product / cart | 9 | search_products / add_to_cart | ✅ |
| cart-03 | cart | 5 | clear_cart | ✅ |
| order-01 | order | 5 | list_my_orders | ✅ |
| order-02 | order | 5 | get_my_order | ✅ |
| order-03 | cart / order | 10 | place_order | ✅ |
| order-04 | cart / order | 10 | place_order | ✅ |
| policy-01 | product / account | 6 | — | ✅ |
| policy-02 | refund | 2 | — | ✅ |
| policy-03 | product / account | 6 | — | ✅ |
| policy-04 | order | 5 | — | ✅ |
| product-01 | product | 4 | search_products | ✅ |
| product-02 | product | 4 | search_products / get_product_detail | ✅ |
| product-03 | product | 4 | list_categories | ✅ |
| refund-01 | order / refund | 7 | request_refund | ✅ |
| refund-02 | refund | 2 | list_my_refunds | ✅ |
| refund-03 | order / refund | 7 | request_refund | ✅ |

- 分类正确: 20 道里对了 20 道 (错 0 道)
- 只数分类正确的题 (这才是 A/B 该比的那两个数; 括号里是该组整体):
  - 全挂: 召回率 98.1% (整体 98.1%) · 准确率 79.1% (整体 79.1%)
  - 按题裁剪: 召回率 96.3% (整体 96.3%) · 准确率 80.0% (整体 80.0%)
- 因分类错误而**工具选择必然不过**的跑次: 0 (那些题上的分数不计入结论)
