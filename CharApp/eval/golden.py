"""题集: 20 道评估题的加载与校验 (issue 42).

一句话理解: `cases/*.yaml` 是**数据** (一页一个场景), 本模块把它读成框架认的那串
`EvalCase`, 读的时候顺手把写坏的地方全挑出来.

**题为什么是数据而不是代码** (L4 规划期定的): badcase 回流的成本要低到「加一段
YAML」—— 题写在 Python 里的话, 每回流一条都要改一个模块、跑一遍全量用例. 而题集
本身会一直长 (L4 收口之后还有 L5), 所以格式与校验都得现在定死.

**校验为什么值得单独做一遍** (票据交付物 #2): 期望工具名写错一个字母, 在跑分那一
刻的表现是「这一跑少调了 `search_product`, 召回率 0.6」—— 一个看着像模型问题的
数字. 而它其实是题写错了. 于是在**读的那一刻**就拦下来, 并且一次把所有问题说完
(题是人写的, 改一趟不容易, 别让他们改完一处再来一处).

**YAML 的字段与 `EvalCase` 的字段有一处不同名**: YAML 里叫 `expect_params` (票据
的叫法), 到 `EvalCase` 上叫 `expect_args` (框架的叫法, 见 `CharAgent/eval/utils/
types.py`). 两边都是既成的, 映射就落在本模块 —— 只此一处.

**场景与挂起期望进 `meta`**: `EvalCase.meta` 是框架「只看不解释」的一块 (与
`RunContext.payload` 同一条纪律), 于是报告会把它原样署名出来, 而跑分器 (issue 43
的恢复流程 / 44 的裁剪分组) 想读也能读 —— 题集里那两个字段因此不必再开一个口子.

大白话版: 这一页是题集的**读入 + 体检**. 题目本身在 `cases/` 那六个 YAML 里.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from CharAgent.eval import EvalCase, EvalConfigError
from CharApp.minimall.client import DEFAULT_BASE_URL, MinimallClient
from CharApp.minimall.tools import build_tools

# 题集目录 (一页一个场景, 见各文件自己的文件头)
CASES_DIR = Path(__file__).resolve().parent / "cases"

# 六个场景 (题的分类): 商品 / 购物车 / 订单 / 退款 / 账户 / 政策咨询.
#
# 它是白名单而不是「文件名的集合」: 题里的 `scene` 字段说了算, 文件只是摆法 ——
# 把一条题从 cart.yaml 挪到 order.yaml 不该改变它的场景, 而写错场景名 (如
# `scence: cart` 那种) 要当场报出来.
SCENES = ("product", "cart", "order", "refund", "account", "policy")

# 一条题必填的三样 (其余都有默认值)
_REQUIRED_FIELDS = ("id", "scene", "question")


def load_cases(directory: Path | None = None) -> tuple[EvalCase, ...]:
    """读题集: 目录下每个 `*.yaml` 一页题, 合成一串 `EvalCase`.

    Args:
        directory: 题集目录; None = 本包下的 `cases/` (用例拿它指向临时目录).

    Returns:
        tuple[EvalCase, ...]: 全部题 (按文件名排, 文件内按书写顺序).

    Raises:
        EvalConfigError: 题写坏了 —— YAML 读不出来 / 顶层不是一串题 / 必填字段缺 /
            场景不在白名单 / 题号重复 / `expect_tools` 不是列表 (或里面写重了) /
            期望工具名不是真有的那 18 个之一 / `expect_params` 不是映射 (或它的键
            不是工具名) / `expect_suspend` 不是布尔 / `meta` 里那两个已知键写错.
            **一次把所有问题说完**, 不是报一条改一条.
    """
    base = CASES_DIR if directory is None else directory
    pages = sorted(base.glob("*.yaml"))
    if not pages:
        raise EvalConfigError(f"题集目录里一页题都没有: {base}")

    cases: list[EvalCase] = []
    problems: list[str] = []
    for path in pages:
        for index, raw in enumerate(_pages_of(path, problems), start=1):
            found = _Problems(f"{path.name} 第 {index} 条")
            case = _case_of(raw, found)
            problems.extend(found.messages)
            if case is not None:
                cases.append(case)
    problems.extend(
        f"题号重复: {case_id!r} (题号是逐题表与差异归因的行标签, 必须唯一)"
        for case_id in _duplicates([case.id for case in cases])
    )
    if problems:
        raise EvalConfigError(
            f"题集有问题 ({len(problems)} 处), 一处一处改:\n- " + "\n- ".join(problems)
        )
    return tuple(cases)


# ---------------------------------------------------------------------------
# 一页 → 一串题
# ---------------------------------------------------------------------------


def _pages_of(path: Path, problems: list[str]) -> list[Any]:
    """一页 YAML 里那串题 (读不出来 / 顶层不对就记一条问题, 交空列表)."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        problems.append(f"{path.name}: 读不出来 ({type(exc).__name__}: {exc})")
        return []
    if not isinstance(data, list):
        problems.append(
            f"{path.name}: 顶层该是一串题 (YAML 列表), 实际是 {type(data).__name__}"
        )
        return []
    return data


