#!/usr/bin/env bash
# OPC 一键启动脚本（macOS / Linux）。用法：bash start.sh [--demo]
# 检查版本、安装依赖、创建配置文件都在 scripts/launch.py 里（和 Windows 的 start.bat 共用）
cd "$(dirname "$0")"

PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "[错误] 没有找到 Python。请安装 Python 3.10 或更高版本：https://www.python.org/downloads/"
  exit 1
fi
exec "$PY" scripts/launch.py "$@"
