"""
多进程 多线程
进程通信 Queue(队列)
"""

import time
from multiprocessing import Process, Queue

""" 特点 """

# 1.队列已满时, 继续 put 进程会 wait, 直至其他进程有 get 才会继续执行
# q1.put(40)

# 2.队列已满时, 继续 put + timeout, 会等待指定秒数, 若还不能入队则报错
# q1.put(40, timeout=3)

# 3.put_nowait / put + block=False, 进程不会 wait, 此时若队列已满则直接抛错
# q1.put_nowait(40)
# q1.put(40, block=False)

# 4.空队列 get 也会 wait
# q1.get()

# 5.空队列 get + timeout, 会等待指定秒数, 若还不能出队则报错
# q1.get(timeout=3)

# 6.get_nowait / get + block=Fals, 进程不会wait, 此时若队列为空则直接抛错
# q1.get_nowait()
# q1.get(block=False)


def test(queue):
    time.sleep(3)
    queue.get()


def async_enqueue(queue, n):
    for i in range(n):
        queue.put(i)
        time.sleep(0.5)


def async_dequeue(queue, n):
    for _i in range(n):
        queue.get()
        time.sleep(1)


if __name__ == "__main__":
    q = Queue(3)

    # # 1.基本定义
    # q.put(10)
    # q.put(20)
    # q.put(30)
    # print(q.qsize(), q.empty(), q.full())
    #
    # v1 = q.get()
    # v2 = q.get()
    # v3 = q.get()
    # print(q.qsize(), q.empty(), q.full(), [v1, v2, v3])
    #
    # print("-"*50)

    # # 2.特点举例
    # q.put(10)
    # q.put(20)
    # q.put(30)
    #
    # print(f"测试入队前队列是否已满: {q.full()}")
    #
    # p1 = Process(target=test, args=(q,))
    # p1.start()
    #
    # print("即将向满队列入队新元素...")
    # q.put(40)
    #
    # print("目前队列元素为:", end=" ")
    # print(q.get(), q.get(), q.get(), sep=", ")
    #
    # print("-"*50)

    # 3.进程通信
    Q = Queue()

    p1 = Process(target=async_enqueue, args=(Q, 10))
    p2 = Process(target=async_dequeue, args=(Q, 10))

    p1.start()
    p2.start()