class _Problems:
    """一条题的问题袋: 攒下这条题写坏的地方, 顺带记住它是哪一页的第几条.

    为什么收成一个对象而不是「一条清单 + 一个位置字符串」: 那两样是**一起走**的
    —— 每条问题都落在同一条题上, 而它们要穿过 `_case_of` → `_tools_of` →
    `_params_of` 三层. 摊成两个参数的话, 位置要说三遍、清单要传三遍, 而「这一条
    到底有没有问题」还得靠调用方比对长度.

    文件级的问题 (整页读不出来) 不走这里 —— 那时还没有「第几条」可言.
    """

    def __init__(self, where: str) -> None:
        """记下位置; 题号要读到那一条才知道, 所以后填."""
        self.where = where
        self.case_id: Any = None
        self.messages: list[str] = []

    def add(self, message: str) -> None:
        """记一条 (自动带上位置与题号)."""
        at = self.where if self.case_id is None else f"{self.where} ({self.case_id})"
        self.messages.append(f"{at}: {message}")

    def __bool__(self) -> bool:
        """这一条题有没有写坏的地方."""
        return bool(self.messages)


def _case_of(raw: Any, problems: _Problems) -> EvalCase | None:
    """一条题 → `EvalCase`; 哪里写坏了就记进问题袋并交 None.

    Args:
        raw: YAML 里那一条 (该是个映射).
        problems: 这一条题的问题袋 (位置在它身上; 调用方一次全抛出去).

    Returns:
        EvalCase | None: 读成的那条题; 校验没过就是 None.
    """
    if not isinstance(raw, dict):
        problems.add(f"该是一个映射, 实际是 {type(raw).__name__}")
        return None

    missing = [name for name in _REQUIRED_FIELDS if not raw.get(name)]
    if missing:
        problems.add(f"缺了 {', '.join(missing)}")
        return None

    case_id, scene, question = raw["id"], raw["scene"], raw["question"]
    problems.case_id = case_id
    if scene not in SCENES:
        problems.add(f"场景 {scene!r} 不在 {list(SCENES)} 里")

    tools = _tools_of(raw.get("expect_tools", []), problems)
    params = _params_of(raw.get("expect_params", {}), problems)
    suspend = raw.get("expect_suspend", False)
    if not isinstance(suspend, bool):
        problems.add("expect_suspend 该是 true/false")
    meta = dict(raw.get("meta") or {})
    _check_meta(meta, problems)
    if problems:
        return None

    return EvalCase(
        id=str(case_id),
        question=str(question),
        expect_tools=tuple(tools),
        expect_args={name: dict(args) for name, args in params.items()},
        # 场景与挂起期望也进 meta: 框架不解释它, 而报告与跑分器都读得到
        meta={**meta, "scene": scene, "expect_suspend": suspend},
    )


def _tools_of(raw: Any, problems: _Problems) -> list[str]:
    """期望工具集: 一串真有的工具名 (写错一个字母就是这一条的用处所在)."""
    if not isinstance(raw, list):
        problems.add("expect_tools 该是一个列表")
        return []
    known = _real_tool_names()
    unknown = [name for name in raw if name not in known]
    if unknown:
        problems.add(
            f"期望的工具 {unknown} 业务里没有 (真有的是: {', '.join(sorted(known))})"
        )
    duplicated = _duplicates([str(name) for name in raw])
    if duplicated:
        problems.add(f"期望工具集里重复写了 {duplicated}")
    return [str(name) for name in raw if name in known]


def _params_of(raw: Any, problems: _Problems) -> dict[str, dict[str, Any]]:
    """期望参数: 工具名 → 参数子集 (键也必须是真有的工具名)."""
    if not isinstance(raw, dict):
        problems.add("expect_params 该是一个映射")
        return {}
    known = _real_tool_names()
    unknown = [name for name in raw if name not in known]
    if unknown:
        problems.add(f"expect_params 里点名了不存在的工具 {unknown}")
    bad = [name for name, args in raw.items() if not isinstance(args, dict)]
    if bad:
        problems.add(f"expect_params[{bad}] 该是一个映射")
    return {
        name: args
        for name, args in raw.items()
        if name in known and isinstance(args, dict)
    }


def _check_meta(meta: dict[str, Any], problems: _Problems) -> None:
    """`meta` 里那两个**跑分自己认**的键 (其余照旧原样透传).

    只认这两个是因为它们各自有一个读取方: `buyer_id` 由跑分器拿去换一只购物车
    (`BIG_CART_BUYER`), `expect_refusal` 由护栏判据拿去验「这一调真的被拦下了」.
    写错的后果都是**静默地验错东西** (拿错的车 / 找不到那个调用), 所以在这一刻
    就拦下来.
    """
    buyer = meta.get("buyer_id")
    if buyer is not None and not isinstance(buyer, int):
        problems.add("meta.buyer_id 该是一个整数")
    refusal = meta.get("expect_refusal")
    if refusal is not None and refusal not in _real_tool_names():
        problems.add(f"meta.expect_refusal={refusal!r} 不是真有的工具名")


def _duplicates(names: list[str]) -> list[str]:
    """出现不止一次的那些名字 (按字母序, 空的重复项去掉)."""
    return sorted({name for name in names if names.count(name) > 1})


@lru_cache(maxsize=1)
def _real_tool_names() -> frozenset[str]:
    """业务真有的那些工具名 —— 从**唯一来源**取 (装配工具的那个工厂).

    不连网: `build_tools` 只是把身份裹进闭包, 构造期一次请求都不发 (连接池是懒建
    的), 那个客户端因此是张空壳、用完即弃. 换成手抄一份名字清单会好写得多, 但抄件
    与真工具漂开的那天, 这套校验就变成「拿错的尺子量题」—— 而它恰恰是为了拦住
    写错的名字才存在的.
    """
    client = MinimallClient(base_url=DEFAULT_BASE_URL, token="")
    return frozenset(tool.name for tool in build_tools(client, 0))


__all__ = ["CASES_DIR", "SCENES", "load_cases"]
