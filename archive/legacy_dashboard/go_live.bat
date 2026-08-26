@echo off
chcp 65001 >nul
title CryptoBot Go-Live Pre-Flight Check
cd /d "%~dp0"

echo.
echo ============================================================
echo            GO-LIVE PRE-FLIGHT CHECK (Mainnet)
echo ============================================================
echo.

call .venv\Scripts\activate
if errorlevel 1 (
    echo [ERROR] Failed to activate .venv
    pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8
python _go_live_check.py
if errorlevel 1 (
    pause
    exit /b 1
)

echo.
echo Launching dashboard + bot...
python dashboard\app.py
pause
