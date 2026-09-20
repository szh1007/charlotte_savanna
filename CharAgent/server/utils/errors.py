"""server 层异常语义: 一族「知道自己该回什么状态码」的错误.

对齐 stream / agent 包的错误组织 (纯 Exception 基类), 但多了一样东西 —— 这些
错误**自带 HTTP 状态码**. 因为 server 层是框架里唯一说 HTTP 的地方: 业务在回调
里抛一个错误, 得有人把它翻成响应; 翻译规则写在这里 (每个类自带), 翻译动作只有
一处 (app.py 注册的异常处理器), 于是「什么错 → 什么状态码」不必散在路由里.

| 类 | 谁抛 | 默认状态码 | 场景 |
|----|------|-----------|------|
| ServerAuthError | 业务 (第一个插座) | 401 | 令牌不对 / 认不出这次运行是谁的 |
| ServerConfigError | 业务 (任一回调) | 503 | 配置缺失 / 服务没就绪 (如令牌没配) |
| InvalidRequestError | 框架 | 400 | 请求体不成形 (不是 JSON / 缺 message) |
| ThreadBusyError | 框架 | 409 | 同一会话已有一次运行在跑 (不并发写) |
| RunNotFoundError | 框架 | 404 | 取消时这次运行已不在册 (跑完了 / 不存在 / 不是你的) |

三条纪律:

1. **消息是事实, 不是人话.** 框架把 code 与 message 原样转发; 面向用户的降级
   话术归业务 —— 与事件流同一条规矩 (框架不编造用户文案). 业务按 code 决定
   给用户看什么.
2. **不泄漏细节是业务的责任, 但默认值已经替它想好.** ServerAuthError 的默认
   消息刻意写成「认证失败」这类不区分「令牌错」还是「用户不存在」的说法: 业务
   若把细节直接塞进 message, 等于亲手交给调用方; 要排查请自己记日志.
3. **响应体与事件流的 error 载荷同一个形状** (`{"error": {"code", "message"}}`),
   于是同一次失败在「HTTP 响应」与「事件流」两条通道上长得一样, 客户端一套
   解析吃两边.

为什么不是 HTTPException: 那是 FastAPI 的错误类型, 业务与框架就得各自 import
一套; 框架自己的错误族让「谁抛的」与「默认怎么翻」都留在框架里, 换成别的传输
(CLI / 消息队列) 时这一族还能复用语义.
"""

from __future__ import annotations


class ServerError(Exception):
    """server 层错误基类: 三个字段, 一处翻译.

    attributes:
        code: 机器读的错误码 (客户端按它决定文案与重试策略).
        status_code: 建议的 HTTP 状态码 (框架按它回响应).
    """

    def __init__(self, message: str, *, code: str, status_code: int) -> None:
        """
        Args:
            message: 事实性说明 (原样进响应体; 不是面向用户的话术).
            code: 机器读的错误码.
            status_code: 建议的 HTTP 状态码.
        """
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    @property
    def message(self) -> str:
        """事实性说明 (构造时给的那个字符串, 不加工)."""
        return str(self)


class ServerAuthError(ServerError):
    """认证 / 解析失败 (由业务的第一个插座抛出).

    默认消息与默认码都不区分失败细节 —— 想区分的是日志, 不是响应 (见模块
    docstring 第 2 条). 要换个说法就传 message, 要个别的状态码就传
    status_code (例如用 403 表达「认出来了但不许」).
    """

    def __init__(
        self,
        message: str = "认证失败",
        *,
        code: str = "unauthorized",
        status_code: int = 401,
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)


class ServerConfigError(ServerError):
    """业务侧配置缺失 / 服务没就绪 (缺环境变量、依赖没起、模型没配...).

    状态码取 503 而不是 500: 这不是「代码挂了」而是「现在跑不了」, 客户端据此
    可以重试, 排查时也一眼分清「配错了」与「有 bug」.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "not_configured",
        status_code: int = 503,
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)


class InvalidRequestError(ServerError):
    """请求体不成形 (不是 JSON / 缺 message / 字段类型不对) —— 框架自己抛."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_request",
        status_code: int = 400,
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)


class ThreadBusyError(ServerError):
    """同一会话已有一次运行在跑 —— 框架自己抛 (见 DESIGN #20: 同一 thread 不并发写).

    为什么是拒绝而不是排队: 静默排队在界面上与卡死没有区别, 而队列策略 (排多久 /
    排到第几个 / 满了怎么办) 是产品决定, 框架不该替业务发明一个. 拒绝是一次
    明确的、可被客户端解释的失败 —— 前端可以提示「上一句还在答」.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "thread_busy",
        status_code: int = 409,
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)


class RunNotFoundError(ServerError):
    """取消时这次运行已经不在册 —— 框架自己抛 (见 app.cancel_run).

    **三种情况共用这一个错误**, 因为本层分不出来, 也不该分: 登记表只记「在跑的」,
    跑完即出册, 于是「已经跑完了」「压根没这个编号」「这段会话里没有它」(即别人的
    运行) 在取消的那一刻是同一件事 —— 都是「这次运行不在了」. 给客户端一个**明确**
    的回答就够了 (404 + 机器读的码), 而它该做什么也很清楚: 什么都不用做, 那次运行
    本来就已经不在跑了.

    为什么把「别人的运行」也并进来而不回 403: 403 会确认「这个编号真实存在过」,
    那是一条白送的情报 (可以拿它枚举别人的运行). 想区分的是日志, 不是响应 ——
    与 ServerAuthError 同一条纪律.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "run_not_found",
        status_code: int = 404,
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)
