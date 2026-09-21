#!/usr/bin/env bash
set -euo pipefail

# Windows Git Bash 终止进程
# taskkill //F //T //PID xxxx

# 查看项目根目录 (charlotte_savanna) 及其所有子项目下启动的进程
# 字段: PID / 进程名 / 监听端口 / 内存(MB) / 启动时间 / 启动命令 / 子进程ID
# 规则: 无端口子进程沿 ParentProcessId 祖先链归并到有端口的主进程, 只展示主进程一行
# 用法: bash sh/_status_.sh

CHARLOTTE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Git Bash 的 pwd 返回 MSYS 风格 (/d/...), 转成 Windows 路径用于进程匹配
CHARLOTTE_ROOT_WIN="$(cygpath -w "$CHARLOTTE_ROOT" 2>/dev/null || echo "$CHARLOTTE_ROOT")"
export CP_ROOT="$CHARLOTTE_ROOT_WIN"

# 1. 收集项目进程: 命令行包含项目根路径的 Win32 进程 (含父进程 ID)
#    - 路径统一小写 + 反斜杠后匹配, 兼容 bash 与 cmd 启动时的路径写法
#    - 排除 VSCode 扩展进程 (ms-python 等 IDE 语言服务, 不属于项目服务)
TMP_PS="$(mktemp --suffix=.ps1)"
trap 'rm -f "$TMP_PS"' EXIT

