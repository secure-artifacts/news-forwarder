@echo off
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-one-click.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Review the message above or server.err.log.
  pause
  exit /b 1
)

echo Startup successful. The browser is opening...
ping 127.0.0.1 -n 3 >nul
exit /b 0
