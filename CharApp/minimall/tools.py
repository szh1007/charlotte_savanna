"""电商客服的 17 个工具: 模型能对这个商城做的全部事情 (9 只读 + 8 写).

一句话理解: 每个工具 = 一次「业务动作」, 背后是 `client.py` 的一个方法。工具
本身**不含逻辑**, 只做三件事: 声明参数 (写进 schema 给模型看)、带上身份调商城、
把结果或「没有」翻成模型看得懂的话。

四条贯穿全篇的约定:

1. **身份不进参数表** (PRD §4.2, 本项目的核心安全设计). 17 个工具的入参全是业务
   字段 —— 没有一个叫 `user_id` 的参数。身份由 `build_tools(client, user_id)`
   在装配时裹进闭包, 模型既看不见也无从伪造。「查一下别人的订单」这条攻击路径
   因此**根本不存在**, 而不是「被挡住了」。
2. **涉及「我」的工具一律用 my 标识** (PRD §4.4): `get_my_cart` / `list_my_orders`
   / `get_my_order` / `get_my_profile` / `list_my_addresses` / `cancel_my_order`
   / `list_my_refunds`. 这既是给模型的语言提示 (「这个工具查的是当前对话者自己
   的东西」), 也是给评审者的信号 —— 名字里的 my 就是「身份不可指定」那句话.
   (购物车那四个写工具没带 my: 它们操作的对象是**车里的东西**而不是「我的档案」,
   名字按动作本身叫更准 —— 见下面那一节的说明.)
3. **全部是 `async def`** (PRD §4.4). 框架把同步工具函数扔进线程池
   (`tool/executor.py` 的 `_invoke`), 同步工具会互相排队; 写成协程才真的并发.
   有一条测试专门遍历这些函数断言这一点.
4. **会改数据的工具打 `annotations`** (PRD §4.6): L2 新加的 8 个里, 7 个会改数据
   (`add_to_cart` / `update_cart_item` / `remove_cart_item` / `clear_cart` /
   `place_order` / `cancel_my_order` / `request_refund`) 带上
   `{WRITE_ANNOTATION_KEY: True}`, 给业务侧的护栏插件认人用. 第 8 个 `list_my_refunds`
   **只是读**退款列表, 所以不打 —— 它不该占买家的写预算. 框架**只透传不解释**
   这个标记 (与 `RunContext.payload` 同一条纪律), 所以它也不进模型的 schema.

金额一律**原样转达**, 不做 float 转换: 商城的序列化契约就是 2 位小数字符串
(`serializers_agent.py`), 而二进制浮点表示不了 0.1 —— 模型只需要照读, 不需要
替买家做算术 (`1.10` 与 `1.1` 的差别在这里是零收益的风险)。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from pydantic import Field

from CharAgent.tool import Tool, ToolActionableError, tool
from CharApp.minimall.client import (
    MinimallClient,
    MinimallNotFoundError,
    MinimallRefusalError,
    Ordering,
    PageSize,
)

# 写操作的标记键 (打在写工具的 annotations 上, 护栏插件靠它认人)
WRITE_ANNOTATION_KEY = "writes"

# 商品 slug 的形状 —— 商城侧的路由是 `<slug:slug>`, 这个 pattern 与它对齐.
# 写进 schema 的好处是模型填错当场被拦, 而不是拼出一个打到别处的路径
SLUG_PATTERN = r"^[a-zA-Z0-9_-]+$"


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


class RefusedActionError(ToolActionableError):
    """商城按业务规则拒了这次动作 —— 文案照旧, 变的是框架给这次执行记的 `status`.

    **抛而不返回** (2026-09-22 改): 返回一句「操作没有完成 …」的话, 框架把它当成
    正常结果 (`status=ok`), 页面按**成功**话术渲染 —— 买家看到「订单已取消」, 而
    订单根本没动. 抛出去才走失败那条路 (executor 把消息原文透传, 页面换 failed
    话术), 而**模型收到的文本一个字不变**.

    借的是 `ToolActionableError` 的**透传**语义 (executor 认出它就照原样把消息回填
    模型, 见 `CharAgent/tool/executor.py`); 它文档里那句「消息必须让模型能修正重试」
    在这里要反着读 —— `_REFUSAL_HINTS` 补的那句多半是「不要重试同一个动作」, 而框架
    自己的重试策略也把这类异常判成不可重试.

    `code` 保留商城那份错误码 (`views_agent.ERROR_CODES` 的键), 留给将来的插件看.
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


