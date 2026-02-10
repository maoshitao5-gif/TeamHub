#!/bin/bash

echo "========================================"
echo "团队文件管理系统 - 启动脚本"
echo "========================================"
echo ""

echo "[1/2] 启动后端服务..."
python main.py &
BACKEND_PID=$!
sleep 3

echo "[2/2] 启动前端服务..."
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

echo ""
echo "========================================"
echo "启动完成！"
echo "========================================"
echo ""
echo "后端服务: http://localhost:8080"
echo "前端服务: http://localhost:5173"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo ""

# 等待用户中断
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT
wait
