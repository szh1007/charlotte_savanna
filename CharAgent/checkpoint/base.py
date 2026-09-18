"""CheckpointSaver 协议: 存快照 / 取快照的「插座」(difficulties #5).

一句话理解: 框架只规定两件事 —— **快照要能存下去、能按会话取回来**; 至于存到
哪儿 (内存里的一个字典 / Redis 的一个键 / Postgres 的一张表), 由实现决定.

打个比方: 这就是**插座**. 插头的形状在这里定死 (下面那几个方法), 后面插的是
电池 (内存, 一拔就没) 还是发电机 (Postgres, 断了电再来还有) 都行, 而用插头的
电器 (agent loop) 完全不用改. 所以换存储 = 换一个 saver 对象, 不是改 loop.

为什么是 Protocol 而不是基类 (与 model/protocol.py 的 ChatModel 同一取舍):
只照形状检查, 不要求继承 —— 谁都可以写自己的 saver (比如公司内部的对象存储),
不必 import 本模块的基类.

三种实现的能力差异 (本包的核心教学点, 用 capabilities 声明):

| 实现 | 存什么 | 有历史 | 会过期 | 适合 |
|------|--------|--------|--------|------|
| 内存 | 进程里的字典 | 有 | 不会 | 测试 / 单机玩 |
| Redis (history) | 一条 Stream (流水账) | 有 | 会 (TTL) | 快、可裁剪; 不宜当唯一存档 |
| Redis (latest) | 一个键, 只留最新一帧 | 无 | 会 (TTL) | 只要断点续跑的会话缓存 |
| Postgres | 一张表, 每帧一行 | 有 | 不会 | 强一致; 能按任意字段查 |

「会过期」一列说的是**本实现**给的开关 (TTL, 默认关); 「不宜当唯一存档」说的是
**部署层面**: 快照只活在 Redis 里, Redis 侧丢档 (没配持久化就重启 / 内存压力
逐出 / FLUSHALL) 就找不回来了 —— 要「存了就不会丢」用 Postgres. 注意它与内存版
不是一回事: 内存版是**进程**退出即丢, Redis 版进程重启后照样读得到 (CLI 正是靠
它跨进程接着跑).

「没有历史」这件事在调用时就报 CheckpointCapabilityError (明说做不到), 而不是
悄悄返回一个空列表让调用方以为「这个会话没存过」.
"""

from __future__ import annotations

from typing import Protocol

from CharAgent.checkpoint.utils.types import (
    Checkpoint,
    CheckpointCapabilities,
)


class CheckpointSaver(Protocol):
    """快照存储的协议 (三种实现见 memory.py / redis.py / postgres.py).

    方法全是 async: 存快照发生在 agent loop 跑的过程中, 而 Redis / Postgres 都是
    网络 IO —— 用同步调用会把整个事件循环卡住 (difficulties #65 的「阻塞 IO
    陷阱」), 所以协议从一开始就是异步的.

    **协议只声明各实现都有的那几个方法**. 某个实现可以另加自己的 —— 那些是
    **该实现专有**的, 按协议编程的调用方不该依赖它们 (换个后端就 AttributeError).
    现有的这类方法 (两个都在 Postgres 上):

    - `ensure_schema()`: 把表建出来 (幂等). 内存版没有表, Redis 版没有 schema ——
      只有要建表的实现才有这一步. Postgres 的每次存取会自己顺手调一次, 所以只有
      **测试想先把表备好**时才需要显式调 (本仓 `tests/conftest.py` 就是这么用的).
    - `delete_thread(thread_id)`: 按会话整段删掉 (返回删了几行). 内存版删不删无所谓,
      Redis 版靠键过期. 它要求**表已存在**.
    """

    @property
    def capabilities(self) -> CheckpointCapabilities:
        """这个实现能做到什么 (有没有历史 / 会不会过期).

        调用方可以先问一句再决定怎么做: 例如想 time-travel 就得先看
        `capabilities.history`, 是 False 就换 Postgres, 别等报错.
        """
        ...

    async def save(self, checkpoint: Checkpoint) -> None:
        """存下一帧快照 (Redis 是覆盖, 内存与 Postgres 是追加, 见各实现).

        **同编号重复保存视为同一次存**: 已经存过这个 checkpoint_id 时什么都不改
        (幂等). 这样「存到一半网络断了、重试一次」不会存出两份互相矛盾的记录.

        Args:
            checkpoint: 要存的快照 (调用方用 Checkpoint.create 造好).

        Raises:
            CheckpointStorageError: 存储后端出错 (连不上 / SQL 失败).
        """
        ...

    async def load_latest(self, thread_id: str) -> Checkpoint | None:
        """取某个会话**最新**的一帧快照 (断点续跑的入口).

        三种实现都支持这个方法 (Redis 只有最新一帧, 正好就是它要的语义).

        Args:
            thread_id: 会话标识.

        Returns:
            Checkpoint | None: 最新一帧; 这个会话从没存过则是 None.
        """
        ...

    async def load(
        self, checkpoint_id: str, *, thread_id: str | None = None
    ) -> Checkpoint | None:
        """按编号取某一帧快照 (time-travel 的入口: 挑一个历史时刻回去).

        Args:
            checkpoint_id: 要取的那一帧的编号.
            thread_id: 会话号 —— 给「按会话分区存放的实现」的准备 (Redis 的流式
                历史把帧按会话分区, 只凭编号不知道翻哪本账, 所以它要求一并给
                会话号); 有全局编号索引的实现 (内存 / Postgres) 会忽略这个参数.

        Raises:
            CheckpointCapabilityError: 该实现没有历史 (Redis 的 latest 模式只留
                最新一帧), 按编号翻旧账在它这里做不到.
            CheckpointConfigError: 按会话分区但没给 thread_id (明说缺什么, 别猜).
        """
        ...

    async def list_history(
        self, thread_id: str, *, limit: int | None = None
    ) -> list[Checkpoint]:
        """按时间顺序列出某个会话的全部快照 (从最早到最新).

        分支 (time-travel 分出去的新线) 也在同一个列表里 —— 列表本身就是那棵树
        的**平铺记录**, 谁接着谁看每条记录的 parent_id.

        Args:
            thread_id: 会话标识.
            limit: 只要**最近** N 帧 (None 表示全部); 返回时仍按从早到晚排 ——
                「取最近 N 帧, 但顺序照旧」是翻历史时最常用的读法. N 不是正数时
                返回空列表 (「最近 0 帧」就是没有).

        Raises:
            CheckpointCapabilityError: 该实现没有历史 (Redis).
        """
        ...

    async def aclose(self) -> None:
        """释放底层连接 (进程退出或换 saver 时调).

        没有连接可关的实现 (内存版) 也保留这个方法: 调用方不必知道手里是哪种实现,
        统一调一句就行. 外部传进来的连接不由本对象关闭 (谁建的谁关).
        """
        ...
