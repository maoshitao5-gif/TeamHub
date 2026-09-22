@echo off
echo ========================================
echo   TeamHub Desktop - Clean Start
echo ========================================
echo.

echo [1/3] Cleaning up old processes...
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5173" ^| findstr "LISTENING"') do (
    echo   Killing process %%a on port 5173
    taskkill /F /PID %%a 2>nul
)

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5174" ^| findstr "LISTENING"') do (
    echo   Killing process %%a on port 5174
    taskkill /F /PID %%a 2>nul
)

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8001" ^| findstr "LISTENING"') do (
    echo   Killing process %%a on port 8001
    taskkill /F /PID %%a 2>nul
)

echo   Ports cleaned
echo.

echo [2/3] Checking environment...
if not exist .env (
    echo   Creating .env file...
    echo SECRET_KEY=teamhub-desktop-secret-key-%RANDOM% > .env
)
echo   Environment OK
echo.

echo [3/3] Starting TeamHub Desktop...
echo.
echo This will:
echo   - Start Vite development server (auto-detect port)
echo   - Launch Electron window
echo   - Start Python backend automatically
echo.
echo Press Ctrl+C to stop
echo.

call npm run electron:dev
