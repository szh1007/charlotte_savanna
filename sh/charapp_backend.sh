#!/bin/bash
set -euo pipefail

# CharApp 客服服务进程 (CharApp/minimall/server.py).
# 本地端口: 1007 (前端复用 Django 8000)
#
# 前置: 运行 Django (python manage.py runserver),
#       根目录下 .env 里配好 CHARAPP_INTERNAL_TOKEN (与 Django 侧同值)
#       与模型 API Key; 监听地址/端口看 CHARAPP_SERVER_* (默认本机 1007).
#       数据库: 本机 Postgres (PGSQL_*, 与商城共用一个库). 快照后端已切 postgres,
#       历史因此跨进程累计 —— **换一台机器 / 换一个库要先建表**, 否则第一次写记录
#       或存快照时才会报错 (进程能起来, 报错落在第一个买家的第一句问话上):
#         cd CharAgent && alembic upgrade head      # 配置见 alembic.ini
# 用法: bash sh/charapp_backend.sh          # 前台跑, Ctrl-C 停
#       curl -N -X POST http://127.0.0.1:1007/runs \
#         -H "X-Internal-Token: <令牌>" -H "X-User-Id: 2" \
#         -H "content-type: application/json" -d '{"message":"我余额还有多少"}'
#
# 日志: logs/charapp_server_<YYYYMMDD>.log, **按天一个文件** (structured_logging
#       落地之后是一行一个 JSON, 带 thread_id / run_id / request_id).
#       同时 tee 到终端 —— 前台跑的时候眼睛也能看见, 不必另开一个窗口 tail.

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 用仓库的 venv 解释器: 裸 `python` 在没激活 venv 的终端里是系统那个, 它会以
# `ModuleNotFoundError: No module named 'httpx'` 收场 —— 看着像代码坏了, 其实是
# 解释器不对 (2026-10-03 实撞, 与 sh/charapp_demo.sh 同一处理)
if [ -x "$CHARLOTTE_ROOT/.venv/Scripts/python.exe" ]; then
    PY="$CHARLOTTE_ROOT/.venv/Scripts/python.exe"
elif command -v python >/dev/null 2>&1; then
    PY="$(command -v python)"
else
    printf '[charapp] ✗ 找不到 python: 先建虚拟环境 (python -m venv .venv) 并装依赖\n' >&2
    exit 1
fi

LOG_DIR="$CHARLOTTE_ROOT/logs"
# 当天日期做后缀 (%Y%m%d 与 eval 报告那份落盘命名同一套写法)
LOG_FILE="$LOG_DIR/charapp_server_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"

# 让子进程按 UTF-8 编码 (与 sh/charapp_demo.sh 同一条理由): Windows 上 Python 默认跟
# 系统码页 (GBK), 而日志内容是中文 —— 不设的话那一行行 JSON 落到文件里不是 UTF-8,
# 后面用 jq / 编辑器读出来是乱码
export PYTHONIOENCODING=utf-8

cd "$CHARLOTTE_ROOT"
printf '日志: %s\n' "$LOG_FILE" >&2
# tee **-a** 而不是 `>`: 按天的文件要**累积**当天的输出 —— 用 > 的话同一天第二次启动
# 会把上午那份整个冲掉, 而那一份正是「今天早上那次为什么失败」的唯一材料
"$PY" -m CharApp.minimall.server 2>&1 | tee -a "$LOG_FILE"
