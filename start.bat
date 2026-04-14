@echo off
chcp 65001 >nul
title CryptoBot V13 - 印鈔機監控台
cd /d "%~dp0"

:: 儀表板 = 控制中心，開啟時自動啟動機器人，關閉時自動停止
call .venv\Scripts\activate
python dashboard\app.py
pause
