@echo off
chcp 65001 >nul
title CryptoBot V11-E - ETH 雙策略
cd /d "%~dp0"

:: 先啟動儀表板（獨立視窗）
start "CryptoBot Dashboard" cmd /c "cd /d %CD% && call .venv\Scripts\activate && python dashboard\app.py"

:: 啟動交易機器人（本視窗）
call .venv\Scripts\activate
python main_eth.py
pause
