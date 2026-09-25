"""业务侧的护栏插件: 挂在「工具执行前」那一个点上的三条规则 (L2 的验收核心).

一句话理解: 助手从「只能看」变成「能改数据」之后, 得有东西拦得住它 —— 这个类
就是那道闸. 它挂在框架的拦截点 (`HookPoint.BEFORE_TOOL_EXECUTE`) 上, 说一句
「不行」, 那个写工具就真的不跑, 理由当作执行结果回填给模型 (框架的
`Tool.annotations` 只透传不解释, 认不认 `writes` 是业务自己的事). 到 L3 它还会
说第二句话 —— 「等一下, 这得本人点头」, 那次工具也不跑, 但整次运行就此停住等人
(挂起), 这是同一个点上的第三种表态 (`Decision.requires_approval`).

三条规则 (前两条的数为什么是那两个数, 见下面):

- **写操作预算 8 次**: 一次正常的购物流程 (加购 → 改量 → 下单 → 取消 → 申请退款)
  大约 6 到 8 次写操作, 8 不误伤; 而模型跑飞时连环下单**一定会撞上它**.
- **单笔金额上限 5000 元**: 卡在「日常购买」与「大额消费」之间. 商城在售的五件
  商品里, 四件都在 5000 元以下 (99 / 238 / 999 / 4000) —— 这些都买得成; 而最贵
  那件 (`iphone-17-pro`, 8000 元) 与「买 100 件 99 元的 = 9900 元」这类**被诱导的
  批量下单**会被拦下, 请买家到页面自己确认. **大额消费本来就该由本人拍板**, 所以
  这不是误伤而是本意: 到 L3, 同一条规则会升级成「挂起 → 买家点确认」, 那时它才
  真的"能做成", 而不是换个地方做.
- **代付挂起** (issue 35): 调 `pay_my_order` 的那一步**不执行**, 整次运行停在这里
  等买家本人点头 —— 这是 L2 那句「框架级的人工确认是 L3 的事」兑现的地方, 也是
  第三条规则与前两条的根本不同: 前两条是**拒绝** (当场有结论, 工具不跑), 这一条
  是**挂起** (还没轮到它, 结论由本人给, 给完从存档点接着跑).

**为什么要有它** (PRD §4.6 的补记): L2 的 17 个工具里有 7 个能改数据, 而同一
阶段没有任何东西拦得住模型连环下单 —— 框架级的人工确认是 L3 的事. 它同时把
「哪些工具是写操作」从散落的 7 个业务函数里提出来, 落到框架的一个统一属性上
(`annotations`), 而不是在每个写工具里各写一遍判断 (这个数会随加片涨).

**这条护栏管到哪儿, 管不到哪儿** (说清, 否则它就是自我辩护):

- 它管的是**助手发起的那条路**: 买家自己在订单页付款不经过它 (那是买家的手,
  不是模型的), 所以"代付要不要本人确认"这件事对它才有意义.

- 它管的是**这一次运行**: 预算在一轮问答里累计, 买家再问一句就是新的一轮,
  账本归零. 会话级的总额度不在这里 (那要靠 L3 的挂起逐笔确认).
- 金额上限管的是**一单**. 把一单拆成几单在账面上绕得过去 —— 真要堵住得按运行
  累计金额, 而那是个阈值问题, 该由 L4 的对照数据决定, 不是现在拍脑袋.
- 它**不改参数, 不能包裹**: 插件能做的只有一件事 —— 说「不许」(PRD §4.6 明确
  否掉了完整中间件链).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from CharAgent.hooks import Decision, HookPoint, HookRegistry
from CharAgent.tool import Tool
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.tools import PAYMENT_PASSWORD_FIELD, WRITE_ANNOTATION_KEY

# 一次运行里的写操作预算 (为什么是 8, 见模块 docstring)
WRITE_BUDGET = 8

# 单笔订单的金额上限 (元). 用 Decimal 而不是 float: 商城的金额契约是 2 位小数
# 字符串 (`serializers_agent.py`), 而二进制浮点表示不了 0.1 —— 上线判「超没超」
# 这件事上, `4999.99 <= 5000` 必须是个确定的结论 (与 tools.py 不转 float 同一条).
MAX_ORDER_AMOUNT = Decimal("5000.00")

# 金额只有在**下单**这一步才成为事实, 所以这条规则只盯它 (见 `_overspend_reason`)
PLACE_ORDER_TOOL = "place_order"

# 代付要挂起的那一条 (issue 35) —— 判据是工具名, 与金额那条同一个做法
PAY_ORDER_TOOL = "pay_my_order"

# 挂起时给**买家**看的那句话 (前端把它印在确认卡上, 原样显示).
#
# 与 `_BUDGET_REASON` 那种"回填给模型"的文案不同: 读者是买家本人, 而这句读完他
# 就该知道下一步该干什么 (输一次密码). 标点照旧用半角 —— 全仓的用户可见文案都是
# 这一种 (`APPROVAL_*` / `TERMINAL_ERROR_TEXT` 同样), ruff 的 RUF001 也认这个.
PAY_APPROVAL_PROMPT = "这一单要付款了, 需要你输一次支付密码"

# 预算用完时回给模型的话. 三条要点缺一不可: 说清是什么用完了 (不是"操作失败"),
# 给出下一步 (买家自己能做), 劝住重试 (再调一次还是这个结果).
_BUDGET_REASON = (
    f"这次能替买家做的写操作已经用完 ({WRITE_BUDGET} 次), 这一步就不做了. "
    f"请如实告诉买家: 剩下的请他自己在商城的页面上完成 "
    f"(购物车 / 下单 / 订单页都能自己操作). "
    f"不要重复调用写工具 —— 再调一次也是这个结果."
)


class WriteGuardrail:
    """两条规则合一个插件: 写操作预算 + 单笔金额上限.

    形状就是一个可调用对象 (框架的 `HookFn` 是「这样调就能问」, 不要求继承),
    所以本类不 import 任何基类. 挂载方式见 `service.MinimallService.session_for`
    —— 那**唯一一处装配**同时管着命令行与 HTTP 两个入口.

    Args:
        client: 商城客户端. 金额那条规则要看购物车 (见 `_overspend_reason`), 而它与工具
            用的是同一个客户端, 同一条连接池.
        user_id: 这条护栏护着哪个买家 —— 与工具闭包同一个身份来源 (装配时从
            运行上下文里取), 所以护栏看的是**这个买家**的车.

    attributes:
        (无公开属性: 预算余额是运行期的私有账本, 不对外暴露)
    """

    def __init__(self, *, client: MinimallClient, user_id: int) -> None:
        self._client = client
        self._user_id = user_id
        self._used = 0

    @property
    def used(self) -> int:
        """这一轮已经放行了几次写操作 (只读视图; 测试与排查用)."""
        return self._used

    def install(self, registry: HookRegistry) -> None:
        """把自己挂到框架的**两个**点上 (为什么是两个, 见 `start_run`).

        挂哪两个点是这条护栏自己的知识, 所以放在它这里而不是散在装配代码里 ——
        装配那侧只有一句「装上这条护栏」.
        """
        registry.register(HookPoint.BEFORE_TURN, self.start_run)
        registry.register(HookPoint.BEFORE_TOOL_EXECUTE, self)

    def start_run(self, *, turn: int, **kwargs: Any) -> None:
        """每轮开工前叫一次: 只在 `turn == 1` 时把账本归零 (新的一轮问答).

        为什么归零不能写在裁决方法里 (`__call__` 收到 turn 时顺手判一下): **同一轮
        里可以有好几次工具调用** (框架支持并行调用), 它们带的 `turn` 是同一个数,
        那样每叫一次就重来一次 —— 模型在第一轮里并发发 8 次写操作就一次都拦不住
        了. `before_turn` 是**每轮恰好一次**, 拿它当「新一轮开始」的信号才对.
        """
        if turn == 1:
            self._used = 0

    async def __call__(self, *, tool: Tool, **kwargs: Any) -> Decision | None:
        """框架在执行每个工具之前问一遍: 这一步能不能做 (返回 None = 管不着).

        只要 `tool` 一个字段 (其余载荷用 **kwargs 收下不看): 判断「这算什么操作」
        靠它身上的 `annotations`, 判断金额去问商城 —— 那两件事都不需要参数表,
        而参数表正是模型能编的地方.

        顺序有意: **先认人, 再判预算, 最后逐条判** —— 判金额要打一次商城, 能省
        则省 (只读调用在第一步就返回了); 而**拒绝优先于挂起**: 预算已经用完时,
        该当场说「不行」, 不该弹一张确认卡让买家白输一次密码.

        Args:
            tool: 命中的 Tool 对象; 写操作靠它的 `annotations` 认出来.
        """
        if not tool.annotations.get(WRITE_ANNOTATION_KEY):
            return None  # 只读操作不管 —— 判断只看注解, 不去比对 18 个工具名字
        if self._used >= WRITE_BUDGET:
            return Decision.reject(_BUDGET_REASON)
        if tool.name == PAY_ORDER_TOOL:
            # 代付: **不执行, 等买家本人给结论** (挂起, #25). 它与上面那条拒绝的
            # 区别不是"让不让", 而是"这个结论由谁在哪一刻给" —— 钱只能由本人点头.
            # 排在预算之后 (上面那条): 该拒的当场拒, 别让人白输一次密码.
            # 这里不记账: `_used` 记的是**真做过的**写操作, 而这一条还没做 (补做
            # 发生在恢复那一段自己的账本里).
            return Decision.requires_approval(
                prompt=PAY_APPROVAL_PROMPT,
                needs=(PAYMENT_PASSWORD_FIELD,),
            )
        # **先记账再判金额**: 判金额中间有一次 await (打商城问购物车), 而同一轮里
        # 的几次调用是并发跑的 —— 读完再写会丢更新 (两次并行的下单都读到 7 就都
        # 放行了). 这里在同一个事件循环步里读完就写, 不会被打断.
        self._used += 1
        if tool.name == PLACE_ORDER_TOOL:
            reason = await self._overspend_reason()
            if reason is not None:
                self._used -= 1  # 没下成的单不占买家的额度
                return Decision.reject(reason)
        return None

    async def _overspend_reason(self) -> str | None:
        """这一单是不是超了金额上限; 超了给一句拒绝原因, 没超给 None.

        为什么护栏要**亲自看一眼购物车**: 金额既不在参数表里 (`place_order` 只收
        `address_id`), 又只有商城知道. 让模型自己报一个金额, 等于让被检查的人填
        检查项 —— 它算错一个数, 或者被买家哄一句, 这道闸就成了摆设. 一次本机
        GET 换的是「这条规则真的算数」.

        Note:
            读不到购物车 (商城故障 / 响应缺字段) 时本函数**直接抛**, 由框架按
            fail closed 处理成「拒绝」: 判不了就不放行 —— 与框架对裁决插件的
            异常策略同一条 (见 `CharAgent/hooks/registry.py`).
        """
        cart = await self._client.get_cart(user_id=self._user_id)
        total = Decimal(str(cart["total_amount"]))
        if total <= MAX_ORDER_AMOUNT:
            return None
        return (
            f"这一单合计 {total:.2f} 元, 超过了助手能替买家下单的上限 "
            f"({MAX_ORDER_AMOUNT:.2f} 元), 不能替他下. 请如实告诉买家: 这单请他"
            f"自己在商城的结算页完成. 也不要拆成几单来凑 —— 他要的就是这些."
        )


__all__ = [
    "MAX_ORDER_AMOUNT",
    "PAY_APPROVAL_PROMPT",
    "PAY_ORDER_TOOL",
    "PLACE_ORDER_TOOL",
    "WRITE_BUDGET",
    "WriteGuardrail",
]
