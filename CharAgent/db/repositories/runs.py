"""运行 (Run) 的存取: 建运行、按编号/幂等键取、推进状态机.

一句话理解: 这是**运行柜员**, 而且它还替你把着状态机那道门 —— 想改状态必须走
`try_transition`, 而它只放行合法的那几步 (规则见 state.py).

**为什么状态推进要下沉到 SQL 而不是「先查出来, 判断, 再改」**: 那三步之间有时
间窗口. 两个人同时点「取消」, 两边都查到状态是 running、都判定「可以取消」,
然后都去改 —— 于是取消动作执行了两次 (取消里如果有真实副作用, 就是重复执行).
换成一条带条件的 UPDATE (`WHERE status = 我以为的那个`), 数据库会用行锁保证
只有一个能改成功, 另一个自然失败 —— 这就是「抢到的那个人负责」.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from CharAgent.db.entities import Run, RunStatus
from CharAgent.db.errors import DataConfigError, DataStoreError
from CharAgent.db.repositories.base import PgRepository
from CharAgent.db.schema import runs
from CharAgent.db.state import TERMINAL_RUN_STATUSES, ensure_transition


class RunsRepository(PgRepository):
    """运行表的读写口 (含状态机推进)."""

    async def add(
        self,
        *,
        thread_id: str,
        run_id: str | None = None,
        status: RunStatus = RunStatus.CREATED,
        request_id: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        created_at: datetime | None = None,
    ) -> Run:
        """建一次运行 (编号与时刻默认自动生成).

        Args:
            thread_id: 属于哪段会话 (会话必须已存在, 否则外键会拦下来).
            run_id: 显式编号 (测试用); None 则生成 uuid4 hex.
            status: 初始状态 (默认 created).
            request_id: 幂等键 (#17); 同一个键只能建一次运行 —— 库里对它有唯一
                约束, 重复插入会**报错** (而不是悄悄建出第二条运行).
            model / prompt_version: 这次用的模型与 prompt 版本 (#40).
            created_at: 显式时刻 (测试用); None 则取当下 (UTC).

        Returns:
            Run: 建好的实体.

        Raises:
            DataStoreError: 写库失败 (request_id 重复 / 会话不存在 / 库连不上).
        """
        moment = created_at if created_at is not None else datetime.now(UTC)
        run = Run(
            run_id=run_id if run_id is not None else uuid4().hex,
            thread_id=thread_id,
            status=status.value,
            request_id=request_id,
            model=model,
            prompt_version=prompt_version,
            # 三个计数列在库里有默认值, 但显式写 0 让「新建的运行账目是零」
            # 这件事在代码里也看得见 (读代码的人不必去翻建表语句)
            total_tokens=0,
            # 用量分解显式写 None: 这次运行**还没跑过**, 所以还没有分量可拆 ——
            # 与「跑过了, 用量是零」不是同一件事 (见 _params 与 db/schema.py)
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
            total_cost=0,
            turn_count=0,
            error=None,
            created_at=moment,
            updated_at=moment,
            finished_at=None,
        )
        async with self._session() as session:
            try:
                session.execute(runs.insert().values(**self._params(run)))
            except IntegrityError as exc:
                # 唯一约束挡下重复的 request_id 时, 报「唯一约束被违反」对调用方
                # 没用 (它不知道是哪一条、为什么重复). 换成一句能直接照着排查的话
                # —— 这是「说清楚」而不是「换一类异常」, 所以在这里就地抛, 不等
                # 外层统一包装.
                raise DataStoreError(
                    f"这次运行没能建起来 (request_id={run.request_id!r} 可能已经被"
                    f"用过了): {exc.orig}"
                ) from exc
            return self._one(session, run.run_id)

    async def add_terminal(
        self,
        *,
        thread_id: str,
        status: RunStatus,
        run_id: str | None = None,
        model: str | None = None,
        turn_count: int = 0,
        total_tokens: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        cache_hit_tokens: int | None = None,
        cache_miss_tokens: int | None = None,
        prompt_version: str | None = None,
        moment: datetime | None = None,
    ) -> Run:
        """把一次**已经跑完**的运行记成一行 (直接以终态落库).

        与 `add` 的差别只有一条: 这是**记录一个已经发生的事实**, 不是**开始一次
        执行**. 所以它不做「建 created 再迁状态」那三步 —— `created → running →
        finished` 每一步都要求一次往返 (每次一个事务), 而这里回放的是一个已知的
        结局; 它也把 `finished_at` 一并写上, 免得库里出现「状态是终态而 finished_at
        是 NULL」这种自相矛盾的行 (`state.py` 明说 NULL 表示还没跑到终点).

        状态机 (state.py 的合法迁移表) 管的是**活着的**运行怎么走; 这个方法写的是
        它的结局, 所以只校验「你给的是不是终态」, 不校验迁移路径.

        Args:
            thread_id: 属于哪段会话 (会话必须已存在, 否则外键会拦下来).
            status: 终态之一 (finished / failed / cancelled).
            run_id: 显式编号 (测试用); None 则生成 uuid4 hex.
            model: 这次用的模型名 —— 与发给 API 的那个**逐字一致** (取
                `prompt/load.py` 的 `resolve_model_name`); None 表示调用方没给
                (框架自己不知道模型对象的名字, 见 `ChatModel` 那份薄协议).
            turn_count: 这次跑了多少轮模型决策.
            total_tokens: 这次累计用量 (账单原值: 上游 usage 的总数).
            input_tokens / output_tokens / reasoning_tokens / cache_hit_tokens /
            cache_miss_tokens: total_tokens 的**归因拆解** (#34). None 表示上游
                一次都没上报过这个分量 —— 与 0 (报过、值就是零) 是两回事, 所以
                这几个参数默认是 None 而不是 0.
            prompt_version: 这次用的提示词名 (如 "system/v2"); None 表示装配时
                没给身份说明 (框架不知道它是什么).
            moment: 显式时刻 (测试用); None 则取当下 (UTC) —— 建 / 更新 / 结束
                三个时刻取同一个值: 这是一条**回顾**记录, 不是三个真实时刻.

        Returns:
            Run: 落好的实体.

        Raises:
            DataConfigError: 给的状态不是终态 (那是调用方搞错了 —— 一条历史记录
                不该停在半路, 那种行该由 `add` 建出来再逐条推进).
            DataStoreError: 写库失败 (会话不存在 / 库连不上).
        """
        if status not in TERMINAL_RUN_STATUSES:
            allowed = ", ".join(sorted(item.value for item in TERMINAL_RUN_STATUSES))
            raise DataConfigError(
                f"记录一条已跑完的运行只能给终态 ({allowed}), 实际: {status.value!r}"
                " —— 记录中的运行本来就该有个结局"
            )
        stamp = moment if moment is not None else datetime.now(UTC)
        run = Run(
            run_id=run_id if run_id is not None else uuid4().hex,
            thread_id=thread_id,
            status=status.value,
            request_id=None,
            # 模型名与提示词版本都是**版本归因** (#40): 回答质量掉了要能查出「是换了
            # 模型还是换了 prompt」—— 两样都由调用方递进来; 花费那一列仍留给 L3 的
            # 成本记账
            model=model,
            prompt_version=prompt_version,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            total_cost=0,
            turn_count=turn_count,
            error=None,
            created_at=stamp,
            updated_at=stamp,
            finished_at=stamp,
        )
        async with self._session() as session:
            session.execute(runs.insert().values(**self._params(run)))
            return self._one(session, run.run_id)

    async def get(self, run_id: str) -> Run | None:
        """按编号取一次运行 (没有则 None)."""
        async with self._session() as session:
            return self._one(session, run_id)

    async def get_by_request_id(self, request_id: str) -> Run | None:
        """按幂等键取已有运行 (没有则 None) —— 重复提交时用它直接返回老结果.

        为什么单独一个方法而不是让调用方自己过滤: 这是幂等流程的入口 (带
        request_id 重复提交 `POST /runs` 时走这条), 写成方法后「幂等键怎么查」
        只有一处实现.
        """
        statement = select(Run).where(runs.c.request_id == request_id)
        async with self._session() as session:
            return session.scalars(statement).first()

    async def try_transition(
        self,
        run_id: str,
        from_status: RunStatus,
        to_status: RunStatus,
        *,
        error: dict[str, object] | None = None,
    ) -> bool:
        """原子地推进状态: 只有「当前确实是 from_status, 且迁移合法」才改得动.

        这是状态推进的**唯一**推荐入口 (取消与审批恢复都该走它).

        Args:
            run_id: 哪次运行.
            from_status: 调用方以为的当前状态 (**乐观锁**: 库里的实际值跟它不
                一样就说明别人先动了手, 这次放弃).
            to_status: 想迁到的状态.
            error: 失败原因 (只在迁到 failed 时有意义); None 表示不动这一列.

        Returns:
            bool: 改成功 True; False = 没改 (别人先动了 / 调用方以为的状态过时了).

        Raises:
            InvalidTransitionError: 这次迁移本身就不在状态机允许表里 —— 那是调用
                方的逻辑错 (比如让 finished 回到 running), 属于开发期就该发现的
                问题, 不该当成「并发没抢到」悄悄返回 False.
        """
        ensure_transition(from_status, to_status)
        values: dict[str, object] = {
            "status": to_status.value,
            "updated_at": datetime.now(UTC),
        }
        if error is not None:
            values["error"] = error
        if to_status in TERMINAL_RUN_STATUSES:
            values["finished_at"] = datetime.now(UTC)

        statement = (
            update(runs)
            .where(runs.c.run_id == run_id)
            # 这一行就是乐观锁: 状态被别处改过 -> 匹配不上 -> 一行都不改
            .where(runs.c.status == from_status.value)
            .values(**values)
        )
        async with self._session() as session:
            result = session.execute(statement)
            return bool(result.rowcount)

    async def set_status(
        self,
        run_id: str,
        to_status: RunStatus,
        *,
        error: dict[str, object] | None = None,
    ) -> bool:
        """把状态直接改成目标值 (不做状态机校验, 也不看当前是什么).

        给**修数据 / 对账脚本**用: 库里的状态因为某次崩溃停在一个不该在的地方,
        需要人工纠正. 正常业务路径请用 `try_transition` —— 那边才有并发保护与
        合法性检查.

        Returns:
            bool: 改到了行 True; False = 没有这个运行.
        """
        values: dict[str, object] = {
            "status": to_status.value,
            "updated_at": datetime.now(UTC),
        }
        if error is not None:
            values["error"] = error
        if to_status in TERMINAL_RUN_STATUSES:
            values["finished_at"] = datetime.now(UTC)
        statement = update(runs).where(runs.c.run_id == run_id).values(**values)
        async with self._session() as session:
            return bool(session.execute(statement).rowcount)

    @staticmethod
    def _params(run: Run) -> dict[str, object]:
        """实体 → 插入参数.

        列清单只有这一处: 表里加一列, 就在这里加一个键 —— 漏了会**当场**被库拦下
        (INSERT 少给一列, 而 NOT NULL 的那些不接受缺省), 不会悄悄写进空值.
        """
        return {
            "run_id": run.run_id,
            "thread_id": run.thread_id,
            "status": run.status,
            "request_id": run.request_id,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "total_tokens": run.total_tokens,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "reasoning_tokens": run.reasoning_tokens,
            "cache_hit_tokens": run.cache_hit_tokens,
            "cache_miss_tokens": run.cache_miss_tokens,
            "total_cost": run.total_cost,
            "turn_count": run.turn_count,
            "error": run.error,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "finished_at": run.finished_at,
        }

    @staticmethod
    def _one(session: Session, run_id: str) -> Run | None:
        """按主键取一行 (强制读库里的最新值, 不吃会话缓存).

        `expire_all()` 的作用: 同一个会话里刚才可能已经查过这一行 (还带着旧
        状态), 不清掉的话 `get` 会直接把内存里那份旧对象还给你 —— 刚改完状态
        再查却是旧值, 这种 bug 极难看出来.
        """
        session.expire_all()
        return session.get(Run, run_id)
