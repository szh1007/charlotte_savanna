"""
单路召回的失败隔离 (issue C02).

查询图在 `extract_keywords` 之后**并行**跑三路召回 (向量 / HyDE / 联网), 三路汇到
`merge_retrieve`. 此前任意一路抛异常, LangGraph 就把整张图判为失败 ——
**一路挂了, 另外两路白跑, 用户拿到 500**.

这里把「单路失败」降级成「这一路没有贡献」: 记一条带堆栈的 error 日志, 返回空列表.

**代价要说清**: 这条路会连编程错误一起吞掉 —— 召回代码里的 bug 会表现为
「检索不到」而不是报错. 所以日志必须是 error 且带 `exc_info=True`,
排查时能一眼看出「这次答不出来是因为某一路炸了」, 而不是真的没内容.
"""

from collections.abc import Callable

from .logger import logger


def isolate_route[T](route_name: str, produce: Callable[[], T], fallback: T) -> T:
    """
    执行一路召回, 失败时降级返回 fallback.

    Args:
        route_name: 这一路的名字 (进日志用, 如「向量召回」)
        produce: 真正干活的零参可调用对象
        fallback: 失败时的返回值 (本项目一律传空列表)

    Returns:
        成功时是 `produce()` 的结果, 失败时是 `fallback`
    """
    try:
        return produce()
    except Exception as e:
        logger.error(
            f"{route_name} 这一路失败, 按「没有贡献」降级继续"
            f" (另外两路不受影响): {e!s}",
            exc_info=True,
        )
        return fallback
