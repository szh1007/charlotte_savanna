"""CharApp 测试共享 fixtures: 假商城 (respx) + 现成的客户端与工具集.

两条原则 (与框架的 tests/conftest.py 同一套):

- **默认用例不触网**: 商城走 respx 拦截, 模型走 MockLLM (从 `CharAgent/tests/`
  借, 见 pytest.ini 的 pythonpath) —— 整套测试离线可跑, 不依赖 Django 也不
  依赖真 API.
- **样本按商城真实契约写**: 字段与取值一律照 `app/minimall/serializers_agent.py`
  抄 (金额是 2 位小数字符串, 状态给 code + 中文 label) —— 样本一旦比契约宽松,
  测试就会在真实链路上放行本该红的东西.

**样本与假商城的本体自 issue 41 起住在 `CharApp/eval/fixtures.py`**: 离线跑分器
不是 pytest 用例 (它要落报告 / 要比两份文件 / 要传参数), 于是吃不到 fixture ——
把样本搬进 eval 模块之后, 跑分器与测试用的是**同一份** (方向是测试 → eval, 生产
不依赖测试). 本文件从那里 import 并转发, 既有用例的 `from conftest import ...`
一行都不用改.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import respx

from CharAgent.db.entities import KIND_BEHAVIORS, MemoryKind
from CharAgent.tests.doubles import no_backup_endpoint as no_backup_endpoint
from CharApp.eval.fixtures import (
    ADDRESSES,
    AGENT_BASE_URL,
    BUYER_ID,
    CANCELLED_ORDER,
    CART,
    CATEGORIES,
    EMPTY_CART,
    ENDPOINTS,
    ORDER_DETAIL,
    ORDER_LIST,
    ORDER_NO,
    PAID_ORDER,
    PAYMENT_PASSWORD,
    PRODUCT,
    PROFILE,
    REFUND,
    SENSITIVE_VALUES,
    TOKEN,
    WRITE_ENDPOINTS,
    agent_url,
    build_mall,
    mock_all,
)
from CharApp.minimall.client import MinimallClient
from CharApp.minimall.config import KnowledgeConfig
from CharApp.minimall.knowledge.citations import Citations
from CharApp.minimall.knowledge.retriever import KnowledgeRetriever

# 全部工具的名字 (顺序即注册顺序: 只读的在前, 会改数据的与那个读退款的接在后面,
# 代付、知识检索、长期记忆收尾) —— **这是这份名单的唯一权威**: 工具的契约用例与
# 提供者用例都比对它, 数量变了改这里.
TOOL_NAMES = (
    "search_products",
    "get_product_detail",
    "list_categories",
    "list_featured_products",
    "get_my_cart",
    "list_my_orders",
    "get_my_order",
    "get_my_profile",
    "list_my_addresses",
    "add_to_cart",
    "update_cart_item",
    "remove_cart_item",
    "clear_cart",
    "place_order",
    "cancel_my_order",
    "request_refund",
    "list_my_refunds",
    "pay_my_order",
    # L5-b 的知识检索 (装在最后一位, 与 build_tools 的装配顺序一致)
    "search_knowledge",
    # C13 的长期记忆 (接在知识检索之后, 与 provider.provide 的装配顺序一致)
    # + C30 的 forget (同一族的第三个, 挨着接)
    "remember",
    "recall",
    "forget",
)

# **会改数据**的那 8 个 (护栏的判据是注解, 而注解的含义就是"会改数据").
#
# 为什么不是上面那 9 个: L2 一次加了 8 个工具, 但 `list_my_refunds` 只是**读**
# 退款列表 —— 它不该占买家的写操作预算, 也不该被金额规则管 (见 tools.py 的
# 那一节说明). 「9 个写工具」是这两批的名字, 「8 个会改数据」才是注解的判据.
#
# `pay_my_order` (issue 35) 排在最后: 它同样是**写**, 只是它的"改"要买家本人点
# 一次头 (护栏让它挂起, 见 guardrail.py) —— 注解与预算照旧适用.
WRITE_TOOL_NAMES = (
    "add_to_cart",
    "update_cart_item",
    "remove_cart_item",
    "clear_cart",
    "place_order",
    "cancel_my_order",
    "request_refund",
    "pay_my_order",
)

# 从 `CharApp/eval/fixtures.py` 转发出来的那一批 (见模块 docstring): 列在这里是
# 为了说明「它们是**有意**再导出给用例的」—— 少了这份清单, ruff 的 F401 会把它们
# 当成没用的 import 删掉 (本文件确实一个都不直接用).
__all__ = [
    "ADDRESSES",
    "AGENT_BASE_URL",
    "BUYER_ID",
    "CANCELLED_ORDER",
    "CART",
    "CATEGORIES",
    "EMPTY_CART",
    "ENDPOINTS",
    "ORDER_DETAIL",
    "ORDER_LIST",
    "ORDER_NO",
    "PAID_ORDER",
    "PAYMENT_PASSWORD",
    "PRODUCT",
    "PROFILE",
    "REFUND",
    "SENSITIVE_VALUES",
    "TOKEN",
    "TOOL_NAMES",
    "WRITE_ENDPOINTS",
    "WRITE_TOOL_NAMES",
    "FakeMemory",
    "FakeMemoryStore",
    "agent_url",
    "build_mall",
    "citations_for_tests",
    "client",
    "mall",
    "memory_for_tests",
    "mock_all",
    "retriever_for_tests",
]


def retriever_for_tests() -> KnowledgeRetriever:
    """装配工具要的那个检索器 (默认配置的**空壳**, 构造期不连任何东西).

    绝大多数用例只是要看「工具集长什么样」(名字 / schema / 注解) —— 检索器在这一层
    是个占位: `KnowledgeRetriever` 构造期既不连 Milvus 也不加载模型 (那些都是
    `knowledge/` 里的惰性单例). 真正会去检索的用例自己注入替身 (见 test_tools 的
    知识库那一段), 不会碰这个.
    """
    return KnowledgeRetriever(KnowledgeConfig())


def citations_for_tests() -> Citations:
    """装配工具要的引用账 (空的一份) —— 与 `retriever_for_tests` 同一个理由.

    真跑起来时它由 `service.session_for` 每会话造一份 (还会把旧会话的检索结果
    垫进去); 这里只求"装配得起来".
    """
    return Citations()


@dataclass
class FakeMemory:
    """替身表里的一行 —— 带工具读写得到的那几个字段.

    (`source_thread_id` / `source_run_id` 不复刻: 工具只**写**它们, 没有任何
    工具路径读回来 —— 真值由框架侧的 `test_tool_memory.py` 用记账替身验.)
    """

    memory_id: str
    tenant_id: str
    user_id: str
    kind: str
    content: str
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    deleted_at: datetime | None = None


class FakeMemoryStore:
    """记忆仓储的测试替身: 内存一张表 + C12 契约那几面的最小复刻.

    为什么复刻而不是只记账: 「跨会话」「同一句话两次不产生两条」「超量淘汰」
    这几条验收要端到端看见**工具行为** —— 只记账的替身会让它们退化成"调用了
    就算过". 复刻的是契约的四面 (隔离 / 去重 / 按时间倒序 / 容量淘汰); 不做的
    是连续衰减分值 (排序取时间倒序 —— 在"越新越靠前"这个单调意义上等价) 与
    软删时刻的覆盖语义. 真 SQL 的那几面仍由 CharAgent 侧 `-m pg_db` 的用例守.
    """

    def __init__(self, *, capacity: int = 50) -> None:
        self.rows: list[FakeMemory] = []
        self.capacity = capacity

    async def add(
        self,
        *,
        tenant_id: str,
        user_id: str,
        content: str,
        kind: MemoryKind,
        source_thread_id: str | None = None,
        source_run_id: str | None = None,
        created_at: datetime | None = None,
    ) -> FakeMemory:
        moment = created_at if created_at is not None else datetime.now(UTC)
        for row in self.rows:
            if (row.tenant_id, row.user_id, row.content) == (
                tenant_id,
                user_id,
                content,
            ) and row.deleted_at is None:
                row.updated_at = moment
                return row
        # C30: 替换型 kind 顶掉旧值 —— 与真仓储同一条行为 (行为表是唯一判据)
        if KIND_BEHAVIORS[MemoryKind(kind)].replaces_previous:
            for row in self._alive(tenant_id, user_id):
                if row.kind == kind.value:
                    row.deleted_at = moment
        row = FakeMemory(
            memory_id=uuid4().hex,
            tenant_id=tenant_id,
            user_id=user_id,
            kind=kind.value,
            content=content,
            created_at=moment,
            updated_at=moment,
        )
        self.rows.append(row)
        self._prune(tenant_id, user_id, moment)
        return row

    async def list_for_user(self, tenant_id: str, user_id: str) -> list[FakeMemory]:
        return sorted(
            self._alive(tenant_id, user_id),
            key=lambda row: (row.created_at, row.memory_id),
            reverse=True,
        )

    async def list_by_id_prefix(
        self, prefix: str, *, tenant_id: str, user_id: str
    ) -> list[FakeMemory]:
        return [
            row
            for row in self._alive(tenant_id, user_id)
            if row.memory_id.startswith(prefix)
        ]

    async def soft_delete(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        user_id: str,
        moment: datetime | None = None,
    ) -> bool:
        for row in self.rows:
            if row.memory_id == memory_id and (row.tenant_id, row.user_id) == (
                tenant_id,
                user_id,
            ):
                row.deleted_at = moment if moment is not None else datetime.now(UTC)
                return True
        return False

    async def touch_used(
        self,
        memory_ids: list[str],
        *,
        tenant_id: str,
        user_id: str,
        moment: datetime | None = None,
    ) -> int:
        stamp = moment if moment is not None else datetime.now(UTC)
        wanted = set(memory_ids)
        count = 0
        for row in self._alive(tenant_id, user_id):
            if row.memory_id in wanted:
                row.last_used_at = stamp
                count += 1
        return count

    def _alive(self, tenant_id: str, user_id: str) -> list[FakeMemory]:
        return [
            row
            for row in self.rows
            if (row.tenant_id, row.user_id) == (tenant_id, user_id)
            and row.deleted_at is None
        ]

    def _prune(self, tenant_id: str, user_id: str, moment: datetime) -> None:
        alive = sorted(
            self._alive(tenant_id, user_id),
            key=lambda row: (row.created_at, row.memory_id),
        )
        overflow = len(alive) - self.capacity
        if overflow <= 0:
            return
        for row in alive[:overflow]:
            row.deleted_at = moment


def memory_for_tests(*, capacity: int = 50) -> FakeMemoryStore:
    """装配工具要的那份记忆仓储替身 (空的一张内存表).

    真跑起来它是 `MemoriesRepository` (Postgres); 这一层只求"装配得起来, 且
    契约那几面的行为一致" (见类 docstring). 默认容量与仓储的 `DEFAULT_CAPACITY`
    同值; 要演淘汰的用例自己传小的.
    """
    return FakeMemoryStore(capacity=capacity)


@pytest.fixture
def mall() -> Iterator[respx.MockRouter]:
    """假商城: 一个不触网的 respx 路由器 (本体在 `eval/fixtures.build_mall`)."""
    with build_mall() as router:
        yield router


@pytest.fixture
async def client() -> AsyncIterator[MinimallClient]:
    """一个接到假商城上的客户端 (地址与令牌都是测试常量)."""
    instance = MinimallClient(base_url=AGENT_BASE_URL, token=TOKEN)
    yield instance
    await instance.aclose()
