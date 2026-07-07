"""
多进程 多线程
进程通信 Pipe(管道)
"""
import time
from multiprocessing import Process, Pipe


def test1(con):
    time.sleep(2)
    con.send(100)
    print("test1 send 100")


def test2(con):
    data = con.recv()
    print(f"test2 recv data: {data}")


if __name__ == "__main__":
    # Pipe() 默认是双向管道
    # duplex=False 可设置为单向管道 con1 -> con2
    con1, con2 = Pipe(duplex=False)

    p1 = Process(target=test1, args=(con1,))
    p2 = Process(target=test2, args=(con2,))

    p1.start()
    p2.start()

    p1.join()
    p2.join()
