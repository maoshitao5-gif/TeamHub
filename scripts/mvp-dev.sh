#!/usr/bin/env bash
# macOS 本机 MVP 基线。沙箱不恢复历史 bootstrap 配置、不读取 .env。
# local / cloud 独立启动；all 联调；start-desktop 仅启动桌面；stop / status。
# 不终止端口上的未知进程。
set -euo pipefail
export LC_ALL=C
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SANDBOX="$ROOT/.mvp-sandbox"
PID_DIR="$SANDBOX/pids"
LOCK_DIR="$SANDBOX/launcher.lock"
LOCAL_PORT=8001
CLOUD_PORT=9000
FRONTEND_PORT=5173
STARTING=0
STARTED=()

process_start() { ps -p "$1" -o lstart= 2>/dev/null | sed 's/^ *//;s/ *$//' || true; }
process_dead() {
  local state
  state="$(ps -p "$1" -o stat= 2>/dev/null || true)"
  [[ -z "$state" || "$state" == *Z* ]]
}
port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# PID、启动时间和准确命令同时匹配才拥有停止权限。
is_ours() {
  local name="$1" pid stamp port command app_dir expected_port
  [[ -f "$PID_DIR/$name.pid" ]] || return 1
  IFS='|' read -r pid stamp port < "$PID_DIR/$name.pid" || return 1
  [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 1 && -n "$stamp" ]] || return 1
  process_dead "$pid" && return 1
  [[ "$(process_start "$pid")" == "$stamp" ]] || return 1
  case "$name" in
    local-backend) app_dir="$ROOT"; expected_port="$LOCAL_PORT" ;;
    cloud-backend) app_dir="$ROOT/cloud_backend"; expected_port="$CLOUD_PORT" ;;
    frontend) expected_port="$FRONTEND_PORT" ;;
    *) return 1 ;;
  esac
  [[ "$port" == "$expected_port" ]] || return 1
  command="$(LC_ALL=en_US.UTF-8 ps -p "$pid" -o command= 2>/dev/null || true)"
  if [[ "$name" == frontend ]]; then
    [[ "$command" == *" $ROOT/frontend/node_modules/vite/bin/vite.js --host 127.0.0.1 --port $port --strictPort" ]]
    return
  fi
  [[ "$command" == *" -m uvicorn main:app --app-dir $app_dir --host 127.0.0.1 --port $port" ]]
}

restore_owned_ports() {
  local pid stamp candidate
  if [[ -f "$PID_DIR/local-backend.pid" ]]; then
    IFS='|' read -r pid stamp candidate < "$PID_DIR/local-backend.pid" || true
    if [[ "$candidate" =~ ^80(0[1-9]|10)$ ]]; then
      LOCAL_PORT="$candidate"
      is_ours local-backend || LOCAL_PORT=8001
    fi
  fi
}

choose_local_port() {
  local candidate
  if is_ours local-backend; then return 0; fi
  for candidate in {8001..8010}; do
    if ! port_in_use "$candidate"; then LOCAL_PORT="$candidate"; return 0; fi
  done
  echo "错误：8001—8010 均被占用；不会关闭现有服务" >&2
  return 1
}

record_pid() {
  local stamp
  stamp="$(process_start "$2")"
  [[ -n "$stamp" ]] || return 1
  printf '%s|%s|%s\n' "$2" "$stamp" "$3" > "$PID_DIR/$1.pid"
}

stop_one() {
  local name="$1" pid stamp port i
  [[ -f "$PID_DIR/$name.pid" ]] || return 0
  IFS='|' read -r pid stamp port < "$PID_DIR/$name.pid" || true
  if ! is_ours "$name"; then
    echo "跳过 $name：记录已失效或归属不匹配，不发信号" >&2
    rm -f "$PID_DIR/$name.pid"
    return 0
  fi
  kill "$pid" 2>/dev/null || true
  for ((i=0; i<25; i++)); do process_dead "$pid" && break; sleep 0.2; done
  if ! process_dead "$pid"; then
    if is_ours "$name"; then kill -KILL "$pid" 2>/dev/null || true; fi
    for ((i=0; i<10; i++)); do process_dead "$pid" && break; sleep 0.1; done
  fi
  if ! process_dead "$pid"; then
    echo "错误：$name 未退出，保留进程记录供重试" >&2
    return 1
  fi
  rm -f "$PID_DIR/$name.pid"
  echo "已停止 $name"
}

