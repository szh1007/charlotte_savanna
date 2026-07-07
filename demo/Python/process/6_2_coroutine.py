""" 协程 coroutine """
import os, time
import aiohttp
import asyncio

""" 定义 """
# 协程函数: async关键字修饰的函数
# 协程对象: 调用协程函数的返回值

# await
# 1.挂起 -> 等待 -> 恢复
# 2.await 后面只能写【可等待对象】, 常见的有: 协程对象、Future对象、Task对象等
# 3.await [I/O操作] (例如网络请求、文件读写等): CPU控制权交给事件循环, 用于调度其他线程任务, 如果没有其他任务则继续执行
# 4.await [非I/O操作] (例如数学运算、逻辑计算等): 事件循环拿不到CPU的控制权, 不会调度其他线程任务, 也就不会发生任务切换

# asyncio.run
# 1.创建事件循环
# 2.将协程对象包装成任务 task, 交给事件循环
# 3.启动事件循环
# 4.阻塞当前线程, 等待任务执行并返回

""" 基础示例 """

async def test():
    print(f"test start")
    print(f"test loading...")  # 无I/O操作
    print(f"test end")
    return "result-test"


async def work(n, delay):
    print(f"work{n} start")
    print(f"work{n} loading...")
    await asyncio.sleep(delay)  # I/O操作
    print(f"work{n} end")
    return f"result-work{n}"


async def main1():
    print("main start")
    start = time.time()

    # # 写法1 (更灵活)
    # task0 = asyncio.create_task(test())
    # task1 = asyncio.create_task(work(1, 1))
    # task2 = asyncio.create_task(work(2, 1))
    #
    # res1 = await task1
    # res2 = await task2
    # res0 = await task0
    #
    # print(res1)
    # print(res2)
    # print(res0)

    # 写法2 (更简洁)
    results = await asyncio.gather(
        test(),
        work(1, 1),
        work(2, 1),
    )
    print(results)  # 按顺序返回结果

    print(f"main end: {round(time.time() - start, 4)}s")
    return "result-main"


# result = asyncio.run(main1())
# print(result)
# print("-" * 100)

""" 拓展示例 """

async def download(http, url: str, n: int):
    print(f"{n} downloading: {url}")

    # 【网络请求】-> I/O 等待
    response = await http.get(url)
    # 【等待数据】-> 数据可能分多次传输, 需要等待数据全部读取完, 也属于I/O等待
    content = await response.read()

    print(f"{n} download finish")

    os.makedirs(f"./download", exist_ok=True)
    with open(f"./download/{n}.png", "wb") as f:
        f.write(content)

    # 【释放链接资源】
    await response.release()


async def main2():
    print("main start")
    start = time.time()

    urls = [
        "https://img1.baidu.com/it/u=2045120719,1916684086&fm=253&fmt=auto&app=120&f=JPEG?w=500&h=909",
        "https://img1.baidu.com/it/u=871743427,176110595&fm=253&app=138&f=JPEG?w=889&h=500",
    ]

    http = aiohttp.ClientSession()

    coroutines = [download(http, url, i + 1) for i, url in enumerate(urls)]
    await asyncio.gather(*coroutines)

    await http.close()

    print(f"main end: {round(time.time() - start, 4)}s")


asyncio.run(main2())
