"""
三 函数参数形式
"""


# 1.位置参数
# 2.关键字参数
# 3.默认参数
# 4.不定长参数
# 4.1 带一个*
def print_info(num, *vartuple):
    pass


print_info(70, 60, 50)


# 如果不定长的参数后面还有参数,必须通过关键字参数传参
def print_info1(num1, *vartuple, num):
    pass


print_info1(10, 20, num=40)

# 如果没有给不定长的参数传参,那么得到的是空元组
print_info1(70, num=60)


# 4.2 带二个*
def print_info(num, **vardict):
    pass
    # return


print_info(10, key1=20, key2=30)