finish() {
  local result=$? name
  trap - EXIT
  if [[ "$STARTING" == 1 ]]; then
    for name in "${STARTED[@]}"; do stop_one "$name" || true; done
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
  exit "$result"
}

wait_healthy() {
  local name="$1" port="$2" pid i health_path=/api/health
  [[ "$name" != frontend ]] || health_path=/
  is_ours "$name" || return 1
  IFS='|' read -r pid _ _ < "$PID_DIR/$name.pid"
  for ((i=0; i<30; i++)); do
    process_dead "$pid" && return 1
    if is_ours "$name" &&
      lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | grep -qx "$pid" &&
      curl --noproxy '*' -fsS -m 1 "http://127.0.0.1:$port$health_path" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done
  return 1
}

start_local() {
  local personal="${1:-a}" personal_root local_pid
  case "$personal" in a|b|c) ;; *) echo "错误：个人沙箱只允许 a、b、c" >&2; return 2 ;; esac
  restore_owned_ports
  if is_ours local-backend; then wait_healthy local-backend "$LOCAL_PORT"; return; fi
  [[ -x "$ROOT/venv/bin/python" ]] || { echo "缺少个人后端 Python 环境" >&2; return 1; }
  choose_local_port || return 1
  personal_root="$SANDBOX/personal-$personal"
  mkdir -p "$personal_root/library" "$PID_DIR"
  STARTING=1
  (cd "$personal_root" && exec nohup env -i PATH="$PATH" HOME="$HOME" LANG="${LANG:-en_US.UTF-8}" \
    TEAMHUB_SANDBOX=1 LIBRARY_PATH="$personal_root/library" TRASH_AUTO_CLEAN_DAYS=0 \
    "$ROOT/venv/bin/python" -m uvicorn main:app --app-dir "$ROOT" --host 127.0.0.1 --port "$LOCAL_PORT" \
    >> logs-local-backend.log 2>&1 < /dev/null) &
  local_pid=$!
  STARTED+=(local-backend)
  record_pid local-backend "$local_pid" "$LOCAL_PORT" || return 1
  wait_healthy local-backend "$LOCAL_PORT" || { echo "个人后端启动失败：$personal_root/logs-local-backend.log" >&2; return 1; }
  STARTING=0
  if [[ "${2:-}" != quiet ]]; then echo "个人后端已就绪：http://127.0.0.1:$LOCAL_PORT（不依赖云端）"; fi
}

