#!/usr/bin/env bash
# Saros 一键启动：后端 FastAPI(8000) + 前端 Vite(5173)，Ctrl+C 同时停止
set -e
cd "$(dirname "$0")"

# 定位 conda 环境 saros（绝对路径，避免 shell profile 的 PATH 干扰）
CONDA_BASE="$(conda info --base)"
SAROS_PY="$CONDA_BASE/envs/saros/bin/python"

if [ ! -x "$SAROS_PY" ]; then
  echo "错误：未找到 conda 环境 saros（$SAROS_PY）"
  echo "请先创建：conda create -n saros python=3.10"
  exit 1
fi

(cd backend && "$SAROS_PY" -m uvicorn app.main:app --reload --port 8000) &
BACK_PID=$!

(cd frontend && npm run dev) &
FRONT_PID=$!

trap 'kill "$BACK_PID" "$FRONT_PID" 2>/dev/null' INT TERM EXIT

echo "前端 http://localhost:5173 ｜ 后端 http://127.0.0.1:8000（API 文档 /docs）"
wait
