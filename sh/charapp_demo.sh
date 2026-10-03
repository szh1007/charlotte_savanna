#!/usr/bin/env bash
set -euo pipefail

# CharApp 演示装置 (C04): 一条命令把「本机当场演示」要活着的东西全部拉起来.
#
# 顺序: 前置检查 (MySQL / Redis / Postgres) → 建表检查 (alembic upgrade head)
#     → 起两个服务 (Django 8000 / 客服服务 1007, 后台 + 日志落 logs/)
#     → 健康检查 → 演示数据准备 (幂等) → 打开客服页 → 打印状态与冷启动秒数.
#
# 已经活着的服务**复用不重启** (跑第二遍不会撞端口), 是不是本轮起的写在输出里;
# 要停: bash sh/_status_.sh 看 PID, 再 taskkill //F //T //PID <pid>.
#
# 用法:
#   bash sh/charapp_demo.sh              # 起全套 + 打开客服页
#   bash sh/charapp_demo.sh --no-open    # 不开浏览器 (只想把服务拉起来)
#
# 演示买家默认 #10 (savanna), 用 CHARAPP_DEMO_USER_ID 覆盖; 剧本与兜底见 sh/charapp_demo.md.

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CHARLOTTE_ROOT"

OPEN_BROWSER=1
# 写成 if 而不是 `[ ... ] && ...`: 测试为假时整条语句的返回值是 1, 在 set -e 下
# 会被当成失败直接把脚本打断 (不带参数的调用恰好走这一条)
if [ "${1:-}" = "--no-open" ]; then
    OPEN_BROWSER=0
fi

DEMO_USER_ID="${CHARAPP_DEMO_USER_ID:-10}"
DJANGO_HOST="127.0.0.1"
DJANGO_PORT=8000
HEALTH_TIMEOUT=60                      # 单个服务最多等这么久 (实测冷启动见 sh/charapp_demo.md)
LOG_DIR="$CHARLOTTE_ROOT/logs"
DJANGO_LOG="$LOG_DIR/charapp_demo_django.log"
SERVER_LOG="$LOG_DIR/charapp_demo_server.log"

START_TS="$(date +%s)"

# ---------------------------------------------------------------------------
# 输出: 全部走 stderr —— 这个脚本的 stdout 留给「结构化数据」, 不给提示语,
# 于是任何一处 $() 抓到的都不会混进装饰性文字
# ---------------------------------------------------------------------------
step() { printf '\n[装置] %s\n' "$1" >&2; }
info() { printf '       %s\n' "$1" >&2; }

# ---------------------------------------------------------------------------
# 解释器与 .env
# ---------------------------------------------------------------------------

# 用仓库的 venv 解释器: 「忘了激活 venv」是这里最常踩的坑, 而它的表现是
# 一堆 ModuleNotFoundError, 看着像代码坏了
if [ -x "$CHARLOTTE_ROOT/.venv/Scripts/python.exe" ]; then
    PY="$CHARLOTTE_ROOT/.venv/Scripts/python.exe"
elif command -v python >/dev/null 2>&1; then
    PY="$(command -v python)"
else
    printf '[装置] ✗ 找不到 python: 先建虚拟环境 (python -m venv .venv) 并装依赖\n' >&2
    exit 1
fi

# 让子进程的标准输出按 UTF-8 编码: Windows 上 Python 默认跟系统码页 (GBK), 而
# Git Bash 与编辑器终端按 UTF-8 解码 —— 少这一条, 命令里打的中文在终端上是乱码
# (2026-10-03 实撞: 日志本身是对的, 只是显示成 "��� savanna (10)")
export PYTHONIOENCODING=utf-8

# 读根 .env 里的一个值 (去引号与 CR); 没配就返回空 —— 调用方给默认值
env_value() {
    local key="$1"
    [ -f "$CHARLOTTE_ROOT/.env" ] || return 0
    grep -E "^${key}=" "$CHARLOTTE_ROOT/.env" | tail -1 | cut -d= -f2- \
        | tr -d '"' | tr -d "'" | tr -d '\r'
}

