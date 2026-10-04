"""工具范围裁剪: 按题面只把相关的几组工具交给模型 (issue 44 的 A/B 装置).

一句话理解: 助手挂着的那批工具**分组**摆着, 这个类按**买家这一句问的是什么**挑出
相关的几组, 让模型**只看见**它们 —— 而看不见的也**调不到**. 两个动作缺一不可,
见下面第二段.

**为什么要有它**: `CharAgent/docs/DESIGN.md` #70 那条 tradeoff 写着「工具越多模型
越容易选错」, 而它至今只是设计上的一句话 —— 要变成结论, 得有一组对照数据. 本类
就是那次 A/B 的自变量: 对照组让模型看见全部工具, 实验组看见的只有这里挑出来的
几组. (它**不是产品功能**: 生产那两个入口照旧把全部工具交给模型, 装不装本类由跑分
那条线说了算. 要变成功能, 得先有数据说它更好.)

**为什么要两个钩子 (框架侧的一处落差)**: `before_turn` 拿到的 `tools` 是那一轮要
发给模型的列表**本体** (活引用, 原地改当轮即生效), 于是「少给几个」在模型那一侧做
得到. **但执行时查的不是那张表** —— 主循环查的是自己那本全量的工具账 (`agent/
loop.py` 的工具执行那一段). 只裁不拒的话, 模型凭历史或幻觉调一个被裁掉的工具**照样
真执行**, 两组比的就不是「少给」而是「看不见」—— A/B 的结论当场作废. 所以裁剪必须
配一条拒绝: 范围外的调用在这里停下, 理由当作工具失败回填给模型. 两条钩子合起来
才是「这次运行的工具集」这一件事的完整表达: **可见集 = 可调用集**.

**它与 `WriteGuardrail` 同挂一个点, 组合行为是定死的** (四行有用例逐行钉住):

| 情形 | 裁剪 | 护栏 | 结果 |
|---|---|---|---|
| 范围外 + 护栏不管它 | 拒绝 | 放行 | 拒绝 |
| 范围外 + 护栏要挂起 (下单那条) | 拒绝 | 挂起 | **拒绝** |
| 范围内 + 护栏要挂起 | 放行 | 挂起 | 挂起 |
| 范围内 + 护栏拒绝 (超预算 / 超金额) | 放行 | 拒绝 | 拒绝 |

第二行那个「拒绝吃掉挂起」不是巧合: 框架的 `decide()` 是**拒绝优先于挂起、且不看
注册顺序** (`hooks/registry.py`). 顺序只决定一件事 —— 两条**都是拒绝**时哪一句理由
先到. 本类因此注册在护栏**之前**: 一个被裁掉的超预算 `place_order` 该听到的是「这
工具不在范围里」(它压根没给模型), 而不是「这一单超了 5000」.

**一处要如实记的边界**: `decide()` 会把注册过的插件**都问一遍** (不短路), 于是一个
范围外的写工具仍然会过一遍护栏的账本 —— 它可能占掉一个写名额, 或者白问一次购物车.
对 A/B 没有影响 (两组走同一条路), 但别把它读成「被裁掉的调用什么都没发生」.

**分类为什么是规则, 不引 embedding / 不调模型**: 这次 A/B 的变量是**工具数量**,
不是「分类器有多聪明」. 规则零成本、可解释、不引入新的模型调用 (那会让「差异是工具
数量带来的」这句话说不清), 而且**分类正确率本身可以单独报一行** —— 分错组等于期望
工具压根没给模型, 那一跑的失败是装置的问题而不是模型选错了 (跑分入口据此把那几行
单列, 不混进 A/B 的那两个数).

大白话版: 这一页是「这次给模型开几组工具」的那把闸, **只在跑分那条线上装**.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.model import ToolSpec
from CharAgent.tool import Tool

# 工具分组: 组名 → 这一组的工具 (名字与 `tools.build_tools` 装出来的那批一致).
#
# 分组的依据是**买家那一件事的落点** (看商品 / 动车里的东西 / 动订单 / 动退款 /
# 看账户), 不是工具自己的读写属性 —— 于是「购物车里那件下单」这种一句话踩两组的
# 题天然落在两组的并集里, 而 `pay_my_order` 跟着订单那一组走 (它动的是订单).
#
# 有一条用例守着: 六组的并集**正好**是工具名那份权威清单, 不多不少也不重 —— 加了
# 工具忘了归组、名字拼错、或者一个工具进了两组, 那条都会红.
TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "product": (
        "search_products",
        "get_product_detail",
        "list_categories",
        "list_featured_products",
    ),
    "cart": (
        "get_my_cart",
        "add_to_cart",
        "update_cart_item",
        "remove_cart_item",
        "clear_cart",
    ),
    "order": (
        "list_my_orders",
        "get_my_order",
        "place_order",
        "cancel_my_order",
        "pay_my_order",
    ),
    "refund": ("request_refund", "list_my_refunds"),
    "account": ("get_my_profile", "list_my_addresses"),
    # L5-b 加的这一组: 政策知识库。它不属于上面任何一组 (那些都是"买家的东西"),
    # 而"并集必须**正好**是工具清单"那条不变量要求每个工具都有归属 —— 于是给它
    # 自己一组. **那组 A/B 已收口** (issue 44 的结论已落报告), 这一组是为"哪天
    # 再跑时工具集是完整的"留的: 少一个工具, 裁出来的两组就不再等价.
    "knowledge": ("search_knowledge",),
}

# 分类的关键词: 组名 → 命中它的词 (子串匹配, 大小写不敏感).
#
# 写词的三条纪律:
#
#   - **词要挑得准, 宁少勿滥**: 一个词把两类题都拉进来, 实验组就悄悄变宽了 (而
#     变宽的那一组看上去成绩更好). 反例留在代码里: 「手机」**不能**进账户那一组
#     —— 商城里就有手机在卖, 而账户那一组要的是「手机号」.
#   - **口语的别名要收**: 买家不会说「购物车」全称 (`车里` 那种也常见), 而分类器
#     只看这一句话.
#   - **跨组的词写进它真正需要的那一组**: 「加进购物车」放在 `product` 而不是
#     `cart` —— 按名字加购得先从搜索里拿到商品 slug (工具的说明里写着不要自己拼),
#     于是这一类题**两步都少不了**; 只给 cart 那一组的话, 模型手里没有搜索工具,
#     这一跑注定失败 (而那是装置的问题).
GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "product": (
        "商品",
        "推荐",
        "有货",
        "库存",
        "价格",
        "价位",
        "分类",
        "搜",
        "加进购物车",
        "加入购物车",
        "加购",
    ),
    "cart": ("购物车", "车里", "清空"),
    "order": ("订单", "下单", "结算", "物流", "快递"),
    "refund": ("退款", "退货", "退钱", "退给我", "售后"),
    "account": ("账户", "账号", "余额", "地址", "收货", "资料", "手机号", "邮箱"),
    # 政策那一组收的是"问规定"的词, 而不是"退款 / 余额"这类**别的组也要**的词 ——
    # 那些词出现时, 并集里本来就有 knowledge 那一组 (`GROUP_KEYWORDS` 是并集, 不是
    # 互斥的划分). 这里只收**光靠别的组看不出要知识库**的: 问"几天""多久"的多半是
    # 在问时效, 问"规定 / 政策"就是明着问条文.
    "knowledge": ("政策", "规定", "运费", "包邮", "时效", "几天", "多久", "发货"),
}

# 一个词都没命中时给哪两组 (开放决策 4): 商品与账户 —— 覆盖面最广、最常用的两类.
#
# 兜底这件事本身是有代价的 (它把「没读懂」变成「按最常见的猜」), 所以只在**一组都
# 没命中**时才用, 且它给的是固定两组而不是全部 —— 后者等于这一题的裁剪没生效.
FALLBACK_GROUPS: tuple[str, ...] = ("product", "account")

# 这次 A/B 的两臂叫什么. **一处定义**, 三处共用: 跑分入口的组名 (`eval/run.py`)、
# 配置快照里「工具范围」那一格 (`eval/subject.py`)、用例的断言.
#
# 为什么值得单独拎出来: 读报告的人第一眼就是拿表头那两个组名去对配置快照那一格,
# 而三者各写一份的漂移方向正是「表头写着 `全挂`、配置那一格写着 `不裁`」—— 一份
# 自相矛盾的报告比没有报告更难查.
SCOPE_FULL = "全挂"
SCOPE_PRUNED = "按题裁剪"

# 裁剪造成的拒绝都带这一句 (报告与用例靠它把两种拒绝分开 —— 逐题表里一格只印几十
# 个字, 而护栏那两句的开头是「这次能替买家做的写操作已经用完」/「这一单合计 …」).
OUT_OF_SCOPE_MARK = "不在这次能用的范围里"


def classify(question: str) -> tuple[str, ...]:
    """买家这一句问的话 → 该开哪几组工具 (顺序照 `TOOL_GROUPS`).

    只看这一句 (不带上文, 也不看上一轮调过什么 —— 开放决策 2): 逐轮变化的可见集
    会让两组之间的差异说不清, 而这一句正好是「这次运行在回答什么」的完整输入.

    Args:
        question: 题面那句话.

    Returns:
        tuple[str, ...]: 命中的组名 (可能有几组); 一组都没命中时是 `FALLBACK_GROUPS`.
    """
    text = question.lower()
    hit = tuple(
        name
        for name, words in GROUP_KEYWORDS.items()
        if any(word in text for word in words)
    )
    return hit or FALLBACK_GROUPS


def tools_of(groups: Sequence[str]) -> frozenset[str]:
    """这几组盖住的工具名 (报告与用例读它; 组名不认识就报 —— 那是代码写错了).

    Raises:
        KeyError: 组名不在 `TOOL_GROUPS` 里.
    """
    return frozenset(name for group in groups for name in TOOL_GROUPS[group])


class ToolScope:
    """这一次运行的范围: 按题面选组 → 裁可见集 → 拦住范围外的调用.

    Args:
        question: 买家这一次问的话 (裁的依据, 见 `classify`).

    attributes:
        (没有公开属性: `groups` / `tool_names` 是只读视图, 给报告与用例看)
    """

    def __init__(self, *, question: str) -> None:
        self._groups = classify(question)
        self._tool_names = tools_of(self._groups)

    @property
    def groups(self) -> tuple[str, ...]:
        """这次开了哪几组 (报告里那一格印它)."""
        return self._groups

    @property
    def tool_names(self) -> frozenset[str]:
        """可见集 = 可调用集 (这一跑的那几个工具名)."""
        return self._tool_names

    def install(self, registry: HookRegistry) -> None:
        """把自己挂到框架的**两个**点上 (为什么是两个, 见模块 docstring).

        挂哪两个点是这个类自己的知识, 于是放在它这里而不是散在装配代码里 ——
        装配那侧只有一句「装上这次的范围」 (与 `WriteGuardrail.install` 同一个做法).

        Note:
            装配处 (`service.session_for`) 把它挂在护栏**之前**. 顺序不改变裁决
            (框架那边拒绝优先、且不看顺序), 只决定两条**都是拒绝**时先到的那句理由:
            一个被裁掉的超预算 `place_order` 该听到的是「不在范围里」(它压根没给
            模型), 而不是「超了 5000」—— 见模块 docstring 的那张表.
        """
        registry.register(HookPoint.BEFORE_TURN, self.keep_visible)
        registry.register(HookPoint.BEFORE_TOOL_EXECUTE, self)

    def keep_visible(self, *, tools: list[ToolSpec] | None, **kwargs: Any) -> None:
        """每轮模型调用前叫一次: 把这一轮要发出去的工具表收窄到范围内.

        **原地改** (`tools[:] = ...` 而不是返回一张新表): 框架递进来的是**列表
        本体**, 另起一张表等于什么都没发生 —— 这一条是框架侧那处落差的另一半,
        有端到端用例盯着「模型实际收到的 tools 只有那几组」.

        **每轮都裁是幂等的**: 依据只有题面那一句 (不随轮次变), 所以第二轮起是空操作.
        留着它每轮都跑, 是因为将来真要按轮次变 (比如把上一轮调过的工具补进来) 时,
        改动只落在这一处.

        Args:
            tools: 这一轮要发给模型的工具表 (活引用); None = 这个会话没有工具
                (框架在这种情形下给 None, 本类原样让过).
        """
        if tools is None:
            return
        tools[:] = [spec for spec in tools if _name_of(spec) in self._tool_names]

    def __call__(self, *, tool: Tool, **kwargs: Any) -> Decision | None:
        """框架在执行每个工具之前问一遍: 这一个在不在这次的范围里.

        只要 `tool` 一个字段 (其余载荷用 **kwargs 收下不看): 判断靠工具自己的名字,
        而名字是框架从注册表里查出来的那一个 —— 模型编出来的名字到不了这里
        (见 `agent/loop.py` 的工具执行那一段).

        Returns:
            Decision | None: 范围外 → `Decision.reject` (原因回填给模型); 范围内 →
            None (管不着, 交给其余插件 —— 比如护栏那条要不要挂起).
        """
        if tool.name in self._tool_names:
            return None
        return Decision.reject(_out_of_scope_reason(tool.name, self._groups))


def _name_of(spec: ToolSpec) -> str:
    """wire 规格里的工具名 (形状由 `Tool.to_spec()` 定, 见 `tool/decorator.py`).

    直接按下标读而不做兜底: 这个形状是框架对外的契约 (OpenAI 的 tools 参数),
    读不出来就是契约变了 —— 那时该让异常进 `registry.failures` 被看见, 而不是
    悄悄按「裁掉」处理 (那会让实验组凭空少给几个工具, 而报告上看不出原因).
    有一条用例拿真工具的 `to_spec()` 输出喂这个函数, 契约漂了它会先红.
    """
    return str(spec["function"]["name"])


def _out_of_scope_reason(tool_name: str, groups: Sequence[str]) -> str:
    """范围外那一调回给模型的话 (说清是什么、下一步怎么办、别重试).

    开头那句是固定的 (`OUT_OF_SCOPE_MARK`): 报告与用例靠它把「裁剪拦的」与「护栏
    拦的」分开 —— 两处的理由都会进逐题表, 混起来读不出是哪一道闸拦下的.
    """
    return (
        f"工具 {tool_name} {OUT_OF_SCOPE_MARK} (这次开的是 "
        f"{' / '.join(groups)} 这几类). 请改用已经给出的工具, 不要重复调用它."
    )


__all__ = [
    "FALLBACK_GROUPS",
    "GROUP_KEYWORDS",
    "OUT_OF_SCOPE_MARK",
    "SCOPE_FULL",
    "SCOPE_PRUNED",
    "TOOL_GROUPS",
    "ToolScope",
    "classify",
    "tools_of",
]