# 开头的 BOM 是**必须的**, 不是顺手加的: Windows PowerShell 5.1 读 .ps1 时没有 BOM
# 就按系统 ANSI 码页解码, 下面那些中文注释会被解成乱码 —— 而乱码里只要冒出一个
# 反引号 (PowerShell 的续行符), 紧随其后的那一行就被吞成注释的一部分.
# 2026-09-19 实撞: 一句中文注释吞掉了紧跟的变量赋值, 那个变量于是永远是空字符串,
# 表现为整张表 COMMAND 一栏全空、进程记录被拆行后脚本崩在空数组下标上.
{ printf '\xEF\xBB\xBF'; cat <<'EOF'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = $env:CP_ROOT.ToLower().Replace('/', '\')
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine.ToLower().Replace('/', '\').Contains($root) -and
    $_.CommandLine -notmatch '\.vscode\\extensions'
} | ForEach-Object {
    $mem = [math]::Round($_.WorkingSetSize / 1MB, 1)
    $started = $_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss')
    # 命令行里的换行必须压掉: bash -c 的多行脚本会让 CommandLine 自带换行,
    # 一条记录被拆成好几行后, 下面按行读时那几行没有制表符, 字段就全错位了
    $cmd = $_.CommandLine -replace '[\r\n]+', ' '
    # 制表符分隔: PID|PPID|NAME|STARTED|MEM|COMMAND
    "$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.Name)`t$started`t$mem`t$cmd"
}
EOF
} >"$TMP_PS"

PROCS="$(powershell -NoProfile -ExecutionPolicy Bypass -File "$TMP_PS")"
# 排除自身 (powershell 查询进程 / 本脚本)
PROCS="$(printf '%s\n' "$PROCS" | grep -viE 'powershell|_status_\.sh' || true)"

# 2. 收集监听端口: PID -> 端口列表 (netstat -ano 解析)
PORTMAP="$(netstat -ano 2>/dev/null | awk '
    /LISTENING/ {
        pid = $NF
        port = $2; gsub(/.*:/, "", port) # 兼容 [::]:3001 / 127.0.0.1:8000
        if (pid in map) map[pid] = map[pid] "," port
        else map[pid] = port
    }
    END { for (p in map) print p, map[p] }
')"

# 3. 解析进程行与端口, 建立 PPID 映射
if [ -z "$PROCS" ]; then
    echo "未发现项目进程 (命令行均不含 $CHARLOTTE_ROOT_WIN)"
    exit 0
fi

declare -A PROJ   # pid -> "ppid|name|started|mem|cmd"
declare -A PORTS  # pid -> 端口列表
while IFS=$'\t' read -r pid ppid name started mem cmd; do
    # 只认 PID 与 PPID 都是数字的记录. 这是一道兜底: 命令行被拆行 / PowerShell
    # 输出形状变了的时候, 会掉出字段错位的碎片行, 那种行没有 PPID —— 放进去会让
    # PROJ 里多出一个空父键, 而关联数组的空下标是硬错误 (bad array subscript),
    # 整个脚本会当场挂掉 (2026-09-19 实撞).
    [[ "$pid" =~ ^[0-9]+$ && "$ppid" =~ ^[0-9]+$ ]] || continue
    PROJ["$pid"]="$ppid|$name|$started|$mem|$cmd"
done <<<"$PROCS"
while read -r pid ports; do
    [ -z "$pid" ] && continue
    PORTS["$pid"]="$ports"
done <<<"$PORTMAP"

# 4. 归属分析: 以「有端口」进程为主进程, 无端口服务进程归并
#    - 进程树实际形态: bash -> python(无端口包装) -> python(有端口服务器),
#      无端口进程既可能是子也可能是父, 故先沿祖先链上溯、再沿后代链下溯双向查找
#    - bash.exe 视为启动器 (Claude shell / 用户终端), 不归并, 独立显示
declare -A CHILDREN # ppid -> 项目内子进程 pid 列表 (逗号拼接)
for pid in "${!PROJ[@]}"; do
    row="${PROJ[$pid]}"
    par="${row%%|*}"
    CHILDREN["$par"]="${CHILDREN[$par]:-}$pid,"
done

declare -A ROOT # pid -> 归属的主进程 pid
for pid in "${!PORTS[@]}"; do
    ROOT["$pid"]="$pid"
done

for pid in "${!PROJ[@]}"; do
    [[ -n "${PORTS[$pid]:-}" ]] && continue # 主进程已归自身
    row="${PROJ[$pid]}"
    name="${row#*|}"; name="${name%%|*}"
    [ "$name" = "bash.exe" ] && continue # bash 启动器独立显示

    # 1) 沿祖先链上溯, 找有端口的项目内祖先
    found=""
    cur="$pid"
    while :; do
        r="${PROJ[$cur]:-}"
        [ -z "$r" ] && break # 父不是项目进程, 停止
        par="${r%%|*}"
        if [[ -n "${PORTS[$par]:-}" ]]; then
            found="$par"
            break
        fi
        cur="$par"
    done

    # 2) 未找到则沿后代链下溯 (BFS), 找有端口的项目内后代
    if [ -z "$found" ]; then
        queue=("$pid")
        while [ ${#queue[@]} -gt 0 ]; do
            node="${queue[0]}"
            queue=("${queue[@]:1}")
            # `:-` 不能省: 叶子节点 (没有子进程的那种) 不在 CHILDREN 里, 而 set -u
            # 把「取一个不存在的键」当硬错误 —— BFS 一弹出叶子就崩在这行
            # (2026-09-22 实撞: CHILDREN[$node]: unbound variable).
            # 逗号换空格要留在**取值之后**做: 默认值那一段不能接 // 替换
            children="${CHILDREN[$node]:-}"
            for c in ${children//,/ }; do
                [ -z "$c" ] && continue
                if [[ -n "${PORTS[$c]:-}" ]]; then
                    found="$c"
                    break 2
                fi
                queue+=("$c")
            done
        done
    fi

    [ -n "$found" ] && ROOT["$pid"]="$found"
done

# 收集子进程 ID 列表 (逗号 + 空格拼接, 如 "5688, 24552")
declare -A CHILDLIST
for pid in "${!ROOT[@]}"; do
    r="${ROOT[$pid]}"
    [ "$pid" = "$r" ] && continue
    CHILDLIST["$r"]="${CHILDLIST[$r]:-}$pid, "
done

# 5. 输出记录块 (字段各占一行, 块间分隔线, 按 PID 排序)
SEP="-------------------------------------------------------"

# 待显示行: "pid|childs", 仅主进程与无归属的独立进程
ROWS=()
for pid in "${!PROJ[@]}"; do
    [ -n "${ROOT[$pid]:-}" ] && [ "${ROOT[$pid]}" != "$pid" ] && continue # 子进程不显示
    ROWS+=("$pid|${CHILDLIST[$pid]:-}")
done

printf '%s\n' "${ROWS[@]}" | sort -n -t'|' -k1,1 | while IFS='|' read -r pid childs; do
    [ -z "$pid" ] && continue # 空列表时 printf 无参数输出空行, 跳过
    row="${PROJ[$pid]}"
    ppid="${row%%|*}"
    rest="${row#*|}"
    name="${rest%%|*}"; rest="${rest#*|}"
    started="${rest%%|*}"; rest="${rest#*|}"
    mem="${rest%%|*}"; cmd="${rest#*|}"
    port="${PORTS[$pid]:--}"
    [ -z "$childs" ] && childs="null" || childs="${childs%, }" # 去末尾 ", "
    printf 'PID: %s\nCHILD PIDS: %s\nPORT: %s\nMEM(MB): %s\nNAME: %s\nSTARTED: %s\nCOMMAND: %s\n%s\n' \
        "$pid" "$childs" "$port" "$mem" "$name" "$started" "$cmd" "$SEP"
done
