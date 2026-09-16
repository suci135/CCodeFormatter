@echo off
setlocal
cd /d "%~dp0.."
echo Building portable single-file CCodeFormatter.exe...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1" -OneFile
if errorlevel 1 (
    echo.
    echo Build failed. See the error message above.
    pause
    exit /b 1
)
echo.
echo Build complete. Send this file to others:
echo %CD%\dist\CCodeFormatter.exe
echo The temporary extraction folder is created under %%LOCALAPPDATA%%\Temp.
echo Press any key to close.
pause
