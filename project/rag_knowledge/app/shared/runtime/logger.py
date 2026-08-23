"""
项目日志工具类
基于 loguru 实现, 支持 .env 配置控制台/文件双输出, 自动生成 logs/app_年月日.log
特性:
1. 配置驱动: 通过 .env 开关输出, 修改日志级别
2. 自动路径: 文件日志默认输出到 项目根/logs/app_YYYYMMDD.log
3. 自动清理: 按配置保留日志, 自动删除过期文件
4. 中文友好: utf-8 编码, 彻底解决中文乱码
5. 异步安全: 开启异步入队, 支持多线程/异步场景, 避免日志错乱
6. 开箱即用: 项目所有模块直接导入 logger 即可使用
7. 位置终极精准: 穿透 loguru 内部 + 工具类自身, 完美显示业务模块实际调用位置
"""

import inspect
import os
import sys
import time
from collections.abc import Mapping
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger as _loguru_logger

# --------------- 第一步: 加载 .env 配置文件 ---------------
load_dotenv()

# --------------- 第二步: 读取 .env 配置(带默认值, 防止配置缺失) ---------------
LOG_CONSOLE_ENABLE = os.getenv("RK_LOG_CONSOLE_ENABLE", "True").lower() == "true"
LOG_CONSOLE_LEVEL = os.getenv("RK_LOG_CONSOLE_LEVEL", "INFO").upper()
LOG_FILE_ENABLE = os.getenv("RK_LOG_FILE_ENABLE", "True").lower() == "true"
LOG_FILE_LEVEL = os.getenv("RK_LOG_FILE_LEVEL", "INFO").upper()
LOG_FILE_RETENTION = os.getenv("RK_LOG_FILE_RETENTION", "7 days")

# --------------- 第三步: 定义日志路径(自动推导项目根) ---------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE_NAME = "app_{time:YYYYMMDD}.log"
LOG_FILE_PATH = LOG_DIR / LOG_FILE_NAME

# --------------- 第四步: 定义日志格式(彩色, 结构化, 易读) ---------------
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name: <20}</cyan>:<cyan>{function: <15}</cyan>:<cyan>{line: <4}</cyan> - "
    "<level>{message}</level>"
)


# --------------- 第五步: 初始化日志配置(核心方法) ---------------
def init_logger():
    """
    初始化全局日志配置
    1. 移除 loguru 默认控制台输出(避免重复打印)
    2. 根据 .env 配置开启/关闭控制台输出
    3. 根据 .env 配置开启/关闭文件输出(自动创建 logs 文件夹)
    4. 配置日志格式, 级别, 分割, 保留策略
    :return: 配置完成的 loguru logger 实例
    """
    # 1. 移除 loguru 默认的控制台输出
    _loguru_logger.remove()

    # 2. 配置控制台输出(若 .env 开启)
    if LOG_CONSOLE_ENABLE:
        _loguru_logger.add(
            sink=sys.stdout,
            level=LOG_CONSOLE_LEVEL,
            format=LOG_FORMAT,
            colorize=True,
            enqueue=True,
        )

    # 3. 配置文件输出(若 .env 开启)
    if LOG_FILE_ENABLE:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _loguru_logger.add(
            sink=LOG_FILE_PATH,
            level=LOG_FILE_LEVEL,
            format=LOG_FORMAT,
            rotation="00:00",
            retention=LOG_FILE_RETENTION,
            encoding="utf-8",
            enqueue=True,
            backtrace=True,
            diagnose=True,
        )

    return _loguru_logger


# --------------- 第六步: 初始化并终极修正全局 logger ---------------
base_logger = init_logger()


def fix_log_position(record):
    """遍历调用栈, 跳过 loguru 内部帧 + 工具类自身帧, 提取业务代码实际调用位置"""
    for frame in inspect.stack():
        # 终极过滤: 排除 loguru 内部 + 排除工具类 logger.py 自身, 直接定位业务模块
        if (
            "_logger.py" in frame.filename or frame.function == "_log"
        ) or "logger.py" in frame.filename:
            continue
        # 更新日志字段为业务代码实际位置
        record.update(
            name=frame.filename.split("/")[-1].split("\\")[-1],
            function=frame.function,
            line=frame.lineno,
        )
        break


# 应用终极修复, 导出全局可用的 logger
logger = base_logger.patch(fix_log_position)


def _trace_id(state) -> str:
    if isinstance(state, Mapping):
        return str(state.get("session_id") or state.get("task_id") or "-")
    return "-"


def node_log(node_name: str):
    def deco(func):
        @wraps(func)
        def wrapper(state, *args, **kwargs):
            trace_id = _trace_id(state)
            start_ts = time.time()
            logger.info(f"[{node_name}] 节点开始, 追踪 ID={trace_id}")
            try:
                result = func(state, *args, **kwargs)
                cost_ms = int((time.time() - start_ts) * 1000)
                logger.info(
                    f"[{node_name}] 节点完成, 追踪 ID={trace_id}, 耗时={cost_ms}ms"
                )
                return result
            except Exception:
                logger.exception(f"[{node_name}] 节点异常, 追踪 ID={trace_id}")
                raise

        return wrapper

    return deco


def step_log(step_name: str):
    """
    步骤日志装饰器:
    - 自动打印 步骤开始 / 步骤完成 / 步骤异常(含堆栈)
    - 不吞异常, 保持原有业务语义
    """

    def deco(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_ts = time.time()
            logger.info(f"[{step_name}] 步骤开始")
            try:
                result = func(*args, **kwargs)
                cost_ms = int((time.time() - start_ts) * 1000)
                logger.info(f"[{step_name}] 步骤完成, 耗时={cost_ms}ms")
                return result
            except Exception:
                logger.exception(f"[{step_name}] 步骤异常")
                raise

        return wrapper

    return deco


# -------------------------- 测试代码(验证修复效果) --------------------------
if __name__ == "__main__":
    print(f"日志文件输出路径: {LOG_FILE_PATH}")

    # [debug]开发调试用, 记录细节, 变量, 上线一般关闭
    logger.debug("[调试]进入主程序入口, 开始初始化日志")

    # [info]正常流程日志, 记录程序运行状态
    logger.info("[信息]logger.py 内部调用(仅测试, 业务模块调用会显示正确文件名)")

    # [warning]警告, 不影响运行, 但需要关注
    logger.warning("[警告]未读取到自定义配置, 使用默认配置")

    # [error]当前功能出错, 程序不会崩溃, 但业务失败
    logger.error("[错误]logger.py 内部调用(仅测试, 业务模块调用会显示正确文件名)")

    # [exception]必须在 except 里, 自动打印完整异常堆栈(定位 bug 用)
    try:
        result = 10 / 0
        logger.info(f"[信息]业务计算结果: {result}")
    except Exception:
        logger.exception("[异常]捕获到业务异常, 输出完整堆栈信息")
