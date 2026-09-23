"""展示层脱敏: 工具事件的载荷换成人话 (ADR-0003).

一句话理解: 框架的 `tool_call` / `tool_result` 事件带的是**事实** —— 工具名、
参数原文、工具返回的正文片段 (订单号 / 地址 / 余额都在里面). 浏览器不该看到
这些, 所以业务在**自己这一层**把它们换成一句中文短语, 再交给出口.

**这条保证只画在工具事件上**, 说清它成立的范围: 本模块保证的是「工具的参数原文与
返回正文不出本进程」, 而**不是**「浏览器上搜不到任何敏感值」—— 模型自己在
`reasoning` / `final` 里复述的值照旧出去 (那是它的话, 遮掉会让 L3 的「当时它看到
了什么」不可解释, 见下面那张表). 「让它少复述」是 prompt 的事 (L4 的调优清单),
不是这一层能兜的.

为什么是这一层 (三层取舍见 ADR-0003): 事件出口 (`EventSink`) 是框架交给业务的,
业务本来就站在唯一出口上 —— 包一层就是脱敏, 框架与 Django BFF 一行不改. 前端
隐藏不算脱敏 (devtools 的网络面板里原文可见), BFF 改写则要把一个「逐帧搬字节」
的薄层变成「解析协议 + 改写」的有知识层.

**哪些改、哪些不改** (ADR-0003 的表格, 这里就是它的实现):

| 字段 | 处置 | 理由 |
|------|------|------|
| `arguments` | → `label` | 原样 JSON, 里面有订单号 / slug / 价格区间 |
| `summary` / `error` | → `label` | 工具返回值截断到 200 字符的正文 |
| `tool_name` | 保留 | 不含用户数据; 却是讲「模型选了哪个工具」的唯一素材 |
| `tool_call_id` / `duration_ms` / `turn` | 保留 | 内部噪音, 无信息风险 |
| `status` | 保留 | 同样无风险, 而页面靠它决定那一行红不红 |
| 其余事件的载荷 | 不动 | `reasoning` 是模型自己的话; `final` 是答复正文 |

留字段用的是**白名单**(把不在表里的键删掉), 不是黑名单: 框架以后往载荷里加什么
新字段, 默认都进不了浏览器 —— 要放行得回来改这一张表.

**不留演示开关**: 一个「演示时把敏感数据打开」的环境变量是安全反模式, 它迟早会
在某个不该开的场合被打开.

**脱敏改的是事件对象本身** (框架明说 `StreamEvent` 不冻结、`data` 是自由字典),
所以此后看到这个对象的任何东西 (含 `ON_EVENT` 插件) 一并不见原文. 这正是想要的:
只有一个出口, 就没有第二份原文在别处流转.

**开关由入口显式给** (`service.session_for(..., redact=)`, ticket 18): 威胁模型是
「谁能看到**浏览器**」, 而「这个出口是不是浏览器」只有入口知道 —— 框架递进来的
`EventSink` 是个协议, 终端的 `EventPrinter` 与它长得一样. 命令行入口给 False
(开发者自己的终端, 它按框架的载荷契约渲染 `name(args)` / `name ok: summary`,
脱敏之后那两行会变成 `add_to_cart( (畸形 JSON))` 与 `add_to_cart ok (1ms):`).

**这个参数没有默认值** —— 它曾经是一条"靠人记得包"的隐式约定 (脱敏发生在服务入口,
谁也没拦着第三个入口不包). 摆到台面上之后, 漏了它的表现是 `TypeError` (装配那一次
就炸), 而不是某天发现工具参数原文进了浏览器.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from typing import NamedTuple

from CharAgent.stream import EventSink, EventType, StreamEvent

# 事件里**保留**的字段白名单 (上表那几行; 其余一律删掉)
KEPT_KEYS = frozenset({"tool_call_id", "tool_name", "status", "duration_ms", "turn"})

# 换成人话的那个字段名 —— 前端按它渲染工具行 (见 templates/minimall/agent.html)
LABEL_KEY = "label"

# 要脱敏的两类事件 (只有它们带工具的参数与返回正文)
TOOL_EVENT_TYPES = frozenset({EventType.TOOL_CALL, EventType.TOOL_RESULT})


class Phrase(NamedTuple):
    """一个工具在页面上的三种话术.

    attributes:
        calling: 正在做 (tool_call 那一行).
        done: 做成了 (tool_result 且 status=ok).
        failed: 没做成 (tool_result 且 status=error) —— 用户最需要看见的一行.

    为什么是三种而不是两种: 「失败」单独有一句话, 页面才不必把成功那句加个
    「没」字前缀 (中文里那拼不出通顺的话), 模型也不必为展示层改写措辞.
    """

    calling: str
    done: str
    failed: str


# 工具名 → 三种话术 (**17 个都要有**, 有一条用例守着这张表与工具集一一对应).
#
# 措辞的两条规矩:
#   - 说人话, 不露出工具名 (页面上一行英文函数名对买家没有意义);
#   - 写工具用完成态 (「已经加进购物车」), 只读工具用「查到了」—— 让买家一眼看出
#     这一次到底改没改数据.
TOOL_PHRASES: dict[str, Phrase] = {
    "search_products": Phrase("正在搜索商品", "商品列表找到了", "商品没搜出来"),
    "get_product_detail": Phrase(
        "正在查看商品详情", "商品详情拿到了", "商品详情没查到"
    ),
    "list_categories": Phrase("正在看商品分类", "分类拿到了", "分类没取到"),
    "list_featured_products": Phrase(
        "正在看精选商品", "精选商品拿到了", "精选商品没取到"
    ),
    "get_my_cart": Phrase("正在看购物车", "购物车看过了", "购物车没取到"),
    "list_my_orders": Phrase("正在查订单列表", "订单列表查到了", "订单列表没查到"),
    "get_my_order": Phrase("正在查这笔订单", "订单详情查到了", "订单详情没查到"),
    "get_my_profile": Phrase("正在看账户和余额", "账户信息拿到了", "账户信息没取到"),
    "list_my_addresses": Phrase("正在查收货地址", "收货地址查到了", "收货地址没查到"),
    "add_to_cart": Phrase("正在加进购物车", "已经加进购物车", "没能加进购物车"),
    "update_cart_item": Phrase("正在改购物车里的数量", "数量改好了", "数量没改成"),
    "remove_cart_item": Phrase("正在从购物车里移除", "已经移除", "没能移除"),
    "clear_cart": Phrase("正在清空购物车", "购物车已清空", "购物车没清空"),
    "place_order": Phrase("正在下单", "订单已提交", "下单没成功"),
    "cancel_my_order": Phrase("正在取消这笔订单", "订单已取消", "订单没取消成"),
    "request_refund": Phrase("正在申请退款", "退款申请已提交", "退款申请没提交上"),
    "list_my_refunds": Phrase("正在查退款进度", "退款进度查到了", "退款进度没查到"),
}

# 表里没有的工具 (框架以后新增 / 业务漏配) 的兜底话术.
#
# 它**不含工具名** —— 页面上一行「正在处理 add_to_cart」比一句模糊的中文更糟:
# 买家看不懂, 而开发者也看不出这是兜底 (会以为是正常显示). 漏配由测试当场报红,
# 这里只负责别让一次演示卡在 Half-English 的一行上.
FALLBACK_PHRASE = Phrase("正在处理这一步", "这一步完成了", "这一步没成功")


def phrase_for(tool_name: str) -> Phrase:
    """工具名 → 三种话术 (没配过就走兜底)."""
    return TOOL_PHRASES.get(tool_name, FALLBACK_PHRASE)


def redact(event: StreamEvent) -> None:
    """**原地**把一个工具事件脱敏: 白名单留字段 + 换上一句人话.

    只动 `tool_call` / `tool_result` 两类 —— 其余事件的载荷里没有工具的参数与
    返回正文 (`thinking` / `reasoning` / `final` 是模型自己的话, 遮掉它们会让
    调试与演示都失去意义, 见 ADR-0003).

    原地改而不是造一个新事件: 事件对象是同一个 (事件流里那份 data 就是本 dict),
    换新对象会让「脱敏后的那一份」与框架后续要用的那一份分家 —— 而本层要的正是
    唯一的出口上没有第二份原文.
    """
    if event.type not in TOOL_EVENT_TYPES:
        return
    data = event.data
    name = data.get("tool_name")
    phrase = phrase_for(name if isinstance(name, str) else "")
    if event.type is EventType.TOOL_CALL:
        label = phrase.calling
    else:
        label = phrase.done if data.get("status") == "ok" else phrase.failed
    # 先删后写: 白名单之外的键 (arguments / summary / error…) 一个都不留
    for key in [key for key in data if key not in KEPT_KEYS]:
        del data[key]
    data[LABEL_KEY] = label


def redacting_sink(sink: EventSink) -> EventSink:
    """把出口包一层: 每个事件先脱敏, 再交给原来的出口.

    同步 / 异步出口都吃 (`EventSink` 两种都允许, 框架按
    `inspect.isawaitable` 认) —— 服务进程递进来的是框架的同步路由, 测试常递一个
    收集器; 包出来的那个一律是协程, 与框架自己的判法一致.

    Args:
        sink: 原来的出口 (框架的路由 / 终端渲染器 / 测试收集器).

    Returns:
        EventSink: 脱敏后再转交的出口 (形状与原来那个一样).
    """

    async def redacted(event: StreamEvent) -> None:
        redact(event)
        result: Awaitable[None] | None = sink(event)
        if inspect.isawaitable(result):
            await result

    return redacted


__all__ = [
    "FALLBACK_PHRASE",
    "KEPT_KEYS",
    "LABEL_KEY",
    "TOOL_PHRASES",
    "Phrase",
    "phrase_for",
    "redact",
    "redacting_sink",
]