async def _act(call: Callable[..., Awaitable[Any]], **kwargs: Any) -> str:
    """调一次接口并把结果翻成回填给模型的文本 —— 与 `_fetch` 相对的**动作侧**.

    叫它「写」那一档并不准: 用它的一共 8 个工具, 7 个写 + `list_my_refunds` 这个
    **只读**的. 那个只读工具走这条路, 是因为它要的失败语义与写工具一样 —— 商城按规则
    拒了就如实报 failed, 而不是像 `_fetch` 那样翻成一句答案. 被拒的读也显示 failed,
    这是对的.

    失败那一支: 商城按业务规则说不时 (库存不够 / 状态不允许 / 没有默认地址), 它给的
    那句中文就是最准的, 照读; 只有它没说清**下一步**时才按错误码补一句
    (`_REFUSAL_HINTS`).

    没有 `missing` 参数: 写端点的 404 是带码的拒绝 (`order_not_found`), 走的是
    `MinimallRefusalError` 那条路, 与只读端点「404 是答案」不是一回事. 与 `_fetch`
    共用的尾巴只有一行 `json.dumps` —— 为此把两个翻译器合并成一个带开关的函数,
    换来的是两个可选参数与两个只有一半用得上的分支, 不划算.

    Raises:
        RefusedActionError: 商城按业务规则拒了这次动作 (消息面向买家).
        MinimallError: 商城故障 (连不上 / 超时 / 5xx / 不带码的 4xx) —— 交给框架
            统一的内部错误文案, 与 `_fetch` 同一条.
    """
    try:
        data = await call(**kwargs)
    except MinimallRefusalError as exc:
        raise RefusedActionError(_refusal_text(exc), code=exc.code) from exc
    return json.dumps(data, ensure_ascii=False)


# 按错误码补的「下一步做什么」. **只补商城那句话没说到的部分** —— 它已经在说
# 「为什么不行」了 (库存不足 / 状态不允许), 再复述一遍只是浪费 token.
#
# 判据是: 照着商城原文念, 模型会不会卡住, 或者去重试同一件事? 会 → 补一句.
#
# 键取自商城侧的 `views_agent.ERROR_CODES` (那边才是错误码的权威). 键对不上时
# 这里静默不补, 退化成照念原文 —— 少一句提示, 不会说错话.
_REFUSAL_HINTS: dict[str, str] = {
    "cart_empty": "先加购再下单",
    "order_not_found": "让买家核对一下订单号",
    "invalid_order_status": "让买家打开订单详情看看当前状态",
    "out_of_stock": "可以让买家换一件, 或稍后再试",
    "insufficient_stock": "可以让买家改小数量, 或换一件",
    "no_default_address": "让买家先在页面上加一条收货地址",
    "refund_already_in_progress": "用 list_my_refunds 看进度, 不要重复申请",
}


def _refusal_text(exc: MinimallRefusalError) -> str:
    """一次被拒的写操作 → 回填给模型的一句话 (带错误码对应的下一步)."""
    hint = _REFUSAL_HINTS.get(exc.code)
    return f"操作没有完成: {exc.message}" + (f" ({hint})" if hint else "")


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
# 写操作 (L2): 7 个会改数据的工具 + 1 个读退款的, 会改数据的都打 WRITE_ANNOTATION_KEY
# ---------------------------------------------------------------------------
# 这一节与上面两节的差别只有一条: 它们**改**数据 (最后一个除外). 其余照旧 —— 身份
# 照样只在闭包里, 参数表里照样没有买家; 失败照样翻成模型看得懂的话 (只是写操作的
# 失败多了「商城按规则说了不行」这一族, 见 `_act` —— 那一族是**抛**出去的, 好让框架
# 把它记成失败).
#
# 购物车四个工具没带 my 前缀, 而 cancel_my_order / list_my_refunds 带了: 判据是
# 那句话读起来指代的是什么 —— 「把购物车里这件事改了」说的是**车里那件东西**,
# 「取消我的订单」说的是**我的一笔单**. 两个都指不到别人身上 (身份在闭包里),
# 所以这是可读性取舍, 不是安全边界.


