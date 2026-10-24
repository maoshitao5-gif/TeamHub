#!/bin/bash
# Finder 双击入口：与终端当前目录无关。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")" || exit 1
bash scripts/mvp-dev.sh start-desktop
result=$?
if [ "$result" -ne 0 ]; then
  osascript -e 'display alert "TeamHub 本地测试未启动" message "请查看终端中的错误提示。桌面启动日志位于项目的 .mvp-sandbox/logs-desktop.log。"'
fi
exit "$result"
