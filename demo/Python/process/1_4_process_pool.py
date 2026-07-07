""" 多进程 多线程 """
import os, time
# 多进程不能直接从主进程传递锁, 需要借助 Manager
from multiprocessing import Manager
# 【进程池执行器】比传统的【进程池】更全面、更好用
from concurrent.futures import ProcessPoolExecutor, as_completed, Future


def work(n: int, lock):
    with lock:
        print(f"executing mission-{n}-{os.getpid()}...")

    if n < 2:
        time.sleep(5)
    else:
        time.sleep(1)
    return str(n)


def done(future: Future):
    print(f"done mission: {future.result()}")


if __name__ == "__main__":
    print("-" * 50, "MAIN START", "-" * 50)

    with Manager() as manager:
        RL = manager.RLock()

        # 可使用 with 写法 (自动 shutdown)
        # executor = ProcessPoolExecutor(max_workers=3)

        with ProcessPoolExecutor(max_workers=3) as executor:
            # submit
            # 1.提交任务 (异步)
            # 2.返回值为 Future 类的实例对象
            futures = [executor.submit(work, i, RL) for i in range(10)]

            # add_done_callback
            # 回调函数, 任务完成时立即执行
            futures[0].add_done_callback(done)
            futures[1].add_done_callback(done)
            futures[2].add_done_callback(done)

            # as_completed
            # 1.按任务的【完成顺序】打印结果
            # 2.必须在 shutdown 之前, 否则无法生效
            # 3.如果任务没有完成, 则 result() 会让进程 wait
            print(f"submit-完成顺序: {', '.join([f.result() for f in as_completed(futures)])}")

            # map
            # 1.批量提交任务
            # 2.立刻返回结果【生成器】
            # 3.结果是按任务的【提交顺序】
            results = executor.map(work, range(10), [RL] * 10)
            print(f"map-提交顺序: {', '.join(list(results))}")

        # shutdown
        # 不再接受新任务, 阻塞主进程, 等待进程池所有任务执行完毕
        # 如果使用 with 创建进程池执行器, 则不需要手动 shutdown
        # executor.shutdown()

    print("-" * 50, "MAIN END", "-" * 50)
