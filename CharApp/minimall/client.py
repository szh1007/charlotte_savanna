"""商城内部端点的异步客户端: 业务侧唯一一处「怎么跟商城说话」.

打的是商城那 17 个内部端点 (9 个只读 + 8 个写; 前缀 `/api/minimall/agent/`,
见 `app/minimall/urls_agent.py`), 一个端面一个方法.

三条约束都不是随手定的:

1. **必须异步** (`httpx.AsyncClient`). 框架把同步工具函数扔进
   `asyncio.to_thread` 执行 (见 `CharAgent/tool/executor.py` 的 `_invoke`),
   同步客户端会把线程池占满 —— 17 个工具互相排队, 「多个查询同时进行」当场
   失效 (PRD §4.4)。
2. **身份不在这里**. 本类只管「怎么打这个接口」, 不管「代表谁打」: 每个方法
   都要一个 `user_id`, 而它由 `tools.build_tools` 裹进工具的闭包, 于是永远
   进不了模型的参数表 (PRD §4.2)。
3. **连接池是进程级的**. 一个进程一个实例 (命令行入口建一个、退出时关掉):
   一次会话里问十几句也只建一次连接池, 而不是每问一句新建一批连接。

错误语义分三路 (为什么这么分): **「没有」与「不行」都是答案, 其余是故障**.
查无此单不是异常情况, 模型应当把它当事实告诉买家 ("没有查到这笔订单"), 所以
单独一个 `MinimallNotFoundError`; 写操作被商城按业务规则拒了 ("库存不足"),
那是另一个方向的答案, 由 `MinimallRefusalError` 带着错误码与中文原话上去 ——
工具层要按码决定怎么跟买家说. 网络/超时/5xx 则原样上抛, 由框架统一的
「内部错误」文案回填模型 (`tool/executor.py` 的 `INTERNAL_ERROR_TEXT` —— 它说的
正是「别用相同参数重试」)。
"""

from __future__ import annotations

from typing import Any, Literal, NamedTuple

import httpx

# 商城 Django 开发服务器的默认地址 (内部端点前缀; 由 CHARAPP_BASE_URL 覆盖)
DEFAULT_BASE_URL = "http://127.0.0.1:8000/api/minimall/agent/"

# 单次请求超时 (秒): 商城是本机服务, 10 秒还没回来说明它出问题了 ——
# 与其让命令行无限等, 不如让工具失败并让模型告诉买家「暂时查不到」.
DEFAULT_TIMEOUT_SECONDS = 10.0

# 两个请求头名 (与商城侧的 app/minimall/permissions.py / views_agent.py 一一对应)
HEADER_TOKEN = "X-Internal-Token"
HEADER_USER_ID = "X-User-Id"

# 排序写法 (商城侧的 django_filters.OrderingFilter 认这几个; 用 Literal 是因为
# 约束写在 schema 里比写在说明里更有效 —— 模型填错当场被拦, 而不是白跑一趟)
Ordering = Literal["price", "-price", "-name", "name", "-created_at"]

# 每页条数 (商城买家侧的白名单: views_buyer.py / views_html.py 都只认这 5 个值,
# 其余会被回退成默认; 这里照抄那份清单, 于是模型连填错的空间都没有)
PageSize = Literal[5, 10, 20, 50, 100]

# 出错时附在异常里的响应正文长度上限 (够定位就行, 不把整页 HTML 塞进日志)
_DETAIL_LIMIT = 200


