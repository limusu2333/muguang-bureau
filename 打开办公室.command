#!/usr/bin/env bash
# 船主专用：双击这个文件 = 启动办公室 + 自动打开浏览器。
# 关闭办公室：关掉弹出的这个终端窗口即可。
cd "$(dirname "$0")"

# python3 检查
if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3。装一下命令行工具后重试: xcode-select --install"
  read -r -p "回车退出"; exit 1
fi

# 双击这个文件就是重启先生本人的唯一开发办公室。
OFFICE_ROOT="$(pwd -P)"
PYTHON="$OFFICE_ROOT/.venv/bin/python3"
PORT="${XJ_OFFICE_PORT:-8787}"
SUPERVISOR_LOG="$HOME/Library/Application Support/XJ Multiuser/logs/supervisor-start.log"

if [ ! -x "$PYTHON" ]; then
  echo "找不到开发办公室的 Python 环境：$PYTHON"
  read -r -p "回车退出"; exit 1
fi

# 本人办公室只确保本人专用的开发管理后台（37660）在线。
# 正式用户后台由发布系统独立托管；双击办公室不得安装、重启或等待它，
# 更不能因为正式后台维护而卡住本人的 8787 开发办公室。
启动本人开发管理() {
  mkdir -p "$(dirname "$SUPERVISOR_LOG")"
  echo "正在从唯一开发区加载本人管理后台..."
  if "$PYTHON" -m 多用户.部署.监督服务命令 \
      install-development-admin --source "$OFFICE_ROOT" >>"$SUPERVISOR_LOG" 2>&1; then
    echo "✅ 本人开发管理后台已加载当前开发代码。"
    return 0
  fi
  echo "⚠️ 本人开发管理后台启动失败，版本发布页暂时无法打开。"
  echo "请查看日志：$SUPERVISOR_LOG"
  return 1
}

启动本人开发管理 || true

# 主开发办公室与正式用户共用同一份 Mac 宿主告警模型。
# 令牌只从钥匙串临时注入当前进程；读不到时仍强制规则兜底，
# 绝不让开发办公室自己再加载一份 MLX 模型。
export XJ_ALERT_RENDER_REQUIRED=1
if ALERT_TOKEN="$(/usr/bin/security find-generic-password -s xj-multiuser-platform -a rerank-token -w 2>/dev/null)" \
  && [ "${#ALERT_TOKEN}" -ge 32 ]; then
  export XJ_ALERT_RENDER_URL="http://127.0.0.1:37657"
  export XJ_ALERT_RENDER_TOKEN="$ALERT_TOKEN"
fi

监听进程() {
  lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
}

页面可用() {
  /usr/bin/curl -fsS --max-time 0.8 "http://127.0.0.1:${PORT}/" >/dev/null 2>&1
}

是本开发办公室() {
  local pid="$1" command
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  # 办公室启动后会把工作目录切到 20_工程_XJ 用于用户产物，
  # 所以不能再用 cwd 判断代码来源。启动命令保留绝对脚本路径，
  # 这样即使 cwd 变化，也只会识别当前唯一开发区的进程。
  [[ "$command" == *"$OFFICE_ROOT/工具/机房.py"* ]]
}

# 当前开发办公室若由 macOS 常驻服务托管，必须让托管服务执行重启。
# 直接杀进程会被它立刻拉起，脚本就会一直等不到端口空闲。
LAUNCH_TARGET="gui/$(id -u)/com.munaigongsi.office.codex"
if launchctl print "$LAUNCH_TARGET" >/dev/null 2>&1; then
  OLD_PIDS="$(监听进程)"
  echo "正在重启开发办公室..."
  if ! launchctl kickstart -k "$LAUNCH_TARGET"; then
    echo "❌ macOS 没能重启开发办公室，请稍后再试。"
    read -r -p "回车退出"
    exit 1
  fi

  attempts=0
  while [ "$attempts" -lt 120 ]; do
    NEW_PID="$(监听进程 | head -1)"
    if [ -n "$NEW_PID" ]; then
      case " $OLD_PIDS " in
        *" $NEW_PID "*) ;;
        *)
          if 是本开发办公室 "$NEW_PID" && 页面可用; then
            echo "✅ 开发办公室已重启（新进程 $NEW_PID）"
            open "http://localhost:${PORT}/?boot=$(date +%s)"
            exit 0
          fi
          ;;
      esac
    fi
    attempts=$((attempts + 1))
    sleep 0.25
  done
  echo "❌ 开发办公室重启超过 30 秒，请看运行日志。"
  read -r -p "回车退出"
  exit 1
fi

# 先确认端口上的所有监听者。只有确认全部都是本开发办公室，才发送优雅关门信号；
# 其他程序绝不替它杀掉，也不先动办公室再发现无法启动。
OLD_PIDS="$(监听进程)"
if [ -n "$OLD_PIDS" ]; then
  OFFICE_PIDS=""
  UNKNOWN_PIDS=""
  for pid in $OLD_PIDS; do
    if 是本开发办公室 "$pid"; then
      OFFICE_PIDS="$OFFICE_PIDS $pid"
    else
      UNKNOWN_PIDS="$UNKNOWN_PIDS $pid"
    fi
  done
  if [ -n "$UNKNOWN_PIDS" ]; then
    echo "❌ 端口 $PORT 被其他程序占用（PID:$UNKNOWN_PIDS）。"
    echo "为避免误关别的程序，本次不启动办公室。"
    read -r -p "回车退出"
    exit 1
  fi
  for pid in $OFFICE_PIDS; do
    echo "请旧办公室进程 $pid 关门并收尾..."
    kill "$pid" 2>/dev/null || true
  done
  waited=0
  while [ -n "$(监听进程)" ] && [ "$waited" -lt 30 ]; do
    sleep 1
    waited=$((waited + 1))
  done
  if [ -n "$(监听进程)" ]; then
    echo "❌ 旧办公室 30 秒内没有释放端口 $PORT，本次不强开新进程。"
    echo "请稍后再双击，或回办公室确认任务已收尾。"
    read -r -p "回车退出"
    exit 1
  fi
fi

# .env 没配key就先用演示模式(界面全功能，只是岗位回复是占位)
MODE=""
if [ ! -f .env ] || ! grep -qE '^[A-Z_]+_API_KEY=.+' .env 2>/dev/null; then
  export XJ_MOCK=1
  MODE="（演示模式：还没填API key，岗位回复是占位。填好 .env 后重开即是真模式）"
fi

echo "════ 办公室启动中 $MODE ════"
# 不再猜 1.2 秒。等首页真的能返回再开浏览器，避免先撞到空端口、白页或旧缓存。
(
  attempts=0
  while [ "$attempts" -lt 120 ]; do
    if 页面可用; then
      open "http://localhost:${PORT}/?boot=$(date +%s)"
      exit 0
    fi
    attempts=$((attempts + 1))
    sleep 0.1
  done
  echo "办公室启动超过 12 秒，浏览器暂未自动打开；请看终端里的具体错误。"
) &
OPENER_PID=$!
cleanup() {
  kill "$OPENER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
# 后端输出同时打到终端 + 存一份带时间戳的日志(排查"会话流连接中断"等用;-u=实时不缓冲)
mkdir -p 运行状态/as_log
LOG="运行状态/as_log/backend.$(date +%Y%m%d_%H%M%S).log"
echo "本次后端日志：$LOG"
"$PYTHON" -u "$OFFICE_ROOT/工具/机房.py" 2>&1 | tee "$LOG"
