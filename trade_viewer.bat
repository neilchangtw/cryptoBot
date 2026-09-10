@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] 找不到 .venv\Scripts\python.exe
  pause
  exit /b 1
)
".venv\Scripts\python.exe" trade_viewer.py
endlocal
