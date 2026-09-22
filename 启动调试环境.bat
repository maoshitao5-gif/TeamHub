@echo off
chcp 65001 >nul
title TeamHub 开发环境

echo.
echo ╔══════════════════════════════════════════════════════╗
echo ║           TeamHub 开发环境一键启动                   ║
echo ╠══════════════════════════════════════════════════════╣
echo ║  本地后端   →  http://localhost:8001                 ║
echo ║  MinIO      →  http://localhost:9001  (API)          ║
echo ║  └控制台    →  http://localhost:9002                 ║
echo ║  云  服务   →  http://localhost:9000                 ║
echo ║  前  端     →  http://localhost:5173                 ║
echo ║  Electron   →  桌面窗口（Vite 就绪后自动打开）      ║
echo ╚══════════════════════════════════════════════════════╝
echo.

REM ── 环境检查 ──
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause & exit /b 1
)
node --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Node.js，请先安装 Node.js 18+
    pause & exit /b 1
)

REM ── MinIO 检查 ──
if not exist tools\minio.exe (
    echo [警告] tools\minio.exe 不存在，MinIO 将被跳过
    echo        如需 MinIO：请先运行 下载MinIO.bat 或手动放置 tools\minio.exe
    echo.
)

REM ── minio-data 目录 ──
if not exist minio-data (
    mkdir minio-data
)

REM ── 依赖检查 ──
if not exist node_modules (
    echo [准备] 安装根目录依赖...
    call npm install
    echo.
)
if not exist frontend\node_modules (
    echo [准备] 安装前端依赖...
    cd frontend && call npm install && cd ..
    echo.
)
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo [准备] 安装 Python 依赖...
    pip install -r requirements.txt -q
    pip install -r cloud_backend\requirements.txt -q
    echo.
)
if not exist cloud_backend\.env.dev (
    echo [准备] 复制云服务配置文件...
    copy cloud_backend\.env.example cloud_backend\.env.dev >nul
    echo        已生成 cloud_backend\.env.dev
    echo.
)

echo [启动] 清理端口并启动所有服务...
echo.
node dev-all.js

echo.
echo 所有服务已停止。按任意键关闭...
pause >nul
