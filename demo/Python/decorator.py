""" 闭包 & 装饰器 """

""" 闭包 """
print("-" * 50, "闭包", "-" * 50)


# 定义:
# 1.闭包 = 内层函数(inner) + 被内层函数引用的外层变量(__closure__)
# 2.闭包产生的条件: 函数嵌套 + 内层访问了外层的变量 + 外层return内层

# 特点:
# 1.闭包可以记住初始状态 多次调用之间保存数据
# 2.没有被内层引用的变量 不会存放在闭包仓库中
# 3.每次调用外层函数 会得到【相互独立】的不同的闭包

# 缺点:
# 1.闭包如果引用【很大的对象】 且长期不释放 会增加内存占用
# 2.相同效果可以用【类】来等价实现

def outer(n: str):
    x = 10
    print(f"outer-参数变量n的地址: {hex(id(n))}, 值: {n}")
    print(f"outer-局部变量x的地址: {hex(id(x))}, 值: {x}")

    def inner():
        nonlocal x
        x += 1
        print(f"{n}-{x}")

    return inner


f = outer("test")
print(f"f-闭包仓库: {f.__closure__}")
print(f"f-闭包单元-n: {f.__closure__[0].cell_contents}")
print(f"f-闭包单元-x: {f.__closure__[1].cell_contents}")
print("-" * 50)

f1 = outer("test1")
f1()
f1()
f2 = outer("test2")
f2()
f2()

""" 装饰器 """
print("-" * 50, "装饰器", "-" * 50)


# 装饰器: 保证不修改原函数的前提下 给函数新增一些额外的功能

# 1.函数装饰器 - 无配置参数
def say_hello_1(fun):
    def wrapper(*args, **kwargs):
        print("fun-decorator")
        return fun(*args, **kwargs)

    return wrapper


# 2.函数装饰器 - 有配置参数
def say_hello_2(msg):  # 外层: 接收配置参数
    def middle(fun):  # 中层: 接收函数

        def wrapper(*args, **kwargs):  # 内层: 接收函数的参数
            print(f"fun-decorator-{msg}")
            return fun(*args, **kwargs)

        return wrapper

    return middle


# 3.类装饰器
class SayHello1:

    def __call__(self, fun):
        def wrapper(*args, **kwargs):
            print(f"class-decorator")
            return fun(*args, **kwargs)

        return wrapper


class SayHello2:
    def __init__(self, msg: str):
        self.msg = msg

    def __call__(self, fun):
        def wrapper(*args, **kwargs):
            print(f"class-decorator-{self.msg}")
            return fun(*args, **kwargs)

        return wrapper


@SayHello1()
@SayHello2("add")
@say_hello_1
@say_hello_2("add")
def add(x, y):
    print(f"{x} + {y} = {x + y}")
    return x + y


class ArgsError(Exception):
    def __init__(self, msg: str):
        super().__init__("【参数异常】" + msg)


# __all__
# from ... import * 时仅__all__中的元素可用
__all__ = ["add", "ArgsError"]

# __name__
# 在其他模块运行时 = 模块名
# 作为主程序运行时 = "__main__"
if __name__ == "__main__":
    n1, n2 = "1", "2"

    try:
        n1, n2 = int(n1), int(n2)
    except ValueError:
        n1, n2 = 10, 20

    if n1 >= n2:
        raise ArgsError("n1必须小于n2")

    add(n1, n2)
