""" 多进程 多线程 """
import os, time
from threading import get_native_id, Thread, RLock
# from concurrent.futures import ThreadPoolExecutor, as_completed, Future

""" 线程 Thread """

# 1.Thread 的使用方法和 Process 一致, 包括 start、join、Lock、RLock 等
# 2.线程池执行器 ThreadPoolExecutor 的使用方法和 ProcessPoolExecutor 一致
# 3.多进程不能直接从主进程传递锁, 要借助 Manager, 但是多线程可以直接从主进程传递锁

def speak(n, lock):
    for i in range(n):
        with lock:
            print(f"speak-{i + 1}: {os.getpid()}-{get_native_id()}")
        time.sleep(1)


def study(n, lock):
    for i in range(n):
        with lock:
            print(f"study-{i + 1}: {os.getpid()}-{get_native_id()}")
        time.sleep(1)


class SpeakThread(Thread):
    def __init__(self, n, lock, **x):
        super().__init__(**x)
        self.n = n
        self.lock = lock

    def run(self):
        for i in range(self.n):
            with self.lock:
                print(f"speak-{i + 1}: {os.getpid()}-{get_native_id()}")
            time.sleep(1)


class StudyThread(Thread):
    def __init__(self, n, lock, **x):
        super().__init__(**x)
        self.n = n
        self.lock = lock

    def run(self):
        for i in range(self.n):
            with self.lock:
                print(f"study-{i + 1}: {os.getpid()}-{get_native_id()}")
            time.sleep(1)


if __name__ == "__main__":
    print("-" * 50, "MAIN START", "-" * 50)

    print(os.getpid(), get_native_id())

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

    print("-" * 50, "MAIN END", "-" * 50)