def _add_to_cart(client: MinimallClient, user_id: int) -> Tool:
    """加购工具."""

    @tool(annotations={WRITE_ANNOTATION_KEY: True})
    async def add_to_cart(
        slug: Annotated[
            str,
            Field(
                description="商品 slug (英文短标识); 从 search_products / "
                "list_featured_products 的结果里取, 在 get_my_cart 的返回体里"
                "叫 product_slug, 不要自己拼",
                pattern=SLUG_PATTERN,
                examples=["redmi-note-13"],
            ),
        ],
        quantity: Annotated[
            int,
            Field(description="加几件 (至少 1); 买家没说数量就加 1 件", ge=1),
        ] = 1,
    ) -> str:
        """把一件商品加进**当前买家自己**的购物车 (同一商品重复加会累加到同一条
        上), 返回加完之后的整车. 买家说「把这个加进购物车」「要两件」时使用;
        买什么还没定就先 search_products, 想先看看车里有什么用 get_my_cart.
        """
        return await _act(
            client.add_to_cart, user_id=user_id, slug=slug, quantity=quantity
        )

    return add_to_cart


def _update_cart_item(client: MinimallClient, user_id: int) -> Tool:
    """改购物车数量工具."""

    @tool(annotations={WRITE_ANNOTATION_KEY: True})
    async def update_cart_item(
        slug: Annotated[
            str,
            Field(
                description="商品 slug; 取自购物车返回体的 product_slug",
                pattern=SLUG_PATTERN,
                examples=["redmi-note-13"],
            ),
        ],
        quantity: Annotated[
            int,
            Field(description="改成几件; **填 0 等于把这一条拿掉**", ge=0),
        ],
    ) -> str:
        """修改**当前买家自己**购物车里某件商品的数量, 返回改完之后的整车.
        买家说「改成两件」「只要一件」时使用; 整条拿掉用 remove_cart_item 更
        直白, 全清空用 clear_cart.
        """
        return await _act(
            client.update_cart_item, user_id=user_id, slug=slug, quantity=quantity
        )

    return update_cart_item


def _remove_cart_item(client: MinimallClient, user_id: int) -> Tool:
    """移除购物车条目的工具."""

    @tool(annotations={WRITE_ANNOTATION_KEY: True})
    async def remove_cart_item(
        slug: Annotated[
            str,
            Field(
                description="商品 slug; 取自购物车返回体的 product_slug",
                pattern=SLUG_PATTERN,
                examples=["redmi-note-13"],
            ),
        ],
    ) -> str:
        """从**当前买家自己**的购物车里拿掉一件商品, 返回拿掉之后的整车. 买家说
        「不要这个了」「把它删掉」时使用; 只想改数量用 update_cart_item, 想整车
        清空用 clear_cart.
        """
        return await _act(client.remove_cart_item, user_id=user_id, slug=slug)

    return remove_cart_item


def _clear_cart(client: MinimallClient, user_id: int) -> Tool:
    """清空购物车工具."""

    @tool(annotations={WRITE_ANNOTATION_KEY: True})
    async def clear_cart() -> str:
        """清空**当前买家自己**的购物车 (整车都拿掉), 返回清空之后的空车. 买家说
        「购物车清空」「都不要了」时使用; 只拿掉其中一件用 remove_cart_item.
        **这是不可撤销的**: 拿掉的东西要重新加购, 所以买家话里有一丝犹豫就先问
        一句.
        """
        return await _act(client.clear_cart, user_id=user_id)

    return clear_cart


def _place_order(client: MinimallClient, user_id: int) -> Tool:
    """下单工具."""

    @tool(annotations={WRITE_ANNOTATION_KEY: True})
    async def place_order(
        address_id: Annotated[
            int | None,
            Field(
                description="收货地址 id; 不填就用买家的默认收货地址. 要指定"
                "别的地址先从 list_my_addresses 拿到 id",
                ge=1,
                examples=[5],
            ),
        ] = None,
    ) -> str:
        """把**当前买家自己**购物车里的全部商品下成一笔订单, 返回订单号与明细.
        买家说「下单」「结账」「买了」时使用; 买什么还没定就先 search_products
        加 add_to_cart, 想先确认买什么就用 get_my_cart.
        **下单不等于付钱** —— 下成的是待付款的订单, 付款要买家本人在商城的订单
        页输支付密码; 也不要向买家承诺到货时间.
        """
        return await _act(client.place_order, user_id=user_id, address_id=address_id)

    return place_order


