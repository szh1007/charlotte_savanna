"""ChatSession: 一次 CLI 会话 —— 把框架零件装配成一台能问答的机器.

一句话理解: 本文件是「接线」的那一页. 前面九个包各自造好了零件 (模型适配器、
重试包装、工具集、agent loop、快照存储), 但零件之间谁跟谁连、按什么参数连,
一直没有一个**生产调用点** —— 之前只有测试在组装它们. ChatSession 就是那个
调用点: 一次装配, 之后只问不管.

装配图:

    ChatSession
      ├── model:  RetryingChatModel(chat_model_from_env())      <- 重试包在协议层
      │             └ 被包的是 httpx 裸调适配器 (或测试塞的替身)
      ├── tools:  DEMO_TOOLS (演示工具集)
      ├── prompt: load_prompt(prompt_name, prompt_dir=...)      <- 身份说明 (读一次)
      │             └ 它的**引用** {名字, sha256} 随快照落盘, 那几千字正文本体
      │               不必逐帧抄 (帧 v5, 见 prompt/ref.py)
      ├── saver:  checkpoint_saver_from_env() / build_saver()   <- 三后端可切换
      └── loop:   AgentLoop(model, tools, saver=..., thread_id=..., prompt_ref=...)

三条得到验证的关系 (P0 验收要的三件事, 都落在这一页):
1. **带工具的问答跑通** —— `ask()` 走完「模型决策 → 工具执行 → 结果回填 → 最终
   答复」; 事件经 event_sink 实时推给终端, 不是等跑完才一次性吐出来.
2. **断点续跑不重复已完成动作** —— `resume()` 从最新快照接着跑. 已完成的轮次
   (含工具结果) 都躺在快照的历史里, 所以那些工具**不会重跑**; 计数器的口径是
   累计值, 于是「最多几轮 / 多少 token」的预算跨断点仍然算数. 同一条性质的
   **口语版**是 `ask()`: 被打断时把已完成的工作从快照收回来 (`_reclaim_progress`),
   于是用户说一句「继续」就能接着跑, 不必知道快照这回事.
3. **存储配置切换** —— 换 saver 就换后端 (内存 / Redis / Postgres), loop 与
   模型一行不动; 差别体现在能力上 (有没有历史、会不会过期), 由 `frame_count`
   与 `history_table` 如实反映.

大白话版: 这是「把零件装成一台机器」的装配台. 装好之后对外只有三个动作:
问一句 (ask)、接着上次跑 (resume)、看一眼存档 (history_table).

**重启之后还认得上一段对话** (ticket 17): 会话历史本来只在内存里, 进程一换就没了
—— 于是「重启服务接着聊」这件事, 光把快照换成 Postgres 是不够的, 还得**有人去读
它**. 这一页读 (`_hydrate_once`: 第一次提问前把最新一帧的历史拿回来), 同时负责把
这一轮**发生过什么**写进记录表 (`recorder=`, 见 db/recorder.py). 两件事都在**会话**
这一层, 于是命令行与 HTTP 两个入口同时覆盖, 谁都不必各写一份.

三者分工 (别混):

| | 归谁 | 语义 |
|---|---|---|
| 快照 (`checkpoint`) | `_saver` | **接着跑**: 断点续跑 / 回溯; |
| | | 每轮一帧, 帧只插不改 (存的是全量账本) |
| 记录 (`charagent_messages`) | `recorder` | **给人看**: 持久、只增不减、永不压缩 |
| 水合 (`_hydrate_once`) | 本文件 | 重启后把快照的历史**读回**内存, |
| | | 接着问的话才有上文 |

**压缩不落地** (#7 的纪律, 见 `agent/loop.py` 的 `_record_turn`): 压缩只决定「这一次
请求送什么」, 账本与按它落盘的每一帧都是全量. 帧里另外带着压缩进度 (summary /
summary_covers 两个字段, 记的是「压到哪一步了」) —— 那是进度, 不是把历史压掉.
快照当不了记录另有两条: 它是**给机器接着跑**的形态 (编号 + 全量账本 + 计数器),
而 Redis 那份还会过期.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Sequence
from dataclasses import replace

from CharAgent.agent import (
    AgentLoop,
    CompactionPolicy,
    LoopGuard,
    LoopResult,
    TokenCounter,
)
from CharAgent.agent.utils.messages import tool_wire
from CharAgent.checkpoint import (
    Checkpoint,
    CheckpointCapabilities,
    CheckpointError,
    CheckpointSaver,
    format_history,
)
from CharAgent.checkpoint.utils.pending import pending_tool_calls
from CharAgent.client.utils.types import DEFAULT_THREAD_ID
from CharAgent.db.entities import RunStatus
from CharAgent.db.recorder import RunRecorder
from CharAgent.hooks import HookRegistry
from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.types import ModelMessage
from CharAgent.prompt import (
    PromptError,
    identity_message,
    load_prompt,
    prompt_ref,
    resolve_model_name,
    restore_identity,
)
from CharAgent.stream.utils.types import EventSink
from CharAgent.tool import Tool, ToolExecution
from CharAgent.tool.tools_demo import (
    batch_convert_lengths,
    convert_length,
    count_text_stats,
    get_current_time,
    query_order_status,
)

# 同一棵日志树 (与 checkpoint / prompt / db 那几处同一个做法): 会话层只在**收尾
# 那条路**上记日志 —— 那条路不能上抛, 于是必须留痕 (见 _reclaim_progress)
logger = logging.getLogger("charagent.client")


# CLI 默认开放的工具集 (演示工具集, 业务无关、无外部依赖、确定性输出).
#
# 为什么只注册五个: tools_demo 里有六个, 第六个 query_order_status_manual 与
# query_order_status **同能力**(同一份 mock 数据、同样输出), 是 manual schema
# 引擎的教学对照. 两个都注册会变成「同一个本事有两个工具名」, 模型只能随机挑
# 一个 —— 演示时看到哪个纯属运气. 那一个留在 tools_demo 里由测试对照两引擎.
DEMO_TOOLS: tuple[Tool, ...] = (
    get_current_time,
    convert_length,
    batch_convert_lengths,
    count_text_stats,
    query_order_status,
)


class ChatSession:
    """一次 CLI 会话: 一个 loop + 一个存储 + 一段不断变长的对话历史.

    会话状态只有一份: `history` (wire 消息历史). 每问一句就把新问题接在它后面
    交给 loop, 跑完再把它换成 loop 返回的完整历史 —— 于是多轮对话自然连上
    (模型能看到前面聊过什么), 落进快照的也是整段对话.

    Args:
        model: 已装好的模型 (生产走 `app.build_model`: 裸适配器 + 重试包装;
            测试可以直接塞 MockLLM —— ChatModel 是薄协议, 换谁都不改本类).
        saver: 快照存储 (三实现之一). CLI 里必有: 断点续跑与历史视图都靠它,
            没有它本类的一半方法无从谈起, 所以不给默认值.
        tools: 开放给模型的工具集, None 表示不开放工具 (纯聊天).
        thread_id: 会话编号 (快照按它分区); 同一个编号才能跨进程接着跑.
        model_name: 实际生效的模型名 (写进身份说明). None 表示按
            `--model` → `.env` 的 DEEPSEEK_MODEL_NAME → 默认值 的次序解析
            (见 resolve_model_name).
        event_sink: 事件出口 (CLI 传 EventPrinter; 测试传收集器). None 表示
            不接出口 —— 事件仍会触发 hooks, 只是没人接收.
        guard: 循环软限制; None 表示 LoopGuard() 默认 (max_turns=10).
        max_tokens: 单次输出上限 (透传 generate); 设小能稳定造出截断.
        thinking: 思考模式开关; None 表示不传 (上游默认开启).
        prompt_name: 身份说明用哪份提示词; 默认 "system" (框架自己那份), 现有
            行为一字不变. 业务助手在这里填自己的客服提示词名.
        prompt_dir: 从哪个目录取那份提示词; None (默认) 表示框架的 `templates/`.
            业务提示词放业务自己的目录, 于是框架目录里不留业务的东西.
        hooks: hook 注册表 (扩展点); None 表示不挂任何插件, 行为与从前一字不变.
            业务侧挂插件走**这条路**, 而不是自己建 `AgentLoop` —— 会话是装配的
            唯一入口, 绕开它建 loop 就等于把快照 / 提示词 / 历史那几件事各做一遍.
        compactor: 上下文压缩策略 (#7); None (默认) 表示不压 —— 每轮把整段历史
            原样发出去, 行为与从前一字不变. 业务按自己的窗口配阈值与保留轮数
            (默认实现 TrimAndSummarize).
        counter: 估算器 (判阈值用); None 表示用默认的校准式估算. 只在配了
            compactor 时有意义.
        hydrate: 第一次提问前要不要把快照里的历史读回来 (默认 True). 关掉 =
            每段新会话都从零开始 (调试 / 想开一段全新对话时用); 想彻底不认旧账
            就换个 thread_id, 那比关这个开关更直白.
        recorder: 会话记录员 (db/recorder.py 的实现, 或任何有那三个方法的对象:
            `begin` / `record` / `record_unfinished`);
            None (默认) 表示不记账, 行为与从前逐字一样. 记录**是旁挂的**: 它的
            失败不影响 ask 返回结果 (见 `_run` 的说明).

    没跑完的那一轮怎么办 (本类最要紧的一条规矩): loop 干活时用的是自己那份**副本**
    历史, 副本随被取消的任务一起没了 —— 但每一轮结束时落过盘的快照还在. 于是失败
    路径上去快照把**已完成的工作**收回来 (`_reclaim_progress`), 会话历史因此始终
    等于「最后落盘的那个完整状态 + 用户之后新说的话」. 用户由此不用学任何命令:
    打断之后接着说一句「继续」就是一条普通提问, 模型看着历史自己就接上了.
    """

    def __init__(
        self,
        model: ChatModel,
        *,
        saver: CheckpointSaver,
        tools: Sequence[Tool] | None = None,
        thread_id: str = DEFAULT_THREAD_ID,
        model_name: str | None = None,
        event_sink: EventSink | None = None,
        guard: LoopGuard | None = None,
        max_tokens: int | None = None,
        thinking: bool | None = None,
        prompt_name: str = "system",
        prompt_dir: str | os.PathLike[str] | None = None,
        hooks: HookRegistry | None = None,
        compactor: CompactionPolicy | None = None,
        counter: TokenCounter | None = None,
        hydrate: bool = True,
        recorder: RunRecorder | None = None,
    ) -> None:
        self._model = model
        self._saver = saver
        self._thread_id = thread_id
        # 身份说明里要写它, 所以装配时就定下来 (值必须跟实际跑的模型一致)
        self._model_name = resolve_model_name(model_name)
        # 对话历史 (wire 消息): 跑完换成 loop 的完整历史; 没跑完则从快照收回
        # 已完成的工作 (见 _reclaim_progress) —— 两条路都让「上一轮做过什么」
        # 留在历史里, 模型下一轮就看得到.
        #
        # 会话历史的第一条恒为身份说明 (system). 正文按 prompt_name 从 prompt_dir
        # 取; 两个参数都不给时 = 框架自己的 system.prompt, 与从前完全一致.
        # model_name 照传: 框架那份模板里有 ${model_name} 占位符, 业务模板若要
        # 自己写身份说明也用得上; 模板里没这个占位符时多传的值会被忽略.
        #
        # 它在**构造期**只读一次盘 (prompt 包刻意不缓存: 一个会话读一次正好, 而
        # 缓存会带来「改了文件不生效」的调试陷阱); 从快照读回历史时要按引用再读
        # 一次, 那时还得知道去哪儿取 —— 所以目录留着备用 (名字不用留: 它在引用里).
        self._prompt_dir = prompt_dir
        system_prompt = load_prompt(
            prompt_name,
            prompt_dir=prompt_dir,
            model_name=self._model_name,
        )
        # 引用 = 「这段会话用的是哪一份身份说明」的凭据 (名字 + 正文哈希). 它进
        # 每一帧快照, 于是那几千字正文不必逐帧抄一遍 (帧 v5, 见 prompt/ref.py);
        # 正文本体仍留在下面那条 history[0] 里 —— 请求照旧带着它发出去.
        self._prompt_ref = prompt_ref(prompt_name, system_prompt)
        self._history: list[ModelMessage] = [identity_message(system_prompt)]
        # 两个开关原样收下 (会话不解释它们: 一个管「读不读历史」, 一个管「记不记账」)
        self._hydrate = hydrate
        self._recorder = recorder
        self._loop = AgentLoop(
            model,
            tools,
            guard=guard,
            max_tokens=max_tokens,
            thinking=thinking,
            # 压缩策略原样转交 (会话不解释参数; 不配就与从前一样)
            compactor=compactor,
            counter=counter,
            # 事件出口与快照存储一起接上: 前者让用户边跑边看, 后者让中断可续
            event_sink=event_sink,
            saver=saver,
            thread_id=thread_id,
            # 插件注册表原样转交: 会话不解释它挂的是什么, 也不替业务挑点
            # (六个触发点见 HookRegistry; 空注册零开销).
            hooks=hooks,
        )
        # 上下文压缩的进度 (摘要 + 它压到第几条): 与 history 一样是**会话状态** ——
        # 每段 run 结束时从结果里收下, 下一段连同历史一起递回去. 不收的话, 每次
        # 提问都是一个全新的压缩进度, 于是同一段旧历史会被反复重压 (内容不会错,
        # 但白花一次摘要调用, 滚动摘要的收益也就丢了).
        self._summary: str | None = None
        self._summary_covers = 0
        # 下一帧快照挂在哪一帧下面: 水合时取最新那一帧的编号 (接着写同一棵树),
        # 每段 run 结束换成刚落的那一帧. 不维护它的话, 同一段对话每问一句就在
        # 快照存储里多一条新根 (旧链变孤儿) —— 内容不丢, 但「这段对话的快照」看起来
        # 就成了好几条互不相干的线.
        self._parent_id: str | None = None

    # ------------------------------------------------------------------
    # 只读属性
    # ------------------------------------------------------------------

    @property
    def thread_id(self) -> str:
        """本会话的编号 (快照按它分区)."""
        return self._thread_id

    @property
    def model_name(self) -> str:
        """实际生效的模型名 (身份说明里写的就是它).

        启动横幅与身份说明都从这一个值取 —— 两处各读一次 env 迟早会不一致.
        """
        return self._model_name

    @property
    def prompt_ref(self) -> dict[str, str] | None:
        """当前这段历史用的身份说明的引用 (名字 + 正文哈希); None 表示没有.

        它恒与 `history[0]` 相符 —— 历史被换成快照里那段时, 引用跟着一起换 (见
        `_hydrate_once` / `_reclaim_progress` / `_restore` 三处). 要回答「这段会话
        到底用的哪一版提示词」就查它.
        """
        return self._prompt_ref

    @property
    def tool_names(self) -> tuple[str, ...]:
        """开放给模型的工具名 (启动横幅上印个数; 取自 loop 而不是模块常量).

        为什么要问 loop: 「这座会话到底开了哪些工具」的真相在 loop 手里 ——
        读 `DEMO_TOOLS` 那种模块常量在装配被换掉时会印出错的数字.
        """
        return self._loop.tool_names

    @property
    def saver_name(self) -> str:
        """快照存储的类名 (启动横幅上印出来: 换后端时一眼看清正在用哪个).

        读类名而不是让存储自报名字: 三实现各自的名字已经很清楚
        (InMemoryCheckpointSaver / RedisCheckpointSaver / PostgresCheckpointSaver),
        给协议再加一个 `name` 属性是白加一层.
        """
        return type(self._saver).__name__

    @property
    def saver_capabilities(self) -> CheckpointCapabilities:
        """这个后端做得到什么 (有没有历史 / 会不会过期), 由存储自己声明.

        调用方先问再决定 —— 想翻历史就该先看 `capabilities.history`, 是 False
        就换后端 (或把 Redis 切到 history 模式), 不必等一个能力异常.
        """
        return self._saver.capabilities

    @property
    def history(self) -> list[dict]:
        """当前对话历史 (wire 消息的浅拷贝: 调用方拿去展示可以, 改它不影响会话).

        第一条恒为身份说明 (system, 来自 `system.prompt`); 之后是「用户问 + 模型答」
        加上中间的 tool 消息 —— 原样是发给模型的形状, 拿去做任何展示前先想清楚
        要不要滤掉 system 与 tool.

        为什么给拷贝: 这是会话的内部状态, 外部改一下就能把对话搅乱 (下一条消息
        接在哪儿全靠它). 浅拷贝够用 —— 消息 dict 追加后不再变更 (loop 的既有约定).
        """
        return list(self._history)

    # ------------------------------------------------------------------
    # 三个动作: 问一句 / 接着跑 / 看存档
    # ------------------------------------------------------------------

    async def ask(self, question: str) -> LoopResult:
        """问一句 (带工具问答的完整一轮): 跑 loop 直到自然结束或刹车.

        新问题接在当前历史末尾再交给 loop —— 于是这是一段连续对话, 不是每次都
        从零开始; 跑完把 loop 返回的完整历史收下 (含本轮所有工具调用与结果).

        「接着刚才被打断的那件事跑」不需要任何特殊入口: 那句提问还在历史里,
        已完成的工作也已从快照收回 (见 `_reclaim_progress`), 所以用户说一句
        「继续」就是**一条普通提问**, 模型看着历史自己接得上.

        「重启之后接着聊」同样不需要特殊入口: 第一次提问前会先把快照里的历史
        读回来 (`_hydrate_once`), 于是这句提问接的是上一段进程留下的对话.

        Raises:
            ModelError: 模型调用失败 (重试耗尽后上抛; 此时不发终局事件).
            CheckpointError: 快照落盘失败 (存不下存档是可靠性故障, 不吞).
            asyncio.CancelledError: 被 Ctrl-C 打断 (CLI 的 kill switch 就是
                `task.cancel`); 本方法不吞, 由 app.py 接住并提示怎么续跑.

        Note:
            记账是**旁挂的**: 记录员失败不影响本方法返回结果 (它的正常工作方式是
            自己记日志 + 返回 False, 见 db/recorder.py 的契约). 理由很直白 ——
            用户已经看到答复了 (终局事件在 loop 里就发过), 不该因为一行账写不进去
            把一次跑完的问答变成一次失败.
        """
        await self._hydrate_once()
        # 记进记录表时从这里切开: 前面那段历史上一次已经写过了, 重写会写出重复行
        since = len(self._history)
        # 摘要「新不新」也在这里判 (比较基准是跑之前那份, 而 _run 会把它覆盖掉)
        before_summary = self._summary
        self._history.append({"role": "user", "content": question})
        # 开账: 这一轮的运行行**先建出来** (ticket 22). 帧是在运行中途逐轮落盘的,
        # 而它的 `run_id` 是指向那一行的外键 —— 行不在, 帧就盖不上编号. 编号定下来
        # 之后一路带着: 交给 loop (盖到每帧) 与收尾的 `_record`
        run_id = await self._begin_run(question)
        try:
            result = await self._run(
                self._loop.run(
                    self._history,
                    run_id=run_id,
                    # 身份说明的引用**逐次给**: 历史可能是刚从快照读回来的那一段,
                    # 它用的也许是另一版提示词 (引用跟着历史一起换, 见 _hydrate_once)
                    prompt_ref=self._prompt_ref,
                    summary=self._summary,
                    summary_covers=self._summary_covers,
                    # 接着上一段的快照链往下写 (水合之后第一帧就挂在那条链上)
                    parent_id=self._parent_id,
                )
            )
        except BaseException as exc:
            # 取消与失败那一轮也要记: 用户确实说过那句话, 页面上也显示了它 ——
            # 记录里不该凭空少一轮 (取消与失败在记录里长得一样, 区别在运行行)
            await self._record_unfinished(question, exc, run_id=run_id)
            raise
        await self._record(
            result,
            run_id=run_id,
            since=since,
            summary=result.summary if result.summary != before_summary else None,
        )
        return result

    async def resume(self) -> LoopResult | None:
        """从最新一帧快照接着跑; 没有可恢复的快照时返回 None.

        「不重复已完成动作」在这里是**免费**得到的: 起点是快照里的消息历史,
        而已经执行过的工具结果就在那份历史里 —— loop 不会去重跑它们. 快照恰好
        停在「工具调用还没有结果」的半路时 (人工审批挂起点), loop 还会先
        把那几条欠着的调用补做完再继续 (checkpoint/utils/pending.py).

        Returns:
            LoopResult | None: 本次续跑的结果; None 表示这个会话没有任何快照
            (新会话, 或者内存后端在进程退出后把档丢了 —— 后者正是「存储介质
            不同, 语义也不同」的情形).

        Raises:
            CheckpointError: 读取快照失败.
            PromptNotFoundError: 这帧的身份说明不在盘上了 (那段会话用的那版提示词
                被删了) —— 编不出正文就不猜, 见 prompt/ref.py 的 deref_prompt.
            asyncio.CancelledError: 续跑期间又被 Ctrl-C 打断 (同样可再续).
        """
        checkpoint = await self._saver.load_latest(self._thread_id)
        if checkpoint is None:
            return None
        # 身份说明被剥离过的帧 (v5) 先补回来再交给 loop —— 补它要读盘, 而 loop 不碰
        # 磁盘; 不补的话 loop 会当场拦下 (见 AgentLoop.resume 的护栏)
        result = await self._run(self._loop.resume(self._restore(checkpoint)))
        return result

    async def frame_count(self) -> int | None:
        """本会话存了几帧快照; None 表示这个后端不留历史 (如 Redis latest 模式).

        为什么要问一句而不是直接读: 「不留历史」在三个后端里语义不同 (内存与
        Postgres 留, Redis 得开 history 模式), 存储用 `capabilities` 声明它做得到
        什么, 调用方先问再决定 —— 比等一个 CheckpointCapabilityError 更从容.
        """
        frames = await self.history_frames()
        return None if frames is None else len(frames)

    async def history_frames(self) -> list[Checkpoint] | None:
        """本会话的全部快照 (从早到晚); None 表示这个后端不留历史."""
        if not self._saver.capabilities.history:
            return None
        return list(await self._saver.list_history(self._thread_id))

    async def history_table(self) -> str:
        """快照历史 -> 一张对齐的文本表格 (checkpoint/utils/history.py 画).

        表格每行一帧, 列有轮次 / 来源 / 本轮 token 与耗时 / 调了哪些工具 /
        结束原因 / 父帧 —— 「哪一步最贵」「这一帧是从哪儿岔出来的」一眼就看出来.
        """
        frames = await self.history_frames()
        if frames is None:
            return (
                "这个快照存储不留历史 (Redis 的 latest 模式只保留最新一帧). "
                "想翻历史请用 --backend postgres, 或把 Redis 切到 history 模式 "
                "(CHARAGENT_CHECKPOINT_REDIS_MODE=history)."
            )
        return format_history(frames)

    async def aclose(self) -> None:
        """释放模型连接池与存储连接 (谁建谁关: 都是本会话建的)."""
        await self._model.aclose()
        await self._saver.aclose()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _run(self, pending: Awaitable[LoopResult]) -> LoopResult:
        """跑一次 loop 并接管会话状态: 跑完收下完整历史, 没跑完则收回进度.

        为什么失败路径要去快照收一次: 打断的那一轮, loop 的工作数据 (LoopState)
        随异常一起没了, 但它**已经落盘的快照还在** —— 那里面装着已完成的工作
        (工具结果等), 而会话历史里只有用户那句提问. 不收回来的话, 模型下一轮
        就看不到上一轮做过什么, 只能重做一遍.

        记账不在这里 (见 `ask`): 「这一轮问了什么」「新压出来的摘要」都是那次提问
        的事, 而 `resume` 根本没有这两项 —— 它们不该双双挂成 Optional 传进来.
        """
        try:
            result = await pending
        except BaseException:
            # 打断 (CancelledError) 与失败 (模型/存储异常) 都走这里
            await self._reclaim_progress()
            raise
        self._history = result.messages
        # 压缩进度与历史同源: 一起收下, 下一句问话接着用
        self._summary = result.summary
        self._summary_covers = result.summary_covers
        # 下一帧挂在刚落的那一帧下面 (同一段会话的快照因此串成一条链)
        self._parent_id = result.last_checkpoint_id
        return result

    async def _hydrate_once(self) -> None:
        """第一次提问前, 把快照里的历史读回内存 (惰性, 只认那一次).

        这是 ticket 17 补的第三件事: `resume()` 的语义是「**接着跑**」(会把欠着的
        工具调用补做完、再问模型一次), 而这里要的是「**读历史**, 然后正常 ask」.
        两者都拿快照, 用处完全不同 —— 混用会让一次普通提问变成一次续跑.

        三个条件都满足才动: 开关打开 / 会话历史还只有那条身份说明 (本进程还没聊
        过) / 快照里有帧. 于是「同一进程里接着聊」这条老路一步都不多走.

        读完还顺手做两件事 (否则重启之后的行为会前后不一致):

        - **给欠着结果的工具调用补一条回填** (见 `_seal_pending_calls`): 快照可能
          停在「模型要调工具、结果还没回来」的半路, 那样的历史直接喂给模型会被
          上游拒掉 (tool_calls 与 tool 消息必须配对), 也会把模型推向重发写操作.
        - **接着快照的链往下写**: 记下最新那一帧的编号当 `parent_id`, 于是重启
          之后落的帧挂回原来的那棵树上, 而不是又起一条新根.

        读不到快照 (后端故障) 时静默跳过, 当作「这段会话没有历史」: 拿不到旧账
        不该拦住用户这一句问话. 这种情形下不必我们自己喊 —— 这一轮落快照时同一个
        后端会再报一次, 那次是往上抛的 (存不下存档不吞).

        身份说明取不回来 (那段会话用的那版提示词被删了) 是另一回事: 那种情况**上抛**
        —— 编不出正文还继续, 等于让这段会话带着别人的身份往下聊 (与 `load.py` 的
        「读不到就不静默退回上一版」同一条纪律). 快照读不到丢的是「旧账」, 提示词
        读不到丢的是「它是谁」, 两者不该同一条处理.

        Note:
            失败之后**不设「已试过」标记**: 历史还是只有一条, 于是下一次提问会再
            试一次 —— 抖动过去之后自己就好了, 成功之后这条门自然关上.
        """
        if not self._hydrate or len(self._history) > 1:
            return
        try:
            checkpoint = await self._saver.load_latest(self._thread_id)
        except CheckpointError:
            return
        if checkpoint is None:
            return
        # 身份说明被剥离过的帧 (v5) 先补回来: 交给 loop 的历史必须是**完整请求形状**
        # 那一条. 引用跟着一起换 —— 它必须与补回来的那条正文相符, 否则下一次落盘时
        # sha 校验会拦下 (见 prompt/ref.py 的 restore_identity)
        self._history, self._prompt_ref = restore_identity(
            list(checkpoint.state.messages),
            checkpoint.state.prompt_ref,
            prompt_dir=self._prompt_dir,
            model_name=self._model_name,
            context=f"thread={self._thread_id}",
        )
        self._history = _seal_pending_calls(self._history)
        # 压缩进度也一起收 (与 `_reclaim_progress` 同一个口径): 不收的话, 重启后
        # 那一句问话会把同一段旧历史重压一遍 —— 内容不会错, 但白花一次摘要调用
        self._summary = checkpoint.state.summary
        self._summary_covers = checkpoint.state.summary_covers
        self._parent_id = checkpoint.checkpoint_id

    async def _begin_run(self, title: str = "") -> str | None:
        """开这一轮的账, 拿回运行行的编号 (没配记录员 = None, 一步都不走).

        为什么记账要走两拍 (ticket 22): 快照帧在运行中途落盘, 而帧上的 `run_id`
        是指向运行行的外键 —— 行必须先存在, 于是编号由这里提前定下, 一路交给
        loop (盖到每帧) 与收尾的 `_record`.

        `title` 是会话标题的候选 (这次提问): 会话行也得在跑之前建出来 (运行行与
        帧的外键都指着它 —— 帧那条是 ticket 24 补的), 而那时标题还没有别的来源.
        """
        if self._recorder is None:
            return None
        return await self._recorder.begin(thread_id=self._thread_id, title=title)

    async def _record(
        self,
        result: LoopResult,
        *,
        run_id: str | None,
        since: int,
        summary: str | None = None,
    ) -> None:
        """把这一轮交给记录员 (没配记录员 = 一步都不走).

        `run_id` 是 `_begin_run` 那一步建出来的行 —— 收尾是**推进**它, 不是另起
        一行 (帧上的编号已经指向它了).
        """
        if self._recorder is None:
            return
        # 模型名由**会话**告诉记录员: loop 手上是个薄协议的模型对象 (没有名字属性),
        # 而 `LoopResult` 里也没有 —— 装配处是唯一知道「这次跑的是哪个模型」的地方
        await self._recorder.record(
            thread_id=self._thread_id,
            result=result,
            run_id=run_id,
            since=since,
            summary=summary,
            model=self._model_name,
        )

    async def _record_unfinished(
        self, question: str, error: BaseException, *, run_id: str | None
    ) -> None:
        """把「这一轮没答完」交给记录员 (取消与失败都算).

        取消与失败在记录里**写成同一种样子** (提问 + 一句「这一轮没答完」), 区别
        落在运行行的状态上 (cancelled / failed): 对看记录的人来说它们是同一件事,
        而「是谁停的」的用处是排查 —— 那是状态列该回答的.
        """
        if self._recorder is None:
            return
        status = (
            RunStatus.CANCELLED
            if isinstance(error, asyncio.CancelledError)
            else RunStatus.FAILED
        )
        await self._recorder.record_unfinished(
            thread_id=self._thread_id,
            question=question,
            status=status,
            run_id=run_id,
            model=self._model_name,
        )

    async def _reclaim_progress(self) -> None:
        """没跑完时, 把快照里已完成的工作收进会话历史 (**只做加法**).

        这一手替代了原来「把提问撤回」的做法. 两相比较:

        | | 撤回 (旧) | 收回 (现在) |
        |---|---|---|
        | 会话历史 | 这次的提问被删掉, 做的全丢 | = 快照的完整历史 + 新说的话 |
        | 说「继续」 | 得靠命令或关键词路由回快照 | 就是普通提问, 模型自己接上 |
        | 说别的 | 模型看不到刚做过什么 | 模型看得到, 上下文更全 |

        **只做加法**: 快照的历史比当前的长才替换. 短了就说明这一轮还没落过盘
        (比如第一轮模型调用就失败了), 那保持现状 —— 用户刚说的那句话不能丢.

        能这么做的前提: 本会话的快照是一条直线 (`resume` 只取 `load_latest`,
        不走 time-travel 分叉), 所以「快照的历史」永远是「会话历史」的前缀,
        比长度就是比进度. P1 若接 time-travel, 这里要换成显式的版本比较.

        压缩进度 (摘要) 与历史同在那一帧里, 于是**一起收**: 只收历史不收摘要的话,
        下一句问话会把同一段旧历史重压一遍.

        读快照失败不上抛: 收不回来是小事, 把真正的失败原因 (模型错 / 存储错)
        盖掉是大事. **身份说明读不回来按同一条规矩办** (记一条 warning 就返回) ——
        而 `_hydrate_once` 那条路恰好相反 (取不回来就上抛): 差别在「收尾」与「继续」,
        后者要接着用这个身份聊下去, 前者只是少收一次进度.
        """
        try:
            checkpoint = await self._saver.load_latest(self._thread_id)
        except CheckpointError:
            return
        if checkpoint is None:
            return
        try:
            messages, ref = restore_identity(
                list(checkpoint.state.messages),
                checkpoint.state.prompt_ref,
                prompt_dir=self._prompt_dir,
                model_name=self._model_name,
                context=f"thread={self._thread_id}",
            )
        except PromptError as exc:
            # 身份说明取不回来 (文件被删 / 目录改了): 本方法的职责是「把已完成的工作
            # 收回来」, 而不是拦住一次已经在收尾的失败 —— 与上面那条「读快照失败不
            # 上抛」同一条取舍: 收不回来是小事, 把真正的失败原因 (模型错 / 存储错)
            # 盖掉是大事.
            #
            # 与 _hydrate_once 的区别正在这里: 那条路是**用户要接着聊**, 身份说明取
            # 不回来就该当场报错; 这条路是**收尾**, 少收一次进度不该改变失败的性质.
            logger.warning(
                "会话 %s 收回进度时读不到身份说明, 本次不回收: %s",
                self._thread_id,
                exc,
            )
            return
        if len(messages) > len(self._history):
            self._history = messages
            # 引用与历史同源 (restore_identity 的契约): 换了历史就得换引用, 否则
            # 下一次落盘时 sha 校验发现两者对不上而报错
            self._prompt_ref = ref
            self._summary = checkpoint.state.summary
            self._summary_covers = checkpoint.state.summary_covers
        # 下一帧挂到最新那一帧下面: 哪怕这次没收历史, 最新那一帧也是当前进度所在
        self._parent_id = checkpoint.checkpoint_id

    def _restore(self, checkpoint: Checkpoint) -> Checkpoint:
        """把快照里被剥离的身份说明补回来 (交给 loop 之前的准备工作).

        为什么要经会话层这一道: 补它要**读盘** (按引用去提示词目录取正文), 而 loop
        与存储层都不碰磁盘 —— 会话是唯一同时握着「提示词在哪儿」与「快照里那段历史」
        的地方 (与 `_hydrate_once` / `_reclaim_progress` 同一个道理, 区别只在这条路
        是交给 loop.resume 而不是收进内存).

        返回的是**新对象** (dataclasses.replace) 而不是就地改: 传进来的快照可能还被
        调用方拿着 (排查 / 断言), 动它会让「读到的那帧」与「实际用的那帧」悄悄不同.

        Note:
            没被剥离过的帧 (v4 及更早, 或调用方没给引用) 原样返回 —— 那种帧的正文就
            在 messages[0] 里, 不需要补.

        Raises:
            PromptNotFoundError: 那段会话用的那版提示词不在盘上了 (见 deref_prompt).
        """
        if checkpoint.state.prompt_ref is None:
            return checkpoint
        messages, ref = restore_identity(
            list(checkpoint.state.messages),
            checkpoint.state.prompt_ref,
            prompt_dir=self._prompt_dir,
            model_name=self._model_name,
            context=f"thread={self._thread_id}",
        )
        # 引用一起换: 它必须与补回来的正文相符, 否则 loop 之后落的每一帧都会被 sha
        # 校验拦下 (帧里那个老引用说的是当初那份正文, 而正文可能已经不同了)
        return replace(
            checkpoint,
            state=replace(checkpoint.state, messages=messages, prompt_ref=ref),
        )


# 水合时给「欠着结果的工具调用」补的回填文本.
#
# 用「未知」而不是「未执行」: 进程可能死在工具执行的**中间**, 说「没执行」是
# 撒谎 —— 而模型正是靠这句话判断要不要重发.
UNKNOWN_RESULT_TEXT = "本次服务中断, 这一步的结果未知"


def _seal_pending_calls(messages: list[ModelMessage]) -> list[ModelMessage]:
    """给历史里「欠着结果」的工具调用补一条「结果未知」的回填 (水合专用).

    为什么必须补 (而不是把那半截丢掉): 丢掉之后模型看到的是「我从没调过这个
    工具」, 下一轮很可能**重发** —— 而那个工具可能是下单 / 退款, 重发就是重复
    下单. 补一条「结果未知」会把模型推向**先查状态** (读工具) 而不是重发.

    为什么绝不顺手把欠着的调用执行掉: 那等于服务重启后自动重放写操作 —— 与
    「高危操作绝不自动执行」是同一条纪律 (见 checkpoint/utils/pending.py).

    Args:
        messages: 从快照里读回来的历史 (调用方给的是副本, 本函数不改它).

    Returns:
        list[ModelMessage]: 补好回填的列表; 没有欠账时**原样返回**(不复制).
    """
    pending = pending_tool_calls(messages)
    if not pending:
        return messages
    sealed = list(messages)
    for call in pending:
        # 复用工具轮那条 wire 构造: 「tool_calls 与 tool 消息怎么配对」只有一处定义
        sealed.append(
            tool_wire(
                call.id,
                ToolExecution(tool_name=call.name, ok=False, error=UNKNOWN_RESULT_TEXT),
            )
        )
    return sealed