# 服务监听在哪是 .env 说了算 (客服服务自己读的就是这两个值), 这里跟着读一遍
CHARAPP_HOST="$(env_value CHARAPP_SERVER_HOST)"
CHARAPP_HOST="${CHARAPP_HOST:-127.0.0.1}"
CHARAPP_PORT="$(env_value CHARAPP_SERVER_PORT)"
CHARAPP_PORT="${CHARAPP_PORT:-1007}"

# 监听 0.0.0.0 时探测要走回环地址 (0.0.0.0 是「绑哪儿」的写法, 不是「连哪儿」的)
probe_host() {
    if [ "$1" = "0.0.0.0" ] || [ -z "$1" ]; then
        printf '127.0.0.1'
    else
        printf '%s' "$1"
    fi
}

# ---------------------------------------------------------------------------
# 1. 前置检查: 三个依赖 (Django 要 MySQL + Redis, 客服服务要 Postgres)
# ---------------------------------------------------------------------------

# 探针跑在 python 里而不是 bash 里: 端口通不等于能用 —— 口令不对 / 库不存在都要在
# 开演前就知道. 每个依赖一行 "<名字>\t<OK|FAIL>\t<说明>", 由上面的 while 读走.
check_dependencies() {
    "$PY" - <<'PY'
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env")

# 这几行会打在终端上, 也可能被贴进 issue —— 连接串里的口令一律打掉再出去
_SECRET = re.compile(r"://[^:/@\s]+:[^@\s]+@")


def report(name: str, ok: bool, detail: str) -> None:
    print(f"{name}\t{'OK' if ok else 'FAIL'}\t{_SECRET.sub('://***:***@', detail)}")


def check_mysql() -> None:
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    port = int(os.environ.get("MYSQL_PORT") or 3306)
    where = f"{host}:{port}"
    try:
        import pymysql

        pymysql.connect(
            host=host,
            port=port,
            user=os.environ.get("MYSQL_USERNAME", "root"),
            password=os.environ.get("MYSQL_PASSWORD", ""),
            database=os.environ.get("MYSQL_NAME", ""),
            connect_timeout=3,
        ).close()
    except Exception as exc:  # 探针只负责报出来, 不负责处理
        report("MySQL", False, f"{where} {type(exc).__name__}: {exc}")
    else:
        report("MySQL", True, where)


def check_redis() -> None:
    url = os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"
    where = _SECRET.sub("://***:***@", url)
    try:
        import redis

        redis.Redis.from_url(url, socket_connect_timeout=3).ping()
    except Exception as exc:
        report("Redis", False, f"{where} {type(exc).__name__}: {exc}")
    else:
        report("Redis", True, where)


def check_postgres() -> None:
    try:
        from sqlalchemy import create_engine, text

        from CharAgent.db.config import sqlalchemy_url

        url = sqlalchemy_url()
        engine = create_engine(url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:
        report("Postgres", False, f"{type(exc).__name__}: {exc}")
    else:
        report("Postgres", True, _SECRET.sub("://***:***@", str(url)))


check_mysql()
check_redis()
check_postgres()
PY
}

# 每个依赖没起来时该做什么 —— 这张表就是这个脚本「不是堆栈, 是能照着做的话」的兑现
dep_hint() {
    case "$1" in
        MySQL)
            echo "启动本机 MySQL 服务 (Windows 服务里找 mysql)," \
                "或用容器跑的话: docker start mysql"
            echo "服务其实起着的话, 就是连接参数的问题: 核 .env 里的 MYSQL_* 那几行"
            ;;
        Redis)
            echo "docker start redis (本机 redis 跑在容器里)"
            echo "服务其实起着的话, 核 .env 里的 REDIS_URL"
            ;;
        Postgres)
            echo "启动本机 PostgreSQL 服务 (Windows 服务里找 postgresql-x64-*)," \
                "或用容器跑的话: docker start <容器名>"
            echo "服务其实起着的话, 核 .env 里的 PGSQL_* (或 CHARAGENT_DB_DSN) 与库名"
            ;;
    esac
}

