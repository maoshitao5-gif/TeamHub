@echo off
chcp 65001 >nul
title MinIO 本地存储

echo.
echo ╔══════════════════════════════════════════════════╗
echo ║           MinIO 本地对象存储                     ║
echo ╠══════════════════════════════════════════════════╣
echo ║  S3 API    →  http://localhost:9001              ║
echo ║  控制台    →  http://localhost:9002              ║
echo ║  账号/密码 →  minioadmin / minioadmin            ║
echo ╚══════════════════════════════════════════════════╝
echo.

set MINIO_ROOT_USER=minioadmin
set MINIO_ROOT_PASSWORD=minioadmin

if not exist tools\minio.exe (
    echo [错误] tools\minio.exe 不存在，请先运行下载脚本
    pause & exit /b 1
)

if not exist minio-data (
    mkdir minio-data
)

echo [启动] MinIO 正在启动...
tools\minio.exe server minio-data --address :9001 --console-address :9002

echo.
echo MinIO 已停止。按任意键关闭...
pause >nul
