#!/bin/bash
# Finder 双击入口：与终端当前目录无关。
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")" || exit 1
bash scripts/mvp-dev.sh stop
result=$?
if [ "$result" -ne 0 ]; then
  echo "操作未完成，请查看上方提示。按回车关闭。"
  read -r _
fi
exit "$result"
