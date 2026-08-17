"""
多进程 多线程
进程通信 Pipe(管道)
"""

import time
from multiprocessing import Pipe, Process


def test1(con):
    time.sleep(2)
    con.send(100)


def test2(con):
    con.recv()


if __name__ == "__main__":
    # Pipe() 默认是双向管道
    # duplex=False 可设置为单向管道: con1 仅接收, con2 仅发送
    con1, con2 = Pipe(duplex=False)

    p1 = Process(target=test1, args=(con2,))  # 发送方使用 con2
    p2 = Process(target=test2, args=(con1,))  # 接收方使用 con1

    p1.start()
    p2.start()

    p1.join()
    p2.join()
