"""迭代器"""

import tracemalloc

""" 定义 """
# obj.__iter__() = iter(obj) => 迭代器 iterator
# iterator.__next__() = next(iterator)

""" 特点 """
# 1.迭代器都有__next__方法 每次调用会根据当前状态返回下一个元素
# 2.迭代器在next遍历的过程中会被消耗 不会自动重置
x = [1, 2, 3]
it = iter(x)

# 3.所有元素取出后 继续next 会抛出异常StopIteration
# print(next(it))

# 4.迭代器本身也有__iter__方法 并且返回的是迭代器本身【是为了让 for 循环也能遍历迭代器】
it1 = iter(x)
it2 = iter(it1)

# 5.总结: 迭代器协议 = iter() + next()

""" 自定义迭代器 """


# 实现方式1 (不推荐)
class TestIterator:
    def __init__(self, t):
        # 外部实例对象
        self.t = t
        # 初始状态
        self.idx = 0
        # 配置可遍历内容
        self.attrs = [t.a, t.b, t.c]

    def __iter__(self):
        return self

    def __next__(self):
        if self.idx >= len(self.attrs):
            raise StopIteration

        value = self.attrs[self.idx]

        self.idx += 1
        return value


class Test1:
    def __init__(self, a, b, c):
        self.a = a
        self.b = b
        self.c = c

    def __iter__(self):
        return TestIterator(self)


t1 = Test1("cls1-a", "cls1-b", "cls1-c")
for _item in t1:
    pass


# 实现方式2 (推荐)
class Test2:
    def __init__(self, a, b, c):
        """这种方式的类的实例 既是可迭代对象 也是迭代器"""
        self.a = a
        self.b = b
        self.c = c

        # 初始状态
        self.__idx = 0
        # 配置可遍历内容 *或者可以定义相对应的规则*
        self.__attrs = [self.a, self.b, self.c]

    def __iter__(self):
        """ " *关键点* 实例对象self每次调用iter时重置idx"""
        self.__idx = 0
        return self

    def __next__(self):
        """*关键点* 迭代器中各种详细需求都是在next中实现的"""
        if self.__idx >= len(self.__attrs):
            raise StopIteration

        value = self.__attrs[self.__idx]
        if isinstance(value, str):
            value = value.upper()

        self.__idx += 1
        return value


t2 = Test2("cls2-a", "cls2-b", "cls2-c")
for _item in t2:
    pass


class FibonacciIterator:
    def __init__(self, n):
        self.n = n

        self.__idx = 0
        self.__pre = 1
        self.__cur = 1

    def __iter__(self):
        self.__idx = 0
        return self

    def __next__(self):
        if self.__idx >= self.n:
            raise StopIteration

        if self.__idx < 2:
            value = 1
        else:
            value = self.__pre + self.__cur
            self.__pre = self.__cur
            self.__cur = value

        self.__idx += 1
        return value


# 惰性计算:【不会一次性生成所有结果】【使用时才会计算】【节省内存】
tracemalloc.start()
f = FibonacciIterator(10000000)
m = tracemalloc.get_traced_memory()[1]
for i, _item in enumerate(f):
    if i >= 10:
        break
