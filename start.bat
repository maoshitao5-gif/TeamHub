@echo off
chcp 65001 >nul
echo ========================================
echo 团队文件管理系统 - 启动脚本
echo ========================================
echo.

echo [1/2] 启动后端服务...
start "后端服务" cmd /k "python main.py"
timeout /t 3 /nobreak >nul

echo [2/2] 启动前端服务...
cd frontend

REM 检查 node_modules 是否存在
if not exist "node_modules" (
    echo 检测到依赖未安装，正在安装...
    echo 提示：首次安装可能需要1-3分钟，请耐心等待...
    echo.
    
    REM 配置npm代理
    call npm config set proxy http://127.0.0.1:7897
    call npm config set https-proxy http://127.0.0.1:7897
    call npm config set registry https://registry.npmjs.org/
    
    echo 已配置代理: http://127.0.0.1:7897
    echo.
    
    REM 执行安装（设置超时避免无限等待）
    call npm install --timeout=300000
    
    if errorlevel 1 (
        echo.
        echo ========================================
        echo 依赖安装失败！
        echo ========================================
        echo.
        echo 可能的原因：
        echo 1. 代理服务器未启动（127.0.0.1:7897）
        echo 2. 网络连接问题
        echo.
        echo 请检查代理服务是否正常运行
        echo 或尝试手动安装：
        echo   cd frontend
        echo   npm config set proxy http://127.0.0.1:7897
        echo   npm config set https-proxy http://127.0.0.1:7897
        echo   npm install
        echo.
        cd ..
        pause
        exit /b 1
    )
    echo.
    echo 依赖安装完成！
    echo.
)

echo 正在启动前端开发服务器...
start "前端服务" cmd /k "cd /d %~dp0frontend && npm run dev"
cd ..
timeout /t 2 /nobreak >nul
echo.
echo 提示：前端服务正在启动中，请查看"前端服务"窗口的启动信息
echo 如果看到"Local: http://localhost:5173"表示启动成功
echo.

echo.
echo ========================================
echo 启动完成！
echo ========================================
echo.
echo 后端服务: http://localhost:8001
echo 前端服务: http://localhost:5173
echo.
echo 按任意键关闭此窗口（服务将继续运行）...
pause >nul
