import copy

# 1.直接赋值
# -> 两个变量指向同一个【可变对象】, 修改会互相影响
# a = [1, 2, 3]
# b = a
# b[2] = 4
# print(a, id(a))
# print(b, id(b))

# 2.浅拷贝
# -> 一层拷贝, 内部仍引用【所有原来的对象】
# -> 内部嵌套的【可变对象】修改仍然会互相影响
# a = [1, 2, [3, 4]]
# b = copy.copy(a)
# b[0] = 5
# b[2][1] = 6
# print(a, id(a), id(a[2]))
# print(b, id(b), id(b[2]))

# 3.深拷贝: 递归拷贝, 对内部所有的【可变对象】递归复制
# -> 在浅拷贝的基础上, 复制了可变对象, 不可变对象仍直接引用
# a = [1, 2, [3, 4]]
# b = copy.deepcopy(a)
# b[0] = 5
# b[2][1] = 6
# print(a, id(a), id(a[2]))
# print(b, id(b), id(b[2]))

# -> 若复制元组, 且元组中只包含不可变对象, 则深拷贝没有效果
a1 = (1, 2, [3, 4])
a2 = (1, 2, (3, 4))
b1 = copy.deepcopy(a1)
b2 = copy.deepcopy(a2)
print(a1, id(a1), a2, id(a2))
print(b1, id(b1), b2, id(b2))
