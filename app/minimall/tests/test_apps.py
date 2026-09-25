"""首次请求预热: **两种处理器**的递法都不能把它打挂 (2026-09-26 修的一个缺陷).

为什么单开一个文件: 这个接收函数挂在 Django 的 `request_started` 上, 而那个信号
**两种处理器递的字段不一样** —— WSGI 递 `environ`, ASGI 递 `scope`. 签名里写死其中
一个, 另一种部署下的表现是「第一个请求 500」(而且只有第一个请求, 之后那个"只热一次"
的标记自己把症状抹掉), 静态检查与别的用例都看不出来.

所以这里两层都测:

1. **真的走一次 ASGI 处理器** (`ASGITransport` + Django 的 ASGI app) —— 形状由 Django
   自己递, 不是我们猜的; 顺便钉住"第一次请求不是 500"。
2. 直接按两种形状各调一次接收函数 —— 契约写清楚。

缓存预热本身被替身顶掉 (`warmup_cache` 不跑): 这一页测的是**接线**, 而真跑它会拿测试
库的数据去写那套 Redis 键 (见 `cache.py`), 与本用例无关.
"""

from __future__ import annotations

from unittest import mock

import httpx
from django.core.asgi import get_asgi_application
from django.test import SimpleTestCase

from app.minimall import apps

# 预热函数住在 cache 里, 而 apps 是**进函数时**才 import 它的 —— 于是这条路径能被打桩
WARMUP_PATH = "app.minimall.cache.warmup_cache"


class FirstRequestWarmupTest(SimpleTestCase):
    """`_warmup_on_first_request` 的两种递法 + "只热一次"."""

    def setUp(self):
        # 那个标记是模块级的 (进程内只热一次), 所以每个用例自己收好原值再复位
        self._original_warmed = apps._warmed
        apps._warmed = False

    def tearDown(self):
        apps._warmed = self._original_warmed

    async def test_the_first_asgi_request_does_not_blow_up(self):
        """**本文件的核心**: 真走一次 ASGI 处理器, 第一次请求不能是 500.

        判据取"不是 500"而不是具体状态码: 这一条只关心信号那一路没把它打挂 ——
        打一条不存在的路径, 期望是干净的 404.
        """
        with mock.patch(WARMUP_PATH) as warmup:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=get_asgi_application()),
                base_url="http://minimall.test",
            ) as client:
                response = await client.get("/no-such-path/")

        self.assertEqual(response.status_code, 404, "第一个 ASGI 请求炸了 (500)")
        warmup.assert_called_once_with()

    async def test_it_takes_whatever_the_handler_sends(self):
        """除 `sender` 外一概收下不看 —— 换一个处理器的递法不必回来改这一行.

        `environ` / `scope` / 什么都没有, 三种都得过 —— 它关心的是「来了一次请求」,
        不是「那次请求是什么」.
        """
        for extra in ({"environ": {}}, {"scope": {}}, {}):
            with self.subTest(extra=extra):
                apps._warmed = False
                with mock.patch(WARMUP_PATH) as warmup:
                    apps._warmup_on_first_request(sender=object, **extra)

                warmup.assert_called_once()

    async def test_it_only_warms_once(self):
        """第二次请求不再热 —— 那个标记的意义就在这里 (每个请求都热一次等于没热)."""
        with mock.patch(WARMUP_PATH) as warmup:
            apps._warmup_on_first_request(sender=object, scope={})
            apps._warmup_on_first_request(sender=object, scope={})
            apps._warmup_on_first_request(sender=object, environ={})

        warmup.assert_called_once()