start_cloud() {
  local cloud_pid cloud_signing_key
  if is_ours cloud-backend; then wait_healthy cloud-backend "$CLOUD_PORT"; return; fi
  [[ -x "$ROOT/cloud_backend/venv/bin/python" ]] || { echo "缺少云端 Python 环境" >&2; return 1; }
  if port_in_use "$CLOUD_PORT"; then echo "错误：云端端口 $CLOUD_PORT 已占用，不终止占用者" >&2; return 1; fi
  mkdir -p "$SANDBOX/shared-server" "$PID_DIR"
  cloud_signing_key="$("$ROOT/cloud_backend/venv/bin/python" "$ROOT/scripts/mvp-cloud-secret.py" "$SANDBOX/shared-server/.jwt-secret")" || return 1
  STARTING=1
  (cd "$SANDBOX/shared-server" && exec nohup env -i PATH="$PATH" HOME="$HOME" LANG="${LANG:-en_US.UTF-8}" \
    TEAMHUB_SANDBOX=1 DATABASE_URL="sqlite:///$SANDBOX/shared-server/cloud.db" \
    STORAGE_DIR="$SANDBOX/shared-server/storage" JWT_SECRET_KEY="$cloud_signing_key" \
    "$ROOT/cloud_backend/venv/bin/python" -m uvicorn main:app --app-dir "$ROOT/cloud_backend" --host 127.0.0.1 --port "$CLOUD_PORT" \
    >> logs-cloud-backend.log 2>&1 < /dev/null) &
  cloud_pid=$!
  STARTED+=(cloud-backend)
  record_pid cloud-backend "$cloud_pid" "$CLOUD_PORT" || return 1
  if ! wait_healthy cloud-backend "$CLOUD_PORT"; then
    echo "云端启动失败：$SANDBOX/shared-server/logs-cloud-backend.log" >&2
    echo "若日志提示 schema 需要升级，请先停写并备份数据库和 storage，在副本验证后按 docs/product/云端与资料投递实施蓝图.md §10.1 显式执行 Alembic。启动器不会自动迁移既有库。" >&2
    return 1
  fi
  STARTING=0
  if [[ "${1:-}" != quiet ]]; then echo "云端已就绪：http://127.0.0.1:$CLOUD_PORT/cloud.html（不依赖个人后端或 Electron）"; fi
  if [[ ! -f "$ROOT/frontend/dist/cloud.html" ]]; then echo "网页尚未构建：执行 npm run frontend:build 后重启云服务。"; fi
}

start() {
  local personal="${1:-a}"
  case "$personal" in a|b|c) ;; *) echo "错误：个人沙箱只允许 a、b、c" >&2; return 2 ;; esac
  if is_ours local-backend || is_ours cloud-backend || port_in_use "$LOCAL_PORT" || port_in_use "$CLOUD_PORT"; then
    echo "错误：服务或端口仍在使用，请先 stop；联调复用请使用 all" >&2; return 1
  fi
  start_local "$personal" quiet || return $?
  STARTING=1
  start_cloud quiet || return $?
  STARTING=0
  echo "已就绪：个人后端 http://127.0.0.1:$LOCAL_PORT，云端 http://127.0.0.1:$CLOUD_PORT"
}

# 一键入口：复用已验证归属且健康的服务；不接管未知端口。
start_all() {
  local frontend_pid
  restore_owned_ports
  choose_local_port || return 1
  command -v node >/dev/null || { echo "缺少 Node.js，请按启动指引安装依赖" >&2; return 1; }
  [[ -f "$ROOT/frontend/node_modules/vite/bin/vite.js" && -x "$ROOT/venv/bin/python" && -x "$ROOT/cloud_backend/venv/bin/python" ]] || {
    echo "依赖不完整，请按 docs/guides/MVP本机试用启动指引.md 准备环境" >&2; return 1;
  }
  if port_in_use "$FRONTEND_PORT" && ! is_ours frontend; then
    echo "错误：前端端口 $FRONTEND_PORT 被其他程序占用，请先关闭原前端；不终止占用者" >&2; return 1
  fi
  start_local "${1:-a}" quiet || return $?
  STARTING=1
  start_cloud quiet || return $?
  # start 已完成；若前端失败，只回收本次新建的服务。
  STARTING=1
  if ! is_ours frontend; then
    (cd "$ROOT/frontend" && exec nohup env -i PATH="$PATH" HOME="$HOME" LANG="${LANG:-en_US.UTF-8}" \
      VITE_LOCAL_API_URL="http://127.0.0.1:$LOCAL_PORT" \
      "$(command -v node)" "$ROOT/frontend/node_modules/vite/bin/vite.js" --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort \
      >> "$SANDBOX/logs-frontend.log" 2>&1 < /dev/null) &
    frontend_pid=$!
    STARTED+=(frontend)
    record_pid frontend "$frontend_pid" "$FRONTEND_PORT" || return 1
  fi
  wait_healthy frontend "$FRONTEND_PORT" || { echo "前端启动失败，查看 $SANDBOX/logs-frontend.log" >&2; return 1; }
  echo "个人后端 API：http://127.0.0.1:$LOCAL_PORT"
  STARTING=0
  echo "本地测试已就绪：http://127.0.0.1:$FRONTEND_PORT"
  echo "服务在后台运行，可以关闭终端；结束时双击“停止本地测试.command”。"
  if [[ "${TEAMHUB_NO_OPEN:-0}" != 1 ]]; then
    open "http://127.0.0.1:$FRONTEND_PORT" || echo "浏览器未自动打开，请访问上述地址。" >&2
  fi
}

