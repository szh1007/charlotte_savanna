"""业务接入点: 运行上下文 RunContext + 工具提供者 ToolProvider.

一句话理解: 这两个东西是**框架留给业务的插座**. 框架规定插座的形状
(`provide(上下文) -> 工具列表`), 业务决定插上去的是什么; 插座本身不带电,
也不认识插上来的电器.

它解决什么问题: 当业务要**代表某一个具体用户**操作时 (回答「**我的**订单到哪了」),
工具就必须知道「当前是谁」. 但工具的**参数表会被序列化成文本发给模型看** ——
身份一旦成了参数, 模型就看得见, 也可能被诱导填别人的值. 这两个类型的作用就是
让身份绕开参数表: 业务把它放进 `RunContext.payload`, 装配时
`await provider.provide(context)` 用闭包裹进工具函数, 模型从头到尾不知道有这么
个东西 —— 「诱导模型去查别人的数据」这条攻击路径因此**根本不存在**.

一次完整的装配是这样走的 (箭头两端各自归谁, 一眼看清)::

    RunContext(thread_id=..., payload={"user_id": ...})   <- 业务填
        │  await provider.provide(context)
        ▼
    list[Tool]                                            <- 业务交, 身份已裹进闭包
        │  ChatSession(..., tools=...)
        ▼
    AgentLoop(model, tools, ...)                          <- 框架只认「一串工具」

**调用点是装配代码**, 不是框架 —— 业务自己的命令行入口 / 服务层负责把这条线
接起来. 身份从哪来是业务的事, 而且那一段会换 (演示时取命令行参数, 上线后换成
服务端从请求头转发), 换的时候只动最前面「从哪取」那一小段, 后面一行不改.

与 hooks 的分工 (两者都是扩展点, 别混): hooks 是**运行中介入** (框架在五个时刻
喊插件), ToolProvider 是**运行前装配** (这次运行拿哪些工具进场). 前者可选, 后者
也可以不填 —— 不填就是没有工具, 纯聊天.

刻意不做的三件事:

1. **不规定 payload 里有什么**. 框架永远不读它, 连「应该有 user_id」这种假设都
   不写. 一旦框架认了某个字段名, 换个业务就得改框架 —— 面向代码库的助手并没有
   「当前用户」这个概念, 而它要用同一个框架.
2. **不做成基类**. 与 `model.protocol.ChatModel` 同款: 结构化协议, 有那个方法
   就算, 业务不需要 import 一个基类来继承 (`isinstance` 也因此不适用 —— 不是
   `runtime_checkable`, 与既有协议保持一致).
3. **不提供「从提供者一路装到会话」的工厂**. 那会把框架绑死在「一次运行 = 一个
   会话」这个假设上, 也把装配顺序固化进框架; 现在的形状 (业务自己 await 一下,
   再把工具交出去) 已经够短, 且顺序的每一步都在业务眼皮底下.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from CharAgent.tool import Tool


@dataclass(frozen=True, slots=True)
class RunContext:
    """一次运行的上下文: 会话编号 + 一块**框架不解释**的载荷.

    为什么是这两个字段:

    - `thread_id` 是框架**认识**的东西 (快照按它分区, 见 checkpoint), 框架必须
      看得见它. 业务按 PRD §4.11 把它拼成 `业务:用户ID:对话ID` —— 于是多用户
      隔离是快照分区**免费**带来的, 不用额外设计.
    - `payload` 是框架**不认识**的东西. 里面放什么完全由业务决定 (用户 ID、租户、
      权限、语言...), 框架只保证原样交给提供者, 不读、不校验、不补默认值.

    为什么不可变 (frozen): 上下文是**这次运行**的身份凭据, 装配好之后中途被改掉
    是最难查的一类 bug (工具取到一半的身份). 载荷字典本身仍可改 (Python 没有
    廉价的办法连内容一起冻住), 约定是「装配完就别动它」.

    attributes:
        thread_id: 会话编号 (与 ChatSession 的 thread_id 同一个值 —— 两处不一致
            会让快照分区与运行上下文对不上).
        payload: 业务自定义的载荷; 默认空字典, 纯聊天场景不必填.
    """

    thread_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)


class ToolProvider(Protocol):
    """SPI: 业务实现它, 按本次运行的上下文交出这次要用的工具集.

    形状只有一个方法, 且**是异步的** —— 因为真实业务在这里通常要打一次内部接口
    拉权限或配置 (业务工具的既有约定也是异步, 见 PRD §4.4: 框架把同步工具扔进
    线程池, 阻塞会占满它).
    """

    async def provide(self, context: RunContext) -> Sequence[Tool]:
        """按上下文产出这次运行开放给模型的工具.

        Args:
            context: 装配代码填好的运行上下文.

        Returns:
            Sequence[Tool]: 工具集 (list / tuple 都可以); 空表示不开放工具.
        """
        ...
