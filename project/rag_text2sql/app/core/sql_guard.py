"""SQL 护栏: 只读白名单 + LIMIT 补齐/收紧 —— 判据全在这一个模块里.

C15 先在跑批侧立了一版 (`app/eval/guard.py`), C16 把它提升为**生产链路的执行咽喉**:
`DwMysqlRepository.validate_sql` / `execute_sql` 都从这里过, 图里的
`_7_validate_sql` 与 `_9_execute_sql` 因此自动受保护 —— 一处实现, 两个节点,
再加一个跑批器, 不重复造.

三道判据 (对齐 C16 票据):

1. **语句级白名单** —— 只放行单条 SELECT / WITH 查询; 关键词按词边界判, 且先
   屏蔽字符串字面量与注释 (否则 `WHERE name = 'drop'` 这种正常语句会被误杀).
   词表与「开头判定」两条都做: 前者挡藏在子句里的写操作, 后者挡 `;` 之外的多语句.
2. **LIMIT 补齐 / 收紧** —— 没有 LIMIT 的查询补上默认值 (200); 带 LIMIT 但超过
   上限 (1000) 的收紧到上限. 实现是**外层包裹**而不是改写内层语句: 结果集上限
   等价, 但不必去动 SQL 文本 (改写风险大).
3. **拒绝理由给模型看** —— 拒绝是给校正节点的可操作输入 ("只允许…请改成…"),
   不是一句「非法」; 这样 LLM 有机会自己改对, 而不是整图失败.

数据库侧还有一层兜底 (不在此模块): dw 引擎的每个连接从建立起就是 READ ONLY
事务 + `max_execution_time` 预算, 见 `app/clients/mysql.py`.

两个已踩过的坑 (都有回归用例, 见 tests/test_sql_guard.py):

- **掩码只能用于扫描** —— 拿掩码后的文本去拼要执行的 SQL, `'华北'` 会被抹成空白,
  语句"成功执行"但语义变了 (gold 自检当场抓住 12 道).
- **收尾括号前要换行** —— 行尾 `-- 注释` 会把同一行的 `)` 一起注释掉.
"""

import re
from dataclasses import dataclass

# C16 开工前拍板: 默认 200 / 上限 1000 (票面建议值)
DEFAULT_LIMIT = 200
LIMIT_CAP = 1000

# 写操作与管理类语句: 命中任一个即拒 (词边界匹配, 不看子串)
#
# **没有 `REPLACE`**: 它同时是只读字符串函数 `REPLACE(str, from, to)`, 放进来会把
# `SELECT REPLACE(region_name, '省', '')` 这种正常查询误杀 (评审实测抓到);
# 写形态的 `REPLACE INTO` 由「必须以 SELECT / WITH 开头」那条挡住。
FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "RENAME",
    "GRANT",
    "REVOKE",
    "LOAD",
    "OUTFILE",
    "DUMPFILE",
    "CALL",
    "HANDLER",
    "LOCK",
)

# 字符串字面量与**惰性**注释: 先屏蔽再判关键词, 避免 `'drop table'` 这类正常内容误杀。
#
# 三处都是实测踩出来的:
# 1. 块注释那一段曾**漏了开头那个 `|`**, 于是 `/* ... */` 从来没被屏蔽过
#    —— `SELECT /* 说明 */ 1` 因注释里出现禁词被误拒;
# 2. **`/*! ... */` 不是注释**: MySQL 会执行这种版本注释里的内容,
#    所以它必须留在扫描范围里 (`SELECT 1 /*!80000 ; DROP TABLE t */` 得被拒);
# 3. **`--` 后必须跟空白才是注释**: `--x` 在 MySQL 里是运算符/参数名的一部分,
#    把它当注释会让 `SELECT 1 --x; DROP TABLE t` 整个后段隐形。
_LITERAL_OR_COMMENT = re.compile(
    r"'(?:[^'\\]|\\.)*'"
    r'|"(?:[^"\\]|\\.)*"'
    r"|`(?:[^`]|``)*`"
    r"|--(?:[ \t][^\n]*|(?=\n)|$)"
    r"|#[^\n]*"
    r"|/\*(?![!+]).*?\*/",
    re.DOTALL | re.MULTILINE,
)

# `LIMIT n` / `LIMIT offset, n` —— 取「行数」那一个数
_LIMIT_CLAUSE = re.compile(r"\bLIMIT\s+(\d+)\s*(?:,\s*(\d+))?", re.IGNORECASE)


class SqlRejectedError(RuntimeError):
    """生成的 SQL 被护栏拒绝 —— 与「数据库执行报错」是两类, 报告与日志里分开记."""


@dataclass(frozen=True)
class SqlGuardLimits:
    """LIMIT 的两档: 没写 LIMIT 时补多少 / 写了也不许超过多少."""

    default_limit: int = DEFAULT_LIMIT
    cap_limit: int = LIMIT_CAP

    def __post_init__(self) -> None:
        if self.default_limit < 1 or self.cap_limit < 1:
            raise ValueError("LIMIT 档位必须是正整数")
        if self.default_limit > self.cap_limit:
            raise ValueError("默认值不能大于上限")


