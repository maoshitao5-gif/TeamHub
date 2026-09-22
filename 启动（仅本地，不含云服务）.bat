@echo off
chcp 65001 >nul
title TeamHub 本地模式

echo.
echo ╔══════════════════════════════════════════════════════╗
echo ║         TeamHub 本地模式（不含云服务）               ║
echo ╠══════════════════════════════════════════════════════╣
echo ║  本地后端   →  http://localhost:8001                 ║
echo ║  MinIO      →  http://localhost:9001  (API)          ║
echo ║  └控制台    →  http://localhost:9002                 ║
echo ║  前  端     →  http://localhost:5173                 ║
echo ║  Electron   →  桌面窗口（Vite 就绪后自动打开）      ║
echo ╚══════════════════════════════════════════════════════╝
echo.

REM ── MinIO 检查 ──
if not exist tools\minio.exe (
    echo [警告] tools\minio.exe 不存在，MinIO 将被跳过
    echo.
)
if not exist minio-data (
    mkdir minio-data
)

echo [启动] 清理端口并启动所有服务...
echo.
node dev-all.js --no-cloud

echo.
echo 所有服务已停止。按任意键关闭...
pause >nul
