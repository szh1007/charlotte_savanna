from abc import ABC, abstractmethod


# 抽象类
class Test(ABC):
    # 抽象方法
    # 继承抽象类的子类必须要实现所有的抽象方法
    @abstractmethod
    def test(self):
        pass


class Charlotte(Test):
    # 类属性
    # 实例和类都可以访问
    MAX_AGE = 120

    def __init__(self, name="Charlotte", gender="男", age=16):
        # 实例属性
        # 仅实例可以访问
        self.name = name  # public    当前类、子类、类外部
        self._gender = gender  # protected 当前类、子类
        self.__age = age  # private   当前类

    # 实例方法
    # self -> 实例本身 -> 可使用实例属性和类属性
    def fun1(self):
        pass

    # 类方法
    # cls -> 类本身 -> 仅可以使用类属性
    @classmethod
    def fun2(cls):
        pass

    # 静态方法
    # 不访问任何属性 -> 仅推荐使用类调用
    @staticmethod
    def fun3():
        pass

    # 实现抽象方法
    def test(self):
        pass

    # getter
    @property
    def age(self):
        return self.__age if self.__age >= 18 else "**"

    # setter
    @age.setter
    def age(self, value):
        assert isinstance(value, int)
        assert 0 <= value <= self.MAX_AGE
        self.__age = value

    # 魔法方法
    def __str__(self):  # f"...{实例}..."
        return f"{self.name}_{self._gender}_{self.__age}"

    def __len__(self):  # len(实例)
        return len(self.__dict__)

    def __eq__(self, other):  # 实例1 == 实例2
        return self.__dict__ == other.__dict__


if __name__ == "__main__":
    c = Charlotte()

    c.fun1()
    c.fun2()
    Charlotte.fun3()
    c.test()

    c.age = 18
    # c.age = "18"
    # c.age = 121

    s = Charlotte("savanna", "女", 18)