@dataclass(frozen=True)
class GuardNote:
    """一次被护栏处理的留痕, 供日志与跑批报告记「补过 / 收紧过 / 拒过」."""

    rejected_reason: str | None
    own_limits: tuple[int, ...]
    applied_limit: int | None

    @property
    def had_own_limit(self) -> bool:
        return bool(self.own_limits)

    @property
    def limit_enforced(self) -> bool:
        """护栏有没有**动过** LIMIT 文本 (补了一个, 或把过宽的收紧了)."""
        return self.applied_limit is not None

    @property
    def tightened(self) -> bool:
        """原语句的 LIMIT 里有超过上限的 (被收到上限)."""
        if self.applied_limit is None or not self.own_limits:
            return False
        return max(self.own_limits) > self.applied_limit

    def describe(self) -> str:
        if self.rejected_reason is not None:
            return f"拒绝: {self.rejected_reason}"
        if not self.own_limits:
            return f"原语句无 LIMIT, 已补 LIMIT {self.applied_limit}"
        joined = ", ".join(str(value) for value in self.own_limits)
        if self.applied_limit is None:
            return f"原语句自带 LIMIT ({joined}), 未超上限, 原样放行"
        return f"原语句 LIMIT ({joined}) 超过上限, 已收紧到 {self.applied_limit}"


def _masked(sql: str) -> str:
    return _LITERAL_OR_COMMENT.sub(" ", sql)


def _scan(sql: str) -> str:
    """掩码后的扫描文本, **长度与原串一致** (命中的片段换成等长空格).

    长度对齐是为了让 `_LIMIT_CLAUSE` 的 span 能直接落到原串上做改写 —— `_masked`
    把命中换成单个空格, span 就偏了, 拿它去改原串会切错位置。
    """
    return _LITERAL_OR_COMMENT.sub(
        lambda match: " " * (match.end() - match.start()), sql
    )


def strip_trailing_semicolon(text: str) -> str:
    """去掉首尾空白与**单个**收尾分号.

    「扫描」与「重写」两条路都要这一段, 共用一份才不会漂 —— 早先它们是各写一遍的.
    """
    body = text.strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    return body


def _body(sql: str) -> str:
    """语句主体 (掩码后), 供扫描用."""
    return strip_trailing_semicolon(_masked(sql))


def readonly_violation(sql: str) -> str | None:
    """不是单条只读查询时返回「可操作的拒绝理由」, 通过则返回 None."""
    body = _body(sql)

    if ";" in body:
        return "只允许单条 SQL 语句 (检测到语句分隔符 `;`); 请只生成一条 SELECT 查询。"

    first_word = body.split(None, 1)[0].upper() if body.split() else ""
    if first_word not in ("SELECT", "WITH"):
        return (
            f"只允许 SELECT / WITH 开头的只读查询; "
            f"当前语句以 `{first_word or '空语句'}` 开头."
        )

    for keyword in FORBIDDEN_KEYWORDS:
        # 大小写不能成为后门: SQL 关键词不区分大小写, 扫描也不能区分
        if re.search(rf"\b{keyword}\b", body, re.IGNORECASE):
            return f"检测到写操作或管理类关键词 `{keyword}`; 只允许只读查询。"

    return None


def own_limits(sql: str) -> tuple[int, ...]:
    """原语句里所有 LIMIT 的行数 (按出现顺序; 注释/字面量里提到的不算).

    `LIMIT 10, 20` 取 20 (`10` 是偏移); `LIMIT 10 OFFSET 5` 取 10。
    """
    body = _body(sql)
    return tuple(
        int(match.group(2) or match.group(1)) for match in _LIMIT_CLAUSE.finditer(body)
    )


def own_limit_value(sql: str) -> int | None:
    """原语句顶层的 LIMIT 行数; 出现多个 (子查询里还有) 时返回 None."""
    limits = own_limits(sql)
    return limits[0] if len(limits) == 1 else None


def enforce_limit(
    sql: str, limits: SqlGuardLimits = SqlGuardLimits()
) -> tuple[str, int | None]:
    """把行数上限落到 SQL 文本上 —— **不包裹**. 返回 `(改写后的 SQL, 护栏施加的上限)`.

    上限那一位为 None 表示「原样放行」(作者自己已经界住了); 有值表示护栏动过文本
    (补了一个, 或把过宽的收紧了)。

    早先的实现是把整条查询套进 `SELECT * FROM (…) AS _guard LIMIT n`: 那会把
    合法 SQL 弄坏 —— 两表都有同名列时 (`SELECT f.date_id, d.date_id … JOIN …`)
    派生表会报 1060 `Duplicate column name` (评审实测), 于是「能跑的 SQL」变成
    「语法错」, 而外层包裹本来只是想加个上限。改成在文本层面动手:

    - 完全没有 LIMIT → 在**末尾另起一行**补一个默认档。另起一行是因为行尾的
      `-- 注释` 会把同一行的 LIMIT 一起注释掉 (`SELECT 1 -- 只要一行 LIMIT 200`
      等于没加)
    - 有 LIMIT 且都不超上限 → 原样返回 (不动它)
    - 有 LIMIT 且超过上限 → 把**最后一个** LIMIT 的行数改成上限。取最后一个, 是因为
      文本层面分不出哪个在顶层, 而正常写法里最后一个就是最外层; 即便不是,
      「把比上限还宽的界收到上限」也不会把结果截得比作者要的还小
    """
    body = strip_trailing_semicolon(sql)
    candidates = own_limits(body)

    if not candidates:
        return f"{body}\nLIMIT {limits.default_limit}", limits.default_limit

    if max(candidates) <= limits.cap_limit:
        return body, None

    return _cap_last_limit(body, limits.cap_limit), limits.cap_limit


def _cap_last_limit(sql: str, cap: int) -> str:
    """把最后一个 LIMIT 的行数改成 `cap` (只改数字, 其余原样)."""
    matches = list(_LIMIT_CLAUSE.finditer(_scan(sql)))
    last = matches[-1]
    # `LIMIT 10, 20` 的行数是第二组, `LIMIT 20` / `LIMIT 20 OFFSET 5` 是第一组
    group = 2 if last.group(2) is not None else 1
    return f"{sql[: last.start(group)]}{cap}{sql[last.end(group) :]}"