step "1/6 前置检查 (MySQL / Redis / Postgres)"
DEP_OUTPUT="$(check_dependencies)"
DEP_FAILED=()
while IFS=$'\t' read -r dep_name dep_status dep_detail; do
    [ -z "${dep_name:-}" ] && continue
    if [ "$dep_status" = "OK" ]; then
        info "✓ $dep_name  $dep_detail"
    else
        info "✗ $dep_name  $dep_detail"
        DEP_FAILED+=("$dep_name")
    fi
done <<<"$DEP_OUTPUT"

if [ ${#DEP_FAILED[@]} -gt 0 ]; then
    printf '\n[装置] ✗ 依赖没起全, 服务起不来 (先别急着重跑, 按下面做):\n' >&2
    for dep in "${DEP_FAILED[@]}"; do
        printf '       · %s\n' "$dep" >&2
        while IFS= read -r hint_line; do
            printf '         %s\n' "$hint_line" >&2
        done < <(dep_hint "$dep")
    done
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. 建表检查: 换机器 / 换库之后, 第一次写记录或存快照时才会报错 —— 提前跑掉
#    (幂等: 已经在 head 上再跑一次什么都不做)
# ---------------------------------------------------------------------------

step "2/6 建表检查 (CharAgent 迁移)"
if MIGRATE_OUTPUT="$("$PY" -m alembic -c CharAgent/alembic.ini upgrade head 2>&1)"; then
    info "✓ 表结构已在 head (alembic upgrade head 无待跑迁移)"
else
    printf '%s\n' "$MIGRATE_OUTPUT" >&2
    printf '\n[装置] ✗ 迁移没跑成. 常见原因: Postgres 里的库还没建 (PGSQL_* 指的那个),\n' >&2
    printf '         或 .env 里的 PGSQL_* 与 alembic 读的不是同一套\n' >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. 起服务
# ---------------------------------------------------------------------------

port_listening() {
    (exec 3<>"/dev/tcp/$1/$2") 2>/dev/null
}

# 端口已在听就复用 (跑第二遍不撞端口), 否则后台起一个并把 PID 打到 stdout
start_service() {
    local name="$1" host="$2" port="$3" logfile="$4"
    shift 4
    if port_listening "$(probe_host "$host")" "$port"; then
        info "• $name: 端口 $port 已在听, 复用 (不是本轮起的)"
        return 0
    fi
    : >"$logfile"   # 每轮从空日志开始: 失败时 tail 出来的才是这一轮的
    nohup "$@" </dev/null >>"$logfile" 2>&1 &
    printf '%s' "$!"
}

# 起不来时摊出来的三行: 谁、为什么、去哪儿看
service_failed() {
    local name="$1" logfile="$2" why="$3" hint="$4"
    printf '\n[装置] ✗ %s: %s\n' "$name" "$why" >&2
    printf -- '---- %s (最后 20 行) ----\n' "$logfile" >&2
    tail -n 20 "$logfile" >&2 || true
    printf -- '------------------------------\n' >&2
    printf '       · %s\n' "$hint" >&2
}

# 等到端点上真有人应答. 2xx/3xx/4xx 都算活; 5xx 继续等 —— 进程活着但应用坏了
# 正是最该报出来的那种失败, 不能和「起来了」混为一谈
wait_http() {
    local name="$1" url="$2" pid="$3" logfile="$4" timeout="$5" hint="$6"
    local deadline=$((SECONDS + timeout)) code=""
    while ((SECONDS < deadline)); do
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            service_failed "$name" "$logfile" "进程已退出 (起不来)" "$hint"
            return 1
        fi
        code="$(curl -s -o /dev/null -m 2 -w '%{http_code}' "$url" 2>/dev/null || true)"
        case "$code" in
            2* | 3* | 4*)
                info "✓ $name 就绪 ($url → $code)"
                return 0
                ;;
        esac
        sleep 1
    done
    service_failed "$name" "$logfile" \
        "等了 ${timeout}s 还没起来 (最后响应码: ${code:-无响应})" "$hint"
    return 1
}

step "3/6 起服务 (Django $DJANGO_PORT / 客服服务 $CHARAPP_PORT)"
mkdir -p "$LOG_DIR"

DJANGO_PID="$(start_service "Django 业务端" "$DJANGO_HOST" "$DJANGO_PORT" "$DJANGO_LOG" \
    "$PY" manage.py runserver "$DJANGO_HOST:$DJANGO_PORT" --noreload)"
# --noreload: 演示装置要的是「起一次就定住」—— 自动重载会在演示中途因为一个
# 文件改动重启进程, 而且 _status_.sh 里会多出一个包装进程

SERVER_PID="$(start_service "客服服务" "$CHARAPP_HOST" "$CHARAPP_PORT" "$SERVER_LOG" \
    "$PY" -m CharApp.minimall.server)"

step "4/6 健康检查"
wait_http "Django 业务端" "http://$(probe_host "$DJANGO_HOST"):$DJANGO_PORT/minimall/agent/" \
    "$DJANGO_PID" "$DJANGO_LOG" "$HEALTH_TIMEOUT" \
    "表结构没建过就先 python manage.py migrate; 其余看日志: $DJANGO_LOG"
wait_http "客服服务" "http://$(probe_host "$CHARAPP_HOST"):$CHARAPP_PORT/openapi.json" \
    "$SERVER_PID" "$SERVER_LOG" "$HEALTH_TIMEOUT" \
    "CHARAPP_INTERNAL_TOKEN / 模型 API Key 没配时, 它会打印「启动失败: ...」后自己退出"

READY_TS="$(date +%s)"

# ---------------------------------------------------------------------------
# 5. 演示数据准备 (幂等: 每次开演前都跑, 上一轮付掉的单在这儿补回来)
# ---------------------------------------------------------------------------

step "5/6 演示数据准备 (幂等)"
# 它的输出也走 stderr: 这个脚本的 stdout 只留给结构化数据 (上面那条约定)
"$PY" manage.py demo_prepare --user-id "$DEMO_USER_ID" >&2

# ---------------------------------------------------------------------------
# 6. 打开客服页 + 打印要看的东西
# ---------------------------------------------------------------------------

PAGE_URL="http://$DJANGO_HOST:$DJANGO_PORT/minimall/agent/"

step "6/6 打开客服页"
if [ "$OPEN_BROWSER" = "1" ]; then
    # cmd //c start: Git Bash 里开默认浏览器的那一跳 (双斜杠是防路径转换)
    if cmd.exe //c start "" "$PAGE_URL" >/dev/null 2>&1; then
        info "已交给默认浏览器: $PAGE_URL"
    else
        info "浏览器没打开, 手动访问: $PAGE_URL"
    fi
else
    info "--no-open: 浏览器没开, 地址是 $PAGE_URL"
fi

END_TS="$(date +%s)"
# 「冷启动」只在真起了服务时才算数 —— 两个端口都在听的那一轮 (几秒), 标成冷启动
# 是自欺: 那个数是复用的代价, 不是从零到能提问的代价
if [ -n "$DJANGO_PID" ] || [ -n "$SERVER_PID" ]; then
    printf '\n[装置] 就绪 —— 冷启动 %ss (其中服务就绪占 %ss)\n' \
        "$((END_TS - START_TS))" "$((READY_TS - START_TS))" >&2
else
    printf '\n[装置] 就绪 —— %ss (两个服务本来就在跑, 复用了)\n' \
        "$((END_TS - START_TS))" >&2
fi
printf '       演示买家: #%s (用户名看上面 demo_prepare 那一行); 跳到登录页就用它登录\n' \
    "$DEMO_USER_ID" >&2
printf '       剧本与兜底动作: sh/charapp_demo.md\n' >&2
printf '       看进程: bash sh/_status_.sh    停进程: taskkill //F //T //PID <pid>\n' >&2
