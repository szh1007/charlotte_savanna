"""多进程 多线程"""

import time
from threading import RLock, Thread

# from concurrent.futures import ThreadPoolExecutor, as_completed, Future

""" 线程 Thread """

# 1.Thread 的使用方法和 Process 一致, 包括 start、join、Lock、RLock 等
# 2.线程池执行器 ThreadPoolExecutor 的使用方法和 ProcessPoolExecutor 一致
# 3.多进程不能直接从主进程传递锁, 要借助 Manager, 但是多线程可以直接从主进程传递锁


def speak(n, lock):
    for _i in range(n):
        with lock:
            pass
        time.sleep(1)


def study(n, lock):
    for _i in range(n):
        with lock:
            pass
        time.sleep(1)


class SpeakThread(Thread):
    def __init__(self, n, lock, **x):
        super().__init__(**x)
        self.n = n
        self.lock = lock

    def run(self):
        for _i in range(self.n):
            with self.lock:
                pass
            time.sleep(1)


class StudyThread(Thread):
    def __init__(self, n, lock, **x):
        super().__init__(**x)
        self.n = n
        self.lock = lock

    def run(self):
        for _i in range(self.n):
            with self.lock:
                pass
            time.sleep(1)


if __name__ == "__main__":
    RL = RLock()

    # # 使用 Thread 创建线程
    # t1 = Thread(target=speak, args=(5, RL))
    # t2 = Thread(target=study, args=(5, RL))

    # 继承 Thread 类创建线程
    t1 = SpeakThread(5, lock=RL)
    t2 = StudyThread(5, lock=RL)

    t1.start()
    t2.start()

    t1.join()
    t2.join()