def _cancel_my_order(client: MinimallClient, user_id: int) -> Tool:
    """取消订单工具."""

    @tool(annotations={WRITE_ANNOTATION_KEY: True})
    async def cancel_my_order(
        order_no: Annotated[
            str,
            Field(
                description="24 位数字订单号; 从 list_my_orders / get_my_order 的"
                "结果里取, 不要自己拼",
                pattern=r"^\d{24}$",
                examples=["202609191230450000031234"],
            ),
        ],
    ) -> str:
        """取消**当前买家自己**的一笔**还没付款**的订单 (只有待付款的能取消).
        买家说「取消订单」「这单不要了」时使用 —— 它**即刻生效, 不用任何人审批**,
        商品库存当场回滚; 这一单还没付过钱, 所以没有钱要退.
        **付过款就不能取消**: 已付款 / 已发货 / 已收货 / 已完成的订单都得走
        request_refund. 买家说的是「退款」时就申请退款, **不要因为"更快"就替他
        换一个动作** —— 两个动作的结果不一样, 换了他要的那件事就没做.
        """
        return await _act(client.cancel_order, user_id=user_id, order_no=order_no)

    return cancel_my_order


def _request_refund(client: MinimallClient, user_id: int) -> Tool:
    """申请退款工具."""

    @tool(annotations={WRITE_ANNOTATION_KEY: True})
    async def request_refund(
        order_no: Annotated[
            str,
            Field(
                description="24 位数字订单号; 从 list_my_orders / get_my_order 的"
                "结果里取, 不要自己拼",
                pattern=r"^\d{24}$",
                examples=["202609191230450000031234"],
            ),
        ],
    ) -> str:
        """为**当前买家自己**的一笔**付过款**的订单申请退款 (申请之后订单显示
        「退款中」). 买家说「我要退款」「这单退钱」时使用 —— **付款之后一律走这条
        路**, 货发没发出去都一样. **退多少钱不在这一步定, 也不要问买家想退多少**
        —— 金额由管理员审批时协商, 所以这个工具不收金额参数. 商城也没有「退货退款」
        这回事 (不需要把货寄回来), 不要描述任何寄回流程. 申请之后用 list_my_refunds
        看进度.
        """
        return await _act(client.request_refund, user_id=user_id, order_no=order_no)

    return request_refund


def _list_my_refunds(client: MinimallClient, user_id: int) -> Tool:
    """退款进度工具."""

    @tool
    async def list_my_refunds() -> str:
        """列出**当前买家自己**的退款申请 (订单号, 状态中文名, 协商金额, 管理员
        备注, 申请/批准/打款/驳回时间). 买家问「我的退款到哪了」「为什么被驳回」
        时使用 —— 被驳回时管理员会把原因写在备注里 (`admin_note`), 照实转达.
        金额还没协商出来时它是 null, 如实说「还没定」即可. 查订单本身用
        get_my_order.
        """
        return await _act(client.list_refunds, user_id=user_id)

    return list_my_refunds


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------

# 17 个工具的工厂 (顺序即注册顺序; 9 个只读在前, 8 个写在后 —— 只读那 9 个的
# 相对次序从 L1a 起没动过, 加工具是**往后接**而不是插队)
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
    _add_to_cart,
    _update_cart_item,
    _remove_cart_item,
    _clear_cart,
    _place_order,
    _cancel_my_order,
    _request_refund,
    _list_my_refunds,
)


def build_tools(client: MinimallClient, user_id: int) -> tuple[Tool, ...]:
    """把 17 个工具装到「这个客户端 + 这个买家」上.

    身份 (`user_id`) 在这里被写进闭包 —— 这是它**唯一**进入工具的地方, 也因此
    永远不会出现在任何一个工具的 schema 里 (PRD §4.2).

    Args:
        client: 商城客户端 (连接池与令牌在它手里)。
        user_id: 当前买家, 命令行取自 `--user-id`, 服务进程取自 Django 转发的
            请求头 —— 换的只是「从哪取」那一小段, 本函数一行不改。

    Returns:
        tuple[Tool, ...]: 正好 17 个工具 (9 只读 + 8 写).
    """
    return tuple(builder(client, user_id) for builder in _BUILDERS)


# 出去三个名字: 工具集, **写操作的标记键**, 以及「商城按规则拒了」那个异常. 前两个是
# 跨模块的约定 (护栏插件与测试都要拿标记键去读工具身上的注解), 第三个是用例要断言的
# 类型 —— 所以它虽然只在 `_act` 里抛, 也在门面上. `SLUG_PATTERN` 不是: 它只在本模块
# 里用, 出去只会让「还有谁在用它」变得难查.
__all__ = ["WRITE_ANNOTATION_KEY", "RefusedActionError", "build_tools"]
