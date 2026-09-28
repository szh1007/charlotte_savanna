"""跑分器: 把一道题跑成一份事实 —— 挂起的那几道自己走完「买家确认」(issue 43).

一句话理解: 框架的跑批器每一跑调一次 `EvalSubject.run_once`, 这个文件就是业务侧
那个对象 —— 装会话 → 问一句 → **若停在确认点上就替买家点一下** → 把结果抄成
`RunFacts`.

**为什么非要有「替买家点一下」**: `place_order` 与 `pay_my_order` 在护栏里都不当场
执行, 而是把整次运行停在确认点上等人 (`minimall/guardrail.py`), 那一跑**没有最终
回答**、终局是 `SUSPENDED`, 而挂起**不进判据的分母** (`RunOutcome`: 不算数的三种
只计数不评分). 跑分器不处理它, 题集里凡挂在确认点上的 (下单那条, 以及将来任何会
调代付的题) 都白跑 —— 报告上缺掉的恰好是 L3b 最该讲的那一块 (人工确认那条链到底
跑不跑得通). 这与商城真假无关: 护栏是业务侧的, 换假商城照样挂.

**这一页还管着两个 A/B 的开关**: `prune_tools` (issue 44) 一开, 每道题按题面裁出
几组工具交给模型 (`scoping.ToolScope`), 挂起-恢复那两段用的是**同一个**范围对象;
`prompt_version` (issue 45) 一给, 这一跑就用指定的那一版提示词. 两者各自是一次
实验的自变量 —— 于是「两组只差这一个开关」这件事在代码里看得见. 两个开关都随
`open_harness` 进装配, 于是挂起-恢复那一段自动同款 (不会只生效一半).

**形状照抄 HTTP 那条路** (`CharAgent/server/app.py` 的 `resume_run`, 第 5 / 6 / 7 步),
三处语义一处不改:

- **载荷只增不覆盖** —— 服务端那一步是 `{**data, **context.payload}` (本次运行的键
  压过客户端递来的). 这里同样在 `context.payload` 上补键, 不整个换掉 —— 换掉就把
  买家身份丢了.
- **用这次的上下文重新装配一次会话** —— 一次性凭据要进工具闭包, 而旧会话的工具是
  空手装的 (`provider.MinimallToolProvider.provide` 在构造期就把凭据裹进去了).
- **`resume` 沿用挂起那次的 `run_id`** (issue 33) —— 恢复段落的帧与账都挂回那一行.

**一处刻意不同**: 服务端那七步里的第 4 步 (认领幂等键) 这里没有 —— 那是 HTTP 层的
闸门 (防双击 / 防前端重发 / 防断线重连), 而跑分器一次只跑一条、自己知道只调一次,
搬过来只会多一个假的安全感.

**凭据只给点名要它的那一调**: 挂起那几行自己写着要什么 (`approval_needs`), 要密码
才把密码放进**这一次恢复的载荷** —— 下单那一题因此连载荷里都没有它, 工具闭包更是
拿不到. 这与 `provider.one_shot_payload` 挑凭据是同一条纪律.

条件是**注入**那一步, 不是读 env 那一步: 工厂建一次就把值拿在手上, 于是「手上有没有
这个值」与「它进没进那一跑」是两件事 —— 前者全批一致, 后者只看那条挂起点名要什么.
(这一条的可见后果在测试里是**间接**的: 断言得到的是「没配密码时下单那条照跑」, 而
「配了密码也不进下单那一跑的载荷」眼下没有观察口. 留着它是因为它与「缺凭据就报错」
用的是同一个判据 —— 报错看 `approval_needs` 而注入不看, 那才是真会出事的不对称.)

**没配密码不算事故、也不算成功**: 要密码而没配, 这一跑记成 `BROKEN` 加一句「模拟
确认失败: ...」; 恢复那一段自己炸了同样如此. 两条都**不往外抛** —— 抛出去会让整批
跟着停, 而这里是「这一跑的装置没搭好」, 与模型答得好不好是两件事 (跑批器的
`BROKEN` 那一档正是为这类事留的).
**这一处与票面不同**: 开放决策 3 写的是「记成一条判据失败」, 落点改成了 `BROKEN` +
`RunFacts.error`. 理由是那句话自己后半截给的 —— 「不是框架错误」, 而**判据失败会把它
算进分母**, 变成一次「模型答错」. 框架对这类事本来就有专门一档 (`RunOutcome`: 没跑成
的三种如实计数、不进分母), 报告上照样看得见那一条 (逐题表 + `error` 那一列).

**这条路没有日志出口**: 票面 §二 说「跑分器只要走同一个 `redacting_writer` 就自动
打码」, 而跑分这条链上一个 writer / logger 都没有 (`harness.session` 递进去的事件
出口是个丢弃器) —— 于是没有「打了码的日志」, 是**压根没有日志**. 将来谁给跑分入口
加输出 (比如把模型重试提示打出来), 那一个必须走 `log_redaction.redacting_writer`,
否则 `**.payment_password` 那一条打码规则就绕过去了.

大白话版: 这一页是**跑分器的本体**. 题目在 `cases/`, 判据在 `judges.py`, 环境在
`harness.py`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from CharAgent.agent import Approval, LoopOutcome, LoopResult, RunContext
from CharAgent.client import ChatSession
from CharAgent.db.entities import ToolCall, ToolCallStatus
from CharAgent.eval import (
    CallRecord,
    EvalCase,
    RunFacts,
    RunOutcome,
    SubjectFactory,
    run_outcome_of,
)
from CharAgent.model.protocol import ChatModel
from CharApp.eval.fixtures import BUYER_ID
from CharApp.eval.harness import EvalHarness, open_harness
from CharApp.minimall.config import ENV_EVAL_PAYMENT_PASSWORD, eval_payment_password
from CharApp.minimall.scoping import SCOPE_FULL, SCOPE_PRUNED, ToolScope
from CharApp.minimall.tools import PAYMENT_PASSWORD_FIELD


def _wants_password(pending: list[ToolCall]) -> bool:
    """这几条挂起里有没有哪一条点名要支付密码 (`approval_needs`).

    两处要用同一个判断 (缺凭据的报错 / 组载荷时注入不注入), 于是它单独一处 —— 两边
    各写一遍的话, 它们会在某次改动里分家, 而分家的表现是「报了缺密码却把密码注进了
    载荷」这种自相矛盾的样子.
    """
    return any(PAYMENT_PASSWORD_FIELD in (row.approval_needs or ()) for row in pending)


class HarnessSubject:
    """一次跑用的被测对象: 一套跑分环境 + 一个「模拟买家」的开关.

    生命周期照框架那张协议: 造 → `run_once` 一次 → `aclose`. 跑批器每一跑都现造
    一个 (共用会话会让上一题的调用留在历史里, 见 `protocols.py`), 所以这里的字段
    一律只服务**那一次**.

    Args:
        model: 这一跑用的模型. **所有权随装配交出去** —— `run_once` 里那套环境
            收尾时会把它关掉 (见 `open_harness`), 于是不能几个对象共用一个.
        conversation: 会话编号的第三段 (每次问答都要一个新的 —— 题与题之间不共享
            上文, 而编号本身在记录里看得出来这是哪一题的哪一个对象).
        simulate_approval: 挂在确认点上时要不要替买家点「确认」. **默认要**
            (开放决策 1): 跑得完才有分可判. 关掉是为了量「不确认会怎样」——
            那几跑如实记成 `SUSPENDED`, 报告里看得见 (别把「只有确认才跑得完」
            这件事藏起来).
        payment_password: 代付那一调要注入的密码; None = 没配 (要密码而没配时
            这一跑记 `BROKEN`, 见模块 docstring).
        prune_tools: 这一跑要不要**按题面裁工具** (issue 44 的那次 A/B). 默认不裁
            (全部工具都交给模型 = 对照组); 开了就按 `case.question` 选几组
            (`scoping.ToolScope`), 于是模型**只看见、也只调得到**那几组.
            两段 (挂起前 / 恢复) 用的是同一个范围对象 —— 它是无状态的.
        prompt_version: 这一跑用哪一版提示词 (issue 45 的那次 A/B); None = 读清单
            那一版. 它是**整批恒定**的 (不随题面变), 所以进的是服务而不是会话 ——
            见 `MinimallService.prompt_version`.

    attributes:
        (无公开属性; `harness` 是只读取数口, 给用例看记录层用)
    """

    def __init__(
        self,
        model: ChatModel,
        *,
        conversation: str,
        simulate_approval: bool = True,
        payment_password: str | None = None,
        prune_tools: bool = False,
        prompt_version: str | None = None,
    ) -> None:
        self._model = model
        self._conversation = conversation
        self._simulate_approval = simulate_approval
        self._password = payment_password
        self._prune_tools = prune_tools
        self._prompt_version = prompt_version
        self._harness: EvalHarness | None = None
        self._scope: ToolScope | None = None

    async def run_once(self, case: EvalCase) -> RunFacts:
        """把 `case` 问出去, 交回这一跑的全部事实.

        三段: 问 (可能停在确认点上) → 替买家点确认 (**只在那时**) → 抄事实.

        Args:
            case: 这道题 (题面 + 期望 + 元信息; `meta["buyer_id"]` 指名这次以哪个
                买家的身份提问 —— 换一个就是为了换一只购物车).

        Returns:
            RunFacts: 这一跑的事实. 恢复那一段自己炸了时**照样返回** (记成
            `BROKEN` + 一句说明), 不往抛外 —— 一条题的装置坏了不该带走整批.
        """
        async with open_harness(
            self._model, prompt_version=self._prompt_version
        ) as harness:
            self._harness = harness
            context = self._context_of(case, harness)
            # 范围**一次定死在这一跑上**: 两段会话 (挂起前 / 恢复) 装的是同一个对象,
            # 于是恢复段不会忽然多出几个工具来 (见 `_resumed`)
            self._scope = self._scope_of(case)
            session = await harness.session(context, scope=self._scope)
            asked = await session.ask(case.question)
            run_id = session.last_run_id
            resumed, failure = await self._settle(context, harness, asked, run_id)
            return self._facts_of(session, run_id, asked, resumed, failure)

    async def aclose(self) -> None:
        """关掉这一跑用的模型.

        正常情况下 `open_harness` 收尾时已经关过它一次了 (谁建谁关); 这里再关一次
        是**兜底** —— `run_once` 在那套环境拿到模型**之前**就炸掉的那一跑 (上下文
        装配失败 / 题面读不出来), 模型没人管. 两次关的是同一个连接池 (协议明写
        那是幂等的), 于是不必再拿一个「关过没有」的记号去分辨这两种情形.
        """
        await self._model.aclose()

    @property
    def harness(self) -> EvalHarness | None:
        """这一跑用过的跑分环境; 还没跑过就是 None (**只给用例取数用**).

        报告的每一条数都从 `RunFacts` 走, 不看这个口子. 它存在是为了让用例能直接
        断言记录层里落了什么 —— 密码不进记录表那条否定断言尤其需要 (报告里只存
        答复的**字数**, 答复正文与工具结果都不在里面, 于是那条路要靠这里看).
        """
        return self._harness

    # ------------------------------------------------------------------
    # 内部: 上下文 / 挂起-恢复 / 事实
    # ------------------------------------------------------------------

    def _context_of(self, case: EvalCase, harness: EvalHarness) -> RunContext:
        """这道题的运行上下文 (组上下文的规矩在跑分环境里, 这里只是把题读出来).

        `meta["buyer_id"]` 指名这次以哪个买家的身份提问 —— 假商城的车按身份分,
        而「换一只车」正是让护栏那条 5000 上限真的被撞到的手段 (见 `fixtures`).
        """
        buyer = case.meta.get("buyer_id")
        return harness.context(
            self._conversation,
            user_id=BUYER_ID if buyer is None else int(buyer),
        )

    def _scope_of(self, case: EvalCase) -> ToolScope | None:
        """这一跑给模型开哪几组工具; None = 不裁 (对照组).

        裁的依据只有**题面那一句** (开放决策 2): 不带宽窄随轮次变化的口子, 否则
        两组之间的差异里会混进「什么时候裁的」这件与实验无关的事.
        """
        return ToolScope(question=case.question) if self._prune_tools else None

    async def _settle(
        self,
        context: RunContext,
        harness: EvalHarness,
        asked: LoopResult,
        run_id: str | None,
    ) -> tuple[LoopResult | None, str]:
        """把停在确认点上的那一跑接着跑完; 交回 (第二段的结果, 没跑通的说明).

        四种落点, 只有第二种会真的再跑一段 —— 其余三种都是 `(None, ...)`, 而事实面
        据此认定「这一跑只有第一段」:

        1. **没停在确认点上** —— 绝大多数题走这里.
        2. **停住了且要模拟确认** —— 组载荷 → 重新装配 → 恢复 (见模块 docstring).
        3. **停住了但不要模拟确认** (开放决策 1 的开关) 或 **该给凭据而没配** ——
           前者是刻意的 (这一跑就该如实记成挂起), 后者带一句说明.
        4. **恢复那一段自己炸了** —— 一句说明.

        Args:
            context: 第一段的上下文 (恢复那一段在它上面补键).
            harness: 这一跑的环境 (第二段会话从它上面装).
            asked: 第一段的结果.
            run_id: 第一段的运行编号 (恢复段沿用, issue 33).

        Returns:
            tuple[LoopResult | None, str]: 第二段的结果 (**None = 没有第二段**) +
            没跑通的说明 (空串 = 正常收尾). 说明非空时事实面记 `BROKEN`.
        """
        pending = await harness.pending_approvals(context.thread_id)
        if not pending or not self._simulate_approval:
            return None, ""
        missing = self._missing_credential(pending)
        if missing:
            return None, missing
        try:
            resumed = await self._resumed(harness, context, run_id, pending)
        except Exception as exc:
            # 宽是有意的: 这一段里什么都可能坏 (模型重试耗尽 / 快照写不下去 / 上游
            # 半路断了), 而它们的处置**完全一样** —— 记这一跑坏了, 接着跑下一题.
            # 窄成某个类型只会让没列到的那一种把整批带走.
            return None, f"模拟确认失败: {type(exc).__name__}: {exc}"
        if resumed is None:
            return None, "模拟确认失败: 那一段快照读不回来 (没有可恢复的帧)"
        return resumed, ""

    def _missing_credential(self, pending: list[ToolCall]) -> str:
        """这几条挂起里有没有「要密码而这次没配」 -> 一句说明 (没有就空串).

        **两个条件缺一不可**: 那一条点名要密码 (`approval_needs`), 而这次手上没有.
        只看后者的后果是把每一道挂了又该给密码的题都记成失败 —— 包括那些密码明明
        配好了的 (跑分跑出来全是一样的错, 而它会看着像是题的问题).
        """
        if self._password or not _wants_password(pending):
            return ""
        return (
            f"模拟确认失败: 这一调要 {PAYMENT_PASSWORD_FIELD}, 而 "
            f"{ENV_EVAL_PAYMENT_PASSWORD} 没配 (代付那一题跑不了)"
        )

    async def _resumed(
        self,
        harness: EvalHarness,
        context: RunContext,
        run_id: str | None,
        pending: list[ToolCall],
    ) -> LoopResult | None:
        """替买家点「确认」: 组载荷 → 重新装配一次会话 → 从那个运行编号接着跑.

        **重新装配是刻意的** (不是往旧会话里塞东西): 一次性凭据在**构造工具那一刻**
        就裹进了闭包 (`provider.MinimallToolProvider.provide`), 旧会话的工具是空手
        装的 —— 换会话才有那份密码. 带的 scope 是**第一段那个**: 挂起与恢复是同一次
        运行的两截, 中途换一套可见集就说不清这一跑到底给模型开的是什么了.

        能走到这里说明该有的凭据都有了: 缺的那种上一拍就拦下了 (`_settle` 先问
        `_missing_credential`), 于是下面那句赋值不会把 None 塞进载荷.
        """
        payload: dict[str, Any] = dict(context.payload)
        if _wants_password(pending):
            payload[PAYMENT_PASSWORD_FIELD] = self._password
        session = await harness.session(
            dataclasses.replace(context, payload=payload), scope=self._scope
        )
        return await session.resume(run_id=run_id, approval=Approval.approve())

    def _facts_of(
        self,
        session: ChatSession,
        run_id: str | None,
        first: LoopResult,
        resumed: LoopResult | None,
        failure: str,
    ) -> RunFacts:
        """两段结果 + 一句说明 → 这一跑的事实.

        「哪一段说了算」与「两段各自的数」是两件事: 终局 / 答复 / 轮数 / token 一律
        取自**最后那一段** (恢复段的轮数与 token 是**累计值**, 含挂起前跑过的那些,
        见 `LoopResult`), 而墙钟要把两段相加 —— 那两段都是这一跑花掉的时间.
        `resumed is None` 就是「没有第二段」(没停住 / 停住了没确认 / 第二段没跑通),
        这时照第一段算, 不重复计.
        """
        last = first if resumed is None else resumed
        if failure:
            outcome, error = RunOutcome.BROKEN, failure
        elif last.outcome is LoopOutcome.FINISHED:
            outcome, error = RunOutcome.COMPLETED, ""
        else:
            outcome, error = run_outcome_of(last.outcome), last.outcome.value
        return RunFacts(
            tool_calls=self._calls_of(run_id),
            # 挂起那几跑没有答复 (`None`) —— 与「答复是空串」是两回事, 照实传下去
            answer=last.content,
            outcome=outcome,
            error=error,
            turns=last.turn_count,
            tokens=last.total_tokens,
            elapsed_ms=first.elapsed_ms
            + (0.0 if resumed is None else resumed.elapsed_ms),
            run_id=run_id,
            cost=self._cost_of(run_id),
            config=self._config_of(session),
        )

    def _calls_of(self, run_id: str | None) -> tuple[CallRecord, ...]:
        """这一跑的工具调用 (按发生序; 两条路合成的那一份事实)."""
        if self._harness is None:
            return ()
        return tuple(
            CallRecord(
                tool_name=call.tool_name,
                arguments=call.arguments,
                # 行里存的是字符串 (枚举的值), 判据那边认的是枚举本身
                status=ToolCallStatus(call.status),
                duration_ms=call.duration_ms,
            )
            for call in self._harness.calls_of(run_id)
        )

    def _cost_of(self, run_id: str | None) -> float | None:
        """这一跑折算的金额 (元) —— 记录层那条线的取数口在跑分环境上."""
        if self._harness is None:
            return None
        return self._harness.cost_of(run_id)

    def _config_of(self, session: ChatSession) -> dict[str, Any]:
        """这一跑的配置快照 (报告头部那块直接摆它, 框架不解释键的意思).

        只摆**这一跑真的用了什么**: 模型名与提示词版本都从会话上取 (那是实际生效
        的那一份 —— 写死的常量会在换装配的那天与它分家).

        票面只点名要「模拟确认」那一格 (开放决策 1: 挂起题是跑分器替买家点的确认,
        报告不写就是让读者以为模型自己走完了全流程). 另外三格是**本片顺带补齐的**:
        报告头部那块配置快照没有它们就是个空块, 而填得出来的只有装配会话这一方
        (见 `RunFacts.config`: 放跑批器那一侧迟早与真装配漂开).

        **「工具范围」是 issue 44 那次 A/B 的自变量** (报告头部要一眼看出两组差在
        哪): 它是**按组恒定**的 (`全挂` / `按题裁剪`), 而每题开出来几组只记在跑分
        入口那一段分类表里 —— 逐题变化的键会让同一组里冒出几十份不同的配置快照
        (报告会如实点名, 而那份点名列里没有新信息).
        """
        return {
            "模型": session.model_name,
            "提示词": (session.prompt_ref or {}).get("name", ""),
            "工具数": len(session.tool_names),
            "工具范围": SCOPE_PRUNED if self._scope is not None else SCOPE_FULL,
            "模拟确认": self._approval_mode(),
        }

    def _approval_mode(self) -> str:
        """这一跑的模拟确认处在哪一档 (报告头部照原样印)."""
        if not self._simulate_approval:
            return "关 (挂起如实记)"
        return "开 (注入支付密码)" if self._password else "开 (没配支付密码)"


def subject_factory(
    model_for: Callable[[EvalCase], ChatModel],
    *,
    simulate_approval: bool = True,
    payment_password: str | None = None,
    prune_tools: bool = False,
    prompt_version: str | None = None,
) -> SubjectFactory:
    """造一个「给这道题建一个对象」的工厂 (跑批器每一跑调它一次).

    Args:
        model_for: 这道题的模型 —— **每一跑现调一次**: 模型的所有权随装配交出去
            (那一跑收尾时关掉), 共享一个的话第一跑结束就把它关了. 用例里传按题号
            写好的假大脑; 真模型传一个「收下题面、按配置造一个」的小函数 (如
            `lambda _case: build_model_for(options, writer)`) —— **签名里必须收下
            题面**, 工厂是按 `(case) -> 模型` 调的 (见 `protocols.py`); 题面用不上
            时忽略它即可 (两组同源时就是这样).
        simulate_approval: 挂起题要不要替买家点确认 (默认要, 见模块 docstring).
        payment_password: 代付要注入的密码; None = 读
            `CHARAPP_EVAL_PAYMENT_PASSWORD` (`config.eval_payment_password`).
        prune_tools: 每道题按题面裁工具 (issue 44 的实验组); 默认不裁 —— 两组之间
            **只该差这一个开关**, 别的旋钮都要一样.
        prompt_version: 这一批用哪一版提示词 (issue 45 的实验组); 默认 None = 读清单
            那一版. 与上面那条同一个纪律: 一次 A/B 里两组**只该差一个开关**.

    Returns:
        SubjectFactory: 造对象用的协程 —— 会话编号带题号与序号 (`order-03-1`), 于是
        同一条题的几次跑各是一段新会话 (不共享上文), 而记录里看得出哪个对象是哪一
        跑的. 序号是「这条题的第几个对象」: 跑批器开跑前那一次自检**也算一个**
        (`runner._probe` 要造一个看装不装得起来), 它没跑, 所以记录里第一条会是
        `-2` 起 —— 这不影响什么 (编号只要唯一), 但少一个数字时别以为是丢了一跑.
    """
    resolved = eval_payment_password() if payment_password is None else payment_password
    attempts: dict[str, int] = {}

    async def build(case: EvalCase) -> HarnessSubject:
        """按题号数这一次是它的第几个对象."""
        attempts[case.id] = attempts.get(case.id, 0) + 1
        return HarnessSubject(
            model_for(case),
            conversation=f"{case.id}-{attempts[case.id]}",
            simulate_approval=simulate_approval,
            payment_password=resolved,
            prune_tools=prune_tools,
            prompt_version=prompt_version,
        )

    return build


__all__ = ["HarnessSubject", "subject_factory"]
