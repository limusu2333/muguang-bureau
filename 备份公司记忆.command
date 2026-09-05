#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python3 ]; then
  echo "缺少 .venv，无法运行可校验备份。"
  read -r -p "回车关闭"
  exit 1
fi

.venv/bin/python3 工具/备份.py
echo "旧备份没有自动删除；需要清理时请先确认异盘副本。"
read -r -p "回车关闭"
