"""生成器"""

import contextlib

""" 定义 """


# 1.生成器函数: 函数中包含 yield 关键字 (不管是否能执行到 yield 位置)
# 2.生成器对象: 调用生成器函数 -> 返回生成器对象 (不会立即执行)
def test1():
    yield

    x = 10
    yield x

    y = 20
    yield y

    return f"{x} + {y} = {x + y}"


""" 特点 """
# 1.生成器对象也有__next__方法
# 2.每次运行 next 后遇到 yield 会暂停执行 (yield 也可以写在循环中)
# 3.如果 yield 后面有表达式, 则会作为本次 next 的返回值
# 4.遇到 return 会抛出异常 StopIteration, 并将 return 后的表达式作为异常信息
t1 = test1()
next(t1)
X = next(t1)
Y = next(t1)
with contextlib.suppress(StopIteration):
    next(t1)

# 5.生成器本质是一种【函数的迭代器】
t2 = test1()
for _item in t2:
    pass


# 6.yield from 能把一个可迭代对象里的元素依次 yield 出去
# 代替了生成器函数中在 for 循环中写 yield
def test2():
    nums = [1, 2, 3]
    yield from nums


t3 = test2()
for _item in t3:
    pass


# 7.生成器.send(值) 可以让生成器【继续执行的同时】给上一次 yield 传值
# 因为 send 是给上一次的 yield 传值, 所以第一次启动生成器时不能 send
# next 只能取值, send 既能取值也能送值
def test3(n: int):
    x = yield n

    y = yield x

    return f"{x} + {y} = {x + y}"


t4 = test3(10)
try:
    N = next(t4)
    X = t4.send(N * 2)
    Y = t4.send(X * 2)
except StopIteration:
    pass


# 8.生成器表达式 (对比列表推导式)
num = [1, 2, 3, 4, 5]
result = (i**2 for i in num)
for _item in result:
    pass


""" 自定义生成器 (改写之前的迭代器示例) """


# 示例1
class Test:
    def __init__(self, a, b, c):
        self.a = a
        self.b = b
        self.c = c

        self.__attrs = [self.a, self.b, self.c]

    def __iter__(self):
        yield from self.__attrs


for _item in Test(11, 22, 33):
    pass


# 实例2
def fibo(n: int):
    pre = 1
    cur = 1

    for i in range(n):
        if i < 2:
            yield 1
        else:
            value = pre + cur
            pre, cur = cur, value
            yield value


x = list(fibo(10))

# 无论是迭代器还是生成器, 都可以使用list、tuple等直接拿到里面所有内容
y = list(fibo(15))


""" 迭代器 vs 生成器 """
# 1.大部分情况下确实用生成器
# 2.迭代时有其他复杂需求时需要定制化迭代器中的 next
