"""电商客服的 9 个只读工具: 模型能对这个商城做的全部事情 (L1a).

一句话理解: 每个工具 = 一次「业务动作」, 背后是 `client.py` 的一个方法。工具
本身**不含逻辑**, 只做三件事: 声明参数 (写进 schema 给模型看)、带上身份调商城、
把结果或「没有」翻成模型看得懂的话。

三条贯穿全篇的约定:

1. **身份不进参数表** (PRD §4.2, 本项目的核心安全设计). 9 个工具的入参全是业务
   字段 —— 没有一个叫 `user_id` 的参数。身份由 `build_tools(client, user_id)`
   在装配时裹进闭包, 模型既看不见也无从伪造。「查一下别人的订单」这条攻击路径
   因此**根本不存在**, 而不是「被挡住了」。
2. **涉及「我」的工具一律用 my 标识** (PRD §4.4): `get_my_cart` / `list_my_orders`
   / `get_my_order` / `get_my_profile` / `list_my_addresses`。这既是给模型的语言
   提示 (「这个工具查的是当前对话者自己的东西」), 也是给评审者的信号 —— 名字里
   的 my 就是「身份不可指定」那句话。
3. **全部是 `async def`** (PRD §4.4). 框架把同步工具函数扔进线程池
   (`tool/executor.py` 的 `_invoke`), 9 个同步工具会互相排队; 写成协程才真的
   并发。有一条测试专门遍历这 9 个函数断言这一点。

金额一律**原样转达**, 不做 float 转换: 商城的序列化契约就是 2 位小数字符串
(`serializers_agent.py`), 而二进制浮点表示不了 0.1 —— 模型只需要照读, 不需要
替买家做算术 (`1.10` 与 `1.1` 的差别在这里是零收益的风险)。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from pydantic import Field

from CharAgent.tool import Tool, tool
from CharApp.minimall.client import (
    MinimallClient,
    MinimallNotFoundError,
    Ordering,
    PageSize,
)


async def _fetch(
    call: Callable[..., Awaitable[Any]],
    *,
    missing: str | None = None,
    **kwargs: Any,
) -> str:
    """调一次商城接口, 把结果翻成回填给模型的文本。

    Args:
        call: 客户端方法 (如 `client.search_products`)。
        missing: 商城答 404 时回给模型的话。**只有按标识符查单个资源的工具会传它**
            (查商品详情 / 查订单详情) —— 那两个端点的 404 是「你要找的东西不存在」,
            是一句答案。集合类端点 (购物车 / 订单列表 / 地址 / 分类 / 精选) 的 404
            只可能是故障, 客户端已经把它挡成 `MinimallError` 了, 走不到这里 ——
            那些工具因此不传 `missing`: 与其留一句永远说不出口的话, 不如没有。
        **kwargs: 透传给客户端方法 (含 `user_id`)。

    Returns:
        str: 成功时是响应体的 JSON 文本 (中文不转义, 模型读起来与买家说的话一致)。

    Raises:
        MinimallNotFoundError: 没传 `missing` 却收到 404 —— 说明「哪个端点的 404 是
            答案」这个判断在客户端与工具之间对不上了, 当场炸出来比编一句话强。
        MinimallError: 商城故障 (连不上 / 超时 / 5xx / 404 但端点不该那样答)。

    Note:
        承接上面: 查无此物的 404 **不抛异常** —— 抛出去只会让模型收到一句「内部
        错误」, 而它本该对买家说「没有查到这笔订单」。故障照旧上抛, 交给框架统一的
        内部错误文案 (`tool/executor.py` 的 `INTERNAL_ERROR_TEXT`, 那句文案说的正是
        「别用相同参数重试」)。
    """
    try:
        data = await call(**kwargs)
    except MinimallNotFoundError:
        if missing is None:
            raise
        return missing
    return json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 商品 / 分类 (不需要买家身份)
# ---------------------------------------------------------------------------


def _search_products(client: MinimallClient, user_id: int) -> Tool:
    """搜索商品工具 (关键词 / 分类 / 价格区间 / 排序 / 分页)。"""

    @tool
    async def search_products(
        keyword: Annotated[
            str | None,
            Field(
                description="搜索关键词, 会同时匹配商品名与描述; 只按分类或"
                "价格筛选时可以不填",
                examples=["手机"],
            ),
        ] = None,
        category: Annotated[
            str | None,
            Field(
                description="分类 slug (含其所有子分类的商品); slug 从 "
                "list_categories 的结果里取, 不要自己拼",
                examples=["digital"],
            ),
        ] = None,
        min_price: Annotated[
            float | None,
            Field(description="最低价 (元, 含等于)", ge=0, examples=[1000]),
        ] = None,
        max_price: Annotated[
            float | None,
            Field(description="最高价 (元, 含等于)", ge=0, examples=[2000]),
        ] = None,
        ordering: Annotated[
            Ordering | None,
            Field(
                description="排序: price 价格升序, -price 价格降序, name 名称, "
                "-created_at 最新上架; 不填按商城默认顺序"
            ),
        ] = None,
        page: Annotated[
            int | None,
            Field(
                description="第几页 (从 1 开始, 每页条数见 page_size); 返回体里的 "
                "total_pages 说明一共几页",
                ge=1,
                examples=[1],
            ),
        ] = None,
        page_size: Annotated[
            PageSize,
            Field(
                description="每页条数, 只能取 5 / 10 / 20 / 50 / 100 之一; 默认 100",
                examples=[100],
            ),
        ] = 100,
    ) -> str:
        """按条件搜索在售商品, 返回商品列表 (含名称、slug、价格、库存、所属分类)
        与总数/总页数. 买家想找商品、比价、按价格区间或分类筛选时使用; 已经知道
        商品 slug、只想看某一个商品的详情时改用 get_product_detail。返回体里的
        slug 是后续查详情的凭据, 报给买家时可以只报名称与价格。
        """
        return await _fetch(
            client.search_products,
            user_id=user_id,
            keyword=keyword,
            category=category,
            min_price=min_price,
            max_price=max_price,
            ordering=ordering,
            page=page,
            page_size=page_size,
        )

    return search_products


def _get_product_detail(client: MinimallClient, user_id: int) -> Tool:
    """商品详情工具 (含当前库存)。"""

    @tool
    async def get_product_detail(
        slug: Annotated[
            str,
            Field(
                description="商品 slug (英文短标识); 从 search_products 或 "
                "list_featured_products 的结果里取, 不要自己拼",
                min_length=1,
                examples=["iphone-15"],
            ),
        ],
    ) -> str:
        """按 slug 查一个商品的完整信息: 描述、价格、**当前库存**、分类路径。
        买家问「这个还有货吗」「xx 多少钱」而对话里已经出现过具体商品时使用;
        还不知道买哪个就先 search_products。库存是实时查库的当前值, 可以直接
        当作事实报给买家。
        """
        return await _fetch(
            client.get_product_detail,
            user_id=user_id,
            missing=f"没有找到 slug 为 {slug!r} 的在售商品, 请核对后重试",
            slug=slug,
        )

    return get_product_detail


def _list_categories(client: MinimallClient, user_id: int) -> Tool:
    """分类树工具。"""

    @tool
    async def list_categories() -> str:
        """列出商城的全部分类 (树形, 子分类嵌在 children 里)。买家问「都有哪些
        分类」「你们卖什么」时使用; 拿到分类后可以把它作为 search_products 的
        category 参数缩小范围。没有分类时返回空列表。
        """
        return await _fetch(client.list_categories, user_id=user_id)

    return list_categories


def _list_featured_products(client: MinimallClient, user_id: int) -> Tool:
    """精选商品工具。"""

    @tool
    async def list_featured_products() -> str:
        """列出管理员标记为精选的在售商品。买家问「有什么值得买的」「推荐几个」
        而**没有给出具体条件**时使用; 给了关键词或价格区间则改用
        search_products。返回字段与商品列表一致 (含库存)。
        """
        return await _fetch(client.list_featured_products, user_id=user_id)

    return list_featured_products


# ---------------------------------------------------------------------------
# 买家私有数据 (身份由闭包裹入, 参数表里一个字都没有)
# ---------------------------------------------------------------------------


def _get_my_cart(client: MinimallClient, user_id: int) -> Tool:
    """购物车工具 (只读)。"""

    @tool
    async def get_my_cart() -> str:
        """查看**当前买家自己**购物车里的商品 (名称、单价、数量、小计、当前库存)
        与合计金额/件数。买家问「我购物车里有什么」「加起来多少钱」时使用。
        从未加购过时返回空车 (件数 0), 那是正常情况而不是出错。
        """
        return await _fetch(client.get_cart, user_id=user_id)

    return get_my_cart


def _list_my_orders(client: MinimallClient, user_id: int) -> Tool:
    """订单列表工具。"""

    @tool
    async def list_my_orders(
        page: Annotated[
            int | None,
            Field(
                description="第几页 (从 1 开始, 每页条数见 page_size); 返回体里的 "
                "total_pages 说明一共几页",
                ge=1,
                examples=[1],
            ),
        ] = None,
        page_size: Annotated[
            PageSize,
            Field(
                description="每页条数, 只能取 5 / 10 / 20 / 50 / 100 之一; 默认 100",
                examples=[100],
            ),
        ] = 100,
    ) -> str:
        """查看**当前买家自己**的订单列表 (订单号、状态中文名、总额、件数、下单
        时间), 新单在前。买家问「我最近买了什么」「我的订单到哪了」而**没有报出
        订单号**时使用; 买家报了订单号则改用 get_my_order 看那一单的详情。
        """
        return await _fetch(
            client.list_orders,
            user_id=user_id,
            page=page,
            page_size=page_size,
        )

    return list_my_orders


def _get_my_order(client: MinimallClient, user_id: int) -> Tool:
    """订单详情工具。"""

    @tool
    async def get_my_order(
        order_no: Annotated[
            str,
            Field(
                description="24 位数字订单号 (下单时间 14 位 + 6 位买家编号 + "
                "4 位随机数); 从 list_my_orders 的结果里取, 不要自己拼",
                pattern=r"^\d{24}$",
                examples=["202609191230450000031234"],
            ),
        ],
    ) -> str:
        """按订单号查看**当前买家自己**某一笔订单的详情: 商品明细、收货地址快照、
        各状态的中文名与时间线 (下单/付款/发货/收货/取消)。买家报出订单号, 或
        追问「那一单到哪了」时使用。只能查到这个买家自己的订单 —— 别人的订单一律
        查不到 (返回「没有找到」), 这时如实告诉买家, 不要说订单不存在以外的推测。
        """
        return await _fetch(
            client.get_order,
            user_id=user_id,
            missing=f"没有查到订单 {order_no} —— 请让买家核对订单号",
            order_no=order_no,
        )

    return get_my_order


def _get_my_profile(client: MinimallClient, user_id: int) -> Tool:
    """账户信息与余额工具。"""

    @tool
    async def get_my_profile() -> str:
        """查看**当前买家自己**的账户信息: 用户名、邮箱、手机号、**余额** (元)。
        买家问「我余额还有多少」「我是用什么账号登录的」时使用。余额是 2 位小数
        字符串, 原样转达即可, 不要替买家做加减。
        """
        return await _fetch(client.get_profile, user_id=user_id)

    return get_my_profile


def _list_my_addresses(client: MinimallClient, user_id: int) -> Tool:
    """收货地址工具。"""

    @tool
    async def list_my_addresses() -> str:
        """列出**当前买家自己**的收货地址 (收货人、电话、省市区、详细地址、是否为
        默认地址)。买家问「我有几个收货地址」「东西寄到哪儿」时使用。一个地址都
        没有时返回空列表, 如实告知即可。
        """
        return await _fetch(client.list_addresses, user_id=user_id)

    return list_my_addresses


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------

# 9 个工具的工厂 (顺序即注册顺序; 名字用 my 标记「查的是自己的东西」)
_BUILDERS: tuple[Callable[[MinimallClient, int], Tool], ...] = (
    _search_products,
    _get_product_detail,
    _list_categories,
    _list_featured_products,
    _get_my_cart,
    _list_my_orders,
    _get_my_order,
    _get_my_profile,
    _list_my_addresses,
)


def build_tools(client: MinimallClient, user_id: int) -> tuple[Tool, ...]:
    """把 9 个只读工具装到「这个客户端 + 这个买家」上。

    身份 (`user_id`) 在这里被写进闭包 —— 这是它**唯一**进入工具的地方, 也因此
    永远不会出现在任何一个工具的 schema 里 (PRD §4.2).

    Args:
        client: 商城客户端 (连接池与令牌在它手里)。
        user_id: 当前买家, 命令行取自 `--user-id` (第二阶段换成 Django 转发的
            请求头, 换的是「从哪取」那一小段, 本函数一行不改)。

    Returns:
        tuple[Tool, ...]: 正好 9 个只读工具。
    """
    return tuple(builder(client, user_id) for builder in _BUILDERS)


__all__ = ["build_tools"]
