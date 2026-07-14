"""
二 函数参数
"""


# 1. 无参数版本 - 只能计算固定的购物车
def calculate_total_no_params():
    """计算固定购物车总价"""
    prices = [100, 50, 30]  # 商品价格固定写死在函数内
    total = 0
    for price in prices:
        total += price
    return total


# 只能计算一个固定的购物车


# 2.有参数版本 - 可以计算任意购物车
def calculate_total(prices):
    """计算任意购物车总价"""
    total = 0
    for price in prices:
        total += price
    return total


# 可以计算任意购物车
cart1 = [100, 50, 30]
cart2 = [200, 80, 45, 60]
cart3 = [75, 90, 120]


# 3.参数传递
# 3.1 不可变类型 函数传递不可变对象


def change_int(a):
    pass  # 底层会创建一个新对象 然后给新对象一个新值


a = 2  # 创建一个对象 然后给这个对象一个值
change_int(a)


# 输出结果
# 函数体中未改变前a的内存地址 140729722661336
# 函数体中改变后a的内存地址 140729722661592
# 2
# 函数外b的内存地址 140729722661336


# 3.2 可变类型 函数传递不可变对象


def change_list(my_list):
    my_list[1] = 50


mlist = [1, 2, 3]  # 底层创建一个对象 地址0111111
change_list(mlist)

# 输出结果
# 函数内的值 [1, 50, 3]
# 函数内列表的内存 1380193079680
# 函数外的值 [1, 50, 3]
# 函数外列表的内存 1380193079680
