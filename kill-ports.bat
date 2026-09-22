@echo off
echo Checking ports 5173 and 5174...
echo.

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING"') do (
    echo Killing process %%a on port 5173
    taskkill /F /PID %%a
)

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5174" ^| findstr "LISTENING"') do (
    echo Killing process %%a on port 5174
    taskkill /F /PID %%a
)

echo.
echo Ports cleared. You can now run: npm run electron:dev
pause
