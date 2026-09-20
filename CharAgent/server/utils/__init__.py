"""server 包支撑子包 (utils/): 静态支撑物, 不含行为类.

对齐 agent/utils 与 stream/utils 惯例 —— 顶层 (app.py / sessions.py /
runs.py / sse.py) 放行为模块, 静态支撑按主题收进子包:
- types.py   两个接入协议 (ContextProvider / SessionProvider) + 事件流与 HTTP
             之间的契约常量 (终局 error 的 code / 请求体字段名 / 响应头名 /
             SSE media type)
- errors.py  异常族: ServerError 基类 + 四个「知道自己是什么状态码」的错误
             (认证 / 配置 / 请求不成形 / 会话正忙)

模块内部 import 走具体模块路径 (server.sessions, server.utils.types 等), 不绕
包门面, 避免隐式循环依赖.

大白话版: 这里放 server 包的「静态零件」(插座形状 + 常量 + 错误定义), 不含
任何行为逻辑. 插座本身不带电, 也不认识插上来的电器 (与 agent/provider.py 同一条
纪律).
"""

from __future__ import annotations
