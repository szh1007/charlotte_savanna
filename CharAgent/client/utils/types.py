"""client 包静态零件: 启动选项 CliOptions.

一句话理解: 命令行上敲的那串参数, 解析完就装进 CliOptions 这一个对象; 后面
(装配模型 / 挑存储 / 跑会话 / 画终端) 全都读它, 不再回头去看 argv.

为什么要这么一个对象: 与 agent/utils/types.py 的 LoopState 同一条理由 ——
解析、装配、跑会话、打印这几个函数都要读同一批配置, 逐个当参数传来传去会变成
一长串 in/out 且容易漏改; 打包成一个不可变对象后, 每个函数只收 (options) 一项,
读了哪些配置在函数体里一眼可见. frozen=True 还顺带保证「解析出来的配置不会被
半路改掉」—— 演示时最忌跑到一半配置变了.

大白话版: 这是「这次 CLI 怎么跑」的一张工单 —— 存哪个后端、用哪个模型、问哪些
问题、要不要上色, 全写在上面. 工单只读, 谁要什么自己看.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 默认会话编号: 固定值才能跨进程接着跑 (Ctrl-C 退出后重开 --resume 的常用场景;
# 前提是后端为 redis / postgres —— 默认的 memory 版进程退出即丢, 谈不上跨进程);
# 想开一段新会话用 --thread-id 换一个 (标识符规则由 checkpoint 的 check_identifier 管).
#
# 它是**存档主键**, 与包名没有绑定关系 (只是恰好同名). 改主键的代价只有一条:
# 旧主键下的档默认取不到了 —— 2026-09-18 前默认值是 "cli-main", 要取那之前存下的
# 档加 `--thread-id cli-main`. 不存在「数据丢了」或「要迁移旧档」这回事.
DEFAULT_THREAD_ID = "client-main"


@dataclass(frozen=True, slots=True)
class CliOptions:
    """一次 CLI 运行的启动选项 (解析 argv 的产物, 全程只读).

    attributes:
        backend: 快照存储后端 (memory / redis / postgres); None 表示
            听环境变量 CHARAGENT_CHECKPOINT_BACKEND 的 (它也没有就用 memory). 这一项就是
            验收要求的「存储配置切换可演示」—— 换后端只改这一个值, loop 一行不动.
        thread_id: 会话编号 (快照按它分区; 同一个编号才能接着跑).
        questions: 非交互模式下要依次问的问题; 空元组表示进交互模式.
        resume: 启动后先从最新快照接着跑一次 (再按 questions / 交互继续).
        show_history: 只打印该会话的快照历史表, 不跑模型.
        model_name: 模型名覆盖; None 表示听 .env 的 DEEPSEEK_MODEL_NAME.
        thinking: 思考模式开关 (False = 关闭, 省 token 也更快); None 表示不传,
            走上游默认 (开启且 effort=high). 见 ChatModel.generate 的说明.
        max_turns: 轮数上限 (透传 LoopGuard, 防跑飞).
        max_tokens: 单次输出上限 (透传 generate); 设小可以稳定造出 length 截断,
            用来演示截断处理路径 (CONTINUE 续写).
        use_retry: 是否给模型套重试包装 (RetryingChatModel). True 是默认:
            真实端点偶发 429 / 5xx 时能自动再试; False 用于对照演示「不重试会怎样」.
        color: 是否上 ANSI 颜色 (非 TTY / 重定向 / --plain 时关掉).
    """

    backend: str | None = None
    thread_id: str = DEFAULT_THREAD_ID
    questions: tuple[str, ...] = field(default_factory=tuple)
    resume: bool = False
    show_history: bool = False
    model_name: str | None = None
    thinking: bool | None = None
    max_turns: int = 10
    max_tokens: int | None = None
    use_retry: bool = True
    color: bool = True

    @property
    def interactive(self) -> bool:
        """是否进交互模式 (没给 --question 且不是只想看历史)."""
        return not self.questions and not self.show_history
