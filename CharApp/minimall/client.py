"""商城内部端点的异步客户端: 业务侧唯一一处「怎么跟商城说话」.

打的是 issue 02 建的那 9 个只读端点 (前缀 `/api/minimall/agent/`, 见
`app/minimall/urls_agent.py`), 一个端面一个方法.

三条约束都不是随手定的:

1. **必须异步** (`httpx.AsyncClient`). 框架把同步工具函数扔进
   `asyncio.to_thread` 执行 (见 `CharAgent/tool/executor.py` 的 `_invoke`),
   同步客户端会把线程池占满 —— 9 个工具互相排队, 「多个查询同时进行」当场
   失效 (PRD §4.4)。
2. **身份不在这里**. 本类只管「怎么打这个接口」, 不管「代表谁打」: 每个方法
   都要一个 `user_id`, 而它由 `tools.build_tools` 裹进工具的闭包, 于是永远
   进不了模型的参数表 (PRD §4.2)。
3. **连接池是进程级的**. 一个进程一个实例 (命令行入口建一个、退出时关掉):
   一次会话里问十几句也只建一次连接池, 而不是每问一句新建一批连接。

错误语义只有两条分叉 (为什么这么分): **404 是答案, 其余是故障**。查无此单
不是异常情况, 模型应当把它当事实告诉买家 ("没有查到这笔订单"), 所以单独一个
`MinimallNotFoundError` 让工具层翻译成人话; 网络/超时/5xx 则原样上抛, 由框架统一的
「内部错误」文案回填模型 (`tool/executor.py` 的 `INTERNAL_ERROR_TEXT` —— 它说的
正是「别用相同参数重试」)。
"""

from __future__ import annotations

from typing import Any, Literal

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


class MinimallNotFoundError(MinimallError):
    """商城明确回答「没有这个东西」(404).

    与 `MinimallError` 分开是因为两者对买家意味着完全不同的事: 这是**答案**
    (你要找的东西不存在), 那才是**故障** (商城没答上来)。
    """


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
    # 传输: 9 个方法共用的一段
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
        try:
            response = await self._http.get(
                path,
                params=params,
                headers={HEADER_USER_ID: str(user_id)},
            )
        except httpx.HTTPError as exc:
            raise MinimallError(
                f"请求 {path} 失败 ({type(exc).__name__}): {exc}"
            ) from exc

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


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "HEADER_TOKEN",
    "HEADER_USER_ID",
    "MinimallClient",
    "MinimallError",
    "MinimallNotFoundError",
    "Ordering",
    "PageSize",
]
