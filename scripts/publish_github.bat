@echo off
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_github.ps1" %*
if errorlevel 1 (
    echo.
    echo Upload failed. See the error message above.
    pause
    exit /b 1
)
echo.
echo Upload complete. Press any key to close.
pause
