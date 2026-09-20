"""客服页面与转发端点的路由 (用户面, session 认证).

前缀 `/minimall/agent/` —— **刻意与 `/api/minimall/agent/` 分开** (那个前缀属于
issue 02 建的, 吃 X-Internal-Token 的内部端点). 两套认证是两回事:

| 前缀 | 谁在调 | 怎么证明身份 |
|------|--------|-------------|
| `/api/minimall/agent/` | CharApp 助手服务 | X-Internal-Token + 声明的 X-User-Id |
| `/minimall/agent/` | 浏览器 | 登录 cookie (session) |

混在一个前缀下的后果不是「不好看」: 一个吃 cookie 的端点和一吃令牌的端点摆在
一起, 以后加中间件, 加限流, 看日志时都得逐个分辨它们到底信谁.

Note:
    页面与 SSE 端点同在本文件 (没有像 `urls_html.py` / `urls_api.py` 那样按
    「页面 / 接口」再拆一刀): 这两条路由**同属一个接入面** —— 同一个前缀, 同一套
    session 认证, 一起改一起测. 平台那两个文件的切法是按**认证模型**分的 (买家
    DRF 接口 / 页面), 这里两条路由的认证模型是同一个, 再拆只会让人来回跳.
"""

from django.urls import path

from .views_bff import AgentCancelView, AgentChatView, AgentPageView

app_name = "minimall_bff"

urlpatterns = [
    path("", AgentPageView.as_view(), name="page"),
    path("chat/", AgentChatView.as_view(), name="chat"),
    path("cancel/", AgentCancelView.as_view(), name="cancel"),
]
