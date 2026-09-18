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
      ├── saver:  checkpoint_saver_from_env() / build_saver()   <- 三后端可切换
      └── loop:   AgentLoop(model, tools, saver=..., thread_id=..., event_sink=...)

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
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Sequence

from CharAgent.agent import AgentLoop, LoopGuard, LoopResult
from CharAgent.checkpoint import (
    Checkpoint,
    CheckpointCapabilities,
    CheckpointError,
    CheckpointSaver,
    format_history,
)
from CharAgent.client.utils.types import DEFAULT_THREAD_ID
from CharAgent.model.protocol import ChatModel
from CharAgent.model.utils.types import ModelMessage
from CharAgent.prompt import load_prompt, resolve_model_name
from CharAgent.stream.utils.types import EventSink
from CharAgent.tool import Tool
from CharAgent.tool.tools_demo import (
    batch_convert_lengths,
    convert_length,
    count_text_stats,
    get_current_time,
    query_order_status,
)

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
    ) -> None:
        self._model = model
        self._saver = saver
        self._thread_id = thread_id
        # 身份说明里要写它, 所以装配时就定下来 (值必须跟实际跑的模型一致)
        self._model_name = resolve_model_name(model_name)
        self._loop = AgentLoop(
            model,
            tools,
            guard=guard,
            max_tokens=max_tokens,
            thinking=thinking,
            # 事件出口与快照存储一起接上: 前者让用户边跑边看, 后者让中断可续
            event_sink=event_sink,
            saver=saver,
            thread_id=thread_id,
        )
        # 对话历史 (wire 消息): 跑完换成 loop 的完整历史; 没跑完则从快照收回
        # 已完成的工作 (见 _reclaim_progress) —— 两条路都让「上一轮做过什么」
        # 留在历史里, 模型下一轮就看得到.
        #
        # 会话历史的第一条恒为身份说明 (system). 正文按 prompt_name 从 prompt_dir
        # 取; 两个参数都不给时 = 框架自己的 system.prompt, 与从前完全一致.
        # model_name 照传: 框架那份模板里有 ${model_name} 占位符, 业务模板若要
        # 自己写身份说明也用得上; 模板里没这个占位符时多传的值会被忽略.
        system_prompt = load_prompt(
            prompt_name,
            prompt_dir=prompt_dir,
            model_name=self._model_name,
        )
        self._history: list[ModelMessage] = [
            {"role": "system", "content": system_prompt}
        ]

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

        Raises:
            ModelError: 模型调用失败 (重试耗尽后上抛; 此时不发终局事件).
            CheckpointError: 快照落盘失败 (存不下存档是可靠性故障, 不吞).
            asyncio.CancelledError: 被 Ctrl-C 打断 (CLI 的 kill switch 就是
                `task.cancel`); 本方法不吞, 由 app.py 接住并提示怎么续跑.
        """
        self._history.append({"role": "user", "content": question})
        return await self._run(self._loop.run(self._history))

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
            asyncio.CancelledError: 续跑期间又被 Ctrl-C 打断 (同样可再续).
        """
        checkpoint = await self._saver.load_latest(self._thread_id)
        if checkpoint is None:
            return None
        result = await self._run(self._loop.resume(checkpoint))
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
        """
        try:
            result = await pending
        except BaseException:
            # 打断 (CancelledError) 与失败 (模型/存储异常) 都走这里
            await self._reclaim_progress()
            raise
        self._history = result.messages
        return result

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

        读快照失败不上抛: 收不回来是小事, 把真正的失败原因 (模型错 / 存储错)
        盖掉是大事.
        """
        try:
            checkpoint = await self._saver.load_latest(self._thread_id)
        except CheckpointError:
            return
        if checkpoint is None:
            return
        messages = list(checkpoint.state.messages)
        if len(messages) > len(self._history):
            self._history = messages
