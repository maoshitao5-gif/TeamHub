@echo off
echo ========================================
echo   TeamHub Desktop - Development Mode
echo ========================================
echo.

REM 检查 .env 文件
if not exist .env (
    echo [!] .env file not found. Creating one...
    echo SECRET_KEY=teamhub-desktop-secret-key-%RANDOM% > .env
    echo [+] .env file created
    echo.
)

REM 检查 node_modules
if not exist node_modules (
    echo [!] node_modules not found. Installing dependencies...
    call npm install
    echo.
)

REM 检查前端 node_modules
if not exist frontend\node_modules (
    echo [!] Frontend dependencies not found. Installing...
    cd frontend
    call npm install
    cd ..
    echo.
)

echo [+] Starting TeamHub Desktop in development mode...
echo.
echo This will:
echo   1. Start Vite development server
echo   2. Launch Electron window
echo   3. Start Python backend automatically
echo.
echo Press Ctrl+C to stop
echo.

call npm run electron:dev
