"""多进程 多线程"""

import time
from multiprocessing import Lock, Process, RLock

""" 基础示例 """


def speak(n, lock):
    for _i in range(n):
        lock.acquire()  # 上锁: 对于Lock, 锁是空闲的则立刻上锁, 否则会阻塞原地等待 (对于RLock, 可多次上锁)
        lock.release()  # 解锁: 对于Lock, acquire 和 release 必须成对出现, 否则会死锁 (对于RLock, 可出现多对)

        time.sleep(1)


def study(n, lock):
    for _i in range(n):
        with lock:  # 【推荐】简单写法: 自动上锁/解锁, 相比传统写法, 即便代码有异常也能解锁, 避免死锁
            pass

        time.sleep(1)


def monitor():
    # 【守护进程函数】
    # 因为主进程结束后, 守护进程自动终止
    # 所以直接 while True 不会造成死循环
    while True:
        try:
            with open("log.txt", encoding="utf-8") as log:
                len(log.readlines())
        except FileNotFoundError:
            pass

        time.sleep(1)


if __name__ == "__main__":  # 必须写, 否则会报错: 模块循环调用
    L = Lock()  # 【不推荐】标准进程锁: 只能上锁一次, 并需要解锁后才能继续上锁
    RL = RLock()  # 【推荐】计数进程锁: 可多次上锁, 对应数量解锁即可

    p1 = Process(target=speak, name="process1", args=(10, RL))
    p2 = Process(target=study, name="process2", kwargs={"n": 12, "lock": RL})

    # 【守护进程】
    # -> 依附于主进程存在, 主进程结束后, 守护进程自动终止
    # -> 可负责后台监控类场景: 日志、统计、采样等辅助型"陪跑任务"
    p3 = Process(target=monitor, name="monitor", daemon=True)

    p1.start()
    p2.start()
    p3.start()

    with open("log.txt", "w", encoding="utf-8") as file:
        for _ in range(10):
            file.write("test\n")
            file.flush()
            time.sleep(1)

    p1.join()  # 阻塞主进程: p1执行完毕, 主进程再继续执行
    p2.join()  # 阻塞主进程: p2执行完毕, 主进程再继续执行
    # p3.join()  # !!!通常情况, 对于【守护进程】不需要阻塞, 主进程结束会自动终止
