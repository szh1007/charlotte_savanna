""" 多进程 多线程 """
import os, time
from multiprocessing import Process, RLock


# 自定义 Process 核心在于实现 run
# 之前的所有拓展逻辑同样都可以在 run 中实现, 比如锁、守护进程等

class SpeakProcess(Process):
    def __init__(self, n, **kwargs):
        super().__init__(**kwargs)
        self.n = n
        self.lock = RLock()

    def run(self):
        for i in range(self.n):
            with self.lock:
                print(f"{i + 1}-{os.getpid()}-{os.getppid()}")
                time.sleep(1)


class StudyProcess(Process):
    def __init__(self, n, **kwargs):
        super().__init__(**kwargs)
        self.n = n
        self.lock = RLock()

    def run(self):
        for i in range(self.n):
            with self.lock:
                print(f"{i + 1}-{os.getpid()}-{os.getppid()}")
                time.sleep(1)


if __name__ == "__main__":
    print("-" * 50, "MAIN START", "-" * 50)

    p1 = SpeakProcess(10)
    p2 = StudyProcess(12)

    p1.start()
    p2.start()

    p1.join()
    p2.join()

    print("-" * 50, "MAIN END", "-" * 50)