class MinimallError(Exception):
    """调用商城内部端点失败 (网络不通 / 超时 / 非 2xx).

    消息面向**开发者与日志**, 不是给模型看的文案 —— 翻译成模型看得懂的话是
    工具层的活 (见 `tools.py` 的 `_fetch`)。

    attributes:
        status: HTTP 状态码; None 表示请求根本没到达商城 (连接失败/超时).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Refusal(NamedTuple):
    """商城拒绝一次动作时给的那两样东西 (错误码 + 中文原话).

    用具名字段而不是裸的 `tuple[str, str]`: 两样都是字符串, 顺序反了也能跑, 而
    `MinimallRefusalError` 的构造参数顺序正好是反的 (`message` 在前) —— 那种错
    没人看得出来.
    """

    code: str
    message: str


class MinimallNotFoundError(MinimallError):
    """商城明确回答「没有这个东西」(404).

    与 `MinimallError` 分开是因为两者对买家意味着完全不同的事: 这是**答案**
    (你要找的东西不存在), 那才是**故障** (商城没答上来)。
    """


class MinimallRefusalError(MinimallError):
    """商城按业务规则拒绝了这次**动作**(带错误码的 4xx) —— 写操作独有的那一族.

    与上一条同一个道理, 只是换了个方向: 这是**答案** (商城讲清了为什么不行,
    还给了面向模型的中文原话), 那才是**故障** (商城没答上来). 工具层据此回填
    一句人话, 而不是让买家听到「系统出错了」.

    attributes:
        code: 商城那边的错误码 (`views_agent.ERROR_CODES` 的键, 如
            `insufficient_stock`). 工具按它决定给不给「下一步该做什么」的补充,
            所以它必须留到工具层 —— 在这里就被压成一句话的话, 那层信息就没了.
        message: 商城给的中文一句 (为什么不行).
    """

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(f"商城拒绝了这次操作 ({code}): {message}", status=status)
        self.code = code
        self.message = message


def _present(**values: Any) -> dict[str, Any]:
    """丢掉值为 None 的查询参数.

    为什么不直接把 None 交给 httpx: 它会把 None 编成一个空值参数
    (`category=`), 而商城的过滤器读到空串会当成「筛选条件为空」——
    于是「没给」和「给了空值」在 wire 上分不开。
    """
    return {key: value for key, value in values.items() if value is not None}


def _detail(response: httpx.Response) -> str:
    """响应正文的一段摘要 (错误消息里带上, 便于定位是哪一层拒的)."""
    text = response.text.strip()
    return text[:_DETAIL_LIMIT] if text else "(空响应体)"


def _is_json(response: httpx.Response) -> bool:
    """响应是不是 JSON —— 用来分辨「商城的 404」与「别人的 404」.

    DRF 的 404 体是 `{"detail": ...}` (content-type 为 application/json);
    打到了别的路由 (基地址配错) 时拿到的是 Django 那张 HTML 404 页。
    """
    return "json" in response.headers.get("content-type", "").lower()


def _refusal(response: httpx.Response) -> Refusal | None:
    """响应体是不是商城的「按业务规则拒绝」, 是就取出 (码, 中文原话).

    写端点的 4xx 有两种长相, 这个函数就是那道分界线:

    - `{"error": {"code", "message"}}` —— **业务拒绝** (库存不够 / 状态不允许 /
      没有默认地址), 那句话是给模型看的答案, 由工具层按码补一句「下一步做什么」
    - `{"detail": ...}` 或一张 HTML 页 —— **故障** (买家身份无效 / 地址配错打到了
      别的路由), 交给框架那套内部错误文案

    把故障说成「业务上不行」就是对买家撒谎 (而提示词里明写了「不编造」),
    所以这里认不出 `error` 体就返回 None, 由调用方按故障处理.
    """
    if not _is_json(response):
        return None
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):  # 体不是 JSON 对象
        return None
    if not isinstance(error, dict) or not error.get("code"):
        return None
    return Refusal(code=str(error["code"]), message=str(error.get("message") or ""))


class MinimallClient:
    """商城内部端点的客户端: 一条共享连接池 + 一个共享令牌, 身份逐次传入.

    Args:
        base_url: 内部端点前缀 (以 `/` 结尾); 默认为本机 Django 开发服务器。
        token: 内部令牌 (CHARAPP_INTERNAL_TOKEN), 与商城侧同值。
        timeout: 单次请求超时秒数。

    谁建谁关: 本类自己建 `httpx.AsyncClient`, 用完调 `aclose()`——命令行入口在
    `finally` 里关, 与框架关模型连接池是同一处收尾。
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        token: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._http = httpx.AsyncClient(
            # base_url 必须以 `/` 结尾, 否则 httpx 合并相对路径时会吃掉最后一段
            base_url=base_url.rstrip("/") + "/",
            headers={HEADER_TOKEN: token},
            timeout=timeout,
            trust_env=False,  # 内部端点是本机地址, 不透传系统代理 (Clash 会隔离)
        )

    async def aclose(self) -> None:
        """关掉连接池 (谁建谁关)."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # 传输: 17 个方法共用的一段
    # ------------------------------------------------------------------

    async def _get(
        self,
        path: str,
        *,
        user_id: int,
        params: dict[str, Any] | None = None,
        by_identifier: bool = False,
    ) -> Any:
        """打一个 GET 并把 JSON 解出来; 失败翻成本模块的两个异常.

        Args:
            path: 内部端点路径 (相对 base_url)。
            user_id: 这次查询代表谁 (逐个请求带上)。
            params: 查询参数; None 表示不带。
            by_identifier: 这个端点是不是「按标识符要**某一个**资源」(商品 slug /
                订单号)。**决定了 404 的含义**, 见下面的 Note。

        Raises:
            MinimallNotFoundError: 按标识符查的东西不存在 (只有 by_identifier 才可能)。
            MinimallError: 连不上 / 超时 / 其余非 2xx / 404 但端点不该那样答 /
                响应不是 JSON。

        Note:
            **404 不总是「没有这个东西」** —— 这是本方法唯一需要解释的分支。
            「查无此单」是答案, 但下面两种 404 是**故障**, 说成答案就是对买家撒谎
            (而提示词里明写了「不编造」):

            1. 集合类端点 (购物车 / 订单列表 / 地址 / 分类 / 精选) 的 404。商城侧
               「空」一律是 200 + 空数组 (见 `serializers_agent.build_cart_payload`),
               所以这些端点上的 404 只可能来自别处 —— 未知买家 (`views_agent.
               _resolve_buyer` 对不存在的买家返回 404) 或 `CHARAPP_BASE_URL`
               配错打到了别的路由。
            2. 响应的 `Content-Type` 不是 JSON。DRF 的 404 体是 `{"detail": ...}`;
               打错地方时拿到的是 Django 那张 HTML 404 页。
        """
        response = await self._send("GET", path, user_id=user_id, params=params)

        if response.status_code == 404:
            if not by_identifier:
                raise MinimallError(
                    f"{path} 返回 404, 但这个端点用 200 + 空数组表示「空」—— "
                    f"多半是买家身份无效或 CHARAPP_BASE_URL 配错了: "
                    f"{_detail(response)}",
                    status=404,
                )
            if not _is_json(response):
                raise MinimallError(
                    f"{path} 的 404 不是商城的 JSON 响应 (打到了别的路由?): "
                    f"{_detail(response)}",
                    status=404,
                )
            raise MinimallNotFoundError(f"商城没有 {path} 这个资源", status=404)
        if response.status_code >= 400:
            raise MinimallError(
                f"商城对 {path} 返回 {response.status_code}: {_detail(response)}",
                status=response.status_code,
            )
        return self._json(response, path)

    async def _write(
        self,
        method: str,
        path: str,
        *,
        user_id: int,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """打一个写请求 (**业务拒绝**与**故障**在这里第一次分开).

        与 `_get` 只差在 4xx 那一支: 写端点的 4xx 多半是商城在按业务规则说不
        (库存不够 / 状态不允许 / 没有默认地址), 那句话是给模型看的答案, 必须原样
        带上去; 认不出 `error` 体的才是故障.

        Note:
            这里**没有**「404 是答案」那一层: 写端点要的东西不存在时, 商城回的是
            带码的 404 (`order_not_found`), 走的是上面的拒绝路径.
        """
        response = await self._send(method, path, user_id=user_id, body=body)
        if response.status_code >= 400:
            refusal = _refusal(response)
            if refusal is not None:
                raise MinimallRefusalError(
                    refusal.code, refusal.message, status=response.status_code
                )
            raise MinimallError(
                f"商城对 {method} {path} 返回 {response.status_code}: "
                f"{_detail(response)}",
                status=response.status_code,
            )
        return self._json(response, path)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        user_id: int,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """打一个请求 (四种方法共用的一段) —— 只翻「请求根本没打出去」那一类失败.

        状态码的解释留给调用方: 同一个 404 在只读端点是答案, 在写端点是故障,
        那是**端点语义**而不是传输层的事 (见 `_get` 与 `_write`).
        """
        try:
            return await self._http.request(
                method,
                path,
                params=params,
                json=body,
                headers={HEADER_USER_ID: str(user_id)},
            )
        except httpx.HTTPError as exc:
            raise MinimallError(
                f"请求 {path} 失败 ({type(exc).__name__}): {exc}"
            ) from exc

    def _json(self, response: httpx.Response, path: str) -> Any:
        """把 2xx 的响应体解成 JSON; 解不出来算故障 (与非 JSON 的 404 同一条口径)."""
        try:
            return response.json()
        except ValueError as exc:  # json.JSONDecodeError 是它的子类
            raise MinimallError(
                f"商城对 {path} 的响应不是 JSON: {_detail(response)}",
                status=response.status_code,
            ) from exc

    # ------------------------------------------------------------------
    # 商品 / 分类 (不需要买家身份)
    # ------------------------------------------------------------------

    async def search_products(
        self,
        *,
        user_id: int,
        keyword: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        ordering: Ordering | None = None,
        page: int | None = None,
        page_size: PageSize | None = None,
    ) -> dict:
        """商品列表: 关键词 / 分类 / 价格区间 / 排序 / 分页 (字段见列表序列化器).

        `keyword` 在 wire 上叫 `search` (商城侧的过滤器名); 分类给的是 slug。
        `page_size` 只取 `PageSize` 里的值 (商城买家侧的白名单); 不传时由商城
        决定 (默认 20)
        """
        return await self._get(
            "products/",
            user_id=user_id,
            params=_present(
                search=keyword,
                category=category,
                min_price=min_price,
                max_price=max_price,
                ordering=ordering,
                page=page,
                page_size=page_size,
            ),
        )

    async def get_product_detail(self, *, user_id: int, slug: str) -> dict:
        """单个商品详情 (含当前库存), 按 slug 查。

        slug 不存在 (或商品已下架) 时商城答 404 —— 那是**答案**, 由调用方翻成人话。
        """
        return await self._get(f"products/{slug}/", user_id=user_id, by_identifier=True)

    async def list_categories(self, *, user_id: int) -> list:
        """分类树 (只含启用分类, 子节点嵌在 children 里)。"""
        return await self._get("categories/", user_id=user_id)

    async def list_featured_products(self, *, user_id: int) -> list:
        """管理员标记为精选的在售商品 (集合量小, 商城侧不分页)。"""
        return await self._get("featured-products/", user_id=user_id)

    # ------------------------------------------------------------------
    # 买家私有数据 (user_id 决定看到谁的数据)
    # ------------------------------------------------------------------

    async def get_cart(self, *, user_id: int) -> dict:
        """该买家的购物车 (只读; 从未加购过时是空车形态, 不是 404)。"""
        return await self._get("cart/", user_id=user_id)

    async def list_orders(
        self,
        *,
        user_id: int,
        page: int | None = None,
        page_size: PageSize | None = None,
    ) -> dict:
        """该买家的订单列表 (新单在前, 分页)。"""
        return await self._get(
            "orders/",
            user_id=user_id,
            params=_present(page=page, page_size=page_size),
        )

    async def get_order(self, *, user_id: int, order_no: str) -> dict:
        """该买家某一笔订单的详情 (含明细与状态时间线)。

        不是这个买家的单 (或压根没有这个号) 时商城答 404 —— 那是**答案**: 商城侧
        按买家过滤, 所以「别人的单」与「不存在的单」在这里长得一模一样, 这也正是
        它不该被区分的原因 (区分了就等于告诉买家「这单存在, 只是不是你的」)。
        """
        return await self._get(
            f"orders/{order_no}/", user_id=user_id, by_identifier=True
        )

    async def get_profile(self, *, user_id: int) -> dict:
        """该买家的基本信息与余额。"""
        return await self._get("profile/", user_id=user_id)

    async def list_addresses(self, *, user_id: int) -> list:
        """该买家的收货地址列表。"""
        return await self._get("addresses/", user_id=user_id)

    # ------------------------------------------------------------------
    # 写操作 (issue 11 的 8 个端点): 一次调用完成一个动作
    # ------------------------------------------------------------------
    # 与只读那批同一条纪律: 每个方法都要一个 `user_id`, 由工具闭包提供 ——
    # 「改谁的数据」与「查谁的数据」在 wire 上是同一件事 (X-User-Id 头).
    #
    # 购物车四个动作回的**都是动作之后的整车**: 模型一句就能念出来 ("车里现在
    # 有两件, 一共 30 元"), 不用再补一次 GET.
    #
    # 用 slug 而不是 cart_item_id 定位: slug 在商品页, 搜索结果, 购物车返回体里
    # 到处都能看到, 主键对模型是个没有语义的数字 (商城侧的取舍见 views_agent.py).

    async def add_to_cart(self, *, user_id: int, slug: str, quantity: int = 1) -> dict:
        """加购 (同一商品重复加会累加到同一条上)."""
        return await self._write(
            "POST",
            "cart/items/",
            user_id=user_id,
            body={"slug": slug, "quantity": quantity},
        )

    async def update_cart_item(self, *, user_id: int, slug: str, quantity: int) -> dict:
        """改购物车里的数量; `quantity=0` 等于拿掉这一条 (商城侧的哨兵值)."""
        return await self._write(
            "PATCH", f"cart/items/{slug}/", user_id=user_id, body={"quantity": quantity}
        )

    async def remove_cart_item(self, *, user_id: int, slug: str) -> dict:
        """拿掉购物车里的这一条."""
        return await self._write("DELETE", f"cart/items/{slug}/", user_id=user_id)

    async def clear_cart(self, *, user_id: int) -> dict:
        """清空购物车 (回的是空车)."""
        return await self._write("DELETE", "cart/clear/", user_id=user_id)

    async def place_order(self, *, user_id: int, address_id: int | None = None) -> dict:
        """下单 (**整车**), 不带地址就用默认收货地址.

        下多少不由参数决定: 商城侧下的是购物车里现有的全部条目 —— 让模型先查车
        再挑出"买哪几件"正是 PRD §4.3 说的"凑几次调用才拼齐".
        """
        return await self._write(
            "POST", "orders/", user_id=user_id, body=_present(address_id=address_id)
        )

    async def cancel_order(self, *, user_id: int, order_no: str) -> dict:
        """取消订单 (不用审批, 即刻生效) —— 只有**还没付款**的订单走得通.

        回执里带着买家最关心的那件事: 回滚了几件库存 (`restocked_count`).
        `balance_returned` 恒为 "0.00" (取消只认待付款的单, 那种单从没扣过钱).
        """
        return await self._write("POST", f"orders/{order_no}/cancel/", user_id=user_id)

    async def request_refund(self, *, user_id: int, order_no: str) -> dict:
        """申请退款 —— **不带金额**: 退多少由管理员批准时协商 (PRD §4.5)."""
        return await self._write(
            "POST", "refunds/", user_id=user_id, body={"order_no": order_no}
        )

    async def list_refunds(self, *, user_id: int) -> list:
        """该买家的退款申请列表 (**裸数组**, 不分页)."""
        return await self._get("refunds/", user_id=user_id)


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "HEADER_TOKEN",
    "HEADER_USER_ID",
    "MinimallClient",
    "MinimallError",
    "MinimallNotFoundError",
    "MinimallRefusalError",
    "Ordering",
    "PageSize",
]