start_desktop() {
  local desktop_app="$ROOT/dist-electron/retrieval-foundation-2026-10-23/mac-arm64/TeamHub.app"
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/photo-render-fix-2026-10-22/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/image-preorganize-fix-2026-10-21/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/multimodal-organize-2026-10-21/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/folder-ai-2026-10-21/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/document-cards-2026-10-21/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/preorganize-format-fix-2026-10-21/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/security-audit-2026-10-21/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/ai-preorganize-2026-10-20/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/ai-natural-chat-2026-10-19/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/hermes-connection-fix-2026-10-18/mac-arm64/TeamHub.app"
  fi
  if [[ ! -x "$desktop_app/Contents/MacOS/TeamHub" ]]; then
    desktop_app="$ROOT/dist-electron/mac-arm64/TeamHub.app"
  fi
  [[ -x "$desktop_app/Contents/MacOS/TeamHub" ]] || {
    echo "缺少桌面应用：$desktop_app，请先构建桌面版。" >&2; return 1;
  }
  # 用桌面自带 Node 检验就绪；helper 为 GUI 子进程移除 Node 模式变量。
  ELECTRON_RUN_AS_NODE=1 "$desktop_app/Contents/MacOS/TeamHub" \
    "$ROOT/scripts/launch-desktop.cjs" "$desktop_app" "$SANDBOX/logs-desktop.log" || return 1
  echo "需要本机云端联调时，另执行 bash scripts/mvp-dev.sh cloud。"
}

status() {
  local name port failed=0
  restore_owned_ports
  for name in local-backend cloud-backend; do
    if [[ "$name" == local-backend ]]; then port="$LOCAL_PORT"; else port="$CLOUD_PORT"; fi
    if ! is_ours "$name"; then echo "$name：未运行或归属不匹配"; failed=1
    elif ! curl --noproxy '*' -fsS -m 2 "http://127.0.0.1:$port/api/health"; then failed=1
    fi
    echo
  done
  if [[ -f "$PID_DIR/frontend.pid" ]]; then
    if is_ours frontend && curl --noproxy '*' -fsS -m 2 "http://127.0.0.1:$FRONTEND_PORT/" >/dev/null; then
      echo "frontend：已就绪 http://127.0.0.1:$FRONTEND_PORT"
    else echo "frontend：未就绪"; failed=1; fi
  fi
  return "$failed"
}

case "${1:-}" in local|cloud|all|start|start-all|start-desktop|stop|status) ;; *) echo "用法：$0 {local [a|b|c]|cloud|all|start [a|b|c]|start-all|start-desktop|stop|status}" >&2; exit 2 ;; esac
for tool in ps lsof curl; do command -v "$tool" >/dev/null || { echo "缺少命令：$tool" >&2; exit 1; }; done
mkdir -p "$SANDBOX"
# start/stop/status 串行，防止同时启动覆盖 PID 文件或停止尚未完成的启动。
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "启动器正在运行，或上次异常退出留下锁：$LOCK_DIR。确认无启动器运行后再移除该空目录。" >&2
  exit 1
fi
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
restore_owned_ports
case "$1" in
  local) start_local "${2:-a}" ;;
  cloud) start_cloud ;;
  start) start "${2:-a}" ;;
  all|start-all) start_all ;;
  start-desktop) start_desktop ;;
  stop) result=0; for name in frontend local-backend cloud-backend; do stop_one "$name" || result=1; done; exit "$result" ;;
  status) status ;;
esac
